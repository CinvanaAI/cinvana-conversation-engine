from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from .db import transaction
from .definition_validation import validate_definition
from .errors import ContractError
from .jsonutil import read_json, write_json_atomic
from .repository import Repository
from .validators import VALIDATORS


SEED_PACKAGE = "conversation_engine.seed"


def load_seed(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        value = json.loads(
            files(SEED_PACKAGE).joinpath("bootstrap.json").read_text(encoding="utf-8")
        )
    else:
        value = read_json(path)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ContractError("bootstrap bundle must use schema_version 1")
    return value


def bootstrap(repository: Repository, path: Path | None = None) -> dict[str, int]:
    bundle = load_seed(path)
    definitions = bundle.get("definitions")
    contracts = bundle.get("contracts")
    if not isinstance(definitions, list) or not isinstance(contracts, list):
        raise ContractError("bootstrap bundle requires definitions and contracts arrays")
    for contract in contracts:
        key = (contract.get("validator_key"), contract.get("validator_version"))
        if key not in VALIDATORS:
            raise ContractError(f"bootstrap references unavailable validator: {key}")

    with transaction(repository.connection):
        for definition in definitions:
            repository.publish_definition(
                definition["kind"],
                definition["name"],
                definition["payload"],
            )
        for contract in contracts:
            repository.publish_contract(
                stage=contract["stage"],
                worker_policy=contract["worker_policy"],
                validator_key=contract["validator_key"],
                validator_version=contract["validator_version"],
                workspace_spec=contract["workspace_spec"],
                output_schema=contract["output_schema"],
                config=contract["config"],
            )
        repository.set_control(
            "intake",
            True,
            "Fresh vault: publish the live Mapping Index and approve Intake before use.",
        )
    return {"definitions": len(definitions), "contracts": len(contracts)}


def export(repository: Repository, path: Path) -> None:
    write_json_atomic(path, repository.definition_bundle())


def publish_definition_file(
    repository: Repository,
    *,
    kind: str,
    name: str,
    path: Path,
) -> dict[str, Any]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ContractError("definition file must contain a JSON object")
    return repository.publish_definition(kind, name, payload)


def publish_contract_file(
    repository: Repository,
    *,
    path: Path,
) -> dict[str, Any]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ContractError("contract file must contain a JSON object")
    key = (payload.get("validator_key"), payload.get("validator_version"))
    if key not in VALIDATORS:
        raise ContractError(f"contract references unavailable validator: {key}")
    return repository.publish_contract(
        stage=payload["stage"],
        worker_policy=payload["worker_policy"],
        validator_key=payload["validator_key"],
        validator_version=payload["validator_version"],
        workspace_spec=payload["workspace_spec"],
        output_schema=payload["output_schema"],
        config=payload["config"],
    )


def restore(repository: Repository, path: Path) -> dict[str, int]:
    bundle = read_json(path)
    if not isinstance(bundle, dict) or bundle.get("schema_version") != 1:
        raise ContractError("definition recovery bundle must use schema_version 1")
    definitions = bundle.get("definitions")
    heads = bundle.get("heads")
    contracts = bundle.get("contracts")
    contract_heads = bundle.get("contract_heads")
    if not all(
        isinstance(value, list)
        for value in (definitions, heads, contracts, contract_heads)
    ):
        raise ContractError("definition recovery bundle is incomplete")
    occupied = repository.connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM messages)
          + (SELECT COUNT(*) FROM definition_versions)
          + (SELECT COUNT(*) FROM contract_versions)
        """
    ).fetchone()[0]
    if occupied:
        raise ContractError("exact definition restore requires an empty initialized vault")

    from .jsonutil import canonical_json, sha256_json

    with transaction(repository.connection):
        for item in definitions:
            payload = item.get("payload")
            if isinstance(payload, dict):
                validate_definition(item.get("kind"), item.get("name"), payload)
            if not isinstance(payload, dict) or sha256_json(payload) != item.get(
                "payload_sha256"
            ):
                raise ContractError("definition recovery hash mismatch")
            repository.connection.execute(
                """
                INSERT INTO definition_versions(
                    definition_id, kind, name, version, payload_json,
                    payload_sha256, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["definition_id"],
                    item["kind"],
                    item["name"],
                    item["version"],
                    canonical_json(payload),
                    item["payload_sha256"],
                    item["created_at"],
                ),
            )
        for item in contracts:
            key = (item.get("validator_key"), item.get("validator_version"))
            if key not in VALIDATORS:
                raise ContractError(f"recovery requires unavailable validator: {key}")
            canonical = {
                "stage": item["stage"],
                "worker_policy": item["worker_policy"],
                "validator_key": item["validator_key"],
                "validator_version": item["validator_version"],
                "workspace_spec": item["workspace_spec"],
                "output_schema": item["output_schema"],
                "config": item["config"],
            }
            if sha256_json(canonical) != item.get("contract_sha256"):
                raise ContractError("contract recovery hash mismatch")
            repository.connection.execute(
                """
                INSERT INTO contract_versions(
                    contract_id, stage, version, worker_policy,
                    validator_key, validator_version, workspace_spec_json,
                    output_schema_json, config_json, contract_sha256, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["contract_id"],
                    item["stage"],
                    item["version"],
                    item["worker_policy"],
                    item["validator_key"],
                    item["validator_version"],
                    canonical_json(item["workspace_spec"]),
                    canonical_json(item["output_schema"]),
                    canonical_json(item["config"]),
                    item["contract_sha256"],
                    item["created_at"],
                ),
            )
        repository.connection.executemany(
            "INSERT INTO definition_heads(kind, name, definition_id) VALUES(?, ?, ?)",
            [(item["kind"], item["name"], item["definition_id"]) for item in heads],
        )
        repository.connection.executemany(
            "INSERT INTO contract_heads(stage, contract_id) VALUES(?, ?)",
            [(item["stage"], item["contract_id"]) for item in contract_heads],
        )
        repository.set_control(
            "intake",
            True,
            "Recovered vault requires explicit operational approval.",
        )
        repository.audit(
            "definitions.restored",
            details={
                "definitions": len(definitions),
                "contracts": len(contracts),
            },
        )
    return {"definitions": len(definitions), "contracts": len(contracts)}
