from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conversation_engine.db import transaction
from conversation_engine.config import STAGES
from conversation_engine.envelope import load_candidate
from conversation_engine.errors import ContractError, ItemFailure
from conftest import (
    envelope,
    return_workspace,
    task_for,
    workspace_folder,
    workspace_for_stage,
)


def accept(engine, text: str = "A concise public message."):
    candidate = load_candidate(
        envelope(engine.paths.unprocessed / "source.json", text=text)
    )
    return engine.service.intake.accept(candidate)


def test_multiple_updates_coalesce_behind_one_live_workspace(engine):
    accepted = accept(engine)
    initial = task_for(engine.repository, accepted.message_id, "public_safety")
    workspace = engine.workspaces.materialize(initial["task_id"])

    engine.repository.publish_definition(
        "instructions", "public_safety", {"text": "Safety instructions B"}
    )
    with transaction(engine.connection):
        engine.planner.plan_message(accepted.message_id)
    middle = task_for(engine.repository, accepted.message_id, "public_safety")

    engine.repository.publish_definition(
        "instructions", "public_safety", {"text": "Safety instructions C"}
    )
    with transaction(engine.connection):
        engine.planner.plan_message(accepted.message_id)
    latest = task_for(engine.repository, accepted.message_id, "public_safety")

    assert len({initial["task_id"], middle["task_id"], latest["task_id"]}) == 3
    with pytest.raises(ContractError):
        engine.workspaces.materialize(middle["task_id"])

    return_workspace(
        engine,
        workspace,
        response={"decision": "Public", "redactions": []},
    )
    engine.service.run_once()

    assert engine.connection.execute(
        "SELECT COUNT(*) FROM tasks WHERE task_id IN (?, ?)",
        (initial["task_id"], middle["task_id"]),
    ).fetchone()[0] == 0
    assert (
        engine.connection.execute(
            "SELECT COUNT(*) FROM workspaces WHERE task_id = ?",
            (middle["task_id"],),
        ).fetchone()[0]
        == 0
    )
    latest_workspace = engine.connection.execute(
        "SELECT state FROM workspaces WHERE task_id = ?",
        (latest["task_id"],),
    ).fetchone()
    assert latest_workspace is not None
    assert latest_workspace["state"] == "published"


def test_workspace_request_exposes_captured_contract_config(engine):
    accepted = accept(engine)
    task = task_for(engine.repository, accepted.message_id, "mapping")

    workspace = engine.workspaces.materialize(task["task_id"])

    assert workspace["request"]["captured_contract"]["config"] == {
        "context_before": 4,
        "context_after": 2,
    }
    assert (
        workspace_folder(engine, workspace) / "_discarded"
    ).is_dir()


def test_old_workspace_uses_captured_contract_after_head_changes(engine):
    accepted = accept(engine)
    task = task_for(engine.repository, accepted.message_id, "mapping")
    workspace = engine.workspaces.materialize(task["task_id"])
    old_contract = task["contract"]

    new_response_schema = {
        "type": "object",
        "required": ["discord_public_indexing", "confidence"],
        "additionalProperties": False,
        "properties": {
            "discord_public_indexing": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string"},
            },
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        },
    }
    engine.repository.publish_contract(
        stage="mapping",
        worker_policy="model",
        validator_key="mapping",
        validator_version=1,
        workspace_spec={
            "response_type": "json",
            "response_schema": new_response_schema,
        },
        output_schema=old_contract["output_schema"],
        config=old_contract["config"],
    )
    with transaction(engine.connection):
        engine.planner.plan_message(accepted.message_id)

    return_workspace(
        engine,
        workspace,
        response={"discord_public_indexing": ["Alpha"]},
    )
    report = engine.service.run_once()
    assert report["quarantined"] == 0
    assert report["pruned_results"] == 1
    assert engine.connection.execute(
        "SELECT COUNT(*) FROM tasks WHERE task_id = ?",
        (task["task_id"],),
    ).fetchone()[0] == 0


def test_paused_stage_still_absorbs_already_returned_work(engine):
    accepted = accept(engine)
    task = task_for(engine.repository, accepted.message_id, "mapping")
    workspace = engine.workspaces.materialize(task["task_id"])
    engine.repository.set_control("stage:mapping", True, "hold new mapping dispatch")
    return_workspace(
        engine,
        workspace,
        response={"discord_public_indexing": ["Beta"]},
    )

    report = engine.service.run_once()
    current = engine.repository.current_result(accepted.message_id, "mapping")
    assert report["absorbed"] == 1
    assert current["output"]["discord_public_indexing"] == ["Beta"]


def test_preparing_workspace_without_files_is_replanned(engine):
    accepted = accept(engine)
    task = task_for(engine.repository, accepted.message_id, "mapping")
    with transaction(engine.connection):
        workspace = engine.repository.create_workspace(task["task_id"])
    engine.workspaces.reconcile()

    assert (
        engine.connection.execute(
            "SELECT COUNT(*) FROM workspaces WHERE workspace_id = ?",
            (workspace["workspace_id"],),
        ).fetchone()[0]
        == 0
    )
    assert engine.repository.task(task["task_id"])["status"] == "planned"


def test_prepared_folder_publishes_after_restart_boundary(engine):
    accepted = accept(engine)
    task = task_for(engine.repository, accepted.message_id, "mapping")
    workspace = engine.workspaces.materialize(task["task_id"])
    engine.connection.execute(
        "UPDATE workspaces SET state = 'preparing' WHERE workspace_id = ?",
        (workspace["workspace_id"],),
    )
    engine.connection.execute(
        "UPDATE tasks SET status = 'preparing' WHERE task_id = ?",
        (task["task_id"],),
    )
    engine.connection.commit()

    engine.workspaces.reconcile()
    assert engine.repository.workspace(workspace["workspace_id"])["state"] == "published"


