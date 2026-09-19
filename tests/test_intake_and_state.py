from __future__ import annotations

import json
from pathlib import Path

from conversation_engine.db import transaction
from conversation_engine.envelope import load_candidate
from conftest import envelope, task_for


def accept_one(engine, path: Path):
    candidate = load_candidate(path)
    assert candidate.error is None
    return engine.service.intake.accept(candidate)


def test_intake_acceptance_and_initial_planning_are_one_unit(engine):
    source = envelope(engine.paths.unprocessed / "one.json")
    outcome = accept_one(engine, source)

    assert outcome.status == "accepted"
    assert not source.exists()
    assert engine.repository.current_result(outcome.message_id, "intake") is not None
    stages = {
        row["stage"]
        for row in engine.connection.execute(
            "SELECT stage FROM stage_targets WHERE message_id = ?",
            (outcome.message_id,),
        )
    }
    assert stages == {
        "mapping",
        "public_safety",
        "original_versions",
        "discord_original",
    }


def test_exact_duplicate_is_idempotent(engine):
    first = envelope(engine.paths.unprocessed / "first.json")
    accepted = accept_one(engine, first)
    duplicate = envelope(engine.paths.unprocessed / "duplicate.json")
    outcome = accept_one(engine, duplicate)

    assert outcome.status == "duplicate"
    assert outcome.message_id == accepted.message_id
    assert engine.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert (
        engine.connection.execute("SELECT COUNT(*) FROM source_revisions").fetchone()[0]
        == 1
    )


def test_same_chat_position_creates_new_immutable_revision(engine):
    first = accept_one(
        engine,
        envelope(engine.paths.unprocessed / "first.json", text="First text"),
    )
    old_mapping = engine.repository.stage_target(first.message_id, "mapping")
    second = accept_one(
        engine,
        envelope(engine.paths.unprocessed / "second.json", text="Second text"),
    )

    assert second.status == "revised"
    revisions = engine.connection.execute(
        """
        SELECT revision_number, message_text FROM source_revisions
        WHERE message_id = ? ORDER BY revision_number
        """,
        (first.message_id,),
    ).fetchall()
    assert [(row[0], row[1]) for row in revisions] == [
        (1, "First text"),
        (2, "Second text"),
    ]
    assert engine.repository.stage_target(first.message_id, "mapping") != old_mapping


def test_new_neighbor_only_dirties_bounded_mapping_context(engine):
    outcomes = []
    for position in range(1, 10):
        outcomes.append(
            accept_one(
                engine,
                envelope(
                    engine.paths.unprocessed / f"{position}.json",
                    position=position,
                    text=f"message {position}",
                    timestamp=f"2026-01-01T00:{position:02d}:00-05:00",
                ),
            )
        )
    engine.connection.execute("DELETE FROM dirty_messages")
    engine.connection.commit()

    accept_one(
        engine,
        envelope(
            engine.paths.unprocessed / "replacement.json",
            position=5,
            text="replacement five",
            timestamp="2026-01-02T00:00:00-05:00",
        ),
    )
    dirty = {
        row["position"]
        for row in engine.connection.execute(
            """
            SELECT messages.position
            FROM dirty_messages
            JOIN messages ON messages.message_id = dirty_messages.message_id
            """
        )
    }
    assert dirty == {3, 4, 6, 7, 8, 9}


def test_current_result_remains_live_until_replacement_promotes(engine):
    accepted = accept_one(
        engine,
        envelope(engine.paths.unprocessed / "one.json"),
    )
    task = task_for(engine.repository, accepted.message_id, "public_safety")
    first_output = {
        "decision": "Public",
        "redactions": [],
        "message": "Hello from the test message.",
        "char_count": 28,
        "message_sha256": "first-hash",
    }
    first = engine.repository.promote_result(task["task_id"], first_output)
    first_head = engine.repository.current_result(
        accepted.message_id, "public_safety"
    )["result_id"]

    engine.repository.publish_definition(
        "instructions",
        "public_safety",
        {"text": "Replacement safety instructions"},
    )
    with transaction(engine.connection):
        engine.planner.plan_message(accepted.message_id)

    assert (
        engine.repository.current_result(accepted.message_id, "public_safety")[
            "result_id"
        ]
        == first_head
    )
    replacement = task_for(
        engine.repository, accepted.message_id, "public_safety"
    )
    assert replacement["task_id"] != task["task_id"]
    second = engine.repository.promote_result(
        replacement["task_id"],
        {
            "decision": "Public",
            "redactions": [],
            "message": "Hello from the test message.",
            "char_count": 28,
            "message_sha256": "second-hash",
        },
    )
    assert first["disposition"] == "promoted"
    assert second["disposition"] == "promoted"
    assert (
        engine.repository.current_result(accepted.message_id, "public_safety")[
            "result_id"
        ]
        == second["result_id"]
    )


def test_intake_pause_leaves_files_untouched(engine):
    engine.repository.set_control("intake", True, "pause test")
    source = envelope(engine.paths.unprocessed / "paused.json")
    report = engine.service.run_once()
    assert report["intake"] == 0
    assert source.exists()
