"""Contract and integrity regressions for PRD 1.2 task-context binding.

These tests use only deterministic development data. They are not Human Gold,
Host qualification, or release evidence.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_mcp_server import (
    _compatibility_input_validator,
    _validate_knowledge_tool_arguments,
    knowledge_tool_definition,
)
from deeplaw.knowledge_sink_mcp_server import knowledge_sink_tool_definition
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import (
    SCHEMA_VERSION,
    build_task_context_binding,
    normalize_task_context_binding,
)
from deeplaw.util import canonical_json, sha256_bytes


def _digest(label: str) -> str:
    return sha256_bytes(label.encode("utf-8"))


def _binding() -> dict[str, object]:
    return build_task_context_binding(
        _digest("project"),
        _digest("task-line"),
        parent_task_lineage_sha256=_digest("parent-task-line"),
        repository_sha256=_digest("repository"),
        worktree_sha256=_digest("worktree"),
        base_revision="a" * 40,
        dirty_state_sha256=_digest("dirty-state"),
    )


def test_task_context_binding_is_closed_canonical_and_tamper_evident() -> None:
    binding = _binding()

    assert binding["schema_version"] == SCHEMA_VERSION
    assert normalize_task_context_binding(binding, allow_none=False) == binding
    assert list(binding) == [
        "schema_version",
        "project_sha256",
        "task_lineage_sha256",
        "parent_task_lineage_sha256",
        "repository_sha256",
        "worktree_sha256",
        "base_revision",
        "dirty_state_sha256",
        "binding_sha256",
    ]

    tampered = {**binding, "task_lineage_sha256": _digest("other-task-line")}
    with pytest.raises(ValueError, match="binding_sha256"):
        normalize_task_context_binding(tampered, allow_none=False)
    with pytest.raises(ValueError, match="closed"):
        normalize_task_context_binding({**binding, "branch": "private"}, allow_none=False)
    with pytest.raises(ValueError, match="all present or all null"):
        build_task_context_binding(
            _digest("project"),
            _digest("task-line"),
            repository_sha256=_digest("repository"),
        )
    with pytest.raises(ValueError, match="must differ"):
        build_task_context_binding(
            _digest("project"),
            _digest("task-line"),
            parent_task_lineage_sha256=_digest("task-line"),
        )
    with pytest.raises(ValueError, match="required"):
        normalize_task_context_binding(None, allow_none=False)


def test_run_record_binds_receipt_event_and_integrity_verification(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="task-binding-integrity", scope="project")
    initialize_autonomous_core(root)
    binding = _binding()

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="task-binding-test",
            operations=("record_run",),
        )["grant_id"]
        receipt = store.record_run(
            grant_id=grant_id,
            idempotency_key="task-binding-run",
            run_id="run-task-binding-integrity",
            task="Verify a bounded task binding.",
            host_id="pytest",
            status="succeeded",
            metadata={"task_binding": binding},
            confirm_no_case_data=True,
        )

        assert receipt["metadata"]["task_binding"] == binding
        assert store.run_task_context_binding(receipt["run_id"]) == binding
        assert store.verify()["valid"] is True

        tampered = deepcopy(binding)
        tampered["project_sha256"] = _digest("other-project")
        store.connection.execute(
            "UPDATE knowledge_run_records_v4 SET metadata_json = ? WHERE run_id = ?",
            (canonical_json({"task_binding": tampered}), receipt["run_id"]),
        )
        store.connection.commit()
        assert store.run_task_context_binding(receipt["run_id"]) is None
        verification = store.verify()
        assert verification["valid"] is False


def test_mcp_schemas_accept_only_opaque_task_binding() -> None:
    binding = _binding()
    sink_validator = Draft202012Validator(knowledge_sink_tool_definition().inputSchema)
    support_validator = Draft202012Validator(
        knowledge_tool_definition(autonomous=True).inputSchema
    )
    compatibility_validator = _compatibility_input_validator()
    run_request = {
        "operation": "record_run",
        "idempotency_key": "task-binding-schema",
        "confirm_no_case_data": True,
        "run_id": "run-task-binding-schema",
        "task": "Verify MCP task binding schema.",
        "host_id": "pytest",
        "status": "succeeded",
        "run_metadata": {"task_binding": binding},
    }
    query_request = {
        "operation": "context",
        "task": "Resume the exact task line.",
        "confirm_no_case_data": True,
        "task_binding": binding,
    }

    sink_validator.validate(run_request)
    assert list(support_validator.iter_errors(query_request))
    compatibility_validator.validate(query_request)
    assert _validate_knowledge_tool_arguments(query_request, autonomous=True) == (
        "internal_compatibility"
    )
    assert list(
        sink_validator.iter_errors(
            {
                **run_request,
                "run_metadata": {
                    "task_binding": {**binding, "worktree_path": "/private/repo"}
                },
            }
        )
    )
    assert list(
        compatibility_validator.iter_errors(
            {
                **query_request,
                "task_binding": {**binding, "branch": "private-feature"},
            }
        )
    )
    assert list(
        compatibility_validator.iter_errors(
            {
                **query_request,
                "query_plan_version": "5",
            }
        )
    )


def test_run_metadata_rejects_explicit_null_task_binding(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="task-binding-null", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="task-binding-test",
            operations=("record_run",),
        )["grant_id"]
        with pytest.raises(ValueError, match="required"):
            store.record_run(
                grant_id=grant_id,
                idempotency_key="task-binding-null",
                run_id="run-task-binding-null",
                task="Reject an explicitly null binding.",
                host_id="pytest",
                status="succeeded",
                metadata={"task_binding": None},
                confirm_no_case_data=True,
            )


@pytest.mark.parametrize("status", ["failed", "partial", "aborted"])
def test_working_checkpoint_requires_a_successful_bound_run(
    tmp_path: Path,
    status: str,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name=f"task-binding-{status}", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="task-binding-test",
            operations=("record_run", "remember"),
        )["grant_id"]
        run = store.record_run(
            grant_id=grant_id,
            idempotency_key=f"task-binding-{status}-run",
            run_id=f"run-task-binding-{status}",
            task="Do not admit unsuccessful working state.",
            host_id="pytest",
            status=status,
            metadata={"task_binding": _binding()},
            confirm_no_case_data=True,
        )
        with pytest.raises(ValueError, match="successful task-bound Run Record"):
            store.remember(
                grant_id=grant_id,
                idempotency_key=f"task-binding-{status}-checkpoint",
                title="Unsuccessful task checkpoint",
                body="This state must not influence current task context.",
                kind="memory",
                memory_type="working",
                expires_at="2099-01-01T00:00:00Z",
                run_id=run["run_id"],
                confirm_no_case_data=True,
            )
