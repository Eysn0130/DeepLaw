"""PRD regression for task/goal Context route identity.

The Context retrieval query may contain an optional goal, but the exact
Checkpoint route is keyed by the canonical task text.  This development
reproduction exercises the public Python, CLI, and MCP Context seams.  It is
not release, Host, Gold, or evaluation evidence.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.subprocess_environment import _build_subprocess_environment
from deeplaw.task_context import build_task_context_binding, task_route_sha256
from deeplaw.util import sha256_bytes

_TASK = "Continue the exact deployment task."
_GOAL = "Summarize the verified checkpoint."
_PROJECT_SHA256 = sha256_bytes(b"p2b-goal-route-project")
_TASK_LINEAGE_SHA256 = sha256_bytes(b"p2b-goal-route-line")
_REPOSITORY_SHA256 = sha256_bytes(b"p2b-goal-route-repository")
_WORKTREE_SHA256 = sha256_bytes(b"p2b-goal-route-worktree")
_BASE_REVISION = "a" * 40
_DIRTY_STATE_SHA256 = "b" * 64


def _binding() -> dict[str, Any]:
    return build_task_context_binding(
        _PROJECT_SHA256,
        _TASK_LINEAGE_SHA256,
        repository_sha256=_REPOSITORY_SHA256,
        worktree_sha256=_WORKTREE_SHA256,
        base_revision=_BASE_REVISION,
        dirty_state_sha256=_DIRTY_STATE_SHA256,
    )


def _sink(root: Path, grant_id: str, request: dict[str, Any]) -> dict[str, Any]:
    response = handle_knowledge_sink(request, grant_id=grant_id, vault_path=root)
    assert response.get("result") is not None, response
    return response["result"]


def _new_vault(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "p2b-goal-route"
    initialize_knowledge_vault(root, name="p2b-goal-route", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="p2b-goal-route-reproduction",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]
    return root, grant_id


def _seed_checkpoint(root: Path, grant_id: str, binding: dict[str, Any]) -> str:
    run_id = "run-p2b-goal-route"
    _sink(
        root,
        grant_id,
        {
            "operation": "record_run",
            "idempotency_key": "p2b-record-run",
            "confirm_no_case_data": True,
            "run_id": run_id,
            "task": _TASK,
            "host_id": "p2b-development-host",
            "model_id": "p2b-development-model",
            "status": "succeeded",
            "scope": "project",
            "sensitivity": "private",
            "run_metadata": {
                "task_kind": "deployment",
                "artifact_ids": ["p2b-route-marker"],
                "task_binding": binding,
            },
        },
    )
    result = _sink(
        root,
        grant_id,
        {
            "operation": "remember",
            "idempotency_key": "p2b-record-checkpoint",
            "confirm_no_case_data": True,
            "title": "Exact deployment checkpoint",
            "body": "\n".join(
                (
                    f"GOAL: {_TASK}",
                    "CONFIRMED_DECISION: Continue from the selected deployment line.",
                    "CONSTRAINT: Keep the task line isolated.",
                    "VERIFIED_FACT: The route marker is exact.",
                    "OPEN_GAP: None.",
                    "NEXT_ACTION: Resume the selected deployment line.",
                    "ARTIFACT_REF: p2b-route-marker",
                )
            ),
            "kind": "memory",
            "memory_type": "working",
            "semantic_key": "checkpoint:p2b:goal-route",
            "expires_at": "2099-01-01T00:00:00Z",
            "scope": "project",
            "sensitivity": "private",
            "run_id": run_id,
            "model_id": "p2b-development-model",
            "tool_id": "p2b-development-tool",
            "tags": ["checkpoint", "p2b-goal-route"],
        },
    )
    return str(result["knowledge_id"])


def _provider(value: dict[str, Any]) -> dict[str, Any]:
    current = value
    if isinstance(current.get("result"), dict):
        current = current["result"]
    if isinstance(current.get("provider_capsule"), dict):
        current = current["provider_capsule"]
    return current


def _selected_ids(value: dict[str, Any], known_ids: set[str]) -> set[str]:
    provider = _provider(value)
    payload = provider.get("capsule", {})
    return {
        str(item["knowledge_id"])
        for item in payload.get("statements", [])
        if isinstance(item, dict) and item.get("knowledge_id") in known_ids
    }


def _query_sha256(value: dict[str, Any]) -> str | None:
    current = value
    if isinstance(current.get("result"), dict):
        current = current["result"]
    plan = current.get("query_plan", {})
    if not isinstance(plan, dict):
        provider = _provider(value)
        plan = provider.get("capsule", {}).get("query_plan", {})
    return plan.get("query_sha256") if isinstance(plan, dict) else None


def _run_cli_context(root: Path, *, goal: str | None) -> dict[str, Any]:
    repository = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        "-m",
        "deeplaw",
        "knowledge",
        "context",
        "--vault",
        str(root),
        "--task",
        _TASK,
        "--confirm-no-case-data",
    ]
    if goal is not None:
        command.extend(("--goal", goal))
    completed = subprocess.run(
        command,
        cwd=repository,
        env=_build_subprocess_environment(
            overrides={
                "HOME": str(root.parent / "cli-home"),
                "PYTHONPATH": str(repository / "src"),
            }
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert isinstance(result, dict), result
    return result


def _mcp_context(root: Path, *, goal: str | None) -> dict[str, Any]:
    return handle_knowledge_support(
        operation="context",
        task=_TASK,
        goal=goal,
        purpose="answer",
        confirm_no_case_data=True,
        vault_path=root,
    )


def _python_context(root: Path, *, goal: str | None) -> dict[str, Any]:
    with KnowledgeOS.open(root) as knowledge_os:
        return knowledge_os.context.compile(
            task=_TASK,
            goal=goal,
            purpose="answer",
            confirm_no_case_data=True,
        )


def test_prd12_goal_is_retrieval_only_and_not_route_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A goal must enrich retrieval without changing the Checkpoint route key."""

    root, grant_id = _new_vault(tmp_path)
    binding = _binding()
    checkpoint_id = _seed_checkpoint(root, grant_id, binding)
    expected_task_sha256 = sha256_bytes(_TASK.encode("utf-8"))
    expected_goal_query_sha256 = sha256_bytes(f"{_TASK} {_GOAL}".encode())
    expected_route_sha256 = task_route_sha256(binding)

    route_calls: dict[str, list[dict[str, Any]]] = {}
    active_label = ["unlabelled"]
    original_lookup = AutonomousKnowledgeStore.lookup_checkpoint_route_projection

    def recording_lookup(store: AutonomousKnowledgeStore, **kwargs: Any) -> dict[str, Any]:
        result = original_lookup(store, **kwargs)
        route_calls.setdefault(active_label[0], []).append(
            {
                "task_sha256": kwargs["task_sha256"],
                "result": result,
            }
        )
        return result

    monkeypatch.setattr(
        AutonomousKnowledgeStore,
        "lookup_checkpoint_route_projection",
        recording_lookup,
    )

    def capture(label: str, callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        active_label[0] = label
        try:
            return callback()
        finally:
            active_label[0] = "unlabelled"

    python_null = capture(
        "python:null",
        lambda: _python_context(root, goal=None),
    )
    python_goal = capture(
        "python:goal",
        lambda: _python_context(root, goal=_GOAL),
    )

    cli_null = _run_cli_context(root, goal=None)
    cli_goal = _run_cli_context(root, goal=_GOAL)
    mcp_null = capture(
        "mcp:null",
        lambda: _mcp_context(root, goal=None),
    )
    mcp_goal = capture(
        "mcp:goal",
        lambda: _mcp_context(root, goal=_GOAL),
    )

    failures: list[str] = []
    expected_ids = {checkpoint_id}
    for label, value in (
        ("Python goal=null", python_null),
        ("Python goal=non-empty", python_goal),
        ("CLI goal=null", cli_null),
        ("CLI goal=non-empty", cli_goal),
        ("MCP goal=null", mcp_null),
        ("MCP goal=non-empty", mcp_goal),
    ):
        selected = _selected_ids(value, expected_ids)
        if selected != expected_ids:
            failures.append(
                f"{label}: selected Checkpoint ids={sorted(selected)!r}, "
                f"expected={sorted(expected_ids)!r}"
            )

    for label, value in (
        ("Python goal=null", python_null),
        ("Python goal=non-empty", python_goal),
        ("CLI goal=null", cli_null),
        ("CLI goal=non-empty", cli_goal),
    ):
        expected_query_sha256 = (
            expected_task_sha256 if "goal=null" in label else expected_goal_query_sha256
        )
        observed_query_sha256 = _query_sha256(value)
        if observed_query_sha256 != expected_query_sha256:
            failures.append(
                f"{label}: retrieval query SHA={observed_query_sha256!r}, "
                f"expected={expected_query_sha256!r}"
            )

    for label in ("python:null", "python:goal", "mcp:null", "mcp:goal"):
        calls = route_calls.get(label, [])
        if len(calls) != 1:
            failures.append(f"{label}: expected one route lookup, observed {len(calls)}")
            continue
        call = calls[0]
        if call["task_sha256"] != expected_task_sha256:
            failures.append(
                f"{label}: route lookup task_sha256={call['task_sha256']!r}; "
                f"must derive from canonical task only ({expected_task_sha256!r})"
            )
        result = call["result"]
        if result.get("status") != "exact":
            failures.append(
                f"{label}: route lookup status={result.get('status')!r}; "
                "exact canonical Checkpoint route was not recovered"
            )
        if result.get("route_sha256") != expected_route_sha256:
            failures.append(
                f"{label}: task_route_sha256={result.get('route_sha256')!r}; "
                f"expected={expected_route_sha256!r}"
            )

    assert not failures, (
        "PRD-CONT-006/PRD-CTX-011 goal-route identity regression:\n- "
        + "\n- ".join(failures)
        + "\ncall chain: Context seam -> assemble_v6_context -> "
        "PurposeAwareRetrievalService.query -> "
        "lookup_checkpoint_route_projection(task_sha256=sha256(canonical task))"
    )
