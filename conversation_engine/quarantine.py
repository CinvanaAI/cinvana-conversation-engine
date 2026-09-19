from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .config import STAGES, EnginePaths
from .db import transaction
from .errors import ContractError, SystemFailure
from .ids import new_id
from .jsonutil import (
    pretty_json,
    read_json,
    sha256_text,
    tree_manifest,
    write_json_atomic,
)
from .repository import Repository
from .timeutil import utc_now


class QuarantineManager:
    def __init__(self, paths: EnginePaths, repository: Repository):
        self.paths = paths
        self.repository = repository

    def halt(self, reason: dict[str, Any]) -> None:
        marker = {
            "halted_at": utc_now(),
            "reason": reason,
        }
        try:
            if self.paths.halt_marker.exists():
                return
            write_json_atomic(self.paths.halt_marker, marker)
        except OSError as exc:
            raise SystemFailure(f"cannot persist global halt: {exc}") from exc

    def resume(self, reason: str) -> None:
        if not reason.strip():
            raise ContractError("resume requires a reason")
        marker = read_json(self.paths.halt_marker) if self.paths.halt_marker.exists() else None
        with transaction(self.repository.connection):
            self.repository.audit(
                "system.resumed",
                details={"reason": reason.strip(), "previous_halt": marker},
            )
        try:
            if self.paths.halt_marker.exists():
                self.paths.halt_marker.unlink()
        except OSError as exc:
            raise SystemFailure(f"cannot remove global halt: {exc}") from exc

    def is_halted(self) -> bool:
        return self.paths.halt_marker.exists()

    @staticmethod
    def _rejection_stage(reason: dict[str, Any], *, external: bool) -> str:
        stage = reason.get("stage")
        allowed = set(STAGES) | {"intake", "workspaces", "system"}
        if isinstance(stage, str) and stage in allowed:
            return stage
        return "intake" if external and reason.get("kind") == "invalid_envelope" else "system"

    def begin_message(self, message_id: str, reason: dict[str, Any]) -> str:
        snapshot = self.repository.message_snapshot(message_id)
        quarantine_id = new_id("quarantine")
        stage = self._rejection_stage(reason, external=False)
        bundle_name = f"{stage}/{quarantine_id}"
        with transaction(self.repository.connection):
            self.repository.create_quarantine_record(
                quarantine_id=quarantine_id,
                message_id=message_id,
                source_path=None,
                reason=reason,
                snapshot=snapshot,
                bundle_name=bundle_name,
            )
            self.repository.audit(
                "quarantine.started",
                message_id=message_id,
                subject_id=quarantine_id,
                details={"kind": reason.get("kind", "item_failure")},
            )
        self.resume_record(quarantine_id)
        return quarantine_id

    def begin_external(self, source_path: Path, reason: dict[str, Any]) -> str:
        quarantine_id = new_id("quarantine")
        stage = self._rejection_stage(reason, external=True)
        with transaction(self.repository.connection):
            self.repository.create_quarantine_record(
                quarantine_id=quarantine_id,
                message_id=None,
                source_path=str(source_path.resolve()),
                reason=reason,
                snapshot={},
                bundle_name=f"{stage}/{quarantine_id}",
            )
            self.repository.audit(
                "quarantine.started",
                subject_id=quarantine_id,
                details={
                    "kind": reason.get("kind", "external_failure"),
                    "source_name": source_path.name,
                },
            )
        self.resume_record(quarantine_id)
        return quarantine_id

    def _write_base_files(self, record: dict[str, Any], temporary: Path) -> None:
        temporary.mkdir(parents=True, exist_ok=True)
        (temporary / "reason.json").write_text(
            pretty_json(record["reason"]), encoding="utf-8", newline="\n"
        )
        (temporary / "database_snapshot.json").write_text(
            pretty_json(record["snapshot"]), encoding="utf-8", newline="\n"
        )
        revisions = record["snapshot"].get("source_revisions", [])
        heads = record["snapshot"].get("message_heads", [])
        current_id = heads[0]["revision_id"] if heads else None
        current = next(
            (item for item in revisions if item["revision_id"] == current_id),
            revisions[-1] if revisions else None,
        )
        if current is not None:
            messages = record["snapshot"].get("messages", [])
            message = messages[0] if messages else None
            if message is None:
                raise ContractError("quarantine snapshot has no message identity")
            envelope = {
                "chat": {
                    "chat_id": message["chat_id"],
                    "destination_rel_path": current["destination_rel_path"],
                    "position": message["position"],
                },
                "message": {
                    "speaker": current["speaker"],
                    "timestamp": current["message_timestamp"],
                    "text": current["message_text"],
                },
            }
            (temporary / "source_envelope.json").write_text(
                pretty_json(envelope), encoding="utf-8", newline="\n"
            )

    def _move_message_artifacts(
        self,
        record: dict[str, Any],
        temporary: Path,
    ) -> None:
        tasks_by_id = {
            task["task_id"]: task
            for task in record["snapshot"].get("tasks", [])
            if isinstance(task.get("task_id"), str)
        }
        for workspace in record["snapshot"].get("workspaces", []):
            name = workspace["folder_name"]
            task = tasks_by_id.get(workspace["task_id"])
            stage = task.get("stage") if task else None
            if not isinstance(stage, str) or stage not in STAGES:
                raise ContractError(
                    f"quarantine cannot locate workspace stage for {name}"
                )
            for queue, root in (
                ("Drafts", self.paths.workspace_drafts),
                ("To_Do", self.paths.workspace_todo_for_stage(stage)),
                ("Done", self.paths.workspace_done_for_stage(stage)),
                ("Claimed", self.paths.workspace_claimed_for_stage(stage)),
            ):
                source = root / name
                if queue == "Drafts":
                    target = temporary / "Workspaces" / "Drafts" / name
                else:
                    target = temporary / "Workspaces" / stage / queue / name
                if source.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        raise SystemFailure(f"quarantine artifact collision: {target}")
                    os.replace(source, target)
        messages = record["snapshot"].get("messages", [])
        message_id = messages[0]["message_id"] if messages else None
        suggestions: set[Path] = set()
        if isinstance(message_id, str) and self.paths.suggestions.exists():
            suggestions.update(
                path
                for path in self.paths.suggestions.rglob(f"{message_id}_*.json")
                if path.is_file()
            )
        for task in record["snapshot"].get("tasks", []):
            suggestions.update(
                path
                for path in (
                    self.paths.suggestions / f"{task['task_id']}.txt",
                    self.paths.suggestions / task["stage"] / f"{task['task_id']}.txt",
                )
                if path.is_file()
            )
        for suggestion in sorted(suggestions):
            relative = suggestion.relative_to(self.paths.suggestions).as_posix()
            token = sha256_text(relative)[:24]
            target = temporary / "Suggestions" / f"s_{token}{suggestion.suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise SystemFailure(f"quarantine artifact collision: {target}")
            os.replace(suggestion, target)

    @staticmethod
    def _move_external(source: Path, temporary: Path) -> None:
        if not source.exists():
            return
        target = temporary / "External" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise SystemFailure(f"quarantine artifact collision: {target}")
        os.replace(source, target)

    def _publish_bundle(self, record: dict[str, Any]) -> Path:
        temporary = self.paths.temp / Path(record["bundle_name"]).name
        final = self.paths.rejections / record["bundle_name"]
        if final.exists():
            self.verify_bundle(final)
            return final
        try:
            for stale_name in ("manifest.json", "COMPLETE", "REINTRODUCED.json"):
                stale = temporary / stale_name
                if stale.exists():
                    stale.unlink()
            self._write_base_files(record, temporary)
            self._move_message_artifacts(record, temporary)
            if record.get("source_path"):
                self._move_external(Path(record["source_path"]), temporary)
            manifest = {
                "quarantine_id": record["quarantine_id"],
                "created_at": record["created_at"],
                "files": tree_manifest(temporary),
            }
            (temporary / "manifest.json").write_text(
                pretty_json(manifest), encoding="utf-8", newline="\n"
            )
            (temporary / "COMPLETE").write_text("complete\n", encoding="ascii")
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, final)
            self.verify_bundle(final)
            return final
        except OSError as exc:
            raise SystemFailure(f"quarantine publication failed: {exc}") from exc

    def verify_bundle(self, bundle: Path) -> None:
        if not (bundle / "COMPLETE").is_file():
            raise SystemFailure(f"quarantine bundle has no COMPLETE marker: {bundle}")
        try:
            manifest = read_json(bundle / "manifest.json")
        except Exception as exc:
            raise SystemFailure(f"quarantine manifest is unreadable: {bundle}: {exc}") from exc
        expected = manifest.get("files")
        if not isinstance(expected, list):
            raise SystemFailure(f"quarantine manifest has no file list: {bundle}")
        actual = tree_manifest(bundle)
        actual = [
            item
            for item in actual
            if item["path"] not in {"manifest.json", "COMPLETE", "REINTRODUCED.json"}
        ]
        if actual != expected:
            raise SystemFailure(f"quarantine bundle verification failed: {bundle}")

    def resume_record(self, quarantine_id: str) -> None:
        record = self.repository.quarantine_record(quarantine_id)
        if record["state"] == "completed":
            return
        if record["state"] == "preparing":
            self._publish_bundle(record)
            with transaction(self.repository.connection):
                self.repository.mark_quarantine_published(quarantine_id)
            record = self.repository.quarantine_record(quarantine_id)
        if record["state"] == "published":
            final = self.paths.rejections / record["bundle_name"]
            self.verify_bundle(final)
            count = self.repository.complete_quarantine(
                quarantine_id,
                message_id=record["message_id"],
            )
            if count >= 6:
                self.halt(
                    {
                        "kind": "quarantine_fuse",
                        "completed_quarantines_in_60_minutes": count,
                        "triggering_quarantine_id": quarantine_id,
                    }
                )

    def resume_pending(self) -> None:
        for record in self.repository.pending_quarantines():
            self.resume_record(record["quarantine_id"])

    def reintroduce(self, quarantine_id: str) -> Path:
        record = self.repository.quarantine_record(quarantine_id)
        if record["state"] != "completed":
            raise ContractError("only completed quarantines can be reintroduced")
        bundle = self.paths.rejections / record["bundle_name"]
        self.verify_bundle(bundle)
        source = bundle / "source_envelope.json"
        if not source.is_file():
            external = bundle / "External"
            candidates = list(external.glob("*.json")) if external.exists() else []
            if len(candidates) != 1:
                raise ContractError("quarantine has no unambiguous source envelope")
            source = candidates[0]
        target = self.paths.unprocessed / f"reintroduced_{quarantine_id}.json"
        if target.exists():
            if target.read_bytes() != source.read_bytes():
                raise SystemFailure(f"reintroduction target collision: {target}")
        else:
            write_json_atomic(target, read_json(source))
        write_json_atomic(
            bundle / "REINTRODUCED.json",
            {"reintroduced_at": utc_now(), "target": target.name},
        )
        with transaction(self.repository.connection):
            self.repository.audit(
                "quarantine.reintroduced",
                subject_id=quarantine_id,
                details={"target": target.name},
            )
        return target


def read_json_text(value: str) -> Any:
    import json

    return json.loads(value)
