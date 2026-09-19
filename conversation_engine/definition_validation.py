from __future__ import annotations

from typing import Any

from .errors import ContractError


def validate_definition(kind: str, name: str, payload: dict[str, Any]) -> None:
    if not isinstance(kind, str) or not kind:
        raise ContractError("definition kind must be a non-empty string")
    if not isinstance(name, str) or not name:
        raise ContractError("definition name must be a non-empty string")
    if not isinstance(payload, dict):
        raise ContractError("definition payload must be an object")

    if kind == "instructions":
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ContractError(f"instructions/{name} requires non-empty text")
        return

    if kind == "mapping_index":
        titles = payload.get("titles")
        if not isinstance(titles, list):
            raise ContractError(f"mapping_index/{name} requires a titles array")
        seen: set[str] = set()
        for index, item in enumerate(titles):
            if not isinstance(item, dict):
                raise ContractError(f"mapping title {index} must be an object")
            title = item.get("title")
            definition = item.get("definition")
            if not isinstance(title, str) or not title:
                raise ContractError(f"mapping title {index} has no title")
            if not isinstance(definition, str):
                raise ContractError(f"mapping title {title!r} has no definition")
            if title in seen:
                raise ContractError(f"mapping title is duplicated: {title}")
            seen.add(title)


def mapping_index_is_live(payload: dict[str, Any]) -> bool:
    explicit = payload.get("live_ready")
    if explicit is not None:
        return explicit is True
    titles = payload.get("titles")
    return isinstance(titles, list) and bool(titles)
