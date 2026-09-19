from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from conversation_engine.bootstrap import bootstrap
from conversation_engine.config import EnginePaths
from conversation_engine.db import Database
from conversation_engine.planner import Planner
from conversation_engine.repository import Repository
from conversation_engine.service import EngineService
from conversation_engine.workspaces import WorkspaceManager


@dataclass
class EngineHarness:
    paths: EnginePaths
    database: Database
    connection: object
    repository: Repository
    planner: Planner
    workspaces: WorkspaceManager
    service: EngineService


@pytest.fixture
def engine(tmp_path_factory: pytest.TempPathFactory):
    paths = EnginePaths.from_root(tmp_path_factory.mktemp("ce") / "Conversation Engine")
    database = Database(paths)
    database.initialize()
    connection = database.connect()
    repository = Repository(connection)
    bootstrap(repository)
    repository.publish_definition(
        "mapping_index",
        "discord_public",
        {
            "live_ready": True,
            "titles": [
                {"title": "Alpha", "definition": "Alpha work"},
                {"title": "Beta", "definition": "Beta work"},
            ],
        },
    )
    repository.set_control("intake", False, "test fixture is operational")
    harness = EngineHarness(
        paths=paths,
        database=database,
        connection=connection,
        repository=repository,
        planner=Planner(repository),
        workspaces=WorkspaceManager(paths, repository),
        service=EngineService(paths, repository, intake_limit=500, work_limit=500),
    )
    yield harness
    connection.close()


def envelope(
    path: Path,
    *,
    text: str = "Hello from the test message.",
    chat_id: str = "chat-test",
    position: int = 1,
    timestamp: str | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "chat": {
            "chat_id": chat_id,
            "destination_rel_path": f"Archived/{chat_id}",
            "position": position,
        },
        "message": {
            "speaker": "User",
            "timestamp": timestamp or f"2026-01-01T00:00:{position:02d}-05:00",
            "text": text,
        },
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def task_for(repository: Repository, message_id: str, stage: str) -> dict:
    task_id = repository.stage_target(message_id, stage)
    assert task_id is not None
    return repository.task(task_id)


def workspace_for_stage(
    repository: Repository,
    message_id: str,
    stage: str,
) -> dict:
    row = repository.connection.execute(
        """
        SELECT workspaces.workspace_id
        FROM workspaces
        JOIN tasks ON tasks.task_id = workspaces.task_id
        WHERE tasks.message_id = ? AND tasks.stage = ?
          AND workspaces.state IN ('preparing', 'published', 'returned')
        ORDER BY workspaces.created_at DESC
        LIMIT 1
        """,
        (message_id, stage),
    ).fetchone()
    assert row is not None
    return repository.workspace(row["workspace_id"])


def workspace_folder(
    harness: EngineHarness,
    workspace: dict,
    bucket: str = "To_Do",
) -> Path:
    task = harness.repository.task(workspace["task_id"])
    roots = {
        "To_Do": harness.paths.workspace_todo_for_stage(task["stage"]),
        "Done": harness.paths.workspace_done_for_stage(task["stage"]),
        "Claimed": harness.paths.workspace_claimed_for_stage(task["stage"]),
    }
    return roots[bucket] / workspace["folder_name"]


def return_workspace(
    harness: EngineHarness,
    workspace: dict,
    *,
    response: dict | None = None,
    versions: list[str] | None = None,
    suggestion: str | None = None,
) -> Path:
    todo = workspace_folder(harness, workspace, "To_Do")
    if response is not None:
        (todo / "response.json").write_text(
            json.dumps(response), encoding="utf-8"
        )
    for index, text in enumerate(versions or [], start=1):
        (todo / f"version_{index}.txt").write_text(text, encoding="utf-8")
    if suggestion is not None:
        (todo / "Suggestions").write_text(suggestion, encoding="utf-8")
    done = workspace_folder(harness, workspace, "Done")
    shutil.move(str(todo), str(done))
    return done
