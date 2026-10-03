"""A withdrawn upstream witness cannot survive through a working checkpoint."""
from __future__ import annotations

import anyio

from deeplaw.context_compiler import verify_capsule
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    create_autonomous_snapshot,
    restore_autonomous_snapshot,
)
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.task_context import build_task_context_binding

from .test_complete_support_sets import (
    _commit_publication,
    _committed_statement_details,
    _v4_fixture,
)
from .test_mcp_progressive_read import client


def test_checkpoint_transitive_support_withdrawal_and_benign_restore(tmp_path):
    root, compiler_grant, run, plan, *_ = _v4_fixture(tmp_path, semantic_key="claim:parent")
    _commit_publication(root, compiler_grant, run, plan)
    _, parent_revision, parent_id = _committed_statement_details(root, semantic_key="claim:parent")
    for key, mode in (("dependent", "and"), ("independent", "dependency_or")):
        fixture = _v4_fixture(
            tmp_path, root=root, grant_id=compiler_grant, source_name=f"{key}.md",
            semantic_key=f"claim:{key}", knowledge_revision_refs=[parent_revision],
            support_mode=mode,
        )
        _commit_publication(root, compiler_grant, fixture[2], fixture[3])
    checkpoints, bindings, inputs = {}, {}, {}
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="checkpoint-repair", operations=tuple(sorted(SINK_OPERATIONS)),
        )["grant_id"]
        for key, task_digest in (("dependent", "a" * 64), ("independent", "b" * 64)):
            _, input_revision, input_id = _committed_statement_details(
                root, semantic_key=f"claim:{key}"
            )
            inputs[key] = {"kind": "knowledge", "knowledge_id": input_id,
                           "revision_id": input_revision}
            bindings[key] = build_task_context_binding("c" * 64, task_digest)
            run = store.record_run(
                grant_id=grant, idempotency_key=f"run-{key}", task="Orchid archive continuation",
                host_id="synthetic-checkpoint-host", status="succeeded",
                metadata={"task_binding": bindings[key]},
                confirm_no_case_data=True,
            )
            checkpoint = store.remember(
                grant_id=grant, idempotency_key=f"checkpoint-{key}",
                title=f"Orchid {key} checkpoint",
                body="\n".join([
                    "GOAL: Continue the Orchid archive task.",
                    "CONFIRMED_DECISION: Retain only currently supported archive knowledge.",
                    "CONSTRAINT: Preserve the approved archive labels.",
                    "VERIFIED_FACT: The bound archive evidence requires amber labels.",
                    "OPEN_GAP: Verify support before the next task.",
                    "NEXT_ACTION: Inspect the current archive requirements.",
                    "ARTIFACT_REF: artifact_none",
                ]),
                semantic_key=f"checkpoint:{key}", tags=["checkpoint"],
                memory_type="working", expires_at="2099-01-01T00:00:00Z", run_id=run["run_id"],
                source_refs=[{"revision_id": input_revision}], confirm_no_case_data=True,
            )
            checkpoint = store.get_current(checkpoint["knowledge_id"])
            checkpoints[key] = checkpoint
            assert store.revision_provenance_admitted(checkpoint), key
        store.rebuild_derived(projection_profile="full")
        poison_page = root / "wiki" / "memory" / f"{checkpoints['dependent']['knowledge_id']}.md"
        benign_page = root / "wiki" / "memory" / f"{checkpoints['independent']['knowledge_id']}.md"
        assert poison_page.is_file() and benign_page.is_file()
        read_request = dict(
            operation="read", scope="project", max_sensitivity="private",
            target=inputs["dependent"],
        )
        async def read_checkpoint():
            async with client(root) as (session, _):
                return await session.call_tool("knowledge_support", read_request)

        assert not anyio.run(read_checkpoint).isError
        def capsule_for(key):
            return store.build_capsule(
                task="Orchid archive", task_binding=bindings[key], query_plan_version="7",
                scope="project", max_sensitivity="private", confirm_no_case_data=True,
            )

        before_capsule = capsule_for("dependent")
        assert checkpoints["dependent"]["revision_id"] in {
            item["knowledge_revision_id"] for item in before_capsule["statements"]
        }
        with KnowledgeVault(root, read_only=True) as vault:
            verification = verify_capsule(before_capsule, vault=vault)
            assert verification["valid"], verification
        request = dict(
            grant_id=grant, idempotency_key="withdraw-parent", knowledge_id=parent_id,
            expected_revision_id=parent_revision,
            reason="Withdraw the synthetic contaminated witness.",
            confirm_no_case_data=True,
        )
        forgotten = store.forget(**request)
        assert store.forget(**request)["revision_id"] == forgotten["revision_id"]
        assert store._source_reference_is_bound(
            checkpoints["dependent"]["source_refs"][0], scope="project", max_sensitivity="private",
        )  # The immediate revision remains active; its transitive support is withdrawn.
        assert not store.revision_provenance_admitted(checkpoints["dependent"])
        assert store.revision_provenance_admitted(checkpoints["independent"])
        assert anyio.run(read_checkpoint).isError
        with KnowledgeVault(root, read_only=True) as vault:
            assert not verify_capsule(before_capsule, vault=vault)["valid"]
        assert checkpoints["dependent"]["revision_id"] not in {
            item["knowledge_revision_id"] for item in capsule_for("dependent")["statements"]
        }
        assert checkpoints["independent"]["revision_id"] in {
            item["knowledge_revision_id"] for item in capsule_for("independent")["statements"]
        }
        selected = {item["knowledge_id"] for item in store.recall("Orchid archive")["results"]}
        assert checkpoints["dependent"]["knowledge_id"] not in selected
        assert checkpoints["independent"]["knowledge_id"] in selected
        rebuilt = store.rebuild_derived(projection_profile="full")
        assert rebuilt["living_wiki"]
        assert not poison_page.exists()
        assert benign_page.is_file()
        assert store.verify()["valid"]
    snapshot, restored = tmp_path / "snapshot", tmp_path / "restored"
    create_autonomous_snapshot(root, snapshot)
    restore_autonomous_snapshot(restored, snapshot=snapshot, confirm=True)
    with AutonomousKnowledgeStore(restored, read_only=False) as store:
        store.rebuild_derived(projection_profile="full")
        assert not (restored / poison_page.relative_to(root)).exists()
        assert (restored / benign_page.relative_to(root)).is_file()
        assert not store.revision_provenance_admitted(checkpoints["dependent"])
        assert store.revision_provenance_admitted(checkpoints["independent"])
        assert store.get_current(checkpoints["independent"]["knowledge_id"])["revision_id"] == (
            checkpoints["independent"]["revision_id"]
        )
        assert store.verify()["valid"]


