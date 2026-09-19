from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .jsonutil import canonical_json, sha256_text


DEFAULT_TIMEZONE = "America/Indiana/Indianapolis"


@dataclass(frozen=True)
class Envelope:
    chat_id: str
    destination_rel_path: str
    position: int
    speaker: str
    timestamp: str
    text: str
    canonical: dict[str, Any]
    envelope_sha256: str
    message_sha256: str


@dataclass(frozen=True)
class Candidate:
    path: Path
    envelope: Envelope | None
    error: str | None

    @property
    def ordering_key(self) -> tuple[Any, ...]:
        if self.envelope is None:
            return (0, self.path.name.casefold())
        return (
            1,
            self.envelope.timestamp,
            self.envelope.destination_rel_path,
            self.envelope.position,
            self.path.name.casefold(),
        )


def normalize_timestamp(value: Any, timezone_name: str | None = None) -> str:
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("message.timestamp must be an ISO 8601 date-time string")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError("message.timestamp is not valid ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        zone_name = (
            timezone_name
            or os.environ.get("CONVERSATION_ENGINE_TIMEZONE")
            or DEFAULT_TIMEZONE
        )
        parsed = parsed.replace(tzinfo=ZoneInfo(zone_name))
    return parsed.isoformat(timespec="seconds")


def validate_destination(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("chat.destination_rel_path must be a non-empty string")
    if "\\" in value or value.startswith(("/", ".")):
        raise ValueError("destination_rel_path must be a forward-slash relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("destination_rel_path contains an invalid segment")
    if parts[0] != "Archived":
        raise ValueError("destination_rel_path must begin with Archived")
    return value


def validate(raw: Any) -> Envelope:
    if not isinstance(raw, dict) or set(raw) != {"chat", "message"}:
        raise ValueError('envelope keys must be exactly {"chat", "message"}')
    chat = raw["chat"]
    message = raw["message"]
    if not isinstance(chat, dict) or set(chat) != {
        "chat_id",
        "destination_rel_path",
        "position",
    }:
        raise ValueError("chat object has an invalid shape")
    if not isinstance(message, dict) or set(message) != {
        "speaker",
        "timestamp",
        "text",
    }:
        raise ValueError("message object has an invalid shape")

    chat_id = chat["chat_id"]
    position = chat["position"]
    speaker = message["speaker"]
    text = message["text"]
    if not isinstance(chat_id, str) or not chat_id:
        raise ValueError("chat.chat_id must be a non-empty string")
    if type(position) is not int or position < 1:
        raise ValueError("chat.position must be a positive integer")
    if not isinstance(speaker, str) or not speaker:
        raise ValueError("message.speaker must be a non-empty string")
    if not isinstance(text, str):
        raise ValueError("message.text must be a string")

    canonical = {
        "chat": {
            "chat_id": chat_id,
            "destination_rel_path": validate_destination(
                chat["destination_rel_path"]
            ),
            "position": position,
        },
        "message": {
            "speaker": speaker,
            "timestamp": normalize_timestamp(message["timestamp"]),
            "text": text,
        },
    }
    encoded = canonical_json(canonical)
    return Envelope(
        chat_id=chat_id,
        destination_rel_path=canonical["chat"]["destination_rel_path"],
        position=position,
        speaker=speaker,
        timestamp=canonical["message"]["timestamp"],
        text=text,
        canonical=canonical,
        envelope_sha256=sha256_text(encoded),
        message_sha256=sha256_text(text),
    )


def load_candidate(path: Path) -> Candidate:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return Candidate(path=path, envelope=validate(json.load(handle)), error=None)
    except Exception as exc:
        return Candidate(path=path, envelope=None, error=str(exc))


def scan(unprocessed: Path) -> list[Candidate]:
    if not unprocessed.exists():
        return []
    candidates = [
        load_candidate(path)
        for path in unprocessed.rglob("*")
        if path.is_file() and path.suffix.lower() == ".json"
    ]
    candidates.sort(key=lambda item: item.ordering_key)
    return candidates
