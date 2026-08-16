"""Focused compatibility coverage for the additive Knowledge Sink v5 input."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_sink_mcp_server import (
    handle_knowledge_sink,
    knowledge_sink_tool_definition,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import sha256_bytes

_V2_SHA256 = "b3c5c100471cec3a8ecdce115255ae3e4d0d7053800936e5a611fe103527019a"
_V3_SHA256 = "828ccd6ca1faf1229c121236d43844ed7f3e010b98b7cf82d3dfc772958bbcd3"
_V4_SHA256 = "8e127318b821169f19408f60c4be3da19d240a7cc180776adb5715e30efe2b77"
_V5_SHA256 = "fd4dbcb6ab75dca703643f4c09f7f10fa09eddcc63674ae017cb8a77040011f7"


def _repository() -> Path:
    return Path(__file__).resolve().parents[1]


def _digest(label: str) -> str:
    return sha256_bytes(label.encode("utf-8"))


def _binding() -> dict[str, Any]:
    return build_task_context_binding(
        _digest("project"),
        _digest("task-line"),
        parent_task_lineage_sha256=_digest("parent-task-line"),
        repository_sha256=_digest("repository"),
        worktree_sha256=_digest("worktree"),
        base_revision="a" * 40,
        dirty_state_sha256=_digest("dirty-state"),
    )


def _record_run(*, key: str = "sink-v5", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    request: dict[str, Any] = {
        "operation": "record_run",
        "idempotency_key": key,
        "confirm_no_case_data": True,
        "run_id": f"run-{key}",
        "task": "Exercise the additive Knowledge Sink contract.",
        "host_id": "pytest",
        "status": "succeeded",
    }
    if metadata is not None:
        request["run_metadata"] = metadata
    return request


def test_v2_is_byte_exact_and_keeps_unbound_record_run() -> None:
    path = _repository() / "contracts/knowledge-sink.input.v2.schema.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == _V2_SHA256

    schema = json.loads(path.read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(_record_run())
    assert list(validator.iter_errors(_record_run(metadata={"task_binding": {}})))


def test_v5_schema_remains_valid_and_v6_tool_accepts_both_run_shapes() -> None:
    path = _repository() / "contracts/knowledge-sink.input.v5.schema.json"
    schema = json.loads(path.read_text())
    Draft202012Validator.check_schema(schema)

    tool = knowledge_sink_tool_definition()
    assert tool.inputSchema["$id"].endswith("knowledge-sink.input.v6.schema.json")
    validator = Draft202012Validator(tool.inputSchema, format_checker=FormatChecker())
    validator.validate(_record_run(key="legacy"))
    validator.validate(
        _record_run(key="bound", metadata={"task_binding": _binding()})
    )

    invalid_bindings = [
        None,
        {**_binding(), "schema_version": "deeplaw.task-context-binding/v0"},
        {**_binding(), "worktree_path": "/private/repo"},
        {**_binding(), "branch": "private-feature"},
    ]
    for index, binding in enumerate(invalid_bindings):
        request = _record_run(key=f"invalid-{index}", metadata={"task_binding": binding})
        assert list(validator.iter_errors(request))
    unknown = _record_run(
        key="unknown",
        metadata={"task_binding": _binding(), "unknown": "field"},
    )
    assert list(validator.iter_errors(unknown))
    top_level_unknown = {**_record_run(key="top-level-unknown"), "branch": "private-feature"}
    assert list(validator.iter_errors(top_level_unknown))
    unsafe = _record_run(
        key="unsafe-artifact",
        metadata={"task_binding": _binding(), "artifact_ids": ["C:/private/report.json"]},
    )
    assert list(validator.iter_errors(unsafe))


@pytest.mark.parametrize(
    ("operations", "input_version", "output_version"),
    [
        (("remember",), "v2", "v2"),
        (("begin_compilation",), "v3", "v3"),
        (("stage_semantic_observations",), "v4", "v4"),
        (("record_run",), "v6", "v2"),
        (("record_run", "stage_semantic_observations"), "v6", "v4"),
    ],
)
def test_grant_operation_selection_is_additive(
    operations: tuple[str, ...],
    input_version: str,
    output_version: str,
) -> None:
    tool = knowledge_sink_tool_definition(operations=operations)
    assert tool.inputSchema["$id"].endswith(f"knowledge-sink.input.{input_version}.schema.json")
    assert tool.outputSchema["$id"].endswith(f"knowledge-sink.output.{output_version}.schema.json")


def test_handle_accepts_legacy_and_bound_runs_through_one_store(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="sink-v5", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="sink-v5",
            operations=("record_run",),
        )["grant_id"]

    legacy = handle_knowledge_sink(
        _record_run(key="handle-legacy"), grant_id=grant_id, vault_path=root
    )
    bound = handle_knowledge_sink(
        _record_run(
            key="handle-bound",
            metadata={"task_binding": _binding()},
        ),
        grant_id=grant_id,
        vault_path=root,
    )
    assert legacy["result"]["run_id"] == "run-handle-legacy"
    assert bound["result"]["run_id"] == "run-handle-bound"
    assert legacy["schema_version"] == "deeplaw.knowledge-sink-output/v2"
    assert bound["schema_version"] == "deeplaw.knowledge-sink-output/v2"

    tampered = _binding()
    tampered["task_lineage_sha256"] = _digest("tampered-task-line")
    with pytest.raises(ValueError, match="binding_sha256"):
        handle_knowledge_sink(
            _record_run(
                key="handle-tampered",
                metadata={"task_binding": tampered},
            ),
            grant_id=grant_id,
            vault_path=root,
        )


def test_v2_v3_v4_v5_contract_bytes_remain_unchanged() -> None:
    expected = {
        "knowledge-sink.input.v2.schema.json": _V2_SHA256,
        "knowledge-sink.input.v3.schema.json": _V3_SHA256,
        "knowledge-sink.input.v4.schema.json": _V4_SHA256,
        "knowledge-sink.input.v5.schema.json": _V5_SHA256,
    }
    for name, digest in expected.items():
        path = _repository() / "contracts" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
