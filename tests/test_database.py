from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from conversation_engine.bootstrap import bootstrap, export, restore
from conversation_engine.config import EnginePaths
from conversation_engine.db import Database
from conversation_engine.errors import ContractError, MigrationError, VaultIdentityError
from conversation_engine.repository import Repository


def test_unknown_database_is_refused_without_mutation(tmp_path: Path):
    paths = EnginePaths.from_root(tmp_path / "unknown")
    paths.database.parent.mkdir(parents=True)
    connection = sqlite3.connect(paths.database)
    connection.execute("CREATE TABLE legacy_only(value TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(VaultIdentityError):
        Database(paths).initialize()

    connection = sqlite3.connect(paths.database)
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    connection.close()
    assert names == {"legacy_only"}


def test_migration_checksum_drift_is_refused(engine):
    engine.connection.execute(
        "UPDATE schema_migrations SET sha256 = 'tampered' WHERE version = 1"
    )
    engine.connection.commit()
    with pytest.raises(MigrationError):
        engine.database.connect()


def test_integrity_and_foreign_keys_are_clean(engine):
    report = engine.database.verify()
    assert report["integrity_check"] == "ok"
    assert report["foreign_key_violations"] == []
    assert report["identity"]["subsystem"] == "conversation-engine"
    assert report["migrations"][0]["sha256"]


def test_backup_is_a_restorable_identity_preserving_vault(engine, tmp_path: Path):
    target = tmp_path / "backup.sqlite3"
    manifest = engine.database.backup(target)
    assert manifest["bytes"] == target.stat().st_size

    restored_paths = EnginePaths.from_root(tmp_path / "restored")
    restored_paths.create_runtime()
    restored_paths.database.write_bytes(target.read_bytes())
    restored = Database(restored_paths)
    report = restored.verify()
    assert report["identity"]["vault_uuid"] == manifest["identity"]["vault_uuid"]


def test_definition_and_contract_export_restores_exact_heads(engine, tmp_path: Path):
    bundle = tmp_path / "definitions.json"
    export(engine.repository, bundle)
    original = json.loads(bundle.read_text(encoding="utf-8"))

    paths = EnginePaths.from_root(tmp_path / "empty")
    database = Database(paths)
    database.initialize()
    connection = database.connect()
    try:
        repository = Repository(connection)
        outcome = restore(repository, bundle)
        assert outcome == {"definitions": 7, "contracts": 6}
        recovered = repository.definition_bundle()
        assert recovered["heads"] == original["heads"]
        assert recovered["contract_heads"] == original["contract_heads"]
    finally:
        connection.close()


def test_exact_restore_refuses_nonempty_vault(engine, tmp_path: Path):
    bundle = tmp_path / "definitions.json"
    export(engine.repository, bundle)
    with pytest.raises(ContractError):
        restore(engine.repository, bundle)


def test_fresh_bootstrap_keeps_intake_paused(tmp_path: Path):
    paths = EnginePaths.from_root(tmp_path / "fresh")
    database = Database(paths)
    database.initialize()
    connection = database.connect()
    try:
        repository = Repository(connection)
        bootstrap(repository)
        assert repository.is_paused("intake")
        mapping = repository.current_definition("mapping_index", "discord_public")
        assert mapping["payload"]["live_ready"] is False
    finally:
        connection.close()


def test_placeholder_mapping_index_blocks_intake_approval(tmp_path: Path):
    paths = EnginePaths.from_root(tmp_path / "fresh")
    database = Database(paths)
    database.initialize()
    connection = database.connect()
    try:
        repository = Repository(connection)
        bootstrap(repository)
        with pytest.raises(ContractError):
            repository.set_control("intake", False, "not actually ready")
    finally:
        connection.close()