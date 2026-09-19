from __future__ import annotations

from typing import Any

from .errors import ContractError, ItemFailure
from .repository import Repository
from .resolution import resolve_result_text
from .jsonutil import sha256_text
from .schema import validate_schema


class DeterministicExecutor:
    def __init__(self, repository: Repository):
        self.repository = repository

    def _output(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = task["payload"]
        mode = payload.get("mode")
        max_chars = task["contract"]["config"].get("max_chars")

        if mode == "result_pointer":
            return {
                "kind": "result_pointer",
                "source_result_id": payload["source_result_id"],
            }
        if mode == "source_pointer":
            revision_id = payload.get("source_revision_id")
            if isinstance(revision_id, str):
                revision = self.repository.revision(revision_id)
                source = {
                    "kind": "source_revision",
                    "id": revision_id,
                    "sha256": revision["message_sha256"],
                }
            else:
                result_id = payload.get("source_result_id")
                if not isinstance(result_id, str):
                    raise ContractError("source pointer has no captured source")
                text = resolve_result_text(self.repository, result_id)
                source = {
                    "kind": "stage_result",
                    "id": result_id,
                    "sha256": sha256_text(text),
                }
            output = {"kind": "source_pointer", "source": source}
            if type(max_chars) is int:
                output["max_chars"] = max_chars
            return output
        raise ContractError(
            f"deterministic task has unsupported mode: {task['stage']}/{mode}"
        )

    def execute_one(self, *, excluded_stages: set[str]) -> dict[str, Any] | None:
        task = self.repository.next_ready_task(
            "deterministic",
            excluded_stages=excluded_stages,
        )
        if task is None:
            return None
        try:
            output = self._output(task)
            validate_schema(output, task["contract"]["output_schema"], "$output")
            outcome = self.repository.promote_result(task["task_id"], output)
        except ContractError as exc:
            raise ItemFailure(
                str(exc),
                message_id=task["message_id"],
                stage=task["stage"],
            ) from exc
        return {
            **outcome,
            "task_id": task["task_id"],
            "message_id": task["message_id"],
            "stage": task["stage"],
        }
