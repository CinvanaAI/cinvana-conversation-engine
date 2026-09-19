from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .errors import ContractError
from .jsonutil import sha256_text
from .repository import Repository
from .resolution import apply_redactions, task_source_text
from .schema import validate_schema


Validator = Callable[[Repository, dict[str, Any], Path], dict[str, Any]]
VERSION_FILES = tuple(f"version_{index}.txt" for index in range(1, 10))


def _response(task: dict[str, Any], path: Path) -> dict[str, Any]:
    response_path = path / "response.json"
    try:
        value = json.loads(response_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ContractError(f"response.json is not readable JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError("response.json must contain an object")
    response_schema = task["contract"]["workspace_spec"].get("response_schema")
    if not isinstance(response_schema, dict):
        raise ContractError("captured contract has no response schema")
    validate_schema(value, response_schema, "$response")
    return value


def mapping_v1(
    repository: Repository,
    task: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    response = _response(task, path)
    selected = response["discord_public_indexing"]
    definition = repository.definition(
        task["payload"]["mapping_index_definition_id"]
    )
    records = definition["payload"].get("titles")
    if not isinstance(records, list):
        raise ContractError("captured Mapping Index has no titles array")
    allowed = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("title"), str):
            raise ContractError("captured Mapping Index contains an invalid title")
        allowed.add(record["title"])
    invalid = [title for title in selected if title not in allowed]
    if invalid:
        raise ContractError(f"Mapping returned unknown titles: {invalid}")
    return {"discord_public_indexing": selected}


def _public_safety_response(
    repository: Repository, task: dict[str, Any], path: Path
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    response = _response(task, path)
    text, source = task_source_text(repository, task)
    decision = response["decision"]
    redactions = response["redactions"]
    if decision == "Public" and redactions:
        raise ContractError("Public decisions require no redactions")
    if decision == "Private" and not redactions:
        raise ContractError("Private decisions require at least one redaction")

    spans: list[tuple[int, int]] = []
    for index, item in enumerate(redactions):
        start = item["start"]
        end = item["end"]
        if start < 0 or start >= end or end > len(text):
            raise ContractError(f"redactions[{index}] is out of bounds")
        if item["replacement"] == text[start:end]:
            raise ContractError(f"redactions[{index}] does not change the source")
        spans.append((start, end))
    for previous, current in zip(sorted(spans), sorted(spans)[1:]):
        if current[0] < previous[1]:
            raise ContractError("Public Safety redactions overlap")
    return response, text, source


def public_safety_v1(
    repository: Repository,
    task: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    response, text, _ = _public_safety_response(repository, task, path)
    public_text = (
        text
        if response["decision"] == "Public"
        else apply_redactions(text, response["redactions"])
    )
    return {
        "decision": response["decision"],
        "redactions": response["redactions"],
        "message": public_text,
        "char_count": len(public_text),
        "message_sha256": sha256_text(public_text),
    }


def public_safety_v2(
    repository: Repository,
    task: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    response, _, source = _public_safety_response(repository, task, path)
    return {
        "kind": "redactions",
        "source": source,
        "decision": response["decision"],
        "redactions": response["redactions"],
    }


def versions_v1(
    repository: Repository,
    task: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    original, source = task_source_text(repository, task)
    authored: list[str] = []
    missing_seen = False
    previous = original
    for index, filename in enumerate(VERSION_FILES, start=1):
        candidate = path / filename
        if not candidate.exists():
            missing_seen = True
            continue
        if missing_seen:
            raise ContractError(f"{filename} exists after an earlier missing version")
        text = candidate.read_text(encoding="utf-8")
        if not text:
            raise ContractError(f"{filename} must not be empty")
        if len(text) >= len(previous):
            raise ContractError(f"{filename} must be smaller than its source")
        authored.append(text)
        previous = text

    lanes = []
    current = original
    source_level = 0
    for level in range(1, 10):
        if level <= len(authored):
            current = authored[level - 1]
            source_level = level
            source_type = "authored"
        else:
            source_type = "fill_forward"
        lanes.append(
            {
                "level": level,
                "message": current,
                "char_count": len(current),
                "message_sha256": sha256_text(current),
                "source_type": source_type,
                "source_level": source_level,
            }
        )
    return {
        "kind": "versions",
        "source": source,
        "authored_count": len(authored),
        "semantic_floor": len(authored),
        "lanes": lanes,
        "message": original,
    }


def versions_v2(
    repository: Repository,
    task: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    original, source = task_source_text(repository, task)
    authored: list[str] = []
    missing_seen = False
    previous = original
    for filename in VERSION_FILES:
        candidate = path / filename
        if not candidate.exists():
            missing_seen = True
            continue
        if missing_seen:
            raise ContractError(f"{filename} exists after an earlier missing version")
        text = candidate.read_text(encoding="utf-8")
        if not text:
            raise ContractError(f"{filename} must not be empty")
        if len(text) >= len(previous):
            raise ContractError(f"{filename} must be smaller than its source")
        authored.append(text)
        previous = text

    versions: list[dict[str, Any]] = []
    semantic_floor = len(authored)
    for level in range(1, 10):
        if level <= semantic_floor:
            text = authored[level - 1]
            versions.append(
                {
                    "level": level,
                    "kind": "authored",
                    "message": text,
                    "char_count": len(text),
                    "message_sha256": sha256_text(text),
                }
            )
        else:
            versions.append(
                {
                    "level": level,
                    "kind": "pointer",
                    "source_level": semantic_floor,
                }
            )
    return {
        "kind": "versions",
        "source": source,
        "authored_count": semantic_floor,
        "semantic_floor": semantic_floor,
        "versions": versions,
    }


def discord_ranges_v1(
    repository: Repository,
    task: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    response = _response(task, path)
    text, source = task_source_text(repository, task)
    max_chars = task["contract"]["config"]["max_chars"]
    chunks = []
    expected_start = 0
    reconstructed = []
    for expected_index, item in enumerate(response["chunks"], start=1):
        if item["index"] != expected_index:
            raise ContractError("Discord chunk indexes must be contiguous from one")
        start = item["start"]
        end = item["end"]
        if start != expected_start or start < 0 or start >= end or end > len(text):
            raise ContractError("Discord chunk ranges contain a gap, overlap, or empty span")
        chunk = text[start:end]
        if len(chunk) > max_chars:
            raise ContractError("Discord chunk exceeds the captured character limit")
        chunks.append(
            {
                "index": expected_index,
                "start": start,
                "end": end,
                "message": chunk,
                "char_count": len(chunk),
            }
        )
        reconstructed.append(chunk)
        expected_start = end
    if expected_start != len(text) or "".join(reconstructed) != text:
        raise ContractError("Discord chunk ranges do not reconstruct the source")
    return {
        "kind": "ranges",
        "source": source,
        "max_chars": max_chars,
        "chunks": chunks,
    }


def discord_ranges_v2(
    repository: Repository,
    task: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    response = _response(task, path)
    text, source = task_source_text(repository, task)
    max_chars = task["contract"]["config"]["max_chars"]
    expected_start = 0
    for expected_index, item in enumerate(response["chunks"], start=1):
        if item["index"] != expected_index:
            raise ContractError("Discord chunk indexes must be contiguous from one")
        start = item["start"]
        end = item["end"]
        if start != expected_start or start < 0 or start >= end or end > len(text):
            raise ContractError("Discord chunk ranges contain a gap, overlap, or empty span")
        if end - start > max_chars:
            raise ContractError("Discord chunk exceeds the captured character limit")
        expected_start = end
    if expected_start != len(text):
        raise ContractError("Discord chunk ranges do not cover the source")
    return {
        "kind": "ranges",
        "source": source,
        "max_chars": max_chars,
        "chunks": response["chunks"],
    }


VALIDATORS: dict[tuple[str, int], Validator] = {
    ("mapping", 1): mapping_v1,
    ("public_safety", 1): public_safety_v1,
    ("versions", 1): versions_v1,
    ("discord_ranges", 1): discord_ranges_v1,
    ("public_safety", 2): public_safety_v2,
    ("versions", 2): versions_v2,
    ("discord_ranges", 2): discord_ranges_v2,
}


def validate_returned(
    repository: Repository,
    task: dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    key = (
        task["contract"]["validator_key"],
        task["contract"]["validator_version"],
    )
    validator = VALIDATORS.get(key)
    if validator is None:
        raise ContractError(f"captured validator is unavailable: {key}")
    output = validator(repository, task, path)
    validate_schema(output, task["contract"]["output_schema"], "$output")
    return output
