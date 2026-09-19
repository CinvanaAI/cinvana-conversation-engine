from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


STAGES = (
    "mapping",
    "public_safety",
    "original_versions",
    "public_versions",
    "discord_original",
    "discord_public",
)

PAUSE_TARGETS = ("intake", "deterministic", "model_dispatch")
WORKSPACE_BUCKETS = ("To_Do", "Done", "Claimed")


@dataclass(frozen=True)
class EnginePaths:
    root: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "EnginePaths":
        return cls(Path(root).expanduser().resolve())

    @property
    def runtime(self) -> Path:
        return self.root / "Runtime"

    @property
    def database(self) -> Path:
        return self.runtime / "State" / "conversation_engine.sqlite3"

    @property
    def halt_marker(self) -> Path:
        return self.runtime / "State" / "HALTED.json"

    @property
    def lock_file(self) -> Path:
        return self.runtime / "State" / "service.lock"

    @property
    def unprocessed(self) -> Path:
        return self.runtime / "Unprocessed"

    @property
    def workspaces(self) -> Path:
        return self.runtime / "Workspaces"

    @property
    def workspace_drafts(self) -> Path:
        return self.workspaces / "Drafts"

    def workspace_stage(self, stage: str) -> Path:
        if stage not in STAGES:
            raise ValueError(f"unknown workspace stage: {stage}")
        return self.workspaces / stage

    def workspace_bucket(self, stage: str, bucket: str) -> Path:
        if bucket not in WORKSPACE_BUCKETS:
            raise ValueError(f"unknown workspace bucket: {bucket}")
        return self.workspace_stage(stage) / bucket

    def workspace_todo_for_stage(self, stage: str) -> Path:
        return self.workspace_bucket(stage, "To_Do")

    def workspace_done_for_stage(self, stage: str) -> Path:
        return self.workspace_bucket(stage, "Done")

    def workspace_claimed_for_stage(self, stage: str) -> Path:
        return self.workspace_bucket(stage, "Claimed")

    def workspace_roots(self) -> tuple[Path, ...]:
        return (
            self.workspace_drafts,
            *(
                self.workspace_bucket(stage, bucket)
                for stage in STAGES
                for bucket in WORKSPACE_BUCKETS
            ),
        )

    @property
    def suggestions(self) -> Path:
        return self.runtime / "Suggestions"

    @property
    def rejections(self) -> Path:
        return self.runtime / "Rejections"

    @property
    def quarantine(self) -> Path:
        return self.rejections

    @property
    def temp(self) -> Path:
        return self.runtime / "Temp"

    @property
    def reports(self) -> Path:
        return self.runtime / "Reports"

    @property
    def backups(self) -> Path:
        return self.runtime / "Backups"

    def create_runtime(self) -> None:
        for path in (
            self.database.parent,
            self.unprocessed,
            *self.workspace_roots(),
            self.suggestions,
            self.rejections,
            self.temp,
            self.reports,
            self.backups,
            *(self.suggestions / stage for stage in STAGES),
        ):
            path.mkdir(parents=True, exist_ok=True)
