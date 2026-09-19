from __future__ import annotations

from pathlib import Path

import pytest

from conversation_engine.envelope import load_candidate
from conftest import envelope, return_workspace, workspace_folder, workspace_for_stage


def accept(engine, text: str = "private source text"):
    candidate = load_candidate(
        envelope(engine.paths.unprocessed / "source.json", text=text)
    )
    return engine.service.intake.accept(candidate)


def test_invalid_return_totally_isolates_message_and_reintroduces(engine):
    accepted = accept(engine)
    engine.service.run_once()
    mapping = workspace_for_stage(
        engine.repository, accepted.message_id, "mapping"
    )
    return_workspace(
        engine,
        mapping,
        response={"discord_public_indexing": ["Unknown title"]},
        suggestion="This should remain inspectable.",
    )

    report = engine.service.run_once()
    assert report["quarantined"] == 1
    assert engine.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    record = engine.connection.execute(
        "SELECT * FROM quarantine_records ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert record["state"] == "completed"
    assert record["snapshot_json"] == "{}"
    assert record["source_path"] is None
    assert record["message_id"] is None
    assert engine.connection.execute(
        "SELECT COUNT(*) FROM audit_events WHERE message_id = ?",
        (accepted.message_id,),
    ).fetchone()[0] == 0

    bundle = engine.paths.rejections / record["bundle_name"]
    engine.service.quarantine.verify_bundle(bundle)
    assert bundle.parent.name == "mapping"
    assert (bundle / "source_envelope.json").is_file()
    assert (bundle / "database_snapshot.json").is_file()
    assert any((bundle / "Workspaces").rglob("Suggestions"))
    for table in ("messages", "tasks", "stage_results", "workspaces"):
        assert engine.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0

    target = engine.service.quarantine.reintroduce(record["quarantine_id"])
    assert target.is_file()
    engine.service.quarantine.verify_bundle(bundle)


def test_six_quarantines_in_sixty_minutes_persistently_halt(engine):
    for index in range(6):
        path = engine.paths.unprocessed / f"invalid_{index}.json"
        path.write_text("{not-json", encoding="utf-8")

    report = engine.service.run_once()
    assert report["quarantined"] == 6
    assert report["halted"] is True
    assert engine.paths.halt_marker.is_file()
    assert (
        engine.connection.execute(
            "SELECT COUNT(*) FROM quarantine_records WHERE state = 'completed'"
        ).fetchone()[0]
        == 6
    )

    halted = engine.service.run_once()
    assert halted["halted"] is True
    assert halted["intake"] == 0

    engine.service.quarantine.resume("test repair complete")
    assert not engine.paths.halt_marker.exists()


def test_invalid_envelope_never_enters_live_database(engine):
    source = engine.paths.unprocessed / "bad.json"
    source.write_text("[]", encoding="utf-8")
    report = engine.service.run_once()

    assert report["quarantined"] == 1
    assert engine.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert not source.exists()
    bundle = next((engine.paths.rejections / "intake").iterdir())
    assert any((bundle / "External").iterdir())


def test_manual_halt_blocks_every_mutating_boundary(engine):
    source = envelope(engine.paths.unprocessed / "waiting.json")
    engine.service.quarantine.halt({"kind": "manual", "detail": "test"})
    report = engine.service.run_once()

    assert report["halted"] is True
    assert source.exists()
    assert engine.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_tampered_published_input_quarantines_the_whole_message(engine):
    accepted = accept(engine)
    engine.service.run_once()
    mapping = workspace_for_stage(
        engine.repository, accepted.message_id, "mapping"
    )
    source = workspace_folder(engine, mapping) / "message.txt"
    source.write_text("tampered", encoding="utf-8")

    report = engine.service.run_once()
    assert report["quarantined"] == 1
    assert engine.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0


def test_rejection_collects_previously_absorbed_stage_suggestions(engine):
    accepted = accept(engine)
    engine.service.run_once()
    mapping = workspace_for_stage(
        engine.repository, accepted.message_id, "mapping"
    )
    return_workspace(
        engine,
        mapping,
        response={"discord_public_indexing": ["Alpha"]},
        suggestion="Keep this Mapping proposal.",
    )
    engine.service.run_once()

    captured = (
        engine.paths.suggestions
        / "mapping"
        / f"{accepted.message_id}_{mapping['workspace_id']}.json"
    )
    assert captured.is_file()

    public_safety = workspace_for_stage(
        engine.repository, accepted.message_id, "public_safety"
    )
    source = workspace_folder(engine, public_safety) / "message.txt"
    source.write_text("tampered", encoding="utf-8")
    report = engine.service.run_once()

    assert report["quarantined"] == 1
    assert not captured.exists()
    record = engine.connection.execute(
        "SELECT * FROM quarantine_records ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    bundle = engine.paths.rejections / record["bundle_name"]
    rejected = next((bundle / "Suggestions").glob("s*.json"))
    assert rejected.is_file()
    assert "Keep this Mapping proposal." in rejected.read_text(encoding="utf-8")


def test_rejection_resumes_after_artifacts_move_before_bundle_publish(
    engine,
    monkeypatch: pytest.MonkeyPatch,
):
    accepted = accept(engine)
    engine.service.run_once()
    mapping = workspace_for_stage(
        engine.repository, accepted.message_id, "mapping"
    )
    return_workspace(
        engine,
        mapping,
        response={"discord_public_indexing": ["Alpha"]},
        suggestion="Keep this through restart.",
    )
    engine.service.run_once()
    captured = (
        engine.paths.suggestions
        / "mapping"
        / f"{accepted.message_id}_{mapping['workspace_id']}.json"
    )

    public_safety = workspace_for_stage(
        engine.repository, accepted.message_id, "public_safety"
    )
    source = workspace_folder(engine, public_safety) / "message.txt"
    source.write_text("tampered", encoding="utf-8")
    manager = engine.service.quarantine
    real_move = manager._move_message_artifacts

    def interrupt_after_move(record, temporary):
        real_move(record, temporary)
        raise RuntimeError("simulated process exit")

    monkeypatch.setattr(manager, "_move_message_artifacts", interrupt_after_move)
    with pytest.raises(RuntimeError, match="simulated process exit"):
        engine.service.run_once()

    assert not captured.exists()
    assert engine.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    monkeypatch.setattr(manager, "_move_message_artifacts", real_move)
    manager.resume_pending()

    record = engine.connection.execute(
        "SELECT * FROM quarantine_records ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    bundle = engine.paths.rejections / record["bundle_name"]
    manager.verify_bundle(bundle)
    assert record["state"] == "completed"
    assert engine.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert "Keep this through restart." in next(
        (bundle / "Suggestions").glob("s_*.json")
    ).read_text(encoding="utf-8")


def test_unknown_todo_folder_is_externally_quarantined(engine):
    unknown = engine.paths.workspace_todo_for_stage("mapping") / "unknown_workspace"
    unknown.mkdir()
    (unknown / "response.json").write_text("{}", encoding="utf-8")

    report = engine.service.run_once()
    assert report["quarantined"] == 1
    assert not unknown.exists()
    bundle = next((engine.paths.rejections / "workspaces").iterdir())
    assert (bundle / "External" / "unknown_workspace").is_dir()
