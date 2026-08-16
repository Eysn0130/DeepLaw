"""Development evidence for legacy checkpoint handling and reconciliation.

The v2 record-run shape remains accepted, but an unbound working checkpoint is
intentionally not admitted by the current v6 route projection.  An owner can
create a new bound Run Record and an attributable successor instead; this test
checks that recovery never rewrites the old Run or Revision.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import sha256_bytes

_TASK = "Continue the legacy deployment task."
_V2_SHA256 = "b3c5c100471cec3a8ecdce115255ae3e4d0d7053800936e5a611fe103527019a"


def _repository() -> Path:
    return Path(__file__).resolve().parents[1]


def _binding(*, base: str, dirty: str) -> dict[str, Any]:
    return build_task_context_binding(
        sha256_bytes(b"legacy-project"),
        sha256_bytes(b"legacy-task-line"),
        repository_sha256=sha256_bytes(b"legacy-repository"),
        worktree_sha256=sha256_bytes(b"legacy-worktree"),
        base_revision=base,
        dirty_state_sha256=sha256_bytes(dirty.encode()),
    )


def _record_run_request(*, key: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    request: dict[str, Any] = {
        "operation": "record_run",
        "idempotency_key": key,
        "confirm_no_case_data": True,
        "run_id": f"legacy-{key}",
        "task": _TASK,
        "host_id": "pytest",
        "status": "succeeded",
    }
    if metadata is not None:
        request["run_metadata"] = metadata
    return request


def _checkpoint_body(snapshot: str) -> str:
    return "\n".join(
        (
            "GOAL: Continue the legacy deployment task.",
            f"CONFIRMED_DECISION: Continue from {snapshot}.",
            "CONSTRAINT: Keep the task line isolated.",
            f"VERIFIED_FACT: {snapshot} is the recorded checkpoint snapshot.",
            "OPEN_GAP: Owner must verify the current workspace snapshot.",
            "NEXT_ACTION: Continue only after exact route admission.",
            f"ARTIFACT_REF: legacy-{snapshot}",
        )
    )


def _new_store(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "legacy-reconciliation-vault"
    initialize_knowledge_vault(root, name="legacy-reconciliation", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="legacy-reconciliation-test",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]
    return root, grant_id


def test_v2_unbound_run_is_accepted_but_legacy_working_checkpoint_is_withheld(
    tmp_path: Path,
) -> None:
    """Freeze the compatibility boundary instead of inventing an unsafe rebind."""

    path = _repository() / "contracts/knowledge-sink.input.v2.schema.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == _V2_SHA256
    schema = json.loads(path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    legacy_request = _record_run_request(key="v2-unbound")
    validator.validate(legacy_request)

    root, grant_id = _new_store(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        legacy_run = store.record_run(
            grant_id=grant_id,
            idempotency_key="v2-unbound",
            run_id="legacy-v2-unbound",
            task=_TASK,
            host_id="pytest",
            status="succeeded",
            confirm_no_case_data=True,
        )
        assert legacy_run["run_id"] == "legacy-v2-unbound"
        # The v2 public shape remains readable and can leave a legacy working
        # row behind.  v6 deliberately refuses to project that row because it
        # has no routing identity.
        legacy_checkpoint = store.remember(
            grant_id=grant_id,
            idempotency_key="legacy-working-checkpoint",
            title="Legacy working checkpoint",
            body=(
                "GOAL: Continue the legacy deployment task.\n"
                "CONFIRMED_DECISION: Keep the legacy decision.\n"
                "CONSTRAINT: Do not cross task lines.\n"
                "VERIFIED_FACT: This is an unbound legacy row.\n"
                "OPEN_GAP: Owner rebind is required.\n"
                "NEXT_ACTION: Create an attributable successor.\n"
                "ARTIFACT_REF: legacy-v2-artifact"
            ),
            kind="memory",
            memory_type="working",
            expires_at="2099-01-01T00:00:00Z",
            run_id=legacy_run["run_id"],
            confirm_no_case_data=True,
        )
        assert legacy_checkpoint["revision_id"]
        withheld = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode()),
        )
        assert withheld["status"] == "not_found"

    with KnowledgeOS.open(root) as knowledge_os:
        capsule = knowledge_os.context.compile(
            task=_TASK,
            purpose="answer",
            confirm_no_case_data=True,
        )
    provider = capsule["provider_capsule"]["capsule"]
    assert all(
        item.get("knowledge_id") != legacy_checkpoint["knowledge_id"]
        for item in provider["statements"]
    )
    assert "task_binding_required" in {
        gap.get("code") for gap in provider["gaps"] if isinstance(gap, dict)
    }


def test_owner_bound_successor_reconciles_without_rewriting_legacy_history(
    tmp_path: Path,
) -> None:
    """A fresh bound Run + successor restores exact routing and immutable history."""

    root, grant_id = _new_store(tmp_path)
    binding_s2 = _binding(base="b" * 40, dirty="dirty-s2")
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        run_one = store.record_run(
            grant_id=grant_id,
            idempotency_key="unbound-run-one",
            run_id="legacy-unbound-run-one",
            task=_TASK,
            host_id="pytest",
            status="succeeded",
            confirm_no_case_data=True,
        )
        first = store.remember(
            grant_id=grant_id,
            idempotency_key="unbound-checkpoint-one",
            title="Legacy replacement checkpoint",
            body=_checkpoint_body("S1"),
            kind="memory",
            memory_type="working",
            expires_at="2099-01-01T00:00:00Z",
            run_id=run_one["run_id"],
            semantic_key="checkpoint:legacy-reconciliation",
            tags=["checkpoint", "legacy-reconciliation"],
            confirm_no_case_data=True,
        )
        knowledge_id = first["knowledge_id"]
        old_revision_id = first["revision_id"]
        old_run_row = dict(
            store.connection.execute(
                "SELECT * FROM knowledge_run_records_v4 WHERE run_id = ?",
                (run_one["run_id"],),
            ).fetchone()
        )
        old_revision_row = dict(
            store.connection.execute(
                "SELECT * FROM knowledge_revisions_v3 WHERE revision_id = ?",
                (old_revision_id,),
            ).fetchone()
        )

        run_two = store.record_run(
            grant_id=grant_id,
            idempotency_key="bound-run-two",
            run_id="legacy-bound-run-two",
            task=_TASK,
            host_id="pytest",
            status="succeeded",
            metadata={"task_binding": binding_s2},
            confirm_no_case_data=True,
        )
        successor = store.remember(
            grant_id=grant_id,
            idempotency_key="bound-checkpoint-two",
            title="Legacy replacement checkpoint",
            body=_checkpoint_body("S2"),
            kind="memory",
            memory_type="working",
            expires_at="2099-01-01T00:00:00Z",
            knowledge_id=knowledge_id,
            expected_revision_id=old_revision_id,
            run_id=run_two["run_id"],
            semantic_key="checkpoint:legacy-reconciliation",
            tags=["checkpoint", "legacy-reconciliation"],
            confirm_no_case_data=True,
        )
        assert successor["revision_id"] != old_revision_id

        exact = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode()),
            task_binding=binding_s2,
        )
        assert exact["status"] == "exact"
        assert exact["revision_ids"] == [successor["revision_id"]]
        stale = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_TASK.encode()),
            task_binding=_binding(base="a" * 40, dirty="dirty-s1"),
        )
        assert stale["status"] == "workspace_diverged"

        current_route = store.connection.execute(
            "SELECT revision_id, run_id FROM knowledge_checkpoint_routes_v1 "
            "WHERE knowledge_id = ?",
            (knowledge_id,),
        ).fetchone()
        assert dict(current_route) == {
            "revision_id": successor["revision_id"],
            "run_id": run_two["run_id"],
        }
        assert dict(
            store.connection.execute(
                "SELECT * FROM knowledge_run_records_v4 WHERE run_id = ?",
                (run_one["run_id"],),
            ).fetchone()
        ) == old_run_row
        assert dict(
            store.connection.execute(
                "SELECT * FROM knowledge_revisions_v3 WHERE revision_id = ?",
                (old_revision_id,),
            ).fetchone()
        ) == old_revision_row
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM knowledge_revisions_v3 WHERE knowledge_id = ?",
                (knowledge_id,),
            ).fetchone()[0]
            == 2
        )

    with KnowledgeOS.open(root) as knowledge_os:
        recovered = knowledge_os.context.compile(
            task=_TASK,
            purpose="answer",
            task_binding=binding_s2,
            confirm_no_case_data=True,
        )
    assert any(
        item.get("knowledge_id") == knowledge_id
        and item.get("knowledge_revision_id") == successor["revision_id"]
        for item in recovered["provider_capsule"]["capsule"]["statements"]
    )
