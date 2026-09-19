from __future__ import annotations

from conversation_engine import envelope as envelope_module
from conversation_engine.bootstrap import bootstrap
from conversation_engine.config import EnginePaths
from conversation_engine.db import Database
from conversation_engine.repository import Repository
from conversation_engine.service import EngineService
from conversation_engine.db import transaction
from conversation_engine.envelope import load_candidate
from conftest import envelope


def test_definition_change_is_durable_work_for_already_running_service(engine):
    candidate = load_candidate(envelope(engine.paths.unprocessed / "one.json"))
    accepted = engine.service.intake.accept(candidate)
    old_target = engine.repository.stage_target(
        accepted.message_id, "public_safety"
    )

    engine.repository.publish_definition(
        "instructions",
        "public_safety",
        {"text": "A definition committed by a separate publisher process."},
    )
    assert engine.connection.execute(
        "SELECT COUNT(*) FROM dirty_messages"
    ).fetchone()[0] == 1

    engine.repository.set_control("model_dispatch", True, "inspect planning only")
    engine.repository.set_control("deterministic", True, "inspect planning only")
    report = engine.service.run_once()
    new_target = engine.repository.stage_target(
        accepted.message_id, "public_safety"
    )
    assert report["planned"] >= 4
    assert new_target != old_target
    assert engine.connection.execute(
        "SELECT COUNT(*) FROM dirty_messages"
    ).fetchone()[0] == 0


def test_one_service_cycle_parses_each_input_only_once(engine, monkeypatch):
    total = 200
    for position in range(1, total + 1):
        envelope(
            engine.paths.unprocessed / f"{position:04d}.json",
            chat_id="load-chat",
            position=position,
            text=f"load message {position}",
            timestamp="2026-01-01T00:00:00-05:00",
        )

    calls = 0
    original = envelope_module.load_candidate

    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(envelope_module, "load_candidate", counted)
    engine.repository.set_control("model_dispatch", True, "load test")
    engine.repository.set_control("deterministic", True, "load test")
    report = engine.service.run_once()

    assert report["intake"] == total
    assert calls == total
    assert engine.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == total


def test_restart_with_dirty_work_needs_no_startup_only_flag(engine):
    candidate = load_candidate(envelope(engine.paths.unprocessed / "one.json"))
    accepted = engine.service.intake.accept(candidate)
    engine.repository.publish_definition(
        "instructions",
        "mapping",
        {"text": "new mapping definition before simulated restart"},
    )
    old_service = engine.service
    del old_service

    from conversation_engine.service import EngineService

    restarted = EngineService(
        engine.paths,
        engine.repository,
        intake_limit=50,
        work_limit=50,
    )
    restarted.repository.set_control("model_dispatch", True, "restart test")
    restarted.repository.set_control("deterministic", True, "restart test")
    restarted.run_once()

    task_id = engine.repository.stage_target(accepted.message_id, "mapping")
    task = engine.repository.task(task_id)
    definition_ids = {
        item["dependency_id"]
        for item in task["dependencies"]
        if item["dependency_kind"] == "definition"
    }
    current = engine.repository.current_definition("instructions", "mapping")
    assert current["definition_id"] in definition_ids


def test_sustained_service_load_survives_repeated_restarts(tmp_path):
    paths = EnginePaths.from_root(tmp_path / "sustained")
    database = Database(paths)
    database.initialize()
    connection = database.connect()
    repository = Repository(connection)
    bootstrap(repository)
    repository.publish_definition(
        "mapping_index",
        "discord_public",
        {
            "live_ready": True,
            "titles": [{"title": "Load", "definition": "Load test"}],
        },
    )
    repository.set_control("intake", False, "load test ready")
    repository.set_control("model_dispatch", True, "load test")
    repository.set_control("deterministic", True, "load test")
    connection.close()

    total = 120
    for position in range(1, total + 1):
        envelope(
            paths.unprocessed / f"{position:04d}.json",
            chat_id="restart-load",
            position=position,
            text=f"restart message {position}",
            timestamp="2026-01-01T00:00:00-05:00",
        )

    for _ in range(6):
        connection = database.connect()
        try:
            service = EngineService(
                paths,
                Repository(connection),
                intake_limit=20,
                work_limit=200,
            )
            service.run_once()
        finally:
            connection.close()

    connection = database.connect(read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == total
        assert connection.execute("SELECT COUNT(*) FROM dirty_messages").fetchone()[0] == 0
        assert not any(paths.unprocessed.glob("*.json"))
    finally:
        connection.close()
    assert database.verify()["integrity_check"] == "ok"