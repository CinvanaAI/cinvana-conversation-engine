from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .bootstrap import (
    bootstrap,
    export,
    publish_contract_file,
    publish_definition_file,
    restore,
)
from .config import EnginePaths
from .db import Database
from .jsonutil import pretty_json
from .quarantine import QuarantineManager
from .repository import Repository
from .service import EngineService
from .singleton import SingletonLock


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _paths(value: str | None) -> EnginePaths:
    return EnginePaths.from_root(value or default_root())


def _print(value: Any) -> None:
    sys.stdout.write(pretty_json(value))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="conversation-engine")
    root.add_argument("--root", help="Conversation Engine subsystem root")
    commands = root.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create an identity-protected vault")
    init_mode = init.add_mutually_exclusive_group()
    init_mode.add_argument("--bootstrap", type=Path)
    init_mode.add_argument("--empty", action="store_true")

    commands.add_parser("upgrade", help="Upgrade a recognized vault")
    commands.add_parser("verify", help="Verify identity, schema, and integrity")

    backup = commands.add_parser("backup", help="Create a consistent SQLite backup")
    backup.add_argument("target", type=Path)

    run = commands.add_parser("run", help="Run the local coordinator")
    run.add_argument("--once", action="store_true")
    run.add_argument("--interval", type=float, default=1.0)
    run.add_argument("--intake-limit", type=int, default=50)
    run.add_argument("--work-limit", type=int, default=50)

    commands.add_parser("status", help="Read current engine counts")

    state = commands.add_parser("state", help="Read one message's live state")
    state.add_argument("message_id")

    resolve = commands.add_parser(
        "resolve", help="Resolve one stored private/public message version"
    )
    resolve.add_argument("message_id")
    resolve.add_argument("--audience", choices=("private", "public"), default="private")
    resolve.add_argument("--version", type=int, choices=range(0, 10), default=0)

    definition = commands.add_parser(
        "definition-publish", help="Publish an immutable definition version"
    )
    definition.add_argument("kind")
    definition.add_argument("name")
    definition.add_argument("path", type=Path)

    contract = commands.add_parser(
        "contract-publish", help="Publish an immutable stage contract version"
    )
    contract.add_argument("path", type=Path)

    definitions_export = commands.add_parser(
        "definitions-export", help="Export definitions, contracts, and exact heads"
    )
    definitions_export.add_argument("path", type=Path)

    definitions_restore = commands.add_parser(
        "definitions-restore",
        help="Restore exact definitions and contracts into an empty initialized vault",
    )
    definitions_restore.add_argument("path", type=Path)

    pause = commands.add_parser("pause", help="Persistently pause one boundary")
    pause.add_argument("target")
    pause.add_argument("reason")

    unpause = commands.add_parser("unpause", help="Resume one paused boundary")
    unpause.add_argument("target")
    unpause.add_argument("reason")

    drain = commands.add_parser("drain", help="Pause Intake while accepted work drains")
    drain.add_argument("reason")

    halt = commands.add_parser("halt", help="Persistently halt every engine operation")
    halt.add_argument("reason")

    resume = commands.add_parser("resume", help="Remove a persistent global halt")
    resume.add_argument("reason")

    quarantine_verify = commands.add_parser(
        "quarantine-verify", help="Verify a completed quarantine bundle"
    )
    quarantine_verify.add_argument("quarantine_id")

    quarantine_reintroduce = commands.add_parser(
        "quarantine-reintroduce",
        help="Return a repaired quarantine through normal Unprocessed Intake",
    )
    quarantine_reintroduce.add_argument("quarantine_id")

    return root


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    paths = _paths(arguments.root)
    database = Database(paths)
    connection = None
    try:
        if arguments.command == "init":
            identity = database.initialize()
            connection = database.connect()
            repository = Repository(connection)
            count = connection.execute(
                "SELECT COUNT(*) FROM definition_heads"
            ).fetchone()[0]
            seeded = (
                bootstrap(repository, arguments.bootstrap)
                if count == 0 and not arguments.empty
                else {"definitions": 0, "contracts": 0}
            )
            _print({"identity": identity, "seeded": seeded})
            return 0

        if arguments.command == "upgrade":
            database.upgrade()
            _print({"status": "upgraded"})
            return 0

        if arguments.command == "verify":
            _print(database.verify())
            return 0

        if arguments.command == "backup":
            _print(database.backup(arguments.target))
            return 0

        read_only = arguments.command in {"status", "state", "resolve"}
        connection = database.connect(read_only=read_only)
        repository = Repository(connection)

        if arguments.command == "status":
            _print(
                {
                    "halted": paths.halt_marker.exists(),
                    "database": repository.status(),
                    "unprocessed_files": sum(
                        1
                        for item in paths.unprocessed.rglob("*.json")
                        if item.is_file()
                    ),
                }
            )
        elif arguments.command == "state":
            _print(repository.current_state(arguments.message_id))
        elif arguments.command == "resolve":
            _print(
                repository.resolve_message_text(
                    arguments.message_id,
                    audience=arguments.audience,
                    version=arguments.version,
                )
            )
        elif arguments.command == "definition-publish":
            _print(
                publish_definition_file(
                    repository,
                    kind=arguments.kind,
                    name=arguments.name,
                    path=arguments.path,
                )
            )
        elif arguments.command == "contract-publish":
            _print(publish_contract_file(repository, path=arguments.path))
        elif arguments.command == "definitions-export":
            export(repository, arguments.path)
            _print({"status": "exported", "path": str(arguments.path.resolve())})
        elif arguments.command == "definitions-restore":
            _print(restore(repository, arguments.path))
        elif arguments.command in {"pause", "unpause", "drain"}:
            if arguments.command == "drain":
                target = "intake"
                paused = True
            else:
                target = arguments.target
                paused = arguments.command == "pause"
            repository.set_control(target, paused, arguments.reason)
            _print({"target": target, "paused": paused})
        elif arguments.command in {
            "halt",
            "resume",
            "quarantine-verify",
            "quarantine-reintroduce",
        }:
            quarantine = QuarantineManager(paths, repository)
            if arguments.command == "halt":
                quarantine.halt({"kind": "manual", "detail": arguments.reason})
                _print({"halted": True})
            elif arguments.command == "resume":
                quarantine.resume(arguments.reason)
                _print({"halted": False})
            elif arguments.command == "quarantine-verify":
                record = repository.quarantine_record(arguments.quarantine_id)
                quarantine.verify_bundle(paths.rejections / record["bundle_name"])
                _print({"verified": arguments.quarantine_id})
            else:
                target = quarantine.reintroduce(arguments.quarantine_id)
                _print({"reintroduced": arguments.quarantine_id, "target": str(target)})
        elif arguments.command == "run":
            service = EngineService(
                paths,
                repository,
                intake_limit=arguments.intake_limit,
                work_limit=arguments.work_limit,
            )
            with SingletonLock(paths.lock_file):
                if arguments.once:
                    _print(service.run_once())
                else:
                    service.run_forever(arguments.interval)
        else:
            raise AssertionError(arguments.command)
        return 0
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 2
    finally:
        if connection is not None:
            connection.close()
