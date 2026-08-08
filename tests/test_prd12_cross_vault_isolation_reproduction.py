"""Development proof for the default cross-Vault isolation boundary.

This is a characterization test for the v0.13 source candidate, not a
qualification gate.  ``not_reproduced_default_physical_isolation`` records
that the public read seams remain bound to the explicitly opened Vault.  It
does not cover an explicit cross-Vault projection, task-line/worktree
binding, or a real model host, and it must not be read as a Qualified result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import sha256_bytes

_REPRODUCTION_STATUS = "not_reproduced_default_physical_isolation"
_CHECKPOINT_TITLE = "Shared deployment checkpoint"
_CHECKPOINT_ALIAS = "Shared deployment checkpoint alias"
_CHECKPOINT_KEY = "checkpoint:shared-deployment"
_CHECKPOINT_TASK = "Continue the shared deployment task."


def _checkpoint_body(label: str, run_id: str) -> str:
    decision = (
        "Continue the project-A deployment line."
        if label == "a"
        else "Continue the project-B deployment line."
    )
    return "\n".join(
        (
            f"GOAL: {_CHECKPOINT_TASK}",
            f"CONFIRMED_DECISION: {decision}",
            "CONSTRAINT: Keep each project Vault isolated.",
            f"VERIFIED_FACT: run:{run_id}",
            "OPEN_GAP: Cross-Vault task-line binding is outside this candidate.",
            "NEXT_ACTION: Verify the local context receipt.",
            f"ARTIFACT_REF: checkpoint-{label}",
        )
    )


def _seed_vault(tmp_path: Path, label: str, sensitivity: str) -> dict[str, Any]:
    root = tmp_path / f"vault-{label}"
    initialize_knowledge_vault(root, name="prd-k-cross-vault", scope="project")
    initialize_autonomous_core(root)
    run_id = f"run-cross-vault-{label}"
    task_binding = build_task_context_binding(
        sha256_bytes(f"prd12-cross-vault-project:{label}".encode()),
        sha256_bytes(b"prd12-cross-vault-shared-task-line"),
    )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(
            writer_id=f"prd-k-owner-{label}",
            allowed_scope="project",
            max_sensitivity=sensitivity,  # type: ignore[arg-type]
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )
        grant_id = grant["grant_id"]
        store.record_run(
            grant_id=grant_id,
            idempotency_key=f"prd-k-run-{label}",
            run_id=run_id,
            task=_CHECKPOINT_TASK,
            host_id=f"development-host-{label}",
            model_id="development-model",
            status="succeeded",
            scope="project",
            sensitivity=sensitivity,  # type: ignore[arg-type]
            metadata={
                "task_kind": "cross-vault-isolation",
                "artifact_ids": [f"checkpoint-{label}"],
                "task_binding": task_binding,
            },
            confirm_no_case_data=True,
        )
        revision = store.remember(
            grant_id=grant_id,
            idempotency_key=f"prd-k-checkpoint-{label}",
            title=_CHECKPOINT_TITLE,
            body=_checkpoint_body(label, run_id),
            kind="memory",
            memory_type="working",
            semantic_key=_CHECKPOINT_KEY,
            aliases=[_CHECKPOINT_ALIAS],
            tags=["checkpoint", "cross-vault-isolation"],
            expires_at="2099-01-01T00:00:00Z",
            scope="project",
            sensitivity=sensitivity,  # type: ignore[arg-type]
            run_id=run_id,
            model_id="development-model",
            tool_id="development-tool",
            confirm_no_case_data=True,
        )
        vault_id = store.vault_id
    return {
        "root": root,
        "vault_id": vault_id,
        "knowledge_id": revision["knowledge_id"],
        "run_id": run_id,
        "sensitivity": sensitivity,
        "task_binding": task_binding,
    }


def _statement_knowledge_ids(capsule: dict[str, Any]) -> set[str]:
    return {
        item["knowledge_id"]
        for item in capsule.get("statements", [])
        if isinstance(item, dict) and isinstance(item.get("knowledge_id"), str)
    }


def _identity_candidate(root: Path, sensitivity: str) -> dict[str, Any]:
    # KnowledgeOS has no separate identity facade yet.  Use the public
    # read-only MCP identity_lookup operation rather than a private SQL seam.
    response = handle_knowledge_support(
        operation="identity_lookup",
        query=_CHECKPOINT_ALIAS,
        scope="project",
        max_sensitivity=sensitivity,
        limit=10,
        vault_path=root,
    )
    result = response["result"]
    assert result["status"] == "resolved"
    assert len(result["candidates"]) == 1
    return result["candidates"][0]


def test_default_cross_vault_reads_remain_isolated_and_foreign_target_gaps(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Record the development-only negative reproduction at public seams."""

    assert _REPRODUCTION_STATUS == "not_reproduced_default_physical_isolation"
    vault_a = _seed_vault(tmp_path, "a", "internal")
    vault_b = _seed_vault(tmp_path, "b", "private")
    root_a = vault_a["root"]
    root_b = vault_b["root"]
    assert isinstance(root_a, Path) and isinstance(root_b, Path)

    # Identical semantic identity is local to each physical Vault.
    assert vault_a["vault_id"] != vault_b["vault_id"]
    assert vault_a["knowledge_id"] != vault_b["knowledge_id"]
    assert vault_a["sensitivity"] == "internal"
    assert vault_b["sensitivity"] == "private"

    identity_a = _identity_candidate(root_a, "internal")
    identity_b = _identity_candidate(root_b, "private")
    assert identity_a["knowledge_id"] == vault_a["knowledge_id"]
    assert identity_b["knowledge_id"] == vault_b["knowledge_id"]
    assert identity_a["knowledge_id"] != identity_b["knowledge_id"]

    with KnowledgeOS.open(root_a) as knowledge_os_a:
        capsule_a = knowledge_os_a.context.compile(
            task=_CHECKPOINT_TASK,
            scope="project",
            max_sensitivity="internal",
            task_binding=vault_a["task_binding"],
            confirm_no_case_data=True,
        )
    with KnowledgeOS.open(root_b) as knowledge_os_b:
        capsule_b = knowledge_os_b.context.compile(
            task=_CHECKPOINT_TASK,
            scope="project",
            max_sensitivity="private",
            task_binding=vault_b["task_binding"],
            confirm_no_case_data=True,
        )
    assert _statement_knowledge_ids(capsule_a) == {vault_a["knowledge_id"]}
    assert _statement_knowledge_ids(capsule_b) == {vault_b["knowledge_id"]}
    assert capsule_a["vault_id"] == vault_a["vault_id"]
    assert capsule_b["vault_id"] == vault_b["vault_id"]

    # CWD is not a Vault selector: an explicitly opened A remains A even
    # while the process is located inside B.
    monkeypatch.chdir(root_b)
    with KnowledgeOS.open(root_a) as knowledge_os_a:
        cwd_capsule_a = knowledge_os_a.context.compile(
            task=_CHECKPOINT_TASK,
            scope="project",
            max_sensitivity="internal",
            task_binding=vault_a["task_binding"],
            confirm_no_case_data=True,
        )
        foreign_target_capsule = knowledge_os_a.context.compile(
            task=_CHECKPOINT_TASK,
            scope="project",
            max_sensitivity="internal",
            query_target={"knowledge_id": vault_b["knowledge_id"]},
            task_binding=vault_a["task_binding"],
            confirm_no_case_data=True,
        )
    assert _statement_knowledge_ids(cwd_capsule_a) == {vault_a["knowledge_id"]}
    assert cwd_capsule_a["vault_id"] == vault_a["vault_id"]
    assert foreign_target_capsule["statements"] == []
    assert foreign_target_capsule["gaps"]
