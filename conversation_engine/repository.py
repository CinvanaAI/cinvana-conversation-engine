from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from .config import STAGES
from .db import transaction
from .definition_validation import mapping_index_is_live, validate_definition
from .envelope import Envelope
from .errors import ContractError
from .ids import new_id
from .jsonutil import canonical_json, sha256_json, sha256_text
from .timeutil import utc_now


Dependency = tuple[str, str, str]


def _decode(row: sqlite3.Row | None, *fields: str) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    for field in fields:
        if value.get(field) is not None:
            value[field.removesuffix("_json")] = json.loads(value.pop(field))
    return value


class Repository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def audit(
        self,
        event_type: str,
        *,
        message_id: str | None = None,
        subject_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO audit_events(
                occurred_at, event_type, message_id, subject_id, details_json
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                utc_now(),
                event_type,
                message_id,
                subject_id,
                canonical_json(details or {}),
            ),
        )

    def mark_dirty(self, message_id: str, reason: str) -> None:
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO dirty_messages(message_id, reasons_json, marked_at)
            VALUES(?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                reasons_json = excluded.reasons_json,
                marked_at = excluded.marked_at
            """,
            (message_id, canonical_json([reason]), now),
        )

    def mark_all_dirty(self, reason: str) -> None:
        now = utc_now()
        encoded = canonical_json([reason])
        self.connection.execute(
            """
            INSERT INTO dirty_messages(message_id, reasons_json, marked_at)
            SELECT message_id, ?, ? FROM messages WHERE 1 = 1
            ON CONFLICT(message_id) DO UPDATE SET
                reasons_json = excluded.reasons_json,
                marked_at = excluded.marked_at
            """,
            (encoded, now),
        )

    def dirty_batch(self, limit: int) -> list[str]:
        return [
            row["message_id"]
            for row in self.connection.execute(
                """
                SELECT message_id
                FROM dirty_messages
                ORDER BY marked_at, message_id
                LIMIT ?
                """,
                (limit,),
            )
        ]

    def clear_dirty(self, message_id: str) -> None:
        self.connection.execute(
            "DELETE FROM dirty_messages WHERE message_id = ?",
            (message_id,),
        )

    def prune_obsolete_work(self) -> dict[str, Any]:
        obsolete_results = [
            row["result_id"]
            for row in self.connection.execute(
                """
                WITH RECURSIVE protected_results(result_id) AS (
                    SELECT result_id FROM stage_heads
                    UNION
                    SELECT dependencies.dependency_id
                    FROM task_dependencies dependencies
                    JOIN stage_targets targets
                      ON targets.task_id = dependencies.task_id
                    WHERE dependencies.dependency_kind = 'stage_result'
                    UNION
                    SELECT dependencies.dependency_id
                    FROM task_dependencies dependencies
                    JOIN workspaces
                      ON workspaces.task_id = dependencies.task_id
                    WHERE dependencies.dependency_kind = 'stage_result'
                      AND workspaces.state IN (
                          'preparing', 'published', 'returned'
                      )
                    UNION
                    SELECT dependencies.dependency_id
                    FROM result_dependencies dependencies
                    JOIN protected_results protected
                      ON protected.result_id = dependencies.result_id
                    WHERE dependencies.dependency_kind = 'stage_result'
                )
                SELECT results.result_id
                FROM stage_results results
                LEFT JOIN protected_results protected
                  ON protected.result_id = results.result_id
                WHERE protected.result_id IS NULL
                ORDER BY results.created_at, results.result_id
                """
            )
        ]
        if obsolete_results:
            self.connection.executemany(
                "DELETE FROM stage_results WHERE result_id = ?",
                [(result_id,) for result_id in obsolete_results],
            )

        obsolete_tasks = [
            row["task_id"]
            for row in self.connection.execute(
                """
                SELECT tasks.task_id
                FROM tasks
                WHERE NOT EXISTS (
                    SELECT 1 FROM stage_targets targets
                    WHERE targets.task_id = tasks.task_id
                )
                  AND NOT EXISTS (
                    SELECT 1 FROM stage_results results
                    WHERE results.task_id = tasks.task_id
                )
                  AND NOT EXISTS (
                    SELECT 1 FROM workspaces
                    WHERE workspaces.task_id = tasks.task_id
                      AND workspaces.state IN (
                          'preparing', 'published', 'returned'
                      )
                )
                ORDER BY tasks.created_at, tasks.task_id
                """
            )
        ]
        if obsolete_tasks:
            self.connection.executemany(
                "DELETE FROM tasks WHERE task_id = ?",
                [(task_id,) for task_id in obsolete_tasks],
            )
        return {
            "results": len(obsolete_results),
            "tasks": len(obsolete_tasks),
        }

    def publish_definition(
        self,
        kind: str,
        name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        validate_definition(kind, name, payload)
        payload_sha256 = sha256_json(payload)
        with transaction(self.connection):
            current = self.current_definition(kind, name)
            if current and current["payload_sha256"] == payload_sha256:
                return {"status": "unchanged", **current}
            existing = self.connection.execute(
                """
                SELECT * FROM definition_versions
                WHERE kind = ? AND name = ? AND payload_sha256 = ?
                """,
                (kind, name, payload_sha256),
            ).fetchone()
            if existing is None:
                version = self.connection.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM definition_versions WHERE kind = ? AND name = ?
                    """,
                    (kind, name),
                ).fetchone()[0]
                definition_id = new_id("def")
                self.connection.execute(
                    """
                    INSERT INTO definition_versions(
                        definition_id, kind, name, version, payload_json,
                        payload_sha256, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        definition_id,
                        kind,
                        name,
                        version,
                        canonical_json(payload),
                        payload_sha256,
                        utc_now(),
                    ),
                )
            else:
                definition_id = existing["definition_id"]
                version = existing["version"]
            self.connection.execute(
                """
                INSERT INTO definition_heads(kind, name, definition_id)
                VALUES(?, ?, ?)
                ON CONFLICT(kind, name) DO UPDATE SET
                    definition_id = excluded.definition_id
                """,
                (kind, name, definition_id),
            )
            self.mark_all_dirty(f"definition:{kind}/{name}:{definition_id}")
            self.audit(
                "definition.published",
                subject_id=definition_id,
                details={"kind": kind, "name": name, "version": version},
            )
            return {
                "status": "published",
                "definition_id": definition_id,
                "kind": kind,
                "name": name,
                "version": version,
                "payload": payload,
                "payload_sha256": payload_sha256,
            }

    def current_definition(self, kind: str, name: str) -> dict[str, Any] | None:
        return _decode(
            self.connection.execute(
                """
                SELECT versions.*
                FROM definition_heads heads
                JOIN definition_versions versions
                  ON versions.definition_id = heads.definition_id
                WHERE heads.kind = ? AND heads.name = ?
                """,
                (kind, name),
            ).fetchone(),
            "payload_json",
        )

    def definition(self, definition_id: str) -> dict[str, Any]:
        row = _decode(
            self.connection.execute(
                "SELECT * FROM definition_versions WHERE definition_id = ?",
                (definition_id,),
            ).fetchone(),
            "payload_json",
        )
        if row is None:
            raise ContractError(f"unknown definition: {definition_id}")
        return row

    def publish_contract(
        self,
        *,
        stage: str,
        worker_policy: str,
        validator_key: str,
        validator_version: int,
        workspace_spec: dict[str, Any],
        output_schema: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        if stage not in STAGES:
            raise ContractError(f"unknown stage: {stage}")
        if worker_policy not in {"model", "deterministic", "dynamic"}:
            raise ContractError(f"invalid worker policy: {worker_policy}")
        canonical = {
            "stage": stage,
            "worker_policy": worker_policy,
            "validator_key": validator_key,
            "validator_version": validator_version,
            "workspace_spec": workspace_spec,
            "output_schema": output_schema,
            "config": config,
        }
        contract_sha256 = sha256_json(canonical)
        with transaction(self.connection):
            current = self.current_contract(stage)
            if current and current["contract_sha256"] == contract_sha256:
                return {"status": "unchanged", **current}
            existing = self.connection.execute(
                """
                SELECT * FROM contract_versions
                WHERE stage = ? AND contract_sha256 = ?
                """,
                (stage, contract_sha256),
            ).fetchone()
            if existing is None:
                version = self.connection.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM contract_versions WHERE stage = ?
                    """,
                    (stage,),
                ).fetchone()[0]
                contract_id = new_id("contract")
                self.connection.execute(
                    """
                    INSERT INTO contract_versions(
                        contract_id, stage, version, worker_policy,
                        validator_key, validator_version, workspace_spec_json,
                        output_schema_json, config_json, contract_sha256, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        contract_id,
                        stage,
                        version,
                        worker_policy,
                        validator_key,
                        validator_version,
                        canonical_json(workspace_spec),
                        canonical_json(output_schema),
                        canonical_json(config),
                        contract_sha256,
                        utc_now(),
                    ),
                )
            else:
                contract_id = existing["contract_id"]
                version = existing["version"]
            self.connection.execute(
                """
                INSERT INTO contract_heads(stage, contract_id)
                VALUES(?, ?)
                ON CONFLICT(stage) DO UPDATE SET contract_id = excluded.contract_id
                """,
                (stage, contract_id),
            )
            self.mark_all_dirty(f"contract:{stage}:{contract_id}")
            self.audit(
                "contract.published",
                subject_id=contract_id,
                details={"stage": stage, "version": version},
            )
            return {
                "status": "published",
                "contract_id": contract_id,
                "stage": stage,
                "version": version,
                "contract_sha256": contract_sha256,
            }

    def _contract_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        return _decode(
            row,
            "workspace_spec_json",
            "output_schema_json",
            "config_json",
        )

    def current_contract(self, stage: str) -> dict[str, Any] | None:
        return self._contract_row(
            self.connection.execute(
                """
                SELECT versions.*
                FROM contract_heads heads
                JOIN contract_versions versions
                  ON versions.contract_id = heads.contract_id
                WHERE heads.stage = ?
                """,
                (stage,),
            ).fetchone()
        )

    def contract(self, contract_id: str) -> dict[str, Any]:
        value = self._contract_row(
            self.connection.execute(
                "SELECT * FROM contract_versions WHERE contract_id = ?",
                (contract_id,),
            ).fetchone()
        )
        if value is None:
            raise ContractError(f"unknown contract: {contract_id}")
        return value

    def ingest(
        self,
        envelope: Envelope,
        *,
        filename: str,
    ) -> dict[str, Any]:
        existing_source = self.connection.execute(
            """
            SELECT message_id, revision_id FROM ingested_sources
            WHERE envelope_sha256 = ?
            """,
            (envelope.envelope_sha256,),
        ).fetchone()
        if existing_source is not None:
            return {
                "status": "duplicate",
                "message_id": existing_source["message_id"],
                "revision_id": existing_source["revision_id"],
            }

        message = self.connection.execute(
            "SELECT * FROM messages WHERE chat_id = ? AND position = ?",
            (envelope.chat_id, envelope.position),
        ).fetchone()
        now = utc_now()
        if message is None:
            message_id = new_id("msg")
            revision_number = 1
            self.connection.execute(
                """
                INSERT INTO messages(message_id, chat_id, position, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (message_id, envelope.chat_id, envelope.position, now),
            )
            status = "accepted"
        else:
            message_id = message["message_id"]
            revision_number = self.connection.execute(
                """
                SELECT COALESCE(MAX(revision_number), 0) + 1
                FROM source_revisions WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()[0]
            status = "revised"

        revision_id = new_id("rev")
        self.connection.execute(
            """
            INSERT INTO source_revisions(
                revision_id, message_id, revision_number,
                envelope_sha256, destination_rel_path, speaker,
                message_timestamp, message_text, message_sha256,
                char_count, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                message_id,
                revision_number,
                envelope.envelope_sha256,
                envelope.destination_rel_path,
                envelope.speaker,
                envelope.timestamp,
                envelope.text,
                envelope.message_sha256,
                len(envelope.text),
                now,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO message_heads(message_id, revision_id)
            VALUES(?, ?)
            ON CONFLICT(message_id) DO UPDATE SET revision_id = excluded.revision_id
            """,
            (message_id, revision_id),
        )
        self.connection.execute(
            """
            INSERT INTO ingested_sources(
                envelope_sha256, message_id, revision_id, first_filename, accepted_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                envelope.envelope_sha256,
                message_id,
                revision_id,
                filename,
                now,
            ),
        )

        intake_output = {
            "kind": "source_revision",
            "revision_id": revision_id,
            "envelope_sha256": envelope.envelope_sha256,
            "message_sha256": envelope.message_sha256,
            "char_count": len(envelope.text),
        }
        result_id = new_id("result")
        self.connection.execute(
            """
            INSERT INTO stage_results(
                result_id, message_id, stage, task_id, contract_id,
                output_json, output_sha256, created_at
            ) VALUES(?, ?, 'intake', NULL, NULL, ?, ?, ?)
            """,
            (
                result_id,
                message_id,
                canonical_json(intake_output),
                sha256_json(intake_output),
                now,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO result_dependencies(
                result_id, dependency_kind, dependency_id, dependency_sha256
            ) VALUES(?, 'source_revision', ?, ?)
            """,
            (result_id, revision_id, envelope.envelope_sha256),
        )
        self.connection.execute(
            """
            INSERT INTO stage_heads(message_id, stage, result_id, promoted_at)
            VALUES(?, 'intake', ?, ?)
            ON CONFLICT(message_id, stage) DO UPDATE SET
                result_id = excluded.result_id,
                promoted_at = excluded.promoted_at
            """,
            (message_id, result_id, now),
        )

        affected = self.connection.execute(
            """
            SELECT message_id FROM messages
            WHERE chat_id = ? AND position BETWEEN ? AND ?
            """,
            (envelope.chat_id, max(1, envelope.position - 2), envelope.position + 4),
        ).fetchall()
        for row in affected:
            self.mark_dirty(row["message_id"], f"source:{revision_id}")
        self.audit(
            f"intake.{status}",
            message_id=message_id,
            subject_id=revision_id,
            details={"filename": filename, "revision_number": revision_number},
        )
        return {
            "status": status,
            "message_id": message_id,
            "revision_id": revision_id,
            "result_id": result_id,
        }

    def current_revision(self, message_id: str) -> dict[str, Any]:
        value = _decode(
            self.connection.execute(
                """
                SELECT revisions.*
                FROM message_heads heads
                JOIN source_revisions revisions
                  ON revisions.revision_id = heads.revision_id
                WHERE heads.message_id = ?
                """,
                (message_id,),
            ).fetchone(),
        )
        if value is None:
            raise ContractError(f"message has no current revision: {message_id}")
        return value

    def mapping_context(self, message_id: str) -> list[dict[str, Any]]:
        target = self.connection.execute(
            "SELECT chat_id, position FROM messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if target is None:
            raise ContractError(f"unknown message: {message_id}")
        rows = self.connection.execute(
            """
            SELECT messages.message_id, messages.position, revisions.*
            FROM messages
            JOIN message_heads ON message_heads.message_id = messages.message_id
            JOIN source_revisions revisions
              ON revisions.revision_id = message_heads.revision_id
            WHERE messages.chat_id = ?
              AND messages.position BETWEEN ? AND ?
              AND messages.message_id != ?
            ORDER BY messages.position
            """,
            (
                target["chat_id"],
                max(1, target["position"] - 4),
                target["position"] + 2,
                message_id,
            ),
        ).fetchall()
        return [
            {
                **dict(row),
                "relation": (
                    "previous"
                    if row["position"] < target["position"]
                    else "following"
                ),
            }
            for row in rows
        ]

    def plan_task(
        self,
        *,
        message_id: str,
        revision_id: str,
        stage: str,
        kind: str,
        contract: dict[str, Any],
        payload: dict[str, Any],
        dependencies: Iterable[Dependency],
    ) -> dict[str, Any]:
        ordered_dependencies = sorted(set(dependencies))
        desired = {
            "message_id": message_id,
            "revision_id": revision_id,
            "stage": stage,
            "kind": kind,
            "contract_id": contract["contract_id"],
            "contract_sha256": contract["contract_sha256"],
            "payload": payload,
            "dependencies": [
                {"kind": item[0], "id": item[1], "sha256": item[2]}
                for item in ordered_dependencies
            ],
        }
        desired_sha256 = sha256_json(desired)
        existing = self.connection.execute(
            """
            SELECT * FROM tasks
            WHERE message_id = ? AND stage = ? AND desired_sha256 = ?
            """,
            (message_id, stage, desired_sha256),
        ).fetchone()
        now = utc_now()
        if existing is None:
            task_id = new_id("task")
            self.connection.execute(
                """
                INSERT INTO tasks(
                    task_id, message_id, revision_id, stage, kind, contract_id,
                    desired_sha256, payload_json, status, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)
                """,
                (
                    task_id, message_id, revision_id, stage, kind,
                    contract["contract_id"], desired_sha256,
                    canonical_json(payload), now, now,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO task_dependencies(
                    task_id, dependency_kind, dependency_id, dependency_sha256
                ) VALUES(?, ?, ?, ?)
                """,
                [(task_id, item[0], item[1], item[2]) for item in ordered_dependencies],
            )
            created = True
            self.audit(
                "task.planned",
                message_id=message_id,
                subject_id=task_id,
                details={"stage": stage, "kind": kind},
            )
        else:
            task_id = existing["task_id"]
            created = False

        self.connection.execute(
            """
            INSERT INTO stage_targets(message_id, stage, task_id, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(message_id, stage) DO UPDATE SET
                task_id = excluded.task_id,
                updated_at = excluded.updated_at
            """,
            (message_id, stage, task_id, now),
        )

        prior_result = self.connection.execute(
            "SELECT result_id FROM stage_results WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if prior_result is not None:
            self.connection.execute(
                """
                INSERT INTO stage_heads(message_id, stage, result_id, promoted_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(message_id, stage) DO UPDATE SET
                    result_id = excluded.result_id,
                    promoted_at = excluded.promoted_at
                """,
                (message_id, stage, prior_result["result_id"], now),
            )
            self.connection.execute(
                "UPDATE tasks SET status = 'promoted', updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            self.mark_dirty(message_id, f"result-reused:{prior_result['result_id']}")

        return {
            "task_id": task_id,
            "created": created,
            "desired_sha256": desired_sha256,
        }

    def task(self, task_id: str) -> dict[str, Any]:
        value = _decode(
            self.connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone(),
            "payload_json",
        )
        if value is None:
            raise ContractError(f"unknown task: {task_id}")
        value["dependencies"] = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT dependency_kind, dependency_id, dependency_sha256
                FROM task_dependencies WHERE task_id = ?
                ORDER BY dependency_kind, dependency_id
                """,
                (task_id,),
            )
        ]
        value["contract"] = self.contract(value["contract_id"])
        return value

    def next_ready_task(
        self,
        kind: str,
        *,
        excluded_stages: Iterable[str] = (),
    ) -> dict[str, Any] | None:
        excluded = tuple(excluded_stages)
        params: list[Any] = [kind]
        stage_filter = ""
        if excluded:
            placeholders = ",".join("?" for _ in excluded)
            stage_filter = f"AND tasks.stage NOT IN ({placeholders})"
            params.extend(excluded)
        row = self.connection.execute(
            f"""
            SELECT tasks.*
            FROM tasks
            JOIN stage_targets targets ON targets.task_id = tasks.task_id
            WHERE tasks.kind = ?
              AND tasks.status = 'planned'
              {stage_filter}
              AND NOT EXISTS (
                  SELECT 1
                  FROM workspaces
                  JOIN tasks live_tasks ON live_tasks.task_id = workspaces.task_id
                  WHERE live_tasks.message_id = tasks.message_id
                    AND live_tasks.stage = tasks.stage
                    AND workspaces.state IN ('preparing', 'published', 'returned')
              )
            ORDER BY tasks.created_at, tasks.task_id
            LIMIT 1
            """,
            params,
        ).fetchone()
        return self.task(row["task_id"]) if row else None

    def stage_target(self, message_id: str, stage: str) -> str | None:
        row = self.connection.execute(
            "SELECT task_id FROM stage_targets WHERE message_id = ? AND stage = ?",
            (message_id, stage),
        ).fetchone()
        return row["task_id"] if row else None

    def result(self, result_id: str) -> dict[str, Any]:
        value = _decode(
            self.connection.execute(
                "SELECT * FROM stage_results WHERE result_id = ?",
                (result_id,),
            ).fetchone(),
            "output_json",
        )
        if value is None:
            raise ContractError(f"unknown result: {result_id}")
        value["dependencies"] = [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT dependency_kind, dependency_id, dependency_sha256
                FROM result_dependencies WHERE result_id = ?
                ORDER BY dependency_kind, dependency_id
                """,
                (result_id,),
            )
        ]
        return value

    def current_result(self, message_id: str, stage: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT results.*
            FROM stage_heads heads
            JOIN stage_results results ON results.result_id = heads.result_id
            WHERE heads.message_id = ? AND heads.stage = ?
            """,
            (message_id, stage),
        ).fetchone()
        return _decode(row, "output_json") if row else None

    def create_workspace(self, task_id: str) -> dict[str, Any]:
        task = self.task(task_id)
        existing = self.connection.execute(
            "SELECT * FROM workspaces WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        active = self.connection.execute(
            """
            SELECT workspaces.workspace_id
            FROM workspaces
            JOIN tasks ON tasks.task_id = workspaces.task_id
            WHERE tasks.message_id = ? AND tasks.stage = ?
              AND workspaces.state IN ('preparing', 'published', 'returned')
            """,
            (task["message_id"], task["stage"]),
        ).fetchone()
        if active is not None:
            raise ContractError(
                f"stage already has a live workspace: {active['workspace_id']}"
            )
        workspace_id = new_id("ws")
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO workspaces(
                workspace_id, task_id, folder_name, state, created_at, updated_at
            ) VALUES(?, ?, ?, 'preparing', ?, ?)
            """,
            (workspace_id, task_id, workspace_id, now, now),
        )
        self.connection.execute(
            "UPDATE tasks SET status = 'preparing', updated_at = ? WHERE task_id = ?",
            (now, task_id),
        )
        self.audit(
            "workspace.preparing",
            message_id=task["message_id"],
            subject_id=workspace_id,
            details={"task_id": task_id, "stage": task["stage"]},
        )
        return dict(
            self.connection.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        )

    def workspace(self, workspace_id: str) -> dict[str, Any]:
        value = _decode(
            self.connection.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone(),
            "request_json",
            "input_manifest_json",
        )
        if value is None:
            raise ContractError(f"unknown workspace: {workspace_id}")
        return value

    def workspace_by_folder(self, folder_name: str) -> dict[str, Any] | None:
        return _decode(
            self.connection.execute(
                "SELECT * FROM workspaces WHERE folder_name = ?",
                (folder_name,),
            ).fetchone(),
            "request_json",
            "input_manifest_json",
        )

    def set_workspace_prepared(
        self,
        workspace_id: str,
        *,
        request: dict[str, Any],
        request_sha256: str,
        input_manifest: list[dict[str, Any]],
        input_manifest_sha256: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE workspaces SET
                request_json = ?, request_sha256 = ?,
                input_manifest_json = ?, input_manifest_sha256 = ?,
                updated_at = ?
            WHERE workspace_id = ? AND state = 'preparing'
            """,
            (
                canonical_json(request), request_sha256,
                canonical_json(input_manifest), input_manifest_sha256,
                utc_now(), workspace_id,
            ),
        )

    def set_workspace_state(self, workspace_id: str, state: str) -> None:
        if state not in {"preparing", "published", "returned", "absorbed"}:
            raise ContractError(f"invalid workspace state: {state}")
        workspace = self.workspace(workspace_id)
        task = self.task(workspace["task_id"])
        now = utc_now()
        self.connection.execute(
            "UPDATE workspaces SET state = ?, updated_at = ? WHERE workspace_id = ?",
            (state, now, workspace_id),
        )
        if state != "absorbed":
            self.connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (state, now, task["task_id"]),
            )
        self.audit(
            f"workspace.{state}",
            message_id=task["message_id"],
            subject_id=workspace_id,
            details={"task_id": task["task_id"]},
        )

    def discard_preparing_workspace(self, workspace_id: str) -> None:
        workspace = self.workspace(workspace_id)
        task = self.task(workspace["task_id"])
        self.connection.execute(
            "DELETE FROM workspaces WHERE workspace_id = ? AND state = 'preparing'",
            (workspace_id,),
        )
        if self.stage_target(task["message_id"], task["stage"]) == task["task_id"]:
            self.connection.execute(
                "UPDATE tasks SET status = 'planned', updated_at = ? WHERE task_id = ?",
                (utc_now(), task["task_id"]),
            )

    def promote_result(
        self,
        task_id: str,
        output: dict[str, Any],
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        with transaction(self.connection):
            task = self.task(task_id)
            existing = self.connection.execute(
                "SELECT result_id FROM stage_results WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if existing is not None:
                return {
                    "result_id": existing["result_id"],
                    "disposition": (
                        "promoted"
                        if self.stage_target(task["message_id"], task["stage"]) == task_id
                        else "superseded"
                    ),
                }

            now = utc_now()
            result_id = new_id("result")
            self.connection.execute(
                """
                INSERT INTO stage_results(
                    result_id, message_id, stage, task_id, contract_id,
                    output_json, output_sha256, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id, task["message_id"], task["stage"], task_id,
                    task["contract_id"], canonical_json(output),
                    sha256_json(output), now,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO result_dependencies(
                    result_id, dependency_kind, dependency_id, dependency_sha256
                ) VALUES(?, ?, ?, ?)
                """,
                [
                    (
                        result_id, item["dependency_kind"], item["dependency_id"],
                        item["dependency_sha256"],
                    )
                    for item in task["dependencies"]
                ],
            )
            is_desired = self.stage_target(task["message_id"], task["stage"]) == task_id
            disposition = "promoted" if is_desired else "superseded"
            self.connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (disposition, now, task_id),
            )
            if is_desired:
                self.connection.execute(
                    """
                    INSERT INTO stage_heads(message_id, stage, result_id, promoted_at)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(message_id, stage) DO UPDATE SET
                        result_id = excluded.result_id,
                        promoted_at = excluded.promoted_at
                    """,
                    (task["message_id"], task["stage"], result_id, now),
                )
            if workspace_id is not None:
                self.connection.execute(
                    "UPDATE workspaces SET state = 'absorbed', updated_at = ? WHERE workspace_id = ?",
                    (now, workspace_id),
                )
            self.mark_dirty(
                task["message_id"], f"{disposition}:{task['stage']}:{result_id}"
            )
            self.audit(
                f"result.{disposition}",
                message_id=task["message_id"],
                subject_id=result_id,
                details={"task_id": task_id, "stage": task["stage"]},
            )
            return {"result_id": result_id, "disposition": disposition}

    def set_control(self, target: str, paused: bool, reason: str) -> None:
        if not reason.strip():
            raise ContractError("control changes require a reason")
        valid = {"intake", "deterministic", "model_dispatch"} | {
            f"stage:{stage}" for stage in STAGES
        }
        if target not in valid:
            raise ContractError(f"invalid control target: {target}")
        if target == "intake" and not paused:
            mapping = self.current_definition("mapping_index", "discord_public")
            if mapping is None or not mapping_index_is_live(mapping["payload"]):
                raise ContractError(
                    "Intake cannot be unpaused until a live Mapping Index is published"
                )
        with transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO controls(target, paused, reason, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(target) DO UPDATE SET
                    paused = excluded.paused,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
                """,
                (target, int(paused), reason.strip(), utc_now()),
            )
            self.audit(
                "control.changed",
                subject_id=target,
                details={"paused": paused, "reason": reason.strip()},
            )

    def is_paused(self, target: str) -> bool:
        row = self.connection.execute(
            "SELECT paused FROM controls WHERE target = ?",
            (target,),
        ).fetchone()
        return bool(row["paused"]) if row else False

    def paused_stages(self) -> set[str]:
        return {
            row["target"].split(":", 1)[1]
            for row in self.connection.execute(
                "SELECT target FROM controls WHERE paused = 1 AND target LIKE 'stage:%'"
            )
        }

    def message_snapshot(self, message_id: str) -> dict[str, Any]:
        message = self.connection.execute(
            "SELECT * FROM messages WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if message is None:
            raise ContractError(f"unknown message: {message_id}")

        def rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            return [dict(row) for row in self.connection.execute(sql, params)]

        tasks = rows("SELECT * FROM tasks WHERE message_id = ?", (message_id,))
        task_ids = [item["task_id"] for item in tasks]
        results = rows(
            "SELECT * FROM stage_results WHERE message_id = ?", (message_id,)
        )
        result_ids = [item["result_id"] for item in results]
        task_marks = ",".join("?" for _ in task_ids)
        result_marks = ",".join("?" for _ in result_ids)
        snapshot = {
            "messages": [dict(message)],
            "source_revisions": rows(
                "SELECT * FROM source_revisions WHERE message_id = ?", (message_id,)
            ),
            "message_heads": rows(
                "SELECT * FROM message_heads WHERE message_id = ?", (message_id,)
            ),
            "ingested_sources": rows(
                "SELECT * FROM ingested_sources WHERE message_id = ?", (message_id,)
            ),
            "tasks": tasks,
            "task_dependencies": (
                rows(
                    f"SELECT * FROM task_dependencies WHERE task_id IN ({task_marks})",
                    tuple(task_ids),
                )
                if task_ids else []
            ),
            "stage_targets": rows(
                "SELECT * FROM stage_targets WHERE message_id = ?", (message_id,)
            ),
            "stage_results": results,
            "result_dependencies": (
                rows(
                    f"SELECT * FROM result_dependencies WHERE result_id IN ({result_marks})",
                    tuple(result_ids),
                )
                if result_ids else []
            ),
            "stage_heads": rows(
                "SELECT * FROM stage_heads WHERE message_id = ?", (message_id,)
            ),
            "workspaces": (
                rows(
                    f"SELECT * FROM workspaces WHERE task_id IN ({task_marks})",
                    tuple(task_ids),
                )
                if task_ids else []
            ),
            "dirty_messages": rows(
                "SELECT * FROM dirty_messages WHERE message_id = ?", (message_id,)
            ),
        }
        contract_ids = sorted(
            {item["contract_id"] for item in tasks if item.get("contract_id")}
        )
        definition_ids = sorted(
            {
                item["dependency_id"]
                for item in snapshot["task_dependencies"]
                if item["dependency_kind"] == "definition"
            }
        )
        snapshot["captured_contracts"] = (
            rows(
                f"SELECT * FROM contract_versions WHERE contract_id IN ({','.join('?' for _ in contract_ids)})",
                tuple(contract_ids),
            )
            if contract_ids else []
        )
        snapshot["captured_definitions"] = (
            rows(
                f"SELECT * FROM definition_versions WHERE definition_id IN ({','.join('?' for _ in definition_ids)})",
                tuple(definition_ids),
            )
            if definition_ids else []
        )
        return snapshot

    def create_quarantine_record(
        self,
        *,
        quarantine_id: str,
        message_id: str | None,
        source_path: str | None,
        reason: dict[str, Any],
        snapshot: dict[str, Any],
        bundle_name: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO quarantine_records(
                quarantine_id, message_id, source_path, state, reason_json,
                snapshot_json, bundle_name, created_at
            ) VALUES(?, ?, ?, 'preparing', ?, ?, ?, ?)
            """,
            (
                quarantine_id, message_id, source_path,
                canonical_json(reason), canonical_json(snapshot),
                bundle_name, utc_now(),
            ),
        )

    def quarantine_record(self, quarantine_id: str) -> dict[str, Any]:
        value = _decode(
            self.connection.execute(
                "SELECT * FROM quarantine_records WHERE quarantine_id = ?",
                (quarantine_id,),
            ).fetchone(),
            "reason_json",
            "snapshot_json",
        )
        if value is None:
            raise ContractError(f"unknown quarantine record: {quarantine_id}")
        return value

    def pending_quarantines(self) -> list[dict[str, Any]]:
        return [
            _decode(row, "reason_json", "snapshot_json")
            for row in self.connection.execute(
                """
                SELECT * FROM quarantine_records
                WHERE state != 'completed'
                ORDER BY created_at
                """
            )
        ]

    def mark_quarantine_published(self, quarantine_id: str) -> None:
        self.connection.execute(
            "UPDATE quarantine_records SET state = 'published' WHERE quarantine_id = ?",
            (quarantine_id,),
        )

    def complete_quarantine(
        self,
        quarantine_id: str,
        *,
        message_id: str | None,
    ) -> int:
        now = utc_now()
        with transaction(self.connection):
            if message_id is not None:
                self.connection.execute(
                    "DELETE FROM audit_events WHERE message_id = ?", (message_id,)
                )
                self.connection.execute(
                    "DELETE FROM messages WHERE message_id = ?", (message_id,)
                )
            self.connection.execute(
                """
                UPDATE quarantine_records
                SET state = 'completed',
                    completed_at = ?,
                    source_path = NULL,
                    message_id = NULL,
                    reason_json = '{"isolated":true}',
                    snapshot_json = '{}'
                WHERE quarantine_id = ?
                """,
                (now, quarantine_id),
            )
            self.audit(
                "quarantine.completed",
                message_id=None,
                subject_id=quarantine_id,
            )
            return self.connection.execute(
                """
                SELECT COUNT(*) FROM quarantine_records
                WHERE completed_at IS NOT NULL
                  AND julianday(completed_at) >= julianday(?) - (60.0 / 1440.0)
                """,
                (now,),
            ).fetchone()[0]

    def current_state(self, message_id: str) -> dict[str, Any]:
        revision = self.current_revision(message_id)
        heads = {}
        for row in self.connection.execute(
            """
            SELECT heads.stage, results.result_id, results.output_json,
                   results.output_sha256, heads.promoted_at
            FROM stage_heads heads
            JOIN stage_results results ON results.result_id = heads.result_id
            WHERE heads.message_id = ?
            ORDER BY heads.stage
            """,
            (message_id,),
        ):
            heads[row["stage"]] = {
                "result_id": row["result_id"],
                "output": json.loads(row["output_json"]),
                "output_sha256": row["output_sha256"],
                "promoted_at": row["promoted_at"],
            }
        return {"message_id": message_id, "revision": revision, "stages": heads}

    def resolve_message_text(
        self,
        message_id: str,
        *,
        audience: str = "private",
        version: int = 0,
    ) -> dict[str, Any]:
        from .resolution import resolve_result_text

        if audience not in {"private", "public"}:
            raise ContractError("audience must be private or public")
        if type(version) is not int or version < 0 or version > 9:
            raise ContractError("version must be from 0 through 9")
        if version == 0 and audience == "private":
            revision = self.current_revision(message_id)
            text = revision["message_text"]
            source = {"kind": "source_revision", "id": revision["revision_id"]}
        else:
            stage = (
                "public_safety"
                if version == 0
                else "original_versions" if audience == "private" else "public_versions"
            )
            result = self.current_result(message_id, stage)
            if result is None:
                raise ContractError(f"message has no current {stage} result")
            text = resolve_result_text(
                self,
                result["result_id"],
                level=version or None,
            )
            source = {"kind": "stage_result", "id": result["result_id"]}
        return {
            "message_id": message_id,
            "audience": audience,
            "version": version,
            "source": source,
            "message": text,
            "char_count": len(text),
            "message_sha256": sha256_text(text),
        }

    def status(self) -> dict[str, Any]:
        def grouped(table: str, column: str) -> dict[str, int]:
            return {
                str(row[column]): row["count"]
                for row in self.connection.execute(
                    f"SELECT {column}, COUNT(*) count FROM {table} GROUP BY {column}"
                )
            }

        return {
            "messages": self.connection.execute(
                "SELECT COUNT(*) FROM messages"
            ).fetchone()[0],
            "dirty_messages": self.connection.execute(
                "SELECT COUNT(*) FROM dirty_messages"
            ).fetchone()[0],
            "tasks": grouped("tasks", "status"),
            "workspaces": grouped("workspaces", "state"),
            "stage_heads": grouped("stage_heads", "stage"),
            "quarantines": grouped("quarantine_records", "state"),
            "controls": [
                dict(row)
                for row in self.connection.execute(
                    "SELECT * FROM controls ORDER BY target"
                )
            ],
        }

    def definition_bundle(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "definitions": [
                {**dict(row), "payload": json.loads(row["payload_json"])}
                for row in self.connection.execute(
                    "SELECT * FROM definition_versions ORDER BY kind, name, version"
                )
            ],
            "heads": [
                dict(row)
                for row in self.connection.execute(
                    "SELECT * FROM definition_heads ORDER BY kind, name"
                )
            ],
            "contracts": [
                {
                    **dict(row),
                    "workspace_spec": json.loads(row["workspace_spec_json"]),
                    "output_schema": json.loads(row["output_schema_json"]),
                    "config": json.loads(row["config_json"]),
                }
                for row in self.connection.execute(
                    "SELECT * FROM contract_versions ORDER BY stage, version"
                )
            ],
            "contract_heads": [
                dict(row)
                for row in self.connection.execute(
                    "SELECT * FROM contract_heads ORDER BY stage"
                )
            ],
        }

    def revision(self, revision_id: str) -> dict[str, Any]:
        value = _decode(
            self.connection.execute(
                "SELECT * FROM source_revisions WHERE revision_id = ?",
                (revision_id,),
            ).fetchone(),
        )
        if value is None:
            raise ContractError(f"unknown source revision: {revision_id}")
        return value

    def workspaces_in_state(self, *states: str) -> list[dict[str, Any]]:
        if not states:
            return []
        placeholders = ",".join("?" for _ in states)
        return [
            _decode(row, "request_json", "input_manifest_json")
            for row in self.connection.execute(
                f"""
                SELECT * FROM workspaces
                WHERE state IN ({placeholders})
                ORDER BY created_at, workspace_id
                """,
                states,
            )
        ]

    def task_message_id(self, task_id: str) -> str:
        row = self.connection.execute(
            "SELECT message_id FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise ContractError(f"unknown task: {task_id}")
        return row["message_id"]
