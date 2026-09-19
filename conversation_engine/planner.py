from __future__ import annotations

from typing import Any

from .errors import ContractError
from .jsonutil import sha256_text
from .repository import Dependency, Repository
from .resolution import resolve_result_text


class Planner:
    def __init__(self, repository: Repository):
        self.repository = repository

    def _contract(self, stage: str) -> dict[str, Any]:
        contract = self.repository.current_contract(stage)
        if contract is None:
            raise ContractError(f"required stage contract is not published: {stage}")
        return contract

    def _definition(self, kind: str, name: str) -> dict[str, Any]:
        definition = self.repository.current_definition(kind, name)
        if definition is None:
            raise ContractError(f"required definition is not published: {kind}/{name}")
        return definition

    @staticmethod
    def _source_dependency(revision: dict[str, Any]) -> Dependency:
        return (
            "source_revision",
            revision["revision_id"],
            revision["envelope_sha256"],
        )

    @staticmethod
    def _definition_dependency(definition: dict[str, Any]) -> Dependency:
        return (
            "definition",
            definition["definition_id"],
            definition["payload_sha256"],
        )

    @staticmethod
    def _contract_dependency(contract: dict[str, Any]) -> Dependency:
        return (
            "contract",
            contract["contract_id"],
            contract["contract_sha256"],
        )

    @staticmethod
    def _result_dependency(result: dict[str, Any]) -> Dependency:
        return (
            "stage_result",
            result["result_id"],
            result["output_sha256"],
        )

    def _plan(
        self,
        *,
        message_id: str,
        revision: dict[str, Any],
        stage: str,
        kind: str,
        contract: dict[str, Any],
        payload: dict[str, Any],
        dependencies: list[Dependency],
    ) -> dict[str, Any]:
        return self.repository.plan_task(
            message_id=message_id,
            revision_id=revision["revision_id"],
            stage=stage,
            kind=kind,
            contract=contract,
            payload=payload,
            dependencies=[self._contract_dependency(contract), *dependencies],
        )

    def mapping(self, message_id: str) -> dict[str, Any]:
        revision = self.repository.current_revision(message_id)
        contract = self._contract("mapping")
        instructions = self._definition("instructions", "mapping")
        index = self._definition("mapping_index", "discord_public")
        context = self.repository.mapping_context(message_id)
        payload_context = [
            {
                "relation": item["relation"],
                "message_id": item["message_id"],
                "position": item["position"],
                "revision_id": item["revision_id"],
                "speaker": item["speaker"],
                "message_sha256": item["message_sha256"],
                "envelope_sha256": item["envelope_sha256"],
            }
            for item in context
        ]
        return self._plan(
            message_id=message_id,
            revision=revision,
            stage="mapping",
            kind="model",
            contract=contract,
            payload={
                "source_revision_id": revision["revision_id"],
                "instructions_definition_id": instructions["definition_id"],
                "mapping_index_definition_id": index["definition_id"],
                "context": payload_context,
            },
            dependencies=[
                self._source_dependency(revision),
                self._definition_dependency(instructions),
                self._definition_dependency(index),
                *[
                    (
                        "context_revision",
                        item["revision_id"],
                        item["envelope_sha256"],
                    )
                    for item in context
                ],
            ],
        )

    def public_safety(self, message_id: str) -> dict[str, Any]:
        revision = self.repository.current_revision(message_id)
        contract = self._contract("public_safety")
        instructions = self._definition("instructions", "public_safety")
        return self._plan(
            message_id=message_id,
            revision=revision,
            stage="public_safety",
            kind="model",
            contract=contract,
            payload={
                "source_revision_id": revision["revision_id"],
                "instructions_definition_id": instructions["definition_id"],
            },
            dependencies=[
                self._source_dependency(revision),
                self._definition_dependency(instructions),
            ],
        )

    def original_versions(self, message_id: str) -> dict[str, Any]:
        revision = self.repository.current_revision(message_id)
        contract = self._contract("original_versions")
        instructions = self._definition("instructions", "original_versions")
        return self._plan(
            message_id=message_id,
            revision=revision,
            stage="original_versions",
            kind="model",
            contract=contract,
            payload={
                "source_revision_id": revision["revision_id"],
                "instructions_definition_id": instructions["definition_id"],
            },
            dependencies=[
                self._source_dependency(revision),
                self._definition_dependency(instructions),
            ],
        )

    def discord_original(self, message_id: str) -> dict[str, Any]:
        revision = self.repository.current_revision(message_id)
        contract = self._contract("discord_original")
        max_chars = contract["config"].get("max_chars")
        if type(max_chars) is not int or max_chars < 1:
            raise ContractError("discord_original contract has invalid max_chars")
        dependencies = [self._source_dependency(revision)]
        payload = {
            "mode": (
                "source_pointer" if revision["char_count"] <= max_chars else "model"
            ),
            "source_revision_id": revision["revision_id"],
            "source_sha256": revision["message_sha256"],
        }
        if payload["mode"] == "model":
            instructions = self._definition("instructions", "discord")
            payload["instructions_definition_id"] = instructions["definition_id"]
            dependencies.append(self._definition_dependency(instructions))
        return self._plan(
            message_id=message_id,
            revision=revision,
            stage="discord_original",
            kind="deterministic" if payload["mode"] == "source_pointer" else "model",
            contract=contract,
            payload=payload,
            dependencies=dependencies,
        )

    def public_versions(self, message_id: str) -> dict[str, Any] | None:
        safety = self.repository.current_result(message_id, "public_safety")
        if safety is None:
            return None
        revision = self.repository.current_revision(message_id)
        contract = self._contract("public_versions")
        dependencies = [self._result_dependency(safety)]
        if safety["output"]["decision"] == "Public":
            original = self.repository.current_result(message_id, "original_versions")
            if original is None:
                return None
            payload = {
                "mode": "result_pointer",
                "source_result_id": original["result_id"],
                "public_safety_result_id": safety["result_id"],
            }
            dependencies.append(self._result_dependency(original))
            kind = "deterministic"
        else:
            instructions = self._definition("instructions", "public_versions")
            payload = {
                "mode": "model",
                "source_result_id": safety["result_id"],
                "instructions_definition_id": instructions["definition_id"],
            }
            dependencies.append(self._definition_dependency(instructions))
            kind = "model"
        return self._plan(
            message_id=message_id,
            revision=revision,
            stage="public_versions",
            kind=kind,
            contract=contract,
            payload=payload,
            dependencies=dependencies,
        )

    def discord_public(self, message_id: str) -> dict[str, Any] | None:
        safety = self.repository.current_result(message_id, "public_safety")
        if safety is None:
            return None
        revision = self.repository.current_revision(message_id)
        contract = self._contract("discord_public")
        max_chars = contract["config"].get("max_chars")
        if type(max_chars) is not int or max_chars < 1:
            raise ContractError("discord_public contract has invalid max_chars")
        dependencies = [self._result_dependency(safety)]
        if safety["output"]["decision"] == "Public":
            original = self.repository.current_result(message_id, "discord_original")
            if original is None:
                return None
            payload = {
                "mode": "result_pointer",
                "source_result_id": original["result_id"],
                "public_safety_result_id": safety["result_id"],
            }
            dependencies.append(self._result_dependency(original))
            kind = "deterministic"
        else:
            public_text = resolve_result_text(self.repository, safety["result_id"])
            mode = "source_pointer" if len(public_text) <= max_chars else "model"
            payload = {
                "mode": mode,
                "source_result_id": safety["result_id"],
                "source_sha256": sha256_text(public_text),
            }
            kind = "deterministic" if mode == "source_pointer" else "model"
            if mode == "model":
                instructions = self._definition("instructions", "discord")
                payload["instructions_definition_id"] = instructions["definition_id"]
                dependencies.append(self._definition_dependency(instructions))
        return self._plan(
            message_id=message_id,
            revision=revision,
            stage="discord_public",
            kind=kind,
            contract=contract,
            payload=payload,
            dependencies=dependencies,
        )

    def plan_message(self, message_id: str, *, clear_dirty: bool = True) -> list[dict[str, Any]]:
        planned = [
            self.mapping(message_id),
            self.public_safety(message_id),
            self.original_versions(message_id),
            self.discord_original(message_id),
        ]
        for outcome in (
            self.public_versions(message_id),
            self.discord_public(message_id),
        ):
            if outcome is not None:
                planned.append(outcome)
        if clear_dirty:
            self.repository.clear_dirty(message_id)
        return planned
