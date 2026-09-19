from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .db import transaction
from .envelope import Candidate, scan
from .errors import SystemFailure
from .planner import Planner
from .repository import Repository


@dataclass(frozen=True)
class IntakeOutcome:
    candidate: Candidate
    status: str
    message_id: str | None = None
    revision_id: str | None = None


class Intake:
    def __init__(self, repository: Repository, planner: Planner, unprocessed: Path):
        self.repository = repository
        self.planner = planner
        self.unprocessed = unprocessed

    def batch(self, limit: int = 50) -> list[Candidate]:
        if limit < 1:
            raise ValueError("Intake batch limit must be positive")
        return scan(self.unprocessed)[:limit]

    def accept(self, candidate: Candidate) -> IntakeOutcome:
        if candidate.envelope is None:
            return IntakeOutcome(candidate=candidate, status="invalid")
        with transaction(self.repository.connection):
            outcome = self.repository.ingest(
                candidate.envelope,
                filename=candidate.path.name,
            )
            if outcome["status"] != "duplicate":
                self.planner.plan_message(outcome["message_id"])
        try:
            candidate.path.unlink()
        except OSError as exc:
            raise SystemFailure(
                f"accepted source could not be removed: {candidate.path}: {exc}"
            ) from exc
        return IntakeOutcome(
            candidate=candidate,
            status=outcome["status"],
            message_id=outcome["message_id"],
            revision_id=outcome["revision_id"],
        )
