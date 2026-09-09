"""Public commit boundaries and bounded unknown support, using synthetic sources."""

from __future__ import annotations

import pytest

from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.compilation.finalization import SemanticFinalizer
from deeplaw.evidence import support as support_module
from deeplaw.evidence.support import SupportEvaluator
from deeplaw.knowledge_autonomy import SINK_OPERATIONS, AutonomousKnowledgeStore
from deeplaw.persistent_read_runtime import PersistentReadRuntime
from deeplaw.progressive_read import read_exact
from deeplaw.retrieval import PurposeAwareRetrievalService
from deeplaw.util import strict_json_loads

from .test_complete_support_sets import _compile_approved_successor, _v4_fixture


def _commit_fixture(tmp_path):
    root, grant, run, plan, _statement, _sets = _v4_fixture(tmp_path)
    finalizer = SemanticFinalizer(root)
    finalizer.stage_publication(
        grant_id=grant, compilation_run_id=run, plan=plan, confirm_no_case_data=True
    )
    CompilationCoordinator(root).validate(
        grant_id=grant, compilation_run_id=run, confirm_no_case_data=True
    )
    finalizer.commit(grant_id=grant, compilation_run_id=run, confirm_no_case_data=True)
    return root, grant, plan["source_revision_id"]


def test_support_exhaustion_and_missing_revision_remain_unknown(tmp_path, monkeypatch):
    root, _grant, _source = _commit_fixture(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        revision = store.connection.execute(
            "SELECT knowledge_revision_id FROM knowledge_statements_v1"
        ).fetchone()[0]
        exhausted = SupportEvaluator(store, scope="project", sensitivity="private", limit=0)
        assert exhausted.revision("knowledge_revision", revision) == "unknown"
        missing = SupportEvaluator(store, scope="project", sensitivity="private")
        assert missing.revision("knowledge_revision", "knowledgerev_" + "f" * 24) == "unknown"
        assert missing.revision("relation_revision", "relationrev_" + "f" * 24) == "unknown"
        statement_row = store.connection.execute(
            """
            SELECT statements.knowledge_revision_id, revisions.knowledge_id
            FROM knowledge_statements_v1 AS statements
            JOIN knowledge_revisions_v3 AS revisions
              ON revisions.revision_id = statements.knowledge_revision_id
            LIMIT 1
            """
        ).fetchone()
    assert statement_row is not None
    target = {
        "knowledge_id": statement_row["knowledge_id"],
        "revision_id": statement_row["knowledge_revision_id"],
    }

    class ExhaustedSupportEvaluator(SupportEvaluator):
        def __init__(self, store, **kwargs):
            kwargs["limit"] = 0
            super().__init__(store, **kwargs)

    monkeypatch.setattr(support_module, "SupportEvaluator", ExhaustedSupportEvaluator)
    queried = PurposeAwareRetrievalService(root).query(
        "Orchid archive policy requires amber labels.",
        query_target=target,
    )
    assert queried["statements"] == []
    assert any(gap["code"] == "no_answer" for gap in queried["gaps"])
    assert queried["query_plan"]["admitted_statement_count"] == 0

    runtime = PersistentReadRuntime(root, load_wiki=False)
    try:
        snapshot = runtime.get_snapshot(operation="read")
        with pytest.raises(KeyError, match="Knowledge Object is unavailable"):
            read_exact(
                {
                    "operation": "read",
                    "target": {"kind": "knowledge", **target},
                    "scope": "project",
                    "max_sensitivity": "private",
                },
                snapshot,
            )
    finally:
        runtime.close()


def test_refresh_rejects_a_changed_audit_head_before_commit(tmp_path, monkeypatch):
    from deeplaw.compilation import freshness

    root, grant, source = _commit_fixture(tmp_path)
    original_write = freshness._write_object
    raced = False

    def write_then_concurrent_owner_change(*args, **kwargs):
        nonlocal raced
        result = original_write(*args, **kwargs)
        if not raced:
            raced = True
            with AutonomousKnowledgeStore(root, read_only=False) as concurrent:
                concurrent.enable_grant(
                    writer_id="synthetic-concurrent-owner", operations=("remember",)
                )
        return result

    monkeypatch.setattr(freshness, "_write_object", write_then_concurrent_owner_change)
    with pytest.raises(RuntimeError, match="input audit head changed"):
        CompilationCoordinator(root).refresh(
            grant_id=grant, source_revision_id=source, confirm_no_case_data=True
        )
    assert raced
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert (
            store.connection.execute("SELECT count(*) FROM source_freshness_events_v1").fetchone()[
                0
            ]
            == 0
        )
        assert store.verify()["valid"]


def test_refresh_leaves_unrelated_knowledge_and_relation_revisions_unchanged(tmp_path):
    root, compiler_grant, source = _commit_fixture(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        statement_row = store.connection.execute(
            "SELECT statement_json FROM knowledge_statements_v1 LIMIT 1"
        ).fetchone()
        assert statement_row is not None
        statement = strict_json_loads(statement_row["statement_json"])
        unaffected_ref = statement["source_refs"][1]
        grant = store.enable_grant(
            writer_id="refresh-unrelated-objects",
            operations=tuple(sorted(SINK_OPERATIONS)),
        )["grant_id"]
        endpoints = [
            store.remember(
                grant_id=grant,
                idempotency_key=f"refresh-unrelated-endpoint-{index}",
                title=f"Unrelated archive note {index}",
                body=f"The unchanged archive fragment supports note {index}.",
                source_refs=[unaffected_ref],
                confirm_no_case_data=True,
            )
            for index in range(2)
        ]
        relation = store.add_relation(
            grant_id=grant,
            idempotency_key="refresh-unrelated-relation",
            subject_knowledge_id=endpoints[0]["knowledge_id"],
            predicate="related_to",
            object_knowledge_id=endpoints[1]["knowledge_id"],
            evidence_refs=[unaffected_ref],
            confirm_no_case_data=True,
        )
        knowledge_revision_ids = {
            item["knowledge_id"]: item["revision_id"] for item in endpoints
        }
        relation_key = relation["relation_key"]
        relation_revision_id = relation["relation_revision_id"]

    successor = _compile_approved_successor(
        root,
        tmp_path / "source.md",
        "Orchid archive policy requires amber labels. "
        "The first independent archive record is revised in the successor.",
    )
    report = CompilationCoordinator(root).refresh(
        grant_id=compiler_grant,
        source_revision_id=source,
        replacement_source_revision_id=successor["identity"]["source_revision_id"],
        confirm_no_case_data=True,
    )
    assert len(report["changed_fragment_ids"]) == 1
    assert len(report["unchanged_fragment_ids"]) == 1

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert {
            knowledge_id: store.get_current(knowledge_id)["revision_id"]
            for knowledge_id in knowledge_revision_ids
        } == knowledge_revision_ids
        relation_row = store.connection.execute(
            "SELECT current_revision_id FROM knowledge_relations_v3 WHERE relation_key = ?",
            (relation_key,),
        ).fetchone()
        assert relation_row is not None
        assert relation_row["current_revision_id"] == relation_revision_id
        assert store.verify()["valid"] is True


def test_forget_commit_failure_rolls_back_and_reopen_preserves_support(tmp_path, monkeypatch):
    root, _grant, _source = _commit_fixture(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        target_row = store.connection.execute(
            """
            SELECT revisions.knowledge_id, revisions.revision_id
            FROM knowledge_statements_v1 AS statements
            JOIN knowledge_revisions_v3 AS revisions
              ON revisions.revision_id = statements.knowledge_revision_id
            LIMIT 1
            """
        ).fetchone()
        assert target_row is not None
        knowledge_id = target_row["knowledge_id"]
        revision_id = target_row["revision_id"]
        forget_grant = store.enable_grant(
            writer_id="forget-commit-fault",
            operations=("forget",),
        )["grant_id"]
        before_heads = (store.audit_head, store.legacy_audit_head)
        before = store.get_current(knowledge_id, include_inactive=True)
        original_append = store._append_event
        failed = False

        def fail_forget_commit(**kwargs):
            nonlocal failed
            payload = kwargs.get("payload")
            if (
                not failed
                and kwargs.get("event_type") == "knowledge_revision_committed"
                and isinstance(payload, dict)
                and payload.get("operation") == "forget"
            ):
                failed = True
                raise RuntimeError("injected forget commit failure")
            return original_append(**kwargs)

        monkeypatch.setattr(store, "_append_event", fail_forget_commit)
        with pytest.raises(RuntimeError, match="injected forget commit failure"):
            store.forget(
                grant_id=forget_grant,
                idempotency_key="forget-commit-fault",
                knowledge_id=knowledge_id,
                expected_revision_id=revision_id,
                reason="Synthetic precommit failure.",
                confirm_no_case_data=True,
            )
        assert failed
        assert (store.audit_head, store.legacy_audit_head) == before_heads
        current = store.get_current(knowledge_id, include_inactive=True)
        assert current["revision_id"] == before["revision_id"]
        assert current["lifecycle"] == before["lifecycle"]
        assert store.revision_provenance_admitted(current)
        assert store.verify()["valid"] is True

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.recover()
        current = store.get_current(knowledge_id, include_inactive=True)
        assert current["revision_id"] == revision_id
        assert current["lifecycle"] == "active"
        assert store.revision_provenance_admitted(current)
        assert store.verify()["valid"] is True


def test_source_evidence_duty_requires_a_delivered_complete_passage(tmp_path):
    from deeplaw.retrieval.purpose import PurposeAwareRetrievalService

    root, _grant, _source = _commit_fixture(tmp_path)
    query = "Orchid archive policy requires amber labels"
    service = PurposeAwareRetrievalService(root)
    references_only = service.query(
        query, policy="compiled-first-v1", applicable_duties=("source_evidence",)
    )
    assert references_only["statements"]
    assert references_only["statements"][0]["source_refs"]
    assert references_only["evidence"] == []
    duty = next(
        item
        for item in references_only["query_plan"]["duties"]
        if item["duty"] == "source_evidence"
    )
    assert duty["status"] == "unresolved"
    delivered = service.query(
        query, policy="evidence-first-v1", applicable_duties=("source_evidence",)
    )
    assert delivered["evidence"]
    duty = next(
        item for item in delivered["query_plan"]["duties"] if item["duty"] == "source_evidence"
    )
    assert duty["status"] == "satisfied"
    from deeplaw.util import sha256_bytes

    for item in delivered["evidence"]:
        assert sha256_bytes(item["excerpt"].encode()) == item["content_sha256"]


def test_identical_source_reimport_preserves_compiled_revision_identity(tmp_path):
    from deeplaw.knowledge_compiler import compile_source
    from deeplaw.knowledge_store import KnowledgeVault

    root, _, source_revision = _commit_fixture(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        source_id = store.connection.execute(
            "SELECT legacy_source_id FROM evidence_bindings_v3 WHERE source_revision_id = ?",
            (source_revision,),
        ).fetchone()[0]
        before = [tuple(row) for row in store.connection.execute(
            "SELECT revision_id, knowledge_id, markdown_sha256 FROM knowledge_revisions_v3 "
            "ORDER BY revision_id"
        )]
    with KnowledgeVault(root, read_only=False) as vault:
        repeated = compile_source(
            vault, tmp_path / "source.md", source_kind="document", confirm_no_case_data=True,
        )
    assert repeated["source"]["source_id"] == source_id
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        after = [tuple(row) for row in store.connection.execute(
            "SELECT revision_id, knowledge_id, markdown_sha256 FROM knowledge_revisions_v3 "
            "ORDER BY revision_id"
        )]
        assert after == before
        assert store.verify()["valid"]
