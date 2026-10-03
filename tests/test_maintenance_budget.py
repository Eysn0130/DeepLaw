from __future__ import annotations

from pathlib import Path

import pytest

from deeplaw.compilation import freshness
from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.compilation.freshness import MaintenanceBudgetExceeded
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore

from .test_complete_support_sets import (
    _commit_publication,
    _committed_statement_identity,
    _v4_fixture,
)
from .test_support_maintenance_boundaries import _commit_fixture


def _canonical_snapshot(root: Path) -> dict[str, object]:
    tables = (
        "knowledge_dependencies_v1",
        "revision_dependencies_v1",
        "source_freshness_events_v1",
        "source_compilation_artifacts_v1",
        "synthesis_refresh_tasks_v1",
        "derived_rebuild_queue_v3",
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        return {
            "audit_head": store.audit_head,
            "legacy_audit_head": store.legacy_audit_head,
            "tables": {
                table: [
                    tuple(row)
                    for row in store.connection.execute(
                        f"SELECT * FROM {table} ORDER BY rowid"
                    ).fetchall()
                ]
                for table in tables
            },
        }


def _assert_failed_closed(root: Path, before: dict[str, object]) -> None:
    assert _canonical_snapshot(root) == before
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        verification = store.verify()
    assert verification["valid"] is True, verification["failures"]


def _assert_budget_error(
    error: pytest.ExceptionInfo[MaintenanceBudgetExceeded], name: str, limit: int
) -> None:
    assert error.value.code == "maintenance_budget_exceeded"
    assert error.value.review_required is True
    assert error.value.budget_name == name
    assert error.value.limit == limit


def test_fragment_inventory_budget_fails_before_canonical_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, grant_id, source_revision_id = _commit_fixture(tmp_path)
    before = _canonical_snapshot(root)
    monkeypatch.setattr(freshness, "MAX_FRAGMENT_INVENTORY", 1)

    with pytest.raises(MaintenanceBudgetExceeded) as error:
        CompilationCoordinator(root).refresh(
            grant_id=grant_id,
            source_revision_id=source_revision_id,
            confirm_no_case_data=True,
        )

    _assert_budget_error(error, "fragment_inventory", 1)
    _assert_failed_closed(root, before)


def test_direct_dependency_budget_fails_before_canonical_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, grant_id, source_revision_id = _commit_fixture(tmp_path)
    before = _canonical_snapshot(root)
    monkeypatch.setattr(freshness, "MAX_DEPENDENCY_EDGES", 1)

    with pytest.raises(MaintenanceBudgetExceeded) as error:
        CompilationCoordinator(root).refresh(
            grant_id=grant_id,
            source_revision_id=source_revision_id,
            confirm_no_case_data=True,
        )

    _assert_budget_error(error, "dependency_edges", 1)
    _assert_failed_closed(root, before)


def test_transitive_consumer_budget_fails_before_canonical_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, grant_id, parent_run_id, parent_plan, _statement, _support_sets = _v4_fixture(
        tmp_path,
        source_name="budget-parent.md",
        semantic_key="claim:budget-parent",
    )
    _commit_publication(root, grant_id, parent_run_id, parent_plan)
    _parent_statement_id, parent_revision_id = _committed_statement_identity(root)
    dependent = _v4_fixture(
        tmp_path,
        root=root,
        grant_id=grant_id,
        source_name="budget-dependent.md",
        semantic_key="claim:budget-dependent",
        support_mode="and",
        knowledge_revision_refs=[parent_revision_id],
    )
    _commit_publication(root, grant_id, dependent[2], dependent[3])
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        source_revision_id = store.connection.execute(
            """
            SELECT source_revision_id
            FROM knowledge_dependencies_v1
            WHERE consumer_revision_id = ? AND dependency_kind = 'direct'
            ORDER BY fragment_id
            LIMIT 1
            """,
            (parent_revision_id,),
        ).fetchone()[0]
        reverse_edges = store.connection.execute(
            """
            SELECT count(*)
            FROM revision_dependencies_v1
            WHERE input_kind = 'knowledge_revision' AND input_id = ?
            """,
            (parent_revision_id,),
        ).fetchone()[0]
    assert reverse_edges > 0
    before = _canonical_snapshot(root)
    monkeypatch.setattr(freshness, "MAX_TRANSITIVE_CONSUMER_REVISIONS", 0)

    with pytest.raises(MaintenanceBudgetExceeded) as error:
        CompilationCoordinator(root).refresh(
            grant_id=grant_id,
            source_revision_id=source_revision_id,
            confirm_no_case_data=True,
        )

    _assert_budget_error(error, "transitive_consumer_revisions", 0)
    _assert_failed_closed(root, before)
