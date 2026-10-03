from __future__ import annotations

from deeplaw.evidence.support import SupportEvaluator
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.util import strict_json_loads

from .test_support_maintenance_boundaries import _commit_fixture


def test_direct_relation_pins_endpoints_and_missing_dependencies_are_unknown(tmp_path):
    root, _compiler_grant, _source = _commit_fixture(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        refs = strict_json_loads(store.connection.execute(
            "SELECT statement_json FROM knowledge_statements_v1 LIMIT 1"
        ).fetchone()[0])["source_refs"]
        grant = store.enable_grant(
            writer_id="direct-relation-fixture", operations=("remember", "add_relation")
        )["grant_id"]
        endpoints = [store.remember(
            grant_id=grant, idempotency_key=f"endpoint-{i}", title=f"Archive record {i}",
            body="Orchid archive policy requires amber labels.", source_refs=refs,
            confirm_no_case_data=True,
        ) for i in range(2)]
        relation = store.add_relation(
            grant_id=grant, idempotency_key="relation", predicate="related_to",
            subject_knowledge_id=endpoints[0]["knowledge_id"],
            object_knowledge_id=endpoints[1]["knowledge_id"], evidence_refs=refs,
            confirm_no_case_data=True,
        )
        identity = relation["relation_revision_id"]
        def evaluate():
            return SupportEvaluator(store, scope="project", sensitivity="private").revision(
                "relation_revision", identity
            )
        assert evaluate() == "fresh"
        rows = store.connection.execute(
            "SELECT compilation_run_id FROM knowledge_dependencies_v1 "
            "WHERE consumer_revision_id = ?", (identity,),
        ).fetchall()
        assert len(rows) == len(refs) and all(row[0] is None for row in rows)
        assert store.verify()["valid"]
        # Existing snapshots may predate dependency registration. Missing evidence
        # is an unknown support gap, never permission to infer current endpoints.
        store.connection.execute("BEGIN")
        store.connection.execute(
            "DELETE FROM revision_dependencies_v1 WHERE consumer_revision_id = ?", (identity,)
        )
        assert evaluate() == "unknown"
        store.connection.rollback()
        store.remember(
            grant_id=grant, idempotency_key="endpoint-updated",
            knowledge_id=endpoints[0]["knowledge_id"],
            expected_revision_id=endpoints[0]["revision_id"],
            title="Archive record revised", body="The archive record has been revised.",
            source_refs=refs, confirm_no_case_data=True,
        )
        assert evaluate() == "invalidated"
        assert store.verify()["valid"]
