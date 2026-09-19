from __future__ import annotations

import json

from conversation_engine.envelope import load_candidate
from conftest import envelope, return_workspace, workspace_folder, workspace_for_stage


def accept_and_dispatch(engine, text: str):
    candidate = load_candidate(
        envelope(engine.paths.unprocessed / "source.json", text=text)
    )
    accepted = engine.service.intake.accept(candidate)
    engine.service.run_once()
    return accepted


def test_source_and_mapping_context_are_referenced_not_copied(engine):
    first_text = "A neighboring message with a uniquely identifiable sentence."
    first = load_candidate(
        envelope(
            engine.paths.unprocessed / "first.json",
            text=first_text,
            position=1,
        )
    )
    engine.service.intake.accept(first)
    second = load_candidate(
        envelope(
            engine.paths.unprocessed / "second.json",
            text="The target message.",
            position=2,
        )
    )
    accepted = engine.service.intake.accept(second)

    columns = {
        row["name"]
        for row in engine.connection.execute("PRAGMA table_info(source_revisions)")
    }
    assert "message_text" in columns
    assert "envelope_json" not in columns

    task_id = engine.repository.stage_target(accepted.message_id, "mapping")
    task = engine.repository.task(task_id)
    assert task["payload"]["context"]
    assert all("text" not in item for item in task["payload"]["context"])

    workspace = engine.workspaces.materialize(task_id)
    context = (
        workspace_folder(engine, workspace) / "mapping_context.md"
    ).read_text(encoding="utf-8")
    assert first_text in context


def test_compact_results_store_recipes_and_resolve_on_demand(engine):
    original = "Secret Name " + ("a" * 2088)
    public = "(name) " + ("a" * 2088)
    accepted = accept_and_dispatch(engine, original)

    mapping = workspace_for_stage(engine.repository, accepted.message_id, "mapping")
    safety = workspace_for_stage(
        engine.repository, accepted.message_id, "public_safety"
    )
    original_versions = workspace_for_stage(
        engine.repository, accepted.message_id, "original_versions"
    )
    discord_private = workspace_for_stage(
        engine.repository, accepted.message_id, "discord_original"
    )

    return_workspace(
        engine,
        mapping,
        response={"discord_public_indexing": ["Alpha"]},
    )
    return_workspace(
        engine,
        safety,
        response={
            "decision": "Private",
            "redactions": [
                {
                    "start": 0,
                    "end": 11,
                    "replacement": "(name)",
                    "category": "identity",
                }
            ],
        },
    )
    return_workspace(engine, original_versions, versions=["Private v1"])
    return_workspace(
        engine,
        discord_private,
        response={
            "chunks": [
                {"index": 1, "start": 0, "end": 2000},
                {"index": 2, "start": 2000, "end": len(original)},
            ]
        },
    )
    engine.service.run_once()

    safety_result = engine.repository.current_result(
        accepted.message_id, "public_safety"
    )["output"]
    assert set(safety_result) == {"kind", "source", "decision", "redactions"}
    assert original not in json.dumps(safety_result)
    assert public not in json.dumps(safety_result)

    private_ranges = engine.repository.current_result(
        accepted.message_id, "discord_original"
    )["output"]
    assert private_ranges["chunks"] == [
        {"index": 1, "start": 0, "end": 2000},
        {"index": 2, "start": 2000, "end": len(original)},
    ]
    assert all(set(item) == {"index", "start", "end"} for item in private_ranges["chunks"])

    versions_result = engine.repository.current_result(
        accepted.message_id, "original_versions"
    )["output"]
    assert versions_result["versions"][0]["message"] == "Private v1"
    assert all(
        "message" not in item for item in versions_result["versions"][1:]
    )

    public_versions = workspace_for_stage(
        engine.repository, accepted.message_id, "public_versions"
    )
    public_versions_folder = workspace_folder(engine, public_versions)
    assert (public_versions_folder / "message.txt").read_text(encoding="utf-8") == public

    discord_public = workspace_for_stage(
        engine.repository, accepted.message_id, "discord_public"
    )
    return_workspace(engine, public_versions, versions=["Public v1"])
    return_workspace(
        engine,
        discord_public,
        response={
            "chunks": [
                {"index": 1, "start": 0, "end": 2000},
                {"index": 2, "start": 2000, "end": len(public)},
            ]
        },
    )
    engine.service.run_once()

    public_ranges = engine.repository.current_result(
        accepted.message_id, "discord_public"
    )["output"]
    assert public_ranges["chunks"][-1]["end"] == len(public)
    assert public not in json.dumps(public_ranges)

    assert engine.repository.resolve_message_text(
        accepted.message_id, audience="private", version=9
    )["message"] == "Private v1"
    assert engine.repository.resolve_message_text(
        accepted.message_id, audience="public", version=9
    )["message"] == "Public v1"

    redaction_row = engine.connection.execute(
        "SELECT * FROM current_public_safety_redactions WHERE message_id = ?",
        (accepted.message_id,),
    ).fetchone()
    assert dict(redaction_row) | {} == {
        "message_id": accepted.message_id,
        "result_id": engine.repository.current_result(
            accepted.message_id, "public_safety"
        )["result_id"],
        "redaction_number": 1,
        "start": 0,
        "end": 11,
        "replacement": "(name)",
        "category": "identity",
    }

    range_rows = engine.connection.execute(
        """
        SELECT audience, chunk_index, start, end
        FROM current_discord_ranges
        WHERE message_id = ?
        ORDER BY audience, chunk_index
        """,
        (accepted.message_id,),
    ).fetchall()
    assert len(range_rows) == 4
    assert {row["audience"] for row in range_rows} == {"private", "public"}

    version_rows = engine.connection.execute(
        """
        SELECT audience, version, storage_kind, pointer_source_level
        FROM current_message_versions
        WHERE message_id = ?
        """,
        (accepted.message_id,),
    ).fetchall()
    assert len(version_rows) == 18
    assert sum(row["storage_kind"] == "authored" for row in version_rows) == 2
    assert sum(row["storage_kind"] == "pointer" for row in version_rows) == 16

    assert engine.connection.execute(
        "SELECT COUNT(*) FROM source_revisions WHERE message_text = ?",
        (original,),
    ).fetchone()[0] == 1
    assert engine.connection.execute(
        "SELECT COUNT(*) FROM tasks WHERE payload_json LIKE ?",
        (f"%{original}%",),
    ).fetchone()[0] == 0
    assert engine.connection.execute(
        "SELECT COUNT(*) FROM stage_results WHERE output_json LIKE ?",
        (f"%{original}%",),
    ).fetchone()[0] == 0
    assert engine.connection.execute(
        "SELECT COUNT(*) FROM stage_results WHERE output_json LIKE ?",
        (f"%{public}%",),
    ).fetchone()[0] == 0
