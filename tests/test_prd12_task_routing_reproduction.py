"""Deterministic repository-visible development reproductions for P0 routing.

These tests freeze the initially failing public behaviour and now assert the
minimum remediation: binding-aware routing, explicit workspace divergence, and
an ambiguity-safe cold task-text-only path.  They are not Human Gold, holdout,
host, or release qualification.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes

_TASK = "Continue the shared deployment task from the selected worktree."
_PROJECT_SHA256 = sha256_bytes(b"p0-routing-project")
_REPOSITORY_SHA256 = sha256_bytes(b"p0-routing-repository")
_WORKTREE_SHA256 = sha256_bytes(b"p0-routing-worktree")
_BASE_REVISION = "a" * 40
_DIRTY_STATE_SHA256 = "b" * 64
_EXPIRES_AT = "2099-01-01T00:00:00Z"
_MODEL_ID = "p0-routing-development-model"
_TOOL_ID = "p0-routing-development-tool"


def _binding(
    line: str,
    *,
    base: str = _BASE_REVISION,
    dirty: str = _DIRTY_STATE_SHA256,
) -> dict[str, Any]:
    return build_task_context_binding(
        _PROJECT_SHA256,
        sha256_bytes(f"p0-routing-line:{line}".encode()),
        repository_sha256=_REPOSITORY_SHA256,
        worktree_sha256=_WORKTREE_SHA256,
        base_revision=base,
        dirty_state_sha256=dirty,
    )


def _checkpoint_body(line: str) -> str:
    return "\n".join(
        (
            f"GOAL: {_TASK}",
            "CONFIRMED_DECISION: Continue the selected task line.",
            "CONSTRAINT: Keep task-line state isolated.",
            "VERIFIED_FACT: This is a bounded development checkpoint.",
            "OPEN_GAP: Current workspace identity must be checked.",
            "NEXT_ACTION: Continue only the exact admitted line.",
            f"ARTIFACT_REF: route-marker-{line}",
        )
    )


def _sink(root: Path, grant_id: str, request: dict[str, Any]) -> dict[str, Any]:
    result = handle_knowledge_sink(request, grant_id=grant_id, vault_path=root)
    assert result.get("result") is not None, result
    return result["result"]


def _new_vault(tmp_path: Path, *, name: str) -> tuple[Path, str]:
    root = tmp_path / name
    initialize_knowledge_vault(root, name=name, scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id=name,
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]
    return root, grant_id


def _seed_line(
    root: Path,
    grant_id: str,
    *,
    line: str,
    task_binding: dict[str, Any],
) -> str:
    run_id = f"run-p0-{line}"
    _sink(
        root,
        grant_id,
        {
            "operation": "record_run",
            "idempotency_key": f"p0-run-{line}",
            "confirm_no_case_data": True,
            "run_id": run_id,
            "task": _TASK,
            "host_id": f"p0-host-{line}",
            "model_id": _MODEL_ID,
            "status": "succeeded",
            "scope": "project",
            "sensitivity": "private",
            "run_metadata": {
                "task_kind": "deployment",
                "artifact_ids": [f"route-marker-{line}"],
                "task_binding": task_binding,
            },
        },
    )
    result = _sink(
        root,
        grant_id,
        {
            "operation": "remember",
            "idempotency_key": f"p0-checkpoint-{line}",
            "confirm_no_case_data": True,
            "title": "Shared deployment checkpoint",
            "body": _checkpoint_body(line),
            "kind": "memory",
            "memory_type": "working",
            "semantic_key": f"checkpoint:p0-routing:{line}",
            "expires_at": _EXPIRES_AT,
            "scope": "project",
            "sensitivity": "private",
            "run_id": run_id,
            "model_id": _MODEL_ID,
            "tool_id": _TOOL_ID,
            "tags": ["checkpoint", "p0-routing"],
        },
    )
    return str(result["knowledge_id"])


def _provider(local_capsule: dict[str, Any]) -> tuple[dict[str, Any], str]:
    provider_capsule = local_capsule["provider_capsule"]
    return provider_capsule, canonical_json(provider_capsule)


def _selected_ids(provider_capsule: dict[str, Any], known_ids: set[str]) -> set[str]:
    if "provider_capsule" in provider_capsule:
        provider_capsule = provider_capsule["provider_capsule"]
    return {
        item.get("knowledge_id")
        for item in provider_capsule["capsule"].get("statements", [])
        if isinstance(item, dict) and item.get("knowledge_id") in known_ids
    }


def _run_cli_context(root: Path) -> dict[str, Any]:
    repository = Path(__file__).resolve().parents[1]
    env = {
        "HOME": str(root.parent / "cli-home"),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
        "PYTHONPATH": str(repository / "src"),
    }
    completed = subprocess.run(
        [
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
        ],
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    if isinstance(result, dict) and "provider_capsule" in result:
        return result["provider_capsule"]
    if isinstance(result, dict) and isinstance(result.get("result"), dict):
        return result["result"]
    return result


def test_p0a_top20_discovery_must_route_before_task_binding_filter(tmp_path: Path) -> None:
    """Each of 25 exact routes must bypass the ordinary Top-20 ranking cut."""

    root, grant_id = _new_vault(tmp_path, name="p0a-top20")
    checkpoint_ids = {
        line: _seed_line(root, grant_id, line=line, task_binding=_binding(line))
        for line in (f"line-{index:02d}" for index in range(25))
    }
    all_ids = set(checkpoint_ids.values())
    failures: list[str] = []

    for line, knowledge_id in checkpoint_ids.items():
        with KnowledgeOS.open(root) as knowledge_os:
            capsule = knowledge_os.context.compile(
                task=_TASK,
                purpose="answer",
                task_binding=_binding(line),
                confirm_no_case_data=True,
            )
        provider_capsule, _provider_json = _provider(capsule)
        selected = _selected_ids(provider_capsule, all_ids)
        if selected != {knowledge_id}:
            failures.append(line)

    # A global Top-20 candidate cut must never precede exact task-line routing.
    assert not failures, f"P0-A reproduced Top-20 post-filter miss: {failures}"


def test_p0b_workspace_divergence_requires_provider_safe_gap(tmp_path: Path) -> None:
    """A base/dirty-only divergence must not degrade into ordinary no_answer."""

    root, grant_id = _new_vault(tmp_path, name="p0b-workspace-divergence")
    s1_binding = _binding("same-line", base="a" * 40, dirty="b" * 64)
    s2_binding = _binding("same-line", base="c" * 40, dirty="d" * 64)
    s1_id = _seed_line(root, grant_id, line="s1", task_binding=s1_binding)

    with KnowledgeOS.open(root) as knowledge_os:
        capsule = knowledge_os.context.compile(
            task=_TASK,
            purpose="answer",
            task_binding=s2_binding,
            confirm_no_case_data=True,
        )
    provider_capsule, provider_json = _provider(capsule)
    payload = provider_capsule["capsule"]
    selected = _selected_ids(provider_capsule, {s1_id})
    gap_codes = {
        gap.get("code")
        for gap in payload.get("gaps", [])
        if isinstance(gap, dict) and isinstance(gap.get("code"), str)
    }

    assert not selected, "S1 checkpoint was injected into the S2 workspace request"
    assert gap_codes.intersection({"workspace_diverged", "stale_checkpoint"})
    assert gap_codes != {"no_answer"}
    assert s1_binding["binding_sha256"] not in provider_json
    assert s2_binding["binding_sha256"] not in provider_json
    assert str(root) not in provider_json


def test_p0c_python_cold_thread_task_text_only_recovery(tmp_path: Path) -> None:
    """A unique admitted line must be recoverable without binding/ID."""

    root, grant_id = _new_vault(tmp_path, name="p0c-python-cold-thread")
    binding = _binding("unique-line")
    checkpoint_id = _seed_line(root, grant_id, line="unique", task_binding=binding)

    with KnowledgeOS.open(root) as knowledge_os:
        capsule = knowledge_os.context.compile(
            task=_TASK,
            purpose="answer",
            confirm_no_case_data=True,
        )
    provider_capsule, provider_json = _provider(capsule)
    assert _selected_ids(provider_capsule, {checkpoint_id}) == {checkpoint_id}
    assert binding["binding_sha256"] not in provider_json
    assert str(root) not in provider_json


def test_p0c_cli_cold_thread_task_text_only_recovery(tmp_path: Path) -> None:
    """The CLI Context seam must recover a unique line without a hidden ID."""

    root, grant_id = _new_vault(tmp_path, name="p0c-cli-cold-thread")
    checkpoint_id = _seed_line(root, grant_id, line="unique", task_binding=_binding("unique-line"))
    result = _run_cli_context(root)
    provider_capsule = result.get("result", result)
    assert _selected_ids(provider_capsule, {checkpoint_id}) == {checkpoint_id}


def test_p0c_mcp_cold_thread_task_text_only_recovery(tmp_path: Path) -> None:
    """The MCP Context seam must recover a unique line without a hidden ID."""

    root, grant_id = _new_vault(tmp_path, name="p0c-mcp-cold-thread")
    checkpoint_id = _seed_line(root, grant_id, line="unique", task_binding=_binding("unique-line"))
    result = handle_knowledge_support(
        operation="context",
        task=_TASK,
        purpose="answer",
        confirm_no_case_data=True,
        vault_path=root,
    )
    provider_capsule = result["result"]
    assert _selected_ids(provider_capsule, {checkpoint_id}) == {checkpoint_id}


def test_p0c_task_text_only_two_lines_is_ambiguous_and_non_disclosing(
    tmp_path: Path,
) -> None:
    """Two admitted lines must fail closed instead of selecting newest state."""

    root, grant_id = _new_vault(tmp_path, name="p0c-ambiguous-lines")
    first_id = _seed_line(root, grant_id, line="first", task_binding=_binding("first"))
    second_id = _seed_line(root, grant_id, line="second", task_binding=_binding("second"))

    with KnowledgeOS.open(root) as knowledge_os:
        capsule = knowledge_os.context.compile(
            task=_TASK,
            purpose="answer",
            confirm_no_case_data=True,
        )
    provider_capsule, provider_json = _provider(capsule)
    payload = provider_capsule["capsule"]
    gap_codes = {
        gap.get("code")
        for gap in payload.get("gaps", [])
        if isinstance(gap, dict) and isinstance(gap.get("code"), str)
    }
    assert "task_line_ambiguous" in gap_codes
    assert _selected_ids(provider_capsule, {first_id, second_id}) == set()
    assert _checkpoint_body("first") not in provider_json
    assert _checkpoint_body("second") not in provider_json
