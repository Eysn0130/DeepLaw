"""Regression for the reproduced task-lineage defect.

The former characterization proved that two lines were admitted together.
This development regression now requires fail-closed absence and exact-line
selection. It is not qualification or release evidence.
"""

from __future__ import annotations

from pathlib import Path

from deeplaw.api.knowledge_os import KnowledgeOS
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes

_TASK = "Continue the shared deployment task."
_WRITER_ID = "prd12-lineage-reproduction"
_MODEL_ID = "development-model"
_TOOL_ID = "development-tool"


def _working_checkpoint_body(line: str) -> str:
    decision = (
        "Deploy from the main worktree."
        if line == "main"
        else "Deploy from the feature worktree."
    )
    return "\n".join(
        (
            f"GOAL: {_TASK}",
            f"CONFIRMED_DECISION: {decision}",
            "CONSTRAINT: keep the worktree isolated",
            f"VERIFIED_FACT: commit:{line}-development",
            "OPEN_GAP: wrong task-line state must not be admitted",
            "NEXT_ACTION: verify deployment receipt",
            f"ARTIFACT_REF: commit:{line}-development",
        )
    )


def _sink(root: Path, grant_id: str, request: dict[str, object]) -> dict[str, object]:
    result = handle_knowledge_sink(request, grant_id=grant_id, vault_path=root)
    assert result.get("result") is not None, result
    return result["result"]


def test_task_binding_withholds_unbound_context_and_selects_only_the_exact_line(
    tmp_path: Path,
) -> None:
    """A missing binding returns a Gap; an exact binding returns one line."""

    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="prd12-lineage-reproduction", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id=_WRITER_ID,
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]
    checkpoint_ids: dict[str, str] = {}
    task_bindings = {
        line: build_task_context_binding(
            sha256_bytes(b"prd12-shared-project"),
            sha256_bytes(f"prd12-shared-task:{line}".encode()),
        )
        for line in ("main", "feature")
    }

    for line in ("main", "feature"):
        run_id = f"run-{line}-development"
        _sink(
            root,
            grant_id,
            {
                "operation": "record_run",
                "idempotency_key": f"prd12-record-run-{line}",
                "confirm_no_case_data": True,
                "run_id": run_id,
                "task": _TASK,
                "host_id": f"host-{line}-development",
                "model_id": _MODEL_ID,
                "status": "succeeded",
                "scope": "project",
                "sensitivity": "internal",
                "run_metadata": {
                    "task_kind": "deployment",
                    "artifact_ids": [f"commit:{line}-development"],
                    "task_binding": task_bindings[line],
                },
            },
        )
        remember_result = _sink(
            root,
            grant_id,
            {
                "operation": "remember",
                "idempotency_key": f"prd12-record-checkpoint-{line}",
                "confirm_no_case_data": True,
                "title": "Shared deployment checkpoint",
                "body": _working_checkpoint_body(line),
                "kind": "memory",
                "memory_type": "working",
                "semantic_key": "checkpoint:shared-deployment:slot-0",
                "expires_at": "2099-01-01T00:00:00Z",
                "scope": "project",
                "sensitivity": "internal",
                "run_id": run_id,
                "model_id": _MODEL_ID,
                "tool_id": _TOOL_ID,
                "tags": ["checkpoint", "shared-deployment"],
            },
        )
        checkpoint_ids[line] = str(remember_result["knowledge_id"])
    assert checkpoint_ids["main"] != checkpoint_ids["feature"]

    # Public Python Context seam only; absent binding must fail closed for
    # working state instead of treating the newest or most similar line as current.
    with KnowledgeOS.open(root) as knowledge_os:
        absent_capsule = knowledge_os.context.compile(
            task="Continue the shared deployment task from the feature worktree.",
            purpose="answer",
            confirm_no_case_data=True,
        )
        local_capsule = knowledge_os.context.compile(
            task="Continue the shared deployment task from the feature worktree.",
            purpose="answer",
            task_binding=task_bindings["feature"],
            confirm_no_case_data=True,
        )
    absent_payload = absent_capsule["provider_capsule"]["capsule"]
    absent_ids = {
        item.get("knowledge_id")
        for item in absent_payload.get("statements", [])
        if isinstance(item, dict) and item.get("knowledge_id") in checkpoint_ids.values()
    }
    assert absent_ids == set()
    assert {gap.get("code") for gap in absent_payload.get("gaps", [])} >= {
        "task_binding_required"
    }

    provider_capsule = local_capsule["provider_capsule"]
    assert local_capsule["task_binding"] == task_bindings["feature"]
    assert local_capsule["query_plan"]["task_binding"] == task_bindings["feature"]
    payload = provider_capsule["capsule"]
    statements = payload.get("statements", [])
    assert statements
    selected_ids = {
        item.get("knowledge_id")
        for item in statements
        if isinstance(item, dict)
        and item.get("knowledge_id") in checkpoint_ids.values()
    }
    assert selected_ids == {checkpoint_ids["feature"]}

    gaps = payload.get("gaps", [])
    gap_codes = {
        gap.get("code")
        for gap in gaps
        if isinstance(gap, dict) and isinstance(gap.get("code"), str)
    }
    # Exact binding disambiguates the task line. Other task-line candidates
    # remain local rejections and must not disclose their existence through a
    # Provider-visible mismatch Gap.
    assert not gap_codes.intersection(
        {"task_binding_required", "task_binding_unbound", "task_binding_mismatch"}
    )

    provider_bytes = len(
        canonical_json(provider_capsule).encode("utf-8")
    )
    assert provider_bytes <= 64 * 1024
    provider_json = canonical_json(provider_capsule)
    assert all(binding["binding_sha256"] not in provider_json for binding in task_bindings.values())
