from __future__ import annotations

from conversation_engine.envelope import load_candidate
from conftest import envelope, return_workspace, workspace_for_stage


def accept_and_dispatch(engine, text: str):
    candidate = load_candidate(
        envelope(engine.paths.unprocessed / "source.json", text=text)
    )
    accepted = engine.service.intake.accept(candidate)
    engine.service.run_once()
    return accepted


def test_private_safety_creates_public_model_work_and_short_source_pointer(engine):
    text = "Secret Name shared a plan."
    accepted = accept_and_dispatch(engine, text)
    safety = workspace_for_stage(
        engine.repository, accepted.message_id, "public_safety"
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
    engine.service.run_once()

    safety_result = engine.repository.current_result(
        accepted.message_id, "public_safety"
    )
    assert safety_result["output"] == {
        "kind": "redactions",
        "source": {
            "kind": "source_revision",
            "id": accepted.revision_id,
            "sha256": engine.repository.revision(accepted.revision_id)[
                "message_sha256"
            ],
        },
        "decision": "Private",
        "redactions": [
            {
                "start": 0,
                "end": 11,
                "replacement": "(name)",
                "category": "identity",
            }
        ],
    }
    assert engine.repository.resolve_message_text(
        accepted.message_id, audience="public"
    )["message"] == "(name) shared a plan."
    discord_public = engine.repository.current_result(
        accepted.message_id, "discord_public"
    )
    assert discord_public["output"]["kind"] == "source_pointer"
    public_versions = workspace_for_stage(
        engine.repository, accepted.message_id, "public_versions"
    )
    assert public_versions["state"] == "published"


def test_long_discord_work_validates_exact_ranges(engine):
    text = "a" * 2000 + "b" * 100
    accepted = accept_and_dispatch(engine, text)
    discord = workspace_for_stage(
        engine.repository, accepted.message_id, "discord_original"
    )
    return_workspace(
        engine,
        discord,
        response={
            "chunks": [
                {"index": 1, "start": 0, "end": 2000},
                {"index": 2, "start": 2000, "end": 2100},
            ]
        },
    )
    engine.service.run_once()

    result = engine.repository.current_result(
        accepted.message_id, "discord_original"
    )
    assert result["output"]["kind"] == "ranges"
    assert result["output"]["chunks"] == [
        {"index": 1, "start": 0, "end": 2000},
        {"index": 2, "start": 2000, "end": 2100},
    ]
    assert all("message" not in item for item in result["output"]["chunks"])


def test_versions_fill_forward_after_semantic_floor(engine):
    accepted = accept_and_dispatch(engine, "abcdef")
    versions = workspace_for_stage(
        engine.repository, accepted.message_id, "original_versions"
    )
    return_workspace(engine, versions, versions=["abc"])
    engine.service.run_once()

    result = engine.repository.current_result(
        accepted.message_id, "original_versions"
    )["output"]
    assert result["authored_count"] == 1
    assert result["semantic_floor"] == 1
    assert result["versions"][0]["kind"] == "authored"
    assert result["versions"][1] == {
        "level": 2,
        "kind": "pointer",
        "source_level": 1,
    }
    assert result["versions"][8] == {
        "level": 9,
        "kind": "pointer",
        "source_level": 1,
    }
    assert engine.repository.resolve_message_text(
        accepted.message_id, audience="private", version=9
    )["message"] == "abc"