def test_published_todo_workspace_tolerates_transient_extra_file(engine):
    accepted = accept(engine)
    task = task_for(engine.repository, accepted.message_id, "mapping")
    workspace = engine.workspaces.materialize(task["task_id"])
    todo = workspace_folder(engine, workspace)
    (todo / ".response.tmp").write_text("partial", encoding="utf-8")

    engine.workspaces.reconcile()

    assert engine.repository.workspace(workspace["workspace_id"])["state"] == "published"
    assert todo.is_dir()


def test_returned_workspace_allows_discarded_folder(engine):
    accepted = accept(engine)
    task = task_for(engine.repository, accepted.message_id, "mapping")
    workspace = engine.workspaces.materialize(task["task_id"])
    todo = workspace_folder(engine, workspace)
    (todo / "response.json").write_text(
        '{"discord_public_indexing":["Alpha"]}', encoding="utf-8"
    )
    (todo / "_discarded" / "scratch.txt").write_text("notes", encoding="utf-8")
    done = workspace_folder(engine, workspace, "Done")
    os.replace(todo, done)

    engine.workspaces.reconcile()
    outcome = engine.workspaces.absorb_one()

    assert outcome["stage"] == "mapping"
    assert not workspace_folder(engine, workspace, "Claimed").exists()


def test_returned_workspace_rejects_unexpected_entries(engine):
    accepted = accept(engine)
    task = task_for(engine.repository, accepted.message_id, "mapping")
    workspace = engine.workspaces.materialize(task["task_id"])
    todo = workspace_folder(engine, workspace)
    (todo / "response.json").write_text(
        '{"discord_public_indexing":[]}', encoding="utf-8"
    )
    (todo / ".response.tmp").write_text("partial", encoding="utf-8")
    done = workspace_folder(engine, workspace, "Done")
    os.replace(todo, done)

    with pytest.raises(ItemFailure, match="unexpected entries"):
        engine.workspaces._verify_folder(workspace, done, require_outputs=True)


def test_claimed_folder_completes_done_claim_after_restart(engine):
    accepted = accept(engine)
    task = task_for(engine.repository, accepted.message_id, "mapping")
    workspace = engine.workspaces.materialize(task["task_id"])
    todo = workspace_folder(engine, workspace)
    (todo / "response.json").write_text(
        '{"discord_public_indexing":[]}', encoding="utf-8"
    )
    claimed = workspace_folder(engine, workspace, "Claimed")
    os.replace(todo, claimed)

    engine.workspaces.reconcile()
    assert engine.repository.workspace(workspace["workspace_id"])["state"] == "returned"


def test_end_to_end_promotes_all_independent_stage_heads(engine):
    accepted = accept(engine, "A concise public message.")
    engine.service.run_once()

    mapping = workspace_for_stage(
        engine.repository, accepted.message_id, "mapping"
    )
    safety = workspace_for_stage(
        engine.repository, accepted.message_id, "public_safety"
    )
    versions = workspace_for_stage(
        engine.repository, accepted.message_id, "original_versions"
    )
    return_workspace(
        engine,
        mapping,
        response={"discord_public_indexing": ["Alpha"]},
    )
    return_workspace(
        engine,
        safety,
        response={"decision": "Public", "redactions": []},
    )
    return_workspace(engine, versions)

    engine.service.run_once()
    state = engine.repository.current_state(accepted.message_id)
    assert set(state["stages"]) == {
        "intake",
        "mapping",
        "public_safety",
        "original_versions",
        "public_versions",
        "discord_original",
        "discord_public",
    }
    assert state["stages"]["public_versions"]["output"]["kind"] == "result_pointer"
    assert state["stages"]["discord_original"]["output"]["kind"] == "source_pointer"
    assert state["stages"]["discord_public"]["output"]["kind"] == "result_pointer"
    assert engine.repository.resolve_message_text(
        accepted.message_id, audience="public", version=5
    )["message"] == "A concise public message."
    pointers = engine.connection.execute(
        "SELECT stage, pointer_kind FROM current_product_pointers WHERE message_id = ?",
        (accepted.message_id,),
    ).fetchall()
    assert {(row["stage"], row["pointer_kind"]) for row in pointers} == {
        ("public_versions", "result_pointer"),
        ("discord_original", "source_pointer"),
        ("discord_public", "result_pointer"),
    }


def test_successful_suggestion_is_stored_as_one_stage_scoped_capture(engine):
    accepted = accept(engine)
    task = task_for(engine.repository, accepted.message_id, "mapping")
    workspace = engine.workspaces.materialize(task["task_id"])
    return_workspace(
        engine,
        workspace,
        response={"discord_public_indexing": ["Alpha"]},
        suggestion="Clarify the Alpha definition.",
    )

    engine.service.run_once()

    stored = (
        engine.paths.suggestions
        / "mapping"
        / f"{accepted.message_id}_{workspace['workspace_id']}.json"
    )
    payload = json.loads(stored.read_text(encoding="utf-8"))
    assert payload["suggestion_text"] == "Clarify the Alpha definition."
    assert payload["message_id"] == accepted.message_id
    assert payload["stage"] == "mapping"
    assert payload["task_id"] == task["task_id"]
    assert payload["workspace_id"] == workspace["workspace_id"]
    assert len(payload["suggestion_sha256"]) == 64
    assert {item.name for item in engine.paths.suggestions.iterdir()} == set(STAGES)
    assert [item.name for item in (engine.paths.suggestions / "mapping").iterdir()] == [
        stored.name
    ]
