"""P2-A public-context reproduction for route-candidate reservation.

This is a repository-visible development regression fixture, not qualification
evidence.  The source fixture contributes more than the v6 statement-candidate
bound while the route lookup is separately exact.  The assertion intentionally
requires the public Context capsule to retain the checkpoint after the reproduced
pre-fix behavior truncated it with the ordinary 512-candidate pool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
)
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes
from tests.test_v013_query_graph_p0_reproductions import _commit_scale_vault

_TASK = "statement"


def _binding() -> dict[str, Any]:
    return build_task_context_binding(
        sha256_bytes(b"p2-route-project"),
        sha256_bytes(b"p2-route-task-line"),
        repository_sha256=sha256_bytes(b"p2-route-repository"),
        worktree_sha256=sha256_bytes(b"p2-route-worktree"),
        base_revision="a" * 40,
        dirty_state_sha256=sha256_bytes(b"p2-route-dirty-state"),
    )


def _seed_working_checkpoint(root: Path) -> tuple[dict[str, Any], str]:
    binding = _binding()
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="p2-route-reservation",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]
        run = store.record_run(
            grant_id=grant_id,
            idempotency_key="p2-route-run",
            run_id="p2-route-run",
            task=_TASK,
            host_id="pytest",
            status="succeeded",
            metadata={"task_binding": binding},
            confirm_no_case_data=True,
        )
        checkpoint = store.remember(
            grant_id=grant_id,
            idempotency_key="p2-route-checkpoint",
            title="P2 route reservation checkpoint",
            body=(
                "GOAL: statement\n"
                "CONFIRMED_DECISION: Retain the exact task-line checkpoint.\n"
                "CONSTRAINT: Do not admit another task line.\n"
                "VERIFIED_FACT: Route lookup is exact and bounded.\n"
                "OPEN_GAP: Candidate reservation is not yet proven.\n"
                "NEXT_ACTION: Verify the public Context capsule.\n"
                "ARTIFACT_REF: p2-route-reservation"
            ),
            kind="memory",
            memory_type="working",
            expires_at="2099-01-01T00:00:00Z",
            run_id=run["run_id"],
            semantic_key="checkpoint:p2-route-reservation",
            tags=["checkpoint", "p2-route-reservation"],
            confirm_no_case_data=True,
        )
        lookup = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode("utf-8")),
            task_binding=binding,
            limit=1,
        )
        assert lookup["status"] == "exact"
        assert lookup["revision_ids"] == [checkpoint["revision_id"]]
    return binding, checkpoint["knowledge_id"]


def test_public_context_reserves_exact_route_after_520_statement_pool(
    tmp_path: Path,
) -> None:
    """Freeze the observed failure at the public Python Context seam."""

    root = _commit_scale_vault(tmp_path, 520)
    binding, checkpoint_id = _seed_working_checkpoint(root)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        ordinary_statement_count = store.connection.execute(
            "SELECT COUNT(*) FROM knowledge_statements_v1"
        ).fetchone()[0]
    assert ordinary_statement_count >= 520

    with KnowledgeOS.open(root) as knowledge_os:
        context = knowledge_os.context.compile(
            task=_TASK,
            purpose="answer",
            task_binding=binding,
            limit=8,
            max_chars=8_000,
            max_tokens=6_000,
            confirm_no_case_data=True,
        )

    provider = context["provider_capsule"]["capsule"]
    assert len(canonical_json(provider).encode("utf-8")) <= 65_536
    selected_ids = {
        item.get("knowledge_id")
        for item in provider.get("statements", [])
        if isinstance(item, dict)
    }
    # Pre-fix failure: route lookup was exact, but route working memory was
    # appended to ordinary candidates and removed by the 512-candidate cut.
    assert checkpoint_id in selected_ids
