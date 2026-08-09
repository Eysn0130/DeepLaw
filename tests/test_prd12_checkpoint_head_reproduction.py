"""P2-C development evidence for governed Task Checkpoint heads.

The first test follows one route from workspace snapshot S1 to S2, rewrites
the same Knowledge identity under CAS, rebuilds the derived route projection,
and verifies that owner withdrawal does not resurrect the old revision. The
last test preserves the pre-fix multi-head reproduction and verifies that the
public route seam fails closed until Owner reconciliation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes

_TASK = "Continue the checkpoint head task."
_PROJECT_SHA256 = sha256_bytes(b"p2-c-project")
_TASK_LINE_SHA256 = sha256_bytes(b"p2-c-task-line")
_REPOSITORY_SHA256 = sha256_bytes(b"p2-c-repository")
_WORKTREE_SHA256 = sha256_bytes(b"p2-c-worktree")
_EXPIRES_AT = "2099-01-01T00:00:00Z"


def _binding(*, base: str, dirty: str) -> dict[str, Any]:
    """Keep route identity fixed while changing only the workspace snapshot."""

    return build_task_context_binding(
        _PROJECT_SHA256,
        _TASK_LINE_SHA256,
        repository_sha256=_REPOSITORY_SHA256,
        worktree_sha256=_WORKTREE_SHA256,
        base_revision=base,
        dirty_state_sha256=dirty,
    )


def _checkpoint_body(marker: str) -> str:
    return "\n".join(
        (
            f"GOAL: {_TASK}",
            f"CONFIRMED_DECISION: Continue from checkpoint {marker}.",
            "CONSTRAINT: Keep one task line and one current head.",
            f"VERIFIED_FACT: The governed checkpoint marker is {marker}.",
            "OPEN_GAP: Verify the current workspace snapshot before resuming.",
            "NEXT_ACTION: Continue only after exact route admission.",
            f"ARTIFACT_REF: p2-c-{marker}",
        )
    )


def _new_vault(tmp_path: Path, *, name: str) -> tuple[Path, str]:
    root = tmp_path / name
    initialize_knowledge_vault(root, name=name, scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id=f"{name}-writer",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]
    return root, grant_id


def _record_run(
    store: AutonomousKnowledgeStore,
    grant_id: str,
    *,
    key: str,
    run_id: str,
    binding: dict[str, Any],
) -> dict[str, Any]:
    return store.record_run(
        grant_id=grant_id,
        idempotency_key=key,
        run_id=run_id,
        task=_TASK,
        host_id="pytest",
        status="succeeded",
        scope="project",
        sensitivity="private",
        metadata={"task_binding": binding},
        confirm_no_case_data=True,
    )


def _remember_checkpoint(
    store: AutonomousKnowledgeStore,
    grant_id: str,
    *,
    key: str,
    marker: str,
    run_id: str,
    semantic_key: str,
    knowledge_id: str | None = None,
    expected_revision_id: str | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "grant_id": grant_id,
        "idempotency_key": key,
        "title": "P2-C checkpoint",
        "body": _checkpoint_body(marker),
        "kind": "memory",
        "memory_type": "working",
        "expires_at": _EXPIRES_AT,
        "scope": "project",
        "sensitivity": "private",
        "run_id": run_id,
        "semantic_key": semantic_key,
        "tags": ["checkpoint", "p2-c"],
        "confirm_no_case_data": True,
    }
    if knowledge_id is not None:
        request["knowledge_id"] = knowledge_id
    if expected_revision_id is not None:
        request["expected_revision_id"] = expected_revision_id
    return store.remember(**request)


def _selected_ids(context: dict[str, Any], known_ids: set[str]) -> set[str]:
    provider = context["provider_capsule"]["capsule"]
    return {
        item.get("knowledge_id")
        for item in provider.get("statements", [])
        if isinstance(item, dict) and item.get("knowledge_id") in known_ids
    }


def test_same_route_rewrite_cas_rebuild_and_forget_withdrawal(tmp_path: Path) -> None:
    """S1→S2 rewrite keeps one current revision and never revives withdrawn state."""

    root, grant_id = _new_vault(tmp_path, name="p2-c-rewrite")
    binding_s1 = _binding(base="a" * 40, dirty=sha256_bytes(b"p2-c-snapshot-s1"))
    binding_s2 = _binding(base="b" * 40, dirty=sha256_bytes(b"p2-c-snapshot-s2"))
    semantic_key = "checkpoint:p2-c:single-head"

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        run_s1 = _record_run(
            store,
            grant_id,
            key="p2-c-run-s1",
            run_id="p2-c-run-s1",
            binding=binding_s1,
        )
        first = _remember_checkpoint(
            store,
            grant_id,
            key="p2-c-checkpoint-s1",
            marker="s1",
            run_id=run_s1["run_id"],
            semantic_key=semantic_key,
        )
        first_route = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode()),
            task_binding=binding_s1,
        )
        assert first_route["status"] == "exact"
        assert first_route["revision_ids"] == [first["revision_id"]]

        run_s2 = _record_run(
            store,
            grant_id,
            key="p2-c-run-s2",
            run_id="p2-c-run-s2",
            binding=binding_s2,
        )
        second = _remember_checkpoint(
            store,
            grant_id,
            key="p2-c-checkpoint-s2",
            marker="s2",
            run_id=run_s2["run_id"],
            semantic_key=semantic_key,
            knowledge_id=first["knowledge_id"],
            expected_revision_id=first["revision_id"],
        )
        assert second["parent_revision_id"] == first["revision_id"]
        assert store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode()),
            task_binding=binding_s1,
        )["status"] == "workspace_diverged"

        rewritten = _remember_checkpoint(
            store,
            grant_id,
            key="p2-c-checkpoint-s2-rewrite",
            marker="s2-rewrite",
            run_id=run_s2["run_id"],
            semantic_key=semantic_key,
            knowledge_id=first["knowledge_id"],
            expected_revision_id=second["revision_id"],
        )
        exact = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode()),
            task_binding=binding_s2,
        )
        assert exact["status"] == "exact"
        assert exact["revision_ids"] == [rewritten["revision_id"]]
        assert exact["knowledge_ids"] == [first["knowledge_id"]]
        with pytest.raises(RuntimeError, match="compare-and-swap conflict"):
            _remember_checkpoint(
                store,
                grant_id,
                key="p2-c-checkpoint-stale",
                marker="stale",
                run_id=run_s2["run_id"],
                semantic_key=semantic_key,
                knowledge_id=first["knowledge_id"],
                expected_revision_id=second["revision_id"],
            )

        rebuild = store.rebuild_checkpoint_route_projection()
        assert rebuild["rebuildable"] is True
        assert rebuild["row_count"] == 1
        rebuilt = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode()),
            task_binding=binding_s2,
        )
        assert rebuilt["status"] == "exact"
        assert rebuilt["revision_ids"] == [rewritten["revision_id"]]

    with KnowledgeOS.open(root) as knowledge_os:
        context = knowledge_os.context.compile(
            task=_TASK,
            purpose="answer",
            task_binding=binding_s2,
            confirm_no_case_data=True,
        )
    assert _selected_ids(context, {first["knowledge_id"]}) == {first["knowledge_id"]}
    provider_statements = context["provider_capsule"]["capsule"]["statements"]
    assert any(
        item.get("knowledge_revision_id") == rewritten["revision_id"]
        for item in provider_statements
        if isinstance(item, dict)
    )

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        withdrawn = store.forget(
            grant_id=grant_id,
            idempotency_key="p2-c-forget-current",
            knowledge_id=first["knowledge_id"],
            expected_revision_id=rewritten["revision_id"],
            reason="Owner withdrawal of the current checkpoint.",
            confirm_no_case_data=True,
        )
        assert withdrawn["lifecycle"] == "forgotten"
        assert store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode()),
            task_binding=binding_s2,
        )["status"] == "not_found"

    with KnowledgeOS.open(root) as knowledge_os:
        after_withdrawal = knowledge_os.context.compile(
            task=_TASK,
            purpose="answer",
            task_binding=binding_s2,
            confirm_no_case_data=True,
        )
    assert _selected_ids(after_withdrawal, {first["knowledge_id"]}) == set()


def test_duplicate_route_write_requires_current_head_cas_and_forget_withdraws_it(
    tmp_path: Path,
) -> None:
    """A second identity is rejected and owner withdrawal removes the sole head."""

    root, grant_id = _new_vault(tmp_path, name="p2-c-forget")
    binding = _binding(base="c" * 40, dirty=sha256_bytes(b"p2-c-forget-snapshot"))
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        run = _record_run(
            store,
            grant_id,
            key="p2-c-forget-run",
            run_id="p2-c-forget-run",
            binding=binding,
        )
        first = _remember_checkpoint(
            store,
            grant_id,
            key="p2-c-forget-first",
            marker="forget-first",
            run_id=run["run_id"],
            semantic_key="checkpoint:p2-c:duplicate:first",
        )
        with pytest.raises(RuntimeError, match="checkpoint_head_conflict"):
            _remember_checkpoint(
                store,
                grant_id,
                key="p2-c-forget-second",
                marker="forget-second",
                run_id=run["run_id"],
                semantic_key="checkpoint:p2-c:duplicate:second",
            )
        before = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode()),
            task_binding=binding,
        )
        assert before["status"] == "exact"
        assert before["knowledge_ids"] == [first["knowledge_id"]]

        forgotten = store.forget(
            grant_id=grant_id,
            idempotency_key="p2-c-forget-first-owner-withdrawal",
            knowledge_id=first["knowledge_id"],
            expected_revision_id=first["revision_id"],
            reason="Owner withdrawal removes the superseded duplicate head.",
            confirm_no_case_data=True,
        )
        assert forgotten["lifecycle"] == "forgotten"
        after = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode()),
            task_binding=binding,
        )
        assert after["status"] == "not_found"

    with KnowledgeOS.open(root) as knowledge_os:
        context = knowledge_os.context.compile(
            task=_TASK,
            purpose="answer",
            task_binding=binding,
            confirm_no_case_data=True,
        )
    assert _selected_ids(context, {first["knowledge_id"]}) == set()


def test_legacy_multi_head_projection_fails_closed_and_can_be_reconciled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-fix multi-head state yields only a Gap until the owner withdraws one head."""

    root, grant_id = _new_vault(tmp_path, name="p2-c-multi-head")
    binding = _binding(base="d" * 40, dirty=sha256_bytes(b"p2-c-multi-head-snapshot"))
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        run = _record_run(
            store,
            grant_id,
            key="p2-c-multi-head-run",
            run_id="p2-c-multi-head-run",
            binding=binding,
        )
        first = _remember_checkpoint(
            store,
            grant_id,
            key="p2-c-multi-head-first",
            marker="multi-first",
            run_id=run["run_id"],
            semantic_key="checkpoint:p2-c:multi:first",
        )
        # Model an already-persisted pre-fix anomaly through the old public
        # mutation path.  New writes are covered above and cannot reach this
        # state; the remaining assertions exercise rebuild/read/recovery.
        monkeypatch.setattr(
            AutonomousKnowledgeStore,
            "_assert_checkpoint_head_write",
            lambda *_args, **_kwargs: None,
        )
        second = _remember_checkpoint(
            store,
            grant_id,
            key="p2-c-multi-head-second",
            marker="multi-second",
            run_id=run["run_id"],
            semantic_key="checkpoint:p2-c:multi:second",
        )
        rebuild = store.rebuild_checkpoint_route_projection()
        assert rebuild["row_count"] == 2
        conflicted = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode()),
            task_binding=binding,
        )
        assert conflicted["status"] == "head_conflict"
        assert "revision_ids" not in conflicted
        assert "knowledge_ids" not in conflicted

    with KnowledgeOS.open(root) as knowledge_os:
        context = knowledge_os.context.compile(
            task=_TASK,
            purpose="answer",
            task_binding=binding,
            confirm_no_case_data=True,
        )
    assert _selected_ids(context, {first["knowledge_id"], second["knowledge_id"]}) == set()
    assert any(
        gap.get("code") == "checkpoint_head_conflict"
        for gap in context["provider_capsule"]["capsule"].get("gaps", [])
    )
    provider_bytes = canonical_json(context["provider_capsule"])
    for private_value in (
        binding["binding_sha256"],
        binding["project_sha256"],
        binding["task_lineage_sha256"],
        binding["repository_sha256"],
        binding["worktree_sha256"],
        binding["dirty_state_sha256"],
    ):
        assert private_value not in provider_bytes

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.forget(
            grant_id=grant_id,
            idempotency_key="p2-c-reconcile-second",
            knowledge_id=second["knowledge_id"],
            expected_revision_id=second["revision_id"],
            reason="Owner reconciliation of a pre-fix duplicate route head.",
            confirm_no_case_data=True,
        )
        reconciled = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode()),
            task_binding=binding,
        )
        assert reconciled["status"] == "exact"
        assert reconciled["knowledge_ids"] == [first["knowledge_id"]]
