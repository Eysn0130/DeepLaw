from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from deeplaw.knowledge_mcp_server import (
    _default_provider_max_chars,
    _provider_input_validator,
    _validate_knowledge_tool_arguments,
    knowledge_tool_definition,
)

REPOSITORY = Path(__file__).resolve().parents[1]
CONTRACT = REPOSITORY / "contracts/knowledge-support.input.v7.schema.json"


def _canonical_bytes(value: object) -> int:
    return len(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _advertised_operations(schema: dict[str, object]) -> set[str]:
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    operations: set[str] = set()
    for name in ("query", "context", "explain"):
        branch = definitions[name]
        assert isinstance(branch, dict)
        properties = branch["properties"]
        assert isinstance(properties, dict)
        operation = properties["operation"]
        assert isinstance(operation, dict)
        operations.add(str(operation["const"]))
    return operations


def test_provider_contract_is_closed_and_advertises_only_three_operations() -> None:
    schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    definition = knowledge_tool_definition(autonomous=True)
    assert _advertised_operations(definition.inputSchema) == {
        "query",
        "context",
        "explain",
    }
    assert "search" not in json.dumps(definition.inputSchema, sort_keys=True)


def test_canonical_tool_definition_stays_within_eight_kibibytes() -> None:
    definition = knowledge_tool_definition(autonomous=True)
    payload = definition.model_dump(by_alias=True, exclude_none=True)
    assert _canonical_bytes(definition.inputSchema) <= 7_000
    assert _canonical_bytes(payload) <= 8 * 1024


def test_legacy_v1_to_v6_calls_are_internal_compatibility_only() -> None:
    legacy = {"operation": "search", "query": "governed decision", "limit": 3}
    assert next(_provider_input_validator().iter_errors(legacy), None) is not None
    assert (
        _validate_knowledge_tool_arguments(legacy, autonomous=True)
        == "internal_compatibility"
    )


def test_unknown_operation_is_rejected_by_both_contract_planes() -> None:
    invalid = {"operation": "hidden_admin", "query": "x"}
    try:
        _validate_knowledge_tool_arguments(invalid, autonomous=True)
    except ValueError as error:
        assert "current Provider contract or a historical compatibility contract" in str(
            error
        )
    else:
        raise AssertionError("unknown operation was admitted")


def test_initial_capsule_character_budgets_are_bounded() -> None:
    assert _default_provider_max_chars({"operation": "context"}) == 8_000
    assert (
        _default_provider_max_chars({"operation": "query", "purpose": "legal"})
        == 16_000
    )
    assert (
        _default_provider_max_chars({"operation": "context", "max_chars": 1_500})
        == 1_500
    )
