from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes

knowledge_autonomy = importlib.import_module("deeplaw.knowledge_autonomy")


_RUN_ID = "run-checkpoint-continuity"
_MISMATCHED_RUN_ID = "run-checkpoint-other-task"
_SEMANTIC_KEY = "checkpoint:task-4f3a:slot-0"
_EXPIRES_AT = "2099-01-01T00:00:00Z"
_TASK_BINDING = build_task_context_binding(
    sha256_bytes(b"v013-checkpoint-project"),
    sha256_bytes(b"v013-checkpoint-task-line"),
)
_MISMATCHED_TASK_BINDING = build_task_context_binding(
    sha256_bytes(b"v013-checkpoint-project"),
    sha256_bytes(b"v013-checkpoint-other-task-line"),
)
_OLD_BODY = """GOAL: Preserve the old task state.
CONFIRMED_DECISION: Use the old cursor.
CONSTRAINT: Keep the checkpoint bounded.
VERIFIED_FACT: The old cursor is three.
OPEN_GAP: Current state is not yet available.
NEXT_ACTION: Prepare the old task state.
ARTIFACT_REF: commit:old."""
_CURRENT_BODY = """GOAL: Preserve the current task state.
CONFIRMED_DECISION: Use the current cursor.
CONSTRAINT: Keep the checkpoint bounded.
VERIFIED_FACT: The current cursor is seven.
OPEN_GAP: Provider-host evidence is absent.
NEXT_ACTION: Review the current task state.
ARTIFACT_REF: commit:current."""
_DISTRACTOR_BODY = """GOAL: Preserve an unrelated task.
CONFIRMED_DECISION: Use the unrelated cursor.
CONSTRAINT: Keep the distractor bounded.
VERIFIED_FACT: The unrelated cursor is ninety-nine.
OPEN_GAP: The unrelated result is absent.
NEXT_ACTION: Discard the unrelated task.
ARTIFACT_REF: commit:distractor."""
_MISMATCHED_BODY = """GOAL: Preserve another task line.
CONFIRMED_DECISION: Use another task-line cursor.
CONSTRAINT: Keep the mismatched memory bounded.
VERIFIED_FACT: The other task-line cursor is unavailable.
OPEN_GAP: It must never enter current context.
NEXT_ACTION: Reject the mismatched task line.
ARTIFACT_REF: commit:mismatched."""
_RAW_LOG_BODY = "tool stdout: full raw tool transcript must not become a Task Checkpoint"


