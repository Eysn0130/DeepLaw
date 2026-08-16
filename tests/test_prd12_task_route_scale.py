"""Development diagnostics for bounded task-route lookup.

These tests exercise the derived route projection only.  The in-memory
10k/100k fixture is deliberately not a canonical full-scale qualification;
it records that the public lookup shape has an index-backed, bounded query.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding, task_route_sha256
from deeplaw.util import sha256_bytes

_ROUTE_INDEX = "knowledge_checkpoint_routes_v1_route"
_TASK_INDEX = "knowledge_checkpoint_routes_v1_task"


def _projection_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE knowledge_checkpoint_routes_v1 (
            route_sha256 TEXT NOT NULL,
            task_sha256 TEXT NOT NULL,
            snapshot_sha256 TEXT NOT NULL,
            knowledge_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            canonical_binding_json TEXT NOT NULL,
            scope TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(route_sha256, task_sha256, knowledge_id)
        ) STRICT;
        CREATE INDEX knowledge_checkpoint_routes_v1_route
            ON knowledge_checkpoint_routes_v1(route_sha256, task_sha256, snapshot_sha256);
        CREATE INDEX knowledge_checkpoint_routes_v1_task
            ON knowledge_checkpoint_routes_v1(task_sha256, route_sha256);
        """
    )
    return connection


@pytest.mark.parametrize("row_count", [10_000, 100_000])
def test_route_projection_diagnostic_uses_indexes_and_a_bounded_limit(row_count: int) -> None:
    """Tail rows remain addressable without an unbounded projection scan."""

    connection = _projection_connection()
    task_sha256 = sha256_bytes(b"scale-task")
    target_route = sha256_bytes(b"target-route")
    rows = [
        (
            sha256_bytes(f"decoy-route-{index}".encode()),
            task_sha256,
            sha256_bytes(f"decoy-snapshot-{index}".encode()),
            f"knowledge_{index:024x}",
            f"knowledgerev_{index:024x}",
            f"run-scale-{index}",
            "{}",
            "project",
            "private",
            "2026-01-01T00:00:00Z",
        )
        for index in range(row_count - 1)
    ]
    # Deliberately import the sought row last: physical/import order must not
    # determine eligibility.
    rows.append(
        (
            target_route,
            task_sha256,
            sha256_bytes(b"target-snapshot"),
            "knowledge_target_00000000000000000000",
            "knowledgerev_target_00000000000000000000",
            "run-scale-target",
            "{}",
            "project",
            "private",
            "2026-01-01T00:00:00Z",
        )
    )
    connection.executemany(
        """
        INSERT INTO knowledge_checkpoint_routes_v1(
            route_sha256, task_sha256, snapshot_sha256, knowledge_id,
            revision_id, run_id, canonical_binding_json, scope, sensitivity,
            recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()

    route_plan = connection.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT revision_id FROM knowledge_checkpoint_routes_v1 "
        "WHERE route_sha256 = ? ORDER BY knowledge_id LIMIT ?",
        (target_route, 20),
    ).fetchall()
    route_details = " ".join(str(row[3]) for row in route_plan)
    assert _ROUTE_INDEX in route_details

    target = connection.execute(
        "SELECT revision_id FROM knowledge_checkpoint_routes_v1 "
        "WHERE route_sha256 = ? ORDER BY knowledge_id LIMIT ?",
        (target_route, 20),
    ).fetchall()
    assert [row["revision_id"] for row in target] == [
        "knowledgerev_target_00000000000000000000"
    ]
    assert len(target) <= 20

    task_plan = connection.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT DISTINCT route_sha256 FROM knowledge_checkpoint_routes_v1 "
        "WHERE task_sha256 = ? ORDER BY route_sha256 LIMIT ?",
        (task_sha256, 20),
    ).fetchall()
    task_details = " ".join(str(row[3]) for row in task_plan)
    assert _TASK_INDEX in task_details
    limited_routes = connection.execute(
        "SELECT DISTINCT route_sha256 FROM knowledge_checkpoint_routes_v1 "
        "WHERE task_sha256 = ? ORDER BY route_sha256 LIMIT ?",
        (task_sha256, 20),
    ).fetchall()
    assert len(limited_routes) == 20
    connection.close()


def _binding(line: str) -> dict[str, Any]:
    return build_task_context_binding(
        sha256_bytes(b"route-scale-project"),
        sha256_bytes(f"route-scale-line:{line}".encode()),
        repository_sha256=sha256_bytes(b"route-scale-repository"),
        worktree_sha256=sha256_bytes(b"route-scale-worktree"),
        base_revision="a" * 40,
        dirty_state_sha256=sha256_bytes(f"dirty:{line}".encode()),
    )


def _new_store(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "route-scale-vault"
    initialize_knowledge_vault(root, name="route-scale", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="route-scale-test",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]
    return root, grant_id


def test_public_store_route_lookup_reports_bounded_scan(tmp_path: Path) -> None:
    """The real public store lookup validates one exact route, not all rows."""

    root, grant_id = _new_store(tmp_path)
    task = "Continue the bounded route task."
    bindings: dict[str, dict[str, Any]] = {}
    revisions: dict[str, str] = {}
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        for index in range(25):
            line = f"line-{index:02d}"
            binding = _binding(line)
            bindings[line] = binding
            run = store.record_run(
                grant_id=grant_id,
                idempotency_key=f"route-scale-run-{line}",
                run_id=f"route-scale-run-{line}",
                task=task,
                host_id="pytest",
                status="succeeded",
                metadata={"task_binding": binding},
                confirm_no_case_data=True,
            )
            checkpoint = store.remember(
                grant_id=grant_id,
                idempotency_key=f"route-scale-checkpoint-{line}",
                title=f"Route checkpoint {line}",
                body=(
                    "GOAL: Continue the bounded route task.\n"
                    "NEXT_ACTION: Verify the selected route."
                ),
                kind="memory",
                memory_type="working",
                expires_at="2099-01-01T00:00:00Z",
                run_id=run["run_id"],
                semantic_key=f"checkpoint:route-scale:{line}",
                tags=["checkpoint", "route-scale"],
                confirm_no_case_data=True,
            )
            revisions[line] = checkpoint["revision_id"]
        result = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(task.encode()),
            task_binding=bindings["line-24"],
            limit=1,
        )
        assert result["status"] == "exact"
        assert result["revision_ids"] == [revisions["line-24"]]
        assert result["scanned"] <= 2
        assert result["limit"] == 1
        assert task_route_sha256(bindings["line-24"]) == result["route_sha256"]
