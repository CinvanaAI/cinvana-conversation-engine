from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .config import EnginePaths
from .db import transaction
from .errors import ContractError, ItemFailure, SystemFailure
from .jsonutil import (
    file_record,
    pretty_json,
    read_json,
    sha256_bytes,
    sha256_json,
    write_json_atomic,
)
from .repository import Repository
from .resolution import task_source_text
from .timeutil import utc_now
from .validators import VERSION_FILES, validate_returned


DISCARDED_DIR = "_discarded"


class WorkspaceManager:
    def __init__(self, paths: EnginePaths, repository: Repository):
        self.paths = paths
        self.repository = repository

    def _instructions(self, definition_id: str) -> str:
        definition = self.repository.definition(definition_id)
        text = definition["payload"].get("text")
        if not isinstance(text, str) or not text:
            raise ContractError(f"instruction definition has no text: {definition_id}")
        return text

    def _mapping_context(self, task: dict[str, Any], target: str) -> str:
        sections = []
        for item in task["payload"]["context"]:
            revision = self.repository.revision(item["revision_id"])
            sections.append(
                f"{item['relation'].upper()} MESSAGE "
                f"(position {item['position']}, speaker {item['speaker']})\n"
                f"{revision['message_text']}"
            )
        sections.append(
            "MESSAGE BEING MAPPED\n"
            + target
            + "\nEND MESSAGE BEING MAPPED"
        )
        return "\n\n".join(sections) + "\n"

    def _input_files(self, task: dict[str, Any]) -> dict[str, bytes]:
        stage = task["stage"]
        text, _ = task_source_text(self.repository, task)
        files: dict[str, bytes] = {"message.txt": text.encode("utf-8")}
        instructions_id = task["payload"].get("instructions_definition_id")
        if isinstance(instructions_id, str):
            files["instructions.md"] = self._instructions(instructions_id).encode(
                "utf-8"
            )

        if stage == "mapping":
            mapping = self.repository.definition(
                task["payload"]["mapping_index_definition_id"]
            )
            titles = mapping["payload"].get("titles")
            if not isinstance(titles, list):
                raise ContractError("captured Mapping Index has no titles array")
            files["mapping_context.md"] = self._mapping_context(task, text).encode(
                "utf-8"
            )
            files["mapping_reference.json"] = pretty_json(
                {"titles": titles}
            ).encode("utf-8")
        return files

    def _verify_folder_contents(
        self,
        workspace: dict[str, Any],
        folder: Path,
        *,
        require_outputs: bool,
        allow_unexpected_entries: bool = False,
    ) -> None:
        if not folder.is_dir():
            raise ContractError(f"workspace folder is missing: {folder}")
        request_path = folder / "request.json"
        try:
            request = read_json(request_path)
        except Exception as exc:
            raise ContractError(f"request.json is unreadable: {exc}") from exc
        if request != workspace["request"]:
            raise ContractError("request.json does not match its captured database record")
        if sha256_json(request) != workspace["request_sha256"]:
            raise ContractError("request.json hash does not match its captured record")

        expected_inputs = {
            item["path"]: item for item in workspace["input_manifest"]
        }
        for name, expected in expected_inputs.items():
            path = folder / name
            if not path.is_file():
                raise ContractError(f"workspace input is missing: {name}")
            actual = file_record(path, folder)
            if actual != expected:
                raise ContractError(f"workspace input changed: {name}")

        task = self.repository.task(workspace["task_id"])
        spec = task["contract"]["workspace_spec"]
        response_type = spec.get("response_type")
        allowed = set(expected_inputs) | {"request.json", "Suggestions", DISCARDED_DIR}
        required: set[str] = set()
        if response_type == "json":
            allowed.add("response.json")
            required.add("response.json")
        elif response_type == "sequential_text":
            allowed.update(VERSION_FILES)
        else:
            raise ContractError(f"unsupported captured response type: {response_type}")

        actual_entries = {item.name for item in folder.iterdir()}
        unexpected = sorted(actual_entries.difference(allowed))
        if unexpected and not allow_unexpected_entries:
            raise ContractError(f"workspace contains unexpected entries: {unexpected}")
        if not allow_unexpected_entries:
            for item in folder.iterdir():
                if item.name == DISCARDED_DIR:
                    if not item.is_dir():
                        raise ContractError(f"{DISCARDED_DIR} must be a folder")
                    continue
                if not item.is_file():
                    raise ContractError(f"workspace entry must be a file: {item.name}")
        if require_outputs:
            missing = sorted(required.difference(actual_entries))
            if missing:
                raise ContractError(f"workspace output is missing: {missing}")

    def _verify_folder(
        self,
        workspace: dict[str, Any],
        folder: Path,
        *,
        require_outputs: bool,
        allow_unexpected_entries: bool = False,
    ) -> None:
        try:
            self._verify_folder_contents(
                workspace,
                folder,
                require_outputs=require_outputs,
                allow_unexpected_entries=allow_unexpected_entries,
            )
        except ContractError as exc:
            task = self.repository.task(workspace["task_id"])
            raise ItemFailure(
                str(exc),
                message_id=task["message_id"],
                stage=task["stage"],
            ) from exc

    def _workspace_locations(
        self,
        workspace: dict[str, Any],
    ) -> tuple[dict[str, Any], Path, Path, Path, Path]:
        task = self.repository.task(workspace["task_id"])
        name = workspace["folder_name"]
        return (
            task,
            self.paths.workspace_drafts / name,
            self.paths.workspace_todo_for_stage(task["stage"]) / name,
            self.paths.workspace_done_for_stage(task["stage"]) / name,
            self.paths.workspace_claimed_for_stage(task["stage"]) / name,
        )

    def materialize(self, task_id: str) -> dict[str, Any]:
        with transaction(self.repository.connection):
            workspace = self.repository.create_workspace(task_id)
        if workspace["state"] != "preparing":
            return workspace

        task = self.repository.task(task_id)
        draft = self.paths.workspace_drafts / workspace["folder_name"]
        todo = (
            self.paths.workspace_todo_for_stage(task["stage"])
            / workspace["folder_name"]
        )
        if draft.exists() or todo.exists():
            raise SystemFailure(f"new workspace identity already exists: {workspace['folder_name']}")
        try:
            draft.parent.mkdir(parents=True, exist_ok=True)
            todo.parent.mkdir(parents=True, exist_ok=True)
            self.paths.workspace_done_for_stage(task["stage"]).mkdir(
                parents=True, exist_ok=True
            )
            self.paths.workspace_claimed_for_stage(task["stage"]).mkdir(
                parents=True, exist_ok=True
            )
            draft.mkdir(parents=False)
            (draft / DISCARDED_DIR).mkdir()
            for name, payload in self._input_files(task).items():
                (draft / name).write_bytes(payload)
            input_manifest = [
                file_record(path, draft)
                for path in sorted(
                    item for item in draft.iterdir() if item.is_file()
                )
            ]
            request = {
                "workspace_id": workspace["workspace_id"],
                "task_id": task_id,
                "stage": task["stage"],
                "captured_contract": {
                    "contract_id": task["contract_id"],
                    "contract_sha256": task["contract"]["contract_sha256"],
                    "validator_key": task["contract"]["validator_key"],
                    "validator_version": task["contract"]["validator_version"],
                    "config": task["contract"]["config"],
                },
                "input_files": input_manifest,
                "output_contract": task["contract"]["workspace_spec"],
            }
            (draft / "request.json").write_text(
                pretty_json(request), encoding="utf-8", newline="\n"
            )
            with transaction(self.repository.connection):
                self.repository.set_workspace_prepared(
                    workspace["workspace_id"],
                    request=request,
                    request_sha256=sha256_json(request),
                    input_manifest=input_manifest,
                    input_manifest_sha256=sha256_json(input_manifest),
                )
            os.replace(draft, todo)
            with transaction(self.repository.connection):
                self.repository.set_workspace_state(
                    workspace["workspace_id"], "published"
                )
            return self.repository.workspace(workspace["workspace_id"])
        except (ContractError, ItemFailure):
            raise
        except OSError as exc:
            raise SystemFailure(f"workspace publication failed: {exc}") from exc

    def reconcile(self) -> list[Path]:
        unknown_done: list[Path] = []
        for workspace in self.repository.workspaces_in_state(
            "preparing", "published", "returned", "absorbed"
        ):
            task, draft, todo, done, claimed = self._workspace_locations(workspace)
            state = workspace["state"]

            if state == "preparing":
                if workspace.get("request") is None:
                    for path in (draft, todo, done, claimed):
                        if path.exists():
                            shutil.rmtree(path)
                    with transaction(self.repository.connection):
                        self.repository.discard_preparing_workspace(
                            workspace["workspace_id"]
                        )
                    continue
                location = draft if draft.exists() else todo if todo.exists() else None
                if location is None:
                    with transaction(self.repository.connection):
                        self.repository.discard_preparing_workspace(
                            workspace["workspace_id"]
                        )
                    continue
                self._verify_folder(workspace, location, require_outputs=False)
                if location == draft:
                    todo.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(draft, todo)
                with transaction(self.repository.connection):
                    self.repository.set_workspace_state(
                        workspace["workspace_id"], "published"
                    )
                continue

            if state == "published":
                if done.exists():
                    claimed.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(done, claimed)
                    with transaction(self.repository.connection):
                        self.repository.set_workspace_state(
                            workspace["workspace_id"], "returned"
                        )
                elif claimed.exists():
                    with transaction(self.repository.connection):
                        self.repository.set_workspace_state(
                            workspace["workspace_id"], "returned"
                        )
                elif todo.exists():
                    self._verify_folder(
                        workspace,
                        todo,
                        require_outputs=False,
                        allow_unexpected_entries=True,
                    )
                else:
                    raise ItemFailure(
                        f"published workspace disappeared: {workspace['workspace_id']}",
                        message_id=task["message_id"],
                        stage=task["stage"],
                    )
                continue

            if state == "returned":
                if done.exists() and not claimed.exists():
                    claimed.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(done, claimed)
                if not claimed.exists():
                    raise ItemFailure(
                        f"returned workspace disappeared: {workspace['workspace_id']}",
                        message_id=task["message_id"],
                        stage=task["stage"],
                    )
                continue

            if state == "absorbed":
                for path in (draft, todo, done, claimed):
                    if path.exists():
                        shutil.rmtree(path)

        known = {
            item["folder_name"]
            for item in self.repository.workspaces_in_state(
                "preparing", "published", "returned", "absorbed"
            )
        }
        for root in self.paths.workspace_roots():
            if not root.exists():
                continue
            for path in root.iterdir():
                if path.is_dir() and path.name not in known:
                    unknown_done.append(path)
        return unknown_done

    def capture_suggestion(
        self,
        task: dict[str, Any],
        workspace: dict[str, Any],
        folder: Path,
    ) -> Path | None:
        source = folder / "Suggestions"
        if not source.exists():
            return None
        if not source.is_file():
            raise ContractError("Suggestions must be a plain file")
        content = source.read_bytes()
        try:
            suggestion_text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError("Suggestions must be valid UTF-8 text") from exc
        payload = {
            "schema_version": 1,
            "captured_at": utc_now(),
            "message_id": task["message_id"],
            "stage": task["stage"],
            "task_id": task["task_id"],
            "workspace_id": workspace["workspace_id"],
            "suggestion_sha256": sha256_bytes(content),
            "suggestion_text": suggestion_text,
        }
        target = (
            self.paths.suggestions
            / task["stage"]
            / f"{task['message_id']}_{workspace['workspace_id']}.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            try:
                existing = read_json(target)
            except Exception as exc:
                raise SystemFailure(
                    f"Suggestion capture is unreadable: {target}"
                ) from exc
            stable_keys = set(payload).difference({"captured_at"})
            if any(existing.get(key) != payload[key] for key in stable_keys):
                raise SystemFailure(f"Suggestion collision: {target}")
            return target
        write_json_atomic(target, payload)
        if read_json(target) != payload:
            raise SystemFailure(f"Suggestion capture verification failed: {target}")
        return target

    def absorb_one(self) -> dict[str, Any] | None:
        returned = self.repository.workspaces_in_state("returned")
        if not returned:
            return None
        workspace = returned[0]
        task = self.repository.task(workspace["task_id"])
        folder = (
            self.paths.workspace_claimed_for_stage(task["stage"])
            / workspace["folder_name"]
        )
        self._verify_folder(workspace, folder, require_outputs=True)
        output = validate_returned(self.repository, task, folder)
        self.capture_suggestion(task, workspace, folder)
        outcome = self.repository.promote_result(
            task["task_id"],
            output,
            workspace_id=workspace["workspace_id"],
        )
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            raise SystemFailure(f"absorbed workspace cleanup failed: {exc}") from exc
        return {
            **outcome,
            "workspace_id": workspace["workspace_id"],
            "message_id": task["message_id"],
            "stage": task["stage"],
        }
