from __future__ import annotations

from typing import Any

from .errors import ContractError
from .jsonutil import sha256_text


def apply_redactions(text: str, redactions: list[dict[str, Any]]) -> str:
    resolved = text
    for item in sorted(redactions, key=lambda value: value["start"], reverse=True):
        resolved = (
            resolved[: item["start"]]
            + item["replacement"]
            + resolved[item["end"] :]
        )
    return resolved


def resolve_reference_text(
    repository: Any,
    reference: dict[str, Any],
    *,
    level: int | None = None,
    seen: set[tuple[str, int | None]] | None = None,
) -> str:
    kind = reference.get("kind")
    identity = reference.get("id")
    if not isinstance(identity, str):
        raise ContractError("text reference has no identity")
    if kind == "source_revision":
        revision = repository.revision(identity)
        text = revision["message_text"]
        valid_hashes = {revision["message_sha256"]}
    elif kind == "stage_result":
        result = repository.result(identity)
        text = resolve_result_text(repository, identity, level=level, seen=seen)
        valid_hashes = {sha256_text(text), result["output_sha256"]}
    else:
        raise ContractError(f"unsupported text reference kind: {kind}")
    expected = reference.get("sha256")
    if isinstance(expected, str) and expected not in valid_hashes:
        raise ContractError(f"text reference hash does not match: {identity}")
    return text


def _resolve_version_entry(
    repository: Any,
    result: dict[str, Any],
    level: int,
    *,
    seen: set[tuple[str, int | None]],
) -> str:
    output = result["output"]
    legacy_lanes = output.get("lanes")
    if isinstance(legacy_lanes, list):
        lane = next((item for item in legacy_lanes if item.get("level") == level), None)
        if lane is None or not isinstance(lane.get("message"), str):
            raise ContractError(f"version result has no level {level}")
        return lane["message"]

    versions = output.get("versions")
    if not isinstance(versions, list):
        raise ContractError("version result has no versions array")
    entry = next((item for item in versions if item.get("level") == level), None)
    if entry is None:
        raise ContractError(f"version result has no level {level}")
    if entry.get("kind") == "authored":
        text = entry.get("message")
        if not isinstance(text, str):
            raise ContractError(f"authored version {level} has no text")
        return text
    if entry.get("kind") != "pointer":
        raise ContractError(f"version {level} has an invalid kind")
    source_level = entry.get("source_level")
    if type(source_level) is not int or source_level < 0 or source_level > 9:
        raise ContractError(f"version {level} has an invalid pointer")
    if source_level == 0:
        source = output.get("source")
        if not isinstance(source, dict):
            raise ContractError("version result has no source reference")
        return resolve_reference_text(repository, source, seen=seen)
    return resolve_result_text(
        repository,
        result["result_id"],
        level=source_level,
        seen=seen,
    )


def resolve_result_text(
    repository: Any,
    result_id: str,
    *,
    level: int | None = None,
    seen: set[tuple[str, int | None]] | None = None,
) -> str:
    chain = seen if seen is not None else set()
    marker = (result_id, level)
    if marker in chain:
        raise ContractError(f"text pointer cycle detected at {result_id}")
    chain.add(marker)
    try:
        result = repository.result(result_id)
        output = result["output"]
        kind = output.get("kind")

        if kind == "result_pointer":
            source_result_id = output.get("source_result_id")
            if not isinstance(source_result_id, str):
                raise ContractError("result pointer has no source result")
            return resolve_result_text(
                repository,
                source_result_id,
                level=level,
                seen=chain,
            )

        if kind == "source_pointer":
            source = output.get("source")
            if not isinstance(source, dict):
                raise ContractError("source pointer has no source")
            return resolve_reference_text(repository, source, level=level, seen=chain)

        if kind == "versions":
            if type(level) is not int or level < 1 or level > 9:
                raise ContractError("version resolution requires a level from 1 through 9")
            return _resolve_version_entry(repository, result, level, seen=chain)

        if kind == "redactions":
            source = output.get("source")
            redactions = output.get("redactions")
            if not isinstance(source, dict) or not isinstance(redactions, list):
                raise ContractError("redaction result is incomplete")
            text = resolve_reference_text(repository, source, seen=chain)
            return apply_redactions(text, redactions)

        legacy_message = output.get("message")
        if isinstance(legacy_message, str):
            return legacy_message
        raise ContractError(f"stage result cannot resolve text: {result_id}")
    finally:
        chain.remove(marker)


def task_source_text(
    repository: Any,
    task: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    payload = task["payload"]
    revision_id = payload.get("source_revision_id")
    if isinstance(revision_id, str):
        revision = repository.revision(revision_id)
        return revision["message_text"], {
            "kind": "source_revision",
            "id": revision_id,
            "sha256": revision["message_sha256"],
        }
    result_id = payload.get("source_result_id")
    if isinstance(result_id, str):
        text = resolve_result_text(repository, result_id)
        return text, {
            "kind": "stage_result",
            "id": result_id,
            "sha256": sha256_text(text),
        }
    raise ContractError("task has no captured source")
