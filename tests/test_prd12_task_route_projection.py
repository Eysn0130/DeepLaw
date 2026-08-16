"""Focused P0 task-route/checkpoint projection candidate tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import (
    build_task_context_binding,
    task_route_identity,
    task_route_sha256,
    task_snapshot_identity,
    task_snapshot_sha256,
)
from deeplaw.util import sha256_bytes


def _binding(
    line: str = "line",
    *,
    parent: str | None = None,
    base: str = "a" * 40,
    dirty: str = "b" * 64,
) -> dict[str, object]:
    return build_task_context_binding(
        sha256_bytes(b"project"),
        sha256_bytes(line.encode("utf-8")),
        parent_task_lineage_sha256=(sha256_bytes(parent.encode("utf-8")) if parent else None),
        repository_sha256=sha256_bytes(b"repository"),
        worktree_sha256=sha256_bytes(b"worktree"),
        base_revision=base,
        dirty_state_sha256=dirty,
    )


def _new_vault(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="route-projection", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="route-projection-test",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]
    return root, grant_id


def _seed(
    store: AutonomousKnowledgeStore,
    grant_id: str,
    *,
    index: str,
    binding: dict[str, object] | None,
    status: str = "succeeded",
) -> tuple[str, str]:
    metadata: dict[str, object] = {"task_kind": "route-test"}
    if binding is not None:
        metadata["task_binding"] = binding
    run = store.record_run(
        grant_id=grant_id,
        idempotency_key=f"run-{index}",
        run_id=f"run-route-{index}",
        task="Continue the routed task.",
        host_id="pytest",
        status=status,
        metadata=metadata,
        confirm_no_case_data=True,
    )
    if status != "succeeded":
        return run["run_id"], ""
    checkpoint = store.remember(
        grant_id=grant_id,
        idempotency_key=f"checkpoint-{index}",
        title=f"Routed checkpoint {index}",
        body="\n".join(
            (
                "GOAL: Continue the routed task.",
                "CONFIRMED_DECISION: Keep the selected route.",
                "CONSTRAINT: Do not cross task lines.",
                "VERIFIED_FACT: Route projection is bounded.",
                "OPEN_GAP: Workspace may diverge.",
                "NEXT_ACTION: Validate the exact route.",
                f"ARTIFACT_REF: route-{index}",
            )
        ),
        kind="memory",
        memory_type="working",
        expires_at="2099-01-01T00:00:00Z",
        run_id=run["run_id"],
        semantic_key=f"checkpoint:route:{index}",
        tags=["checkpoint"],
        confirm_no_case_data=True,
    )
    return run["run_id"], checkpoint["revision_id"]


def test_route_and_snapshot_hashes_separate_lineage_from_workspace_snapshot() -> None:
    first = _binding("line", parent="parent", base="a" * 40, dirty="b" * 64)
    base_changed = _binding("line", parent="parent", base="c" * 40, dirty="d" * 64)
    parent_changed = _binding("line", parent="other-parent", base="a" * 40, dirty="b" * 64)
    line_changed = _binding("other-line", parent="parent", base="a" * 40, dirty="b" * 64)

    assert task_route_identity(first) == task_route_identity(base_changed)
    assert task_route_sha256(first) == task_route_sha256(base_changed)
    assert task_route_sha256(first) == task_route_sha256(parent_changed)
    assert task_route_sha256(first) != task_route_sha256(line_changed)
    assert task_snapshot_identity(first) != task_snapshot_identity(base_changed)
    assert task_snapshot_sha256(first) != task_snapshot_sha256(base_changed)


def test_route_projection_is_bounded_exact_and_workspace_divergence_safe(tmp_path: Path) -> None:
    root, grant_id = _new_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        revision_ids: dict[str, str] = {}
        bindings: dict[str, dict[str, object]] = {}
        for index in range(25):
            line = f"line-{index:02d}"
            bindings[line] = _binding(line)
            _run_id, revision_ids[line] = _seed(
                store,
                grant_id,
                index=line,
                binding=bindings[line],
            )
        exact = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(b"Continue the routed task."),
            task_binding=bindings["line-24"],
            limit=1,
        )
        assert exact["status"] == "exact"
        assert exact["revision_ids"] == [revision_ids["line-24"]]
        assert exact["scanned"] <= 2

        continued = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(b"Continue with different cold-thread wording."),
            task_binding=bindings["line-24"],
            limit=1,
        )
        assert continued["status"] == "exact"
        assert continued["revision_ids"] == [revision_ids["line-24"]]

        changed = _binding("line-24", base="c" * 40, dirty="d" * 64)
        diverged = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(b"Continue the routed task."),
            task_binding=changed,
            limit=1,
        )
        assert diverged["status"] == "workspace_diverged"
        assert "revision_ids" not in diverged


def test_unbound_legacy_write_is_retained_but_not_projected(tmp_path: Path) -> None:
    root, grant_id = _new_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _run_id, revision_id = _seed(store, grant_id, index="legacy", binding=None)
        assert revision_id
        result = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(b"Continue the routed task."),
            limit=1,
        )
        assert result["status"] == "not_found"
        assert store.connection.execute(
            "SELECT COUNT(*) FROM knowledge_checkpoint_routes_v1"
        ).fetchone()[0] == 0


def test_non_working_successor_retires_current_route_row(tmp_path: Path) -> None:
    root, grant_id = _new_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        binding = _binding("successor")
        run_id, revision_id = _seed(
            store,
            grant_id,
            index="successor",
            binding=binding,
        )
        knowledge_id = store.connection.execute(
            "SELECT knowledge_id FROM knowledge_revisions_v3 WHERE revision_id = ? LIMIT 1",
            (revision_id,),
        ).fetchone()["knowledge_id"]
        successor = store.remember(
            grant_id=grant_id,
            idempotency_key="semantic-successor",
            title="Semantic successor",
            body="This is no longer a current working checkpoint.",
            kind="memory",
            memory_type="semantic",
            knowledge_id=knowledge_id,
            expected_revision_id=revision_id,
            run_id=run_id,
            confirm_no_case_data=True,
        )
        assert successor["revision_id"] != revision_id
        assert store.connection.execute(
            "SELECT COUNT(*) FROM knowledge_checkpoint_routes_v1 WHERE knowledge_id = ?",
            (knowledge_id,),
        ).fetchone()[0] == 0


def test_no_binding_unique_then_ambiguous_never_selects_newest(tmp_path: Path) -> None:
    root, grant_id = _new_vault(tmp_path)
    task_sha256 = sha256_bytes(b"Continue the routed task.")
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        first_binding = _binding("unique")
        _run_id, first_revision = _seed(
            store,
            grant_id,
            index="unique",
            binding=first_binding,
        )
        unique = store.lookup_checkpoint_route_projection(
            task_sha256=task_sha256,
        )
        assert unique["status"] == "exact"
        assert unique["revision_ids"] == [first_revision]
        assert unique["canonical_binding"] == first_binding

        _seed(store, grant_id, index="ambiguous", binding=_binding("ambiguous"))
        ambiguous = store.lookup_checkpoint_route_projection(task_sha256=task_sha256)
        assert ambiguous["status"] == "ambiguous"
        assert ambiguous["truncated"] is False
        assert "revision_ids" not in ambiguous


@pytest.mark.parametrize("status", ["failed", "partial", "aborted"])
def test_unsuccessful_runs_do_not_form_current_route(tmp_path: Path, status: str) -> None:
    root, grant_id = _new_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        run_id, _ = _seed(store, grant_id, index=status, binding=_binding(status), status=status)
        with pytest.raises(ValueError, match="successful task-bound Run Record"):
            store.remember(
                grant_id=grant_id,
                idempotency_key=f"failed-checkpoint-{status}",
                title="Unsuccessful checkpoint",
                body="No current state.",
                kind="memory",
                memory_type="working",
                expires_at="2099-01-01T00:00:00Z",
                run_id=run_id,
                confirm_no_case_data=True,
            )


def test_projection_rebuild_and_integrity_detect_stale_or_missing(tmp_path: Path) -> None:
    root, grant_id = _new_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        binding = _binding("rebuild")
        _run_id, revision_id = _seed(store, grant_id, index="rebuild", binding=binding)
        before = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(b"Continue the routed task."),
            task_binding=binding,
        )
        store.rebuild_checkpoint_route_projection()
        after = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(b"Continue the routed task."),
            task_binding=binding,
        )
        assert before == after
        assert after["revision_ids"] == [revision_id]
        store.connection.execute(
            "DELETE FROM knowledge_checkpoint_routes_v1 WHERE revision_id = ?",
            (revision_id,),
        )
        store.connection.commit()
        assert store.verify()["valid"] is False

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.rebuild_checkpoint_route_projection()
        store.connection.execute("DROP TABLE knowledge_checkpoint_routes_v1")
        store.connection.commit()
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        result = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(b"Continue the routed task."),
            task_binding=binding,
        )
        assert result["status"] == "index_unavailable"
        verification = store.verify()
        assert verification["valid"] is False
        assert any(
            failure["code"] == "checkpoint_route_projection_unavailable"
            for failure in verification["failures"]
        )
