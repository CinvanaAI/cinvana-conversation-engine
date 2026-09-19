from __future__ import annotations

from typing import Any

from .errors import ContractError


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    if not isinstance(schema, dict):
        raise ContractError(f"{path}: schema must be an object")

    alternatives = schema.get("oneOf")
    if alternatives is not None:
        if not isinstance(alternatives, list) or not alternatives:
            raise ContractError(f"{path}: oneOf must be a non-empty array")
        matches = 0
        for alternative in alternatives:
            try:
                validate_schema(value, alternative, path)
            except ContractError:
                continue
            matches += 1
        if matches != 1:
            raise ContractError(f"{path}: expected exactly one matching schema, got {matches}")
        return

    expected = schema.get("type")
    type_checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: type(item) is int,
        "number": lambda item: type(item) in {int, float},
        "boolean": lambda item: type(item) is bool,
        "null": lambda item: item is None,
    }
    if expected is not None:
        checker = type_checks.get(expected)
        if checker is None:
            raise ContractError(f"{path}: unsupported schema type {expected!r}")
        if not checker(value):
            raise ContractError(f"{path}: expected {expected}")

    if "enum" in schema and value not in schema["enum"]:
        raise ContractError(f"{path}: value is not in the allowed enum")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise ContractError(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unexpected = sorted(set(value).difference(properties))
            if unexpected:
                raise ContractError(f"{path}: unexpected properties {unexpected}")
        for name, child in properties.items():
            if name in value:
                validate_schema(value[name], child, f"{path}.{name}")

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            raise ContractError(f"{path}: requires at least {minimum} items")
        if maximum is not None and len(value) > maximum:
            raise ContractError(f"{path}: allows at most {maximum} items")
        if schema.get("uniqueItems"):
            encoded = [repr(item) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ContractError(f"{path}: items must be unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                validate_schema(item, item_schema, f"{path}[{index}]")

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if minimum is not None and len(value) < minimum:
            raise ContractError(f"{path}: string is shorter than {minimum}")
        if maximum is not None and len(value) > maximum:
            raise ContractError(f"{path}: string is longer than {maximum}")

    if type(value) in {int, float}:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            raise ContractError(f"{path}: value is below {minimum}")
        if maximum is not None and value > maximum:
            raise ContractError(f"{path}: value is above {maximum}")