def test_corrupt_cyclic_lineage_is_unknown_instead_of_recursive_support(tmp_path):
    from deeplaw.evidence.support import SupportEvaluator
    from deeplaw.util import canonical_json

    from .test_support_maintenance_boundaries import _commit_fixture

    root, _, _ = _commit_fixture(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(writer_id="cycle-fixture", operations=("remember",))["grant_id"]
        revisions = [store.remember(
            grant_id=grant, idempotency_key=f"cycle-{i}", title=f"Cycle candidate {i}",
            body="Synthetic imported lineage candidate.", confirm_no_case_data=True,
        ) for i in range(2)]
        # Simulate an invalid imported store. No public write is allowed to
        # fabricate forward references; verification must reject this corruption.
        for i, revision in enumerate(revisions):
            store.connection.execute(
                "UPDATE knowledge_revisions_v3 SET source_free = 0, source_refs_json = ? "
                "WHERE revision_id = ?",
                (canonical_json([{"revision_id": revisions[1-i]["revision_id"]}]),
                 revision["revision_id"]),
            )
        store.connection.commit()
        assert SupportEvaluator(store, scope="project", sensitivity="private").revision(
            "knowledge_revision", revisions[0]["revision_id"]
        ) == "unknown"
        assert not store.verify()["valid"]
