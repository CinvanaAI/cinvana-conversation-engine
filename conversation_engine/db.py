from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .config import EnginePaths
from .errors import MigrationError, VaultIdentityError
from .jsonutil import sha256_bytes, write_json_atomic
from .timeutil import utc_now


SUBSYSTEM_ID = "conversation-engine"
FORMAT_VERSION = 1
MIGRATION_PACKAGE = "conversation_engine.migrations"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str
    sha256: str


def migrations() -> list[Migration]:
    records: list[Migration] = []
    root = files(MIGRATION_PACKAGE)
    for item in sorted(root.iterdir(), key=lambda value: value.name):
        if not item.name.endswith(".sql"):
            continue
        prefix, _, name = item.name.partition("_")
        try:
            version = int(prefix)
        except ValueError as exc:
            raise MigrationError(f"invalid migration filename: {item.name}") from exc
        payload = item.read_bytes()
        records.append(
            Migration(
                version=version,
                name=name.removesuffix(".sql"),
                sql=payload.decode("utf-8"),
                sha256=sha256_bytes(payload),
            )
        )
    expected = list(range(1, len(records) + 1))
    actual = [record.version for record in records]
    if actual != expected:
        raise MigrationError(f"migration sequence is not contiguous: {actual}")
    return records


class Database:
    def __init__(self, paths: EnginePaths):
        self.paths = paths

    @staticmethod
    def _configure(connection: sqlite3.Connection, *, read_only: bool) -> None:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if not read_only:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")

    def _raw(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            uri = self.paths.database.as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=5)
        else:
            connection = sqlite3.connect(self.paths.database, timeout=5)
        self._configure(connection, read_only=read_only)
        return connection

    def _validate_identity(self, connection: sqlite3.Connection) -> None:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vault_identity'"
        ).fetchone()
        if table is None:
            raise VaultIdentityError(
                f"refusing unrecognized SQLite database: {self.paths.database}"
            )
        row = connection.execute(
            "SELECT subsystem, format_version FROM vault_identity WHERE singleton = 1"
        ).fetchone()
        if row is None or row["subsystem"] != SUBSYSTEM_ID:
            raise VaultIdentityError("database does not identify as Conversation Engine")
        if row["format_version"] != FORMAT_VERSION:
            raise VaultIdentityError(
                f"unsupported vault format: {row['format_version']}"
            )

    def _verify_ledger(
        self,
        connection: sqlite3.Connection,
        *,
        require_latest: bool,
    ) -> None:
        expected = {item.version: item for item in migrations()}
        rows = connection.execute(
            "SELECT version, name, sha256 FROM schema_migrations ORDER BY version"
        ).fetchall()
        for row in rows:
            record = expected.get(row["version"])
            if record is None:
                raise MigrationError(
                    f"database has unknown future migration {row['version']}"
                )
            if row["name"] != record.name or row["sha256"] != record.sha256:
                raise MigrationError(
                    f"migration {row['version']} no longer matches its applied checksum"
                )
        if require_latest and len(rows) != len(expected):
            raise MigrationError("recognized database requires an explicit upgrade")

    def inspect_identity(self) -> dict[str, object]:
        if not self.paths.database.is_file():
            raise VaultIdentityError(f"database does not exist: {self.paths.database}")
        connection = self._raw(read_only=True)
        try:
            self._validate_identity(connection)
            self._verify_ledger(connection, require_latest=False)
            row = connection.execute("SELECT * FROM vault_identity").fetchone()
            return dict(row)
        finally:
            connection.close()

    def initialize(self) -> dict[str, object]:
        self.paths.create_runtime()
        if self.paths.database.exists() and self.paths.database.stat().st_size:
            identity = self.inspect_identity()
            self.upgrade()
            return identity
        if self.paths.database.exists():
            self.paths.database.unlink()

        connection = self._raw(read_only=False)
        try:
            connection.executescript(
                """
                BEGIN EXCLUSIVE;
                CREATE TABLE vault_identity (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    vault_uuid TEXT NOT NULL UNIQUE,
                    subsystem TEXT NOT NULL,
                    format_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                COMMIT;
                """
            )
            identity = {
                "singleton": 1,
                "vault_uuid": str(uuid4()),
                "subsystem": SUBSYSTEM_ID,
                "format_version": FORMAT_VERSION,
                "created_at": utc_now(),
            }
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO vault_identity(
                        singleton, vault_uuid, subsystem, format_version, created_at
                    ) VALUES(1, ?, ?, ?, ?)
                    """,
                    (
                        identity["vault_uuid"],
                        identity["subsystem"],
                        identity["format_version"],
                        identity["created_at"],
                    ),
                )
            self._apply_pending(connection)
            return identity
        except Exception:
            connection.close()
            if self.paths.database.exists():
                self.paths.database.unlink()
            raise
        finally:
            if connection:
                connection.close()

    def _apply_pending(self, connection: sqlite3.Connection) -> None:
        self._validate_identity(connection)
        self._verify_ledger(connection, require_latest=False)
        applied = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        for migration in migrations():
            if migration.version in applied:
                continue
            script = (
                "BEGIN IMMEDIATE;\n"
                + migration.sql
                + "\nINSERT INTO schema_migrations(version, name, sha256, applied_at) "
                + "VALUES("
                + str(migration.version)
                + ", "
                + repr(migration.name)
                + ", "
                + repr(migration.sha256)
                + ", "
                + repr(utc_now())
                + ");\nCOMMIT;"
            )
            try:
                connection.executescript(script)
            except Exception as exc:
                if connection.in_transaction:
                    connection.rollback()
                raise MigrationError(
                    f"migration {migration.version} failed: {exc}"
                ) from exc

    def upgrade(self) -> None:
        self.inspect_identity()
        connection = self._raw(read_only=False)
        try:
            self._apply_pending(connection)
            self._verify_ledger(connection, require_latest=True)
        finally:
            connection.close()

    def connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        self.inspect_identity()
        connection = self._raw(read_only=read_only)
        try:
            self._validate_identity(connection)
            self._verify_ledger(connection, require_latest=True)
            return connection
        except Exception:
            connection.close()
            raise

    def verify(self) -> dict[str, object]:
        connection = self.connect(read_only=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            return {
                "identity": dict(
                    connection.execute("SELECT * FROM vault_identity").fetchone()
                ),
                "integrity_check": integrity,
                "foreign_key_violations": [dict(row) for row in foreign_keys],
                "migrations": [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM schema_migrations ORDER BY version"
                    )
                ],
            }
        finally:
            connection.close()

    def backup(self, target: Path) -> dict[str, object]:
        target = target.expanduser().resolve()
        if target.exists():
            raise FileExistsError(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self.connect(read_only=True)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        payload = target.read_bytes()
        manifest = {
            "created_at": utc_now(),
            "database": target.name,
            "bytes": len(payload),
            "sha256": sha256_bytes(payload),
            "identity": self.inspect_identity(),
        }
        write_json_atomic(target.with_suffix(target.suffix + ".manifest.json"), manifest)
        return manifest


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    if connection.in_transaction:
        yield connection
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
