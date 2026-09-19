from __future__ import annotations

from conversation_engine.envelope import load_candidate
from conversation_engine.repository import Repository
from conversation_engine.service import EngineService
from conftest import envelope, return_workspace, workspace_for_stage


def accept_and_dispatch(engine, text: str = "A public message."):
    candidate = load_candidate(
        envelope(engine.paths.unprocessed / "source.json", text=text)
    )
    accepted = engine.service.intake.accept(candidate)
    engine.service.run_once()
    return accepted


def count_for_stage(engine, table: str, message_id: str, stage: str) -> int:
    return engine.connection.execute(
        f"SELECT COUNT(*) FROM {table} WHERE message_id = ? AND stage = ?",
        (message_id, stage),
    ).fetchone()[0]


def test_leaf_result_stays_live_until_replacement_then_is_reclaimed(engine):
    accepted = accept_and_dispatch(engine)
    first_workspace = workspace_for_stage(
        engine.repository, accepted.message_id, "mapping"
    )
    return_workspace(
        engine,
        first_workspace,
        response={"discord_public_indexing": ["Alpha"]},
    )
    engine.service.run_once()
    first = engine.repository.current_result(accepted.message_id, "mapping")

    engine.repository.publish_definition(
        "instructions", "mapping", {"text": "Replacement Mapping instructions"}
    )
    engine.service.run_once()

    assert engine.repository.current_result(
        accepted.message_id, "mapping"
    )["result_id"] == first["result_id"]
    assert count_for_stage(
        engine, "stage_results", accepted.message_id, "mapping"
    ) == 1

    replacement = workspace_for_stage(
        engine.repository, accepted.message_id, "mapping"
    )
    return_workspace(
        engine,
        replacement,
        response={"discord_public_indexing": ["Beta"]},
    )
    report = engine.service.run_once()
    current = engine.repository.current_result(accepted.message_id, "mapping")

    assert current["result_id"] != first["result_id"]
    assert current["output"]["discord_public_indexing"] == ["Beta"]
    assert report["pruned_results"] >= 1
    assert engine.connection.execute(
        "SELECT COUNT(*) FROM stage_results WHERE result_id = ?",
        (first["result_id"],),
    ).fetchone()[0] == 0
    assert count_for_stage(
        engine, "stage_results", accepted.message_id, "mapping"
    ) == 1
    assert count_for_stage(engine, "tasks", accepted.message_id, "mapping") == 1


def test_upstream_result_is_pinned_until_live_dependents_move(engine):
    original = "Secret Name shared a plan."
    redactions = [
        {
            "start": 0,
            "end": 11,
            "replacement": "(name)",
            "category": "identity",
        }
    ]
    accepted = accept_and_dispatch(engine, original)
    safety = workspace_for_stage(
        engine.repository, accepted.message_id, "public_safety"
    )
    return_workspace(
        engine,
        safety,
        response={"decision": "Private", "redactions": redactions},
    )
    engine.service.run_once()
    first_safety = engine.repository.current_result(
        accepted.message_id, "public_safety"
    )

    first_public_versions = workspace_for_stage(
        engine.repository, accepted.message_id, "public_versions"
    )
    return_workspace(engine, first_public_versions)
    engine.service.run_once()
    assert engine.repository.current_result(
        accepted.message_id, "public_versions"
    ) is not None

    engine.repository.publish_definition(
        "instructions",
        "public_safety",
        {"text": "Replacement Public Safety instructions"},
    )
    engine.service.run_once()
    replacement_safety = workspace_for_stage(
        engine.repository, accepted.message_id, "public_safety"
    )
    return_workspace(
        engine,
        replacement_safety,
        response={"decision": "Private", "redactions": redactions},
    )
    engine.service.run_once()

    second_safety = engine.repository.current_result(
        accepted.message_id, "public_safety"
    )
    assert second_safety["result_id"] != first_safety["result_id"]
    assert engine.connection.execute(
        "SELECT COUNT(*) FROM stage_results WHERE result_id = ?",
        (first_safety["result_id"],),
    ).fetchone()[0] == 1

    replacement_public_versions = workspace_for_stage(
        engine.repository, accepted.message_id, "public_versions"
    )
    return_workspace(engine, replacement_public_versions)
    engine.service.run_once()

    assert engine.connection.execute(
        "SELECT COUNT(*) FROM stage_results WHERE result_id = ?",
        (first_safety["result_id"],),
    ).fetchone()[0] == 0
    assert count_for_stage(
        engine, "stage_results", accepted.message_id, "public_safety"
    ) == 1
    assert count_for_stage(
        engine, "stage_results", accepted.message_id, "public_versions"
    ) == 1


def test_definition_history_survives_message_result_replacement(engine):
    accepted = accept_and_dispatch(engine)
    first_workspace = workspace_for_stage(
        engine.repository, accepted.message_id, "mapping"
    )
    return_workspace(
        engine,
        first_workspace,
        response={"discord_public_indexing": ["Alpha"]},
    )
    engine.service.run_once()
    versions_before = engine.connection.execute(
        """
        SELECT COUNT(*) FROM definition_versions
        WHERE kind = 'instructions' AND name = 'mapping'
        """
    ).fetchone()[0]

    engine.repository.publish_definition(
        "instructions", "mapping", {"text": "Another Mapping definition"}
    )
    engine.service.run_once()
    replacement = workspace_for_stage(
        engine.repository, accepted.message_id, "mapping"
    )
    return_workspace(
        engine,
        replacement,
        response={"discord_public_indexing": ["Beta"]},
    )
    engine.service.run_once()

    assert engine.connection.execute(
        """
        SELECT COUNT(*) FROM definition_versions
        WHERE kind = 'instructions' AND name = 'mapping'
        """
    ).fetchone()[0] == versions_before + 1
    assert count_for_stage(
        engine, "stage_results", accepted.message_id, "mapping"
    ) == 1


def test_restart_reclaims_a_replaced_result_if_shutdown_preceded_sweep(engine):
    accepted = accept_and_dispatch(engine)
    first_workspace = workspace_for_stage(
        engine.repository, accepted.message_id, "mapping"
    )
    return_workspace(
        engine,
        first_workspace,
        response={"discord_public_indexing": ["Alpha"]},
    )
    engine.service.run_once()
    first = engine.repository.current_result(accepted.message_id, "mapping")

    engine.repository.publish_definition(
        "instructions", "mapping", {"text": "Restart-boundary instructions"}
    )
    engine.service.run_once()
    replacement = workspace_for_stage(
        engine.repository, accepted.message_id, "mapping"
    )
    return_workspace(
        engine,
        replacement,
        response={"discord_public_indexing": ["Beta"]},
    )

    engine.workspaces.reconcile()
    engine.workspaces.absorb_one()
    assert count_for_stage(
        engine, "stage_results", accepted.message_id, "mapping"
    ) == 2

    restarted_connection = engine.database.connect()
    try:
        restarted_repository = Repository(restarted_connection)
        restarted_service = EngineService(
            engine.paths,
            restarted_repository,
            intake_limit=500,
            work_limit=500,
        )
        report = restarted_service.run_once()

        assert report["pruned_results"] >= 1
        assert restarted_connection.execute(
            "SELECT COUNT(*) FROM stage_results WHERE result_id = ?",
            (first["result_id"],),
        ).fetchone()[0] == 0
        assert count_for_stage(
            engine, "stage_results", accepted.message_id, "mapping"
        ) == 1
    finally:
        restarted_connection.close()