def _checkpoint_vault(tmp_path: Path) -> tuple[Path, str, str, str, str, str, str]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-checkpoint-continuity", scope="project")
    knowledge_autonomy.initialize_autonomous_core(root)
    with knowledge_autonomy.AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="v013-checkpoint-continuity",
            operations=tuple(sorted(knowledge_autonomy.SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]

    handle_knowledge_sink(
        {
            "operation": "record_run",
            "idempotency_key": "checkpoint-run-record",
            "confirm_no_case_data": True,
            "run_id": _RUN_ID,
            "task": "Resume a deterministic checkpoint fixture.",
            "host_id": "pytest-checkpoint-continuity",
            "model_id": "deterministic-test-model",
            "status": "succeeded",
            "scope": "project",
            "sensitivity": "private",
            "run_metadata": {"task_binding": _TASK_BINDING},
        },
        grant_id=grant_id,
        vault_path=root,
    )
    handle_knowledge_sink(
        {
            "operation": "record_run",
            "idempotency_key": "checkpoint-mismatched-run-record",
            "confirm_no_case_data": True,
            "run_id": _MISMATCHED_RUN_ID,
            "task": "Resume a deterministic checkpoint for another task line.",
            "host_id": "pytest-checkpoint-continuity-other-task",
            "model_id": "deterministic-test-model",
            "status": "succeeded",
            "scope": "project",
            "sensitivity": "private",
            "run_metadata": {"task_binding": _MISMATCHED_TASK_BINDING},
        },
        grant_id=grant_id,
        vault_path=root,
    )

    first = handle_knowledge_sink(
        {
            "operation": "remember",
            "idempotency_key": "checkpoint-create",
            "confirm_no_case_data": True,
            "title": "Task checkpoint",
            "body": _OLD_BODY,
            "kind": "memory",
            "memory_type": "working",
            "semantic_key": _SEMANTIC_KEY,
            "expires_at": _EXPIRES_AT,
            "scope": "project",
            "sensitivity": "private",
            "run_id": _RUN_ID,
            "model_id": "deterministic-test-model",
            "tool_id": "pytest-checkpoint-continuity",
            "tags": ["checkpoint", "task-4f3a"],
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    current = handle_knowledge_sink(
        {
            "operation": "remember",
            "idempotency_key": "checkpoint-cas-update",
            "confirm_no_case_data": True,
            "title": "Task checkpoint",
            "body": _CURRENT_BODY,
            "kind": "memory",
            "memory_type": "working",
            "knowledge_id": first["knowledge_id"],
            "expected_revision_id": first["revision_id"],
            "semantic_key": _SEMANTIC_KEY,
            "expires_at": _EXPIRES_AT,
            "scope": "project",
            "sensitivity": "private",
            "run_id": _RUN_ID,
            "model_id": "deterministic-test-model",
            "tool_id": "pytest-checkpoint-continuity",
            "tags": ["checkpoint", "task-4f3a"],
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    handle_knowledge_sink(
        {
            "operation": "remember",
            "idempotency_key": "checkpoint-distractor",
            "confirm_no_case_data": True,
            "title": "Unrelated state",
            "body": _DISTRACTOR_BODY,
            "kind": "memory",
            "memory_type": "episodic",
            "semantic_key": "checkpoint:other-task:slot-0",
            "expires_at": _EXPIRES_AT,
            "scope": "project",
            "sensitivity": "private",
            "run_id": _RUN_ID,
            "model_id": "deterministic-test-model",
            "tool_id": "pytest-checkpoint-continuity",
            "tags": ["checkpoint", "other-task"],
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    mismatched = handle_knowledge_sink(
        {
            "operation": "remember",
            "idempotency_key": "checkpoint-mismatched",
            "confirm_no_case_data": True,
            "title": "Mismatched task-line state",
            "body": _MISMATCHED_BODY,
            "kind": "memory",
            "memory_type": "working",
            "semantic_key": "checkpoint:mismatched-task:slot-0",
            "expires_at": _EXPIRES_AT,
            "scope": "project",
            "sensitivity": "private",
            "run_id": _MISMATCHED_RUN_ID,
            "model_id": "deterministic-test-model",
            "tool_id": "pytest-checkpoint-continuity",
            "tags": ["checkpoint", "mismatched-task"],
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    raw_log = handle_knowledge_sink(
        {
            "operation": "remember",
            "idempotency_key": "checkpoint-raw-log",
            "confirm_no_case_data": True,
            "title": "Raw tool log",
            "body": _RAW_LOG_BODY,
            "kind": "memory",
            "memory_type": "episodic",
            "semantic_key": "checkpoint:raw-log:slot-0",
            "expires_at": _EXPIRES_AT,
            "scope": "project",
            "sensitivity": "private",
            "run_id": _RUN_ID,
            "model_id": "deterministic-test-model",
            "tool_id": "pytest-checkpoint-continuity",
            "tags": ["checkpoint", "raw-log"],
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    return (
        root,
        grant_id,
        first["revision_id"],
        current["knowledge_id"],
        current["revision_id"],
        mismatched["knowledge_id"],
        raw_log["knowledge_id"],
    )


def test_working_checkpoint_survives_cold_v6_context_read(tmp_path: Path) -> None:
    (
        root,
        _grant_id,
        old_revision_id,
        knowledge_id,
        current_revision_id,
        mismatched_knowledge_id,
        raw_log_knowledge_id,
    ) = _checkpoint_vault(tmp_path)
    with knowledge_autonomy.AutonomousKnowledgeStore(root, read_only=True) as store:
        audit_head_before = store.audit_head

    with KnowledgeOS.open(root) as knowledge_os:
        capsule = knowledge_os.context.compile(
            task=f"Resume {_SEMANTIC_KEY} at the saved cursor.",
            query_target={"knowledge_id": knowledge_id},
            purpose="answer",
            task_binding=_TASK_BINDING,
            confirm_no_case_data=True,
        )

    local_statements = capsule["statements"]
    provider_capsule = capsule["provider_capsule"]
    provider_statements = provider_capsule["capsule"]["statements"]
    local_bytes = canonical_json(capsule).encode("utf-8")
    provider_bytes = canonical_json(provider_capsule).encode("utf-8")

    assert capsule["write_performed"] is False
    assert provider_capsule["delivery"]["write_performed"] is False
    assert provider_capsule["delivery"]["hard_limit_bytes"] == 65_536
    assert len(provider_bytes) <= 65_536
    assert len(local_bytes) <= 262_144
    assert not any(gap.get("code") == "no_answer" for gap in capsule["gaps"])
    assert not any(gap.get("code") == "no_answer" for gap in provider_capsule["capsule"]["gaps"])

    assert any(
        item.get("knowledge_id") == knowledge_id
        and item.get("knowledge_revision_id") == current_revision_id
        and item.get("statement_text") == _CURRENT_BODY
        and item.get("object_summary", {}).get("semantic_key") == _SEMANTIC_KEY
        and item.get("origin") == "agent_derived"
        and item.get("authority") == "agent_derived"
        and item.get("legal_authority") is False
        and item.get("source_refs") == []
        for item in local_statements
    )
    assert any(
        item.get("knowledge_id") == knowledge_id
        and item.get("knowledge_revision_id") == current_revision_id
        and item.get("statement_text") == _CURRENT_BODY
        and item.get("object_summary", {}).get("semantic_key") == _SEMANTIC_KEY
        and item.get("origin") == "agent_derived"
        and item.get("authority") == "agent_derived"
        and item.get("legal_authority") is False
        and item.get("source_refs") == []
        for item in provider_statements
    )

    serialized_local = canonical_json(local_statements)
    serialized_provider = canonical_json(provider_statements)
    assert old_revision_id not in serialized_local
    assert old_revision_id not in serialized_provider
    assert _OLD_BODY not in serialized_local
    assert _OLD_BODY not in serialized_provider
    assert _DISTRACTOR_BODY not in serialized_local
    assert _DISTRACTOR_BODY not in serialized_provider

    with KnowledgeOS.open(root) as knowledge_os:
        mismatched = knowledge_os.context.compile(
            task="Resume the mismatched checkpoint.",
            query_target={"knowledge_id": mismatched_knowledge_id},
            purpose="answer",
            task_binding=_TASK_BINDING,
            confirm_no_case_data=True,
        )
    assert mismatched["statements"] == []
    assert any(gap.get("code") == "no_answer" for gap in mismatched["gaps"])
    assert _MISMATCHED_BODY not in canonical_json(mismatched["provider_capsule"])
    assert "task_binding_mismatch" not in canonical_json(mismatched["provider_capsule"])
    assert (
        _MISMATCHED_TASK_BINDING["binding_sha256"]
        not in canonical_json(mismatched["provider_capsule"])
    )

    with KnowledgeOS.open(root) as knowledge_os:
        raw_log = knowledge_os.context.compile(
            task="Resume the raw tool log.",
            query_target={"knowledge_id": raw_log_knowledge_id},
            purpose="answer",
            task_binding=_TASK_BINDING,
            confirm_no_case_data=True,
        )
    assert raw_log["statements"] == []
    assert any(gap.get("code") == "no_answer" for gap in raw_log["gaps"])
    assert _RAW_LOG_BODY not in canonical_json(raw_log["provider_capsule"])

    with knowledge_autonomy.AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.audit_head == audit_head_before

    # The MCP projection must expose the same bounded, read-only v6 context.
    mcp = handle_knowledge_support(
        operation="context",
        task=f"Resume {_SEMANTIC_KEY} at the saved cursor.",
        query_target={"knowledge_id": knowledge_id},
        purpose="answer",
        task_binding=_TASK_BINDING,
        confirm_no_case_data=True,
        vault_path=root,
    )
    assert mcp["schema_version"] == "deeplaw.knowledge-support-output/v6"
    mcp_provider = mcp["result"]
    assert mcp_provider["capsule"]["statements"] == provider_statements
    assert mcp_provider["delivery"] == provider_capsule["delivery"]
    assert mcp_provider["receipt"]["receipt_id"] == mcp_provider["capsule"]["receipt_id"]
    with knowledge_autonomy.AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.audit_head == audit_head_before


def test_mcp_checkpoint_conflict_rebuild_and_forget_trajectory(tmp_path: Path) -> None:
    (
        root,
        grant_id,
        old_revision_id,
        knowledge_id,
        current_revision_id,
        _mismatched_knowledge_id,
        _raw_log_knowledge_id,
    ) = _checkpoint_vault(tmp_path)

    stale_request = {
        "operation": "remember",
        "idempotency_key": "checkpoint-stale-cas",
        "confirm_no_case_data": True,
        "title": "Task checkpoint",
        "body": _CURRENT_BODY,
        "kind": "memory",
        "memory_type": "working",
        "knowledge_id": knowledge_id,
        "expected_revision_id": old_revision_id,
        "semantic_key": _SEMANTIC_KEY,
        "expires_at": _EXPIRES_AT,
        "scope": "project",
        "sensitivity": "private",
        "run_id": _RUN_ID,
        "model_id": "deterministic-test-model",
        "tool_id": "pytest-checkpoint-continuity",
        "tags": ["checkpoint", "task-4f3a"],
    }
    with pytest.raises(RuntimeError) as caught:
        handle_knowledge_sink(stale_request, grant_id=grant_id, vault_path=root)
    message = str(caught.value)
    assert message.startswith("checkpoint_head_conflict:")
    assert knowledge_id not in message
    assert old_revision_id not in message
    assert current_revision_id not in message

    with knowledge_autonomy.AutonomousKnowledgeStore(root, read_only=False) as store:
        rebuilt = store.rebuild_checkpoint_route_projection()
    assert rebuilt["rebuildable"] is True

    current = handle_knowledge_support(
        operation="context",
        task=f"Resume {_SEMANTIC_KEY} at the saved cursor.",
        query_target={"knowledge_id": knowledge_id},
        purpose="answer",
        task_binding=_TASK_BINDING,
        confirm_no_case_data=True,
        vault_path=root,
    )
    serialized_current = canonical_json(current["result"])
    assert knowledge_id in serialized_current
    assert current_revision_id in serialized_current
    assert old_revision_id not in serialized_current

    forgotten = handle_knowledge_sink(
        {
            "operation": "forget",
            "idempotency_key": "checkpoint-owner-forget",
            "confirm_no_case_data": True,
            "knowledge_id": knowledge_id,
            "expected_revision_id": current_revision_id,
            "reason": "Owner requested checkpoint withdrawal.",
        },
        grant_id=grant_id,
        vault_path=root,
    )
    assert forgotten["result"]["lifecycle"] == "forgotten"
    with knowledge_autonomy.AutonomousKnowledgeStore(root, read_only=False) as store:
        rebuilt_after_forget = store.rebuild_checkpoint_route_projection()
    assert rebuilt_after_forget["rebuildable"] is True

    after_forget = handle_knowledge_support(
        operation="context",
        task=f"Resume {_SEMANTIC_KEY} at the saved cursor.",
        query_target={"knowledge_id": knowledge_id},
        purpose="answer",
        task_binding=_TASK_BINDING,
        confirm_no_case_data=True,
        vault_path=root,
    )
    serialized_after_forget = canonical_json(after_forget["result"])
    assert knowledge_id not in serialized_after_forget
    assert current_revision_id not in serialized_after_forget
