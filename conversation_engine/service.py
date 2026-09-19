from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .config import EnginePaths
from .db import transaction
from .errors import ContractError, ItemFailure, SystemFailure
from .executor import DeterministicExecutor
from .intake import Intake
from .planner import Planner
from .quarantine import QuarantineManager
from .repository import Repository
from .workspaces import WorkspaceManager


class EngineService:
    def __init__(
        self,
        paths: EnginePaths,
        repository: Repository,
        *,
        intake_limit: int = 50,
        work_limit: int = 50,
    ):
        self.paths = paths
        self.repository = repository
        self.planner = Planner(repository)
        self.intake = Intake(repository, self.planner, paths.unprocessed)
        self.workspaces = WorkspaceManager(paths, repository)
        self.executor = DeterministicExecutor(repository)
        self.quarantine = QuarantineManager(paths, repository)
        self.intake_limit = intake_limit
        self.work_limit = work_limit

    def _quarantine_failure(self, failure: ItemFailure) -> str:
        reason = {
            "kind": "item_failure",
            "error_type": type(failure).__name__,
            "detail": str(failure),
        }
        if failure.stage:
            reason["stage"] = failure.stage
        if failure.message_id:
            return self.quarantine.begin_message(failure.message_id, reason)
        if failure.source_path:
            return self.quarantine.begin_external(Path(failure.source_path), reason)
        raise SystemFailure(f"item failure has no isolatable identity: {failure}")

    def _plan_dirty(self, report: dict[str, Any]) -> None:
        for message_id in self.repository.dirty_batch(self.work_limit):
            try:
                with transaction(self.repository.connection):
                    outcomes = self.planner.plan_message(message_id)
                report["planned"] += len(outcomes)
            except ContractError as exc:
                raise SystemFailure(
                    f"planning contract is not operational for {message_id}: {exc}"
                ) from exc

    def _absorb_returned(self, report: dict[str, Any]) -> None:
        for _ in range(self.work_limit):
            returned = self.repository.workspaces_in_state("returned")
            if not returned:
                return
            workspace = returned[0]
            task = self.repository.task(workspace["task_id"])
            try:
                outcome = self.workspaces.absorb_one()
                if outcome:
                    report["absorbed"] += 1
            except (ContractError, ItemFailure) as exc:
                failure = ItemFailure(
                    str(exc),
                    message_id=task["message_id"],
                    stage=task["stage"],
                )
                self._quarantine_failure(failure)
                report["quarantined"] += 1
                if self.quarantine.is_halted():
                    return

    def run_once(self) -> dict[str, Any]:
        report = {
            "halted": self.quarantine.is_halted(),
            "intake": 0,
            "planned": 0,
            "deterministic": 0,
            "published": 0,
            "absorbed": 0,
            "quarantined": 0,
            "pruned_results": 0,
            "pruned_tasks": 0,
        }
        if report["halted"]:
            return report

        self.quarantine.resume_pending()
        if self.quarantine.is_halted():
            report["halted"] = True
            return report

        try:
            unknown = self.workspaces.reconcile()
        except ItemFailure as failure:
            self._quarantine_failure(failure)
            report["quarantined"] += 1
            report["halted"] = self.quarantine.is_halted()
            return report
        except ContractError as exc:
            raise SystemFailure(f"workspace reconciliation lost item identity: {exc}") from exc

        for path in unknown:
            self.quarantine.begin_external(
                path,
                        {
                            "kind": "unknown_workspace_folder",
                            "stage": "workspaces",
                            "detail": "Workspace folder has no database identity",
                        },
            )
            report["quarantined"] += 1
            if self.quarantine.is_halted():
                report["halted"] = True
                return report

        self._absorb_returned(report)
        if self.quarantine.is_halted():
            report["halted"] = True
            return report

        if not self.repository.is_paused("intake"):
            for candidate in self.intake.batch(self.intake_limit):
                if candidate.error is not None:
                    self.quarantine.begin_external(
                        candidate.path,
                        {
                            "kind": "invalid_envelope",
                            "stage": "intake",
                            "detail": candidate.error,
                        },
                    )
                    report["quarantined"] += 1
                else:
                    self.intake.accept(candidate)
                    report["intake"] += 1
                if self.quarantine.is_halted():
                    report["halted"] = True
                    return report

        self._plan_dirty(report)

        paused_stages = self.repository.paused_stages()
        if not self.repository.is_paused("deterministic"):
            for _ in range(self.work_limit):
                try:
                    outcome = self.executor.execute_one(
                        excluded_stages=paused_stages
                    )
                except ItemFailure as failure:
                    self._quarantine_failure(failure)
                    report["quarantined"] += 1
                    break
                if outcome is None:
                    break
                report["deterministic"] += 1

        if not self.repository.is_paused("model_dispatch"):
            for _ in range(self.work_limit):
                task = self.repository.next_ready_task(
                    "model",
                    excluded_stages=paused_stages,
                )
                if task is None:
                    break
                try:
                    self.workspaces.materialize(task["task_id"])
                except ContractError as exc:
                    self._quarantine_failure(
                        ItemFailure(
                            str(exc),
                            message_id=task["message_id"],
                            stage=task["stage"],
                        )
                    )
                    report["quarantined"] += 1
                    break
                report["published"] += 1

        with transaction(self.repository.connection):
            pruned = self.repository.prune_obsolete_work()
        report["pruned_results"] = pruned["results"]
        report["pruned_tasks"] = pruned["tasks"]

        report["halted"] = self.quarantine.is_halted()
        return report

    def run_forever(self, interval_seconds: float = 1.0) -> None:
        while True:
            report = self.run_once()
            if report["halted"]:
                return
            time.sleep(interval_seconds)
