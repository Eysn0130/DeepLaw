from __future__ import annotations

import json
from pathlib import Path

from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.projection import rebuild_living_wiki
from deeplaw.projection.incremental import read_previous_v3
from deeplaw.util import stable_id


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-page-families", scope="project")
    initialize_autonomous_core(root)
    return root


def test_standard_projection_emits_governed_navigation_families_when_empty(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        rebuild_living_wiki(store)

    required = {
        "wiki/guides/index.md",
        "wiki/gaps/index.md",
        "wiki/sources/index.md",
        "wiki/recent-changes/index.md",
        "wiki/contradictions/index.md",
    }
    for relative in required:
        text = (root / relative).read_text(encoding="utf-8")
        assert "## Governance" in text
        assert "Authority:" in text
        assert "Verification:" in text
        assert "Freshness:" in text
        assert "Lifecycle:" in text
        assert "Semantic Status:" in text
        assert "Revision:" in text
        assert "### Evidence" in text
        assert "### History" in text
        assert "### Gap" in text
        assert "### Contradiction" in text
        assert (
            "Explicit gap:" in text
            or "[[wiki/recent-changes/" in text
            or "[[wiki/gaps/" in text
        )
    assert not list((root / "wiki" / "communities").rglob("*.md"))
    assert not list((root / "canvas").rglob("*.canvas"))


def test_source_page_separates_evidence_and_derived_summary(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "source.md"
    source.write_text(
        "# Source\n\nA durable source fragment for page-family rendering.",
        encoding="utf-8",
    )
    from deeplaw.knowledge_compiler import compile_source
    from deeplaw.knowledge_store import KnowledgeVault

    with KnowledgeVault(root, read_only=False) as vault:
        compile_source(vault, source, source_kind="document", confirm_no_case_data=True)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        rebuild_living_wiki(store)

    source_page = next((root / "wiki" / "sources").glob("sourcerev_*.md"))
    text = source_page.read_text(encoding="utf-8")
    assert "## SOURCE EVIDENCE" in text
    assert "## AGENT-DERIVED SOURCE SUMMARY" in text
    assert text.index("## SOURCE EVIDENCE") < text.index("## AGENT-DERIVED SOURCE SUMMARY")
    summary = text[text.index("## AGENT-DERIVED SOURCE SUMMARY") :]
    assert "origin=agent_derived" in summary
    assert "authority=none" in summary
    assert "legal_authority=false" in summary
    assert "Original content SHA-256:" in text


def test_source_compilation_summary_avoids_node_fragment_cross_product(
    tmp_path: Path,
) -> None:
    """A source summary must aggregate nodes and fragments independently.

    Joining both one-to-many tables before ``COUNT(DISTINCT ...)`` creates an
    O(nodes * fragments) intermediate result.  At 100k fragments that made a
    deterministic Wiki rebuild spend hours inside one SQLite statement.
    """

    root = _vault(tmp_path)
    source = tmp_path / "source-scale.md"
    source.write_text(
        "".join(
            f"# Synthetic heading {index:04d}\nSynthetic body {index:04d}.\n"
            for index in range(128)
        ),
        encoding="utf-8",
    )
    from deeplaw.knowledge_compiler import compile_source
    from deeplaw.knowledge_store import KnowledgeVault

    with KnowledgeVault(root, read_only=False) as vault:
        compile_source(vault, source, source_kind="document", confirm_no_case_data=True)

    statements: list[str] = []
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.connection.set_trace_callback(statements.append)
        try:
            rebuild_living_wiki(store)
        finally:
            store.connection.set_trace_callback(None)

    summary_queries = [
        " ".join(statement.split())
        for statement in statements
        if "AS node_count" in statement and "AS fragment_count" in statement
    ]
    assert len(summary_queries) == 1
    summary_query = summary_queries[0]
    assert "LEFT JOIN source_ir_nodes_v2" not in summary_query
    assert "LEFT JOIN fragments_v2" not in summary_query

    source_page = next((root / "wiki" / "sources").glob("sourcerev_*.md"))
    text = source_page.read_text(encoding="utf-8")
    assert "- Source IR nodes / fragments: 256 / 128" in text


def test_current_statement_has_stable_anchor_and_receipt_metadata(tmp_path: Path) -> None:
    # Reuse the repository's exact v3 commit-boundary fixture; this does not synthesize or edit
    # semantic content and therefore exercises the builder's read-only rendering seam.
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from test_v013_statement_evidence import _prepared_v3_run

    root, grant_id, run_id, _publication, _statement = _prepared_v3_run(tmp_path)
    CompilationCoordinator(root).commit(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        rebuild_living_wiki(store, projection_profile="full")
        revision = store.connection.execute(
            "SELECT revision_id, knowledge_id FROM knowledge_revisions_v3 "
            "ORDER BY recorded_at DESC, revision_id DESC LIMIT 1"
        ).fetchone()
        statement = store.connection.execute(
            "SELECT statement_id, receipt_sha256 FROM statement_evidence_receipts_v1 "
            "ORDER BY statement_id LIMIT 1"
        ).fetchone()
        semantic_status = store.connection.execute(
            "SELECT semantic_status FROM semantic_compilation_runs_v2 "
            "WHERE compilation_run_id = ?",
            (run_id,),
        ).fetchone()["semantic_status"]
    assert revision is not None and statement is not None
    snapshot = read_previous_v3(root)
    assert snapshot is not None
    record = next(
        row
        for row in snapshot["components"]["page_registry"]["records"]
        if row.get("knowledge_id") == revision["knowledge_id"]
    )
    text = (root / record["canonical_page_path"]).read_text(encoding="utf-8")
    anchor_id = f"statement-{statement['statement_id']}"
    assert f'<a id="{anchor_id}"></a>' in text
    assert any(
        anchor.get("statement_target", {}).get("statement_id") == statement["statement_id"]
        for anchor in record.get("anchors", [])
    )
    assert f"semantic_status: {semantic_status}" in text
    assert f"receipt:{statement['receipt_sha256']}" in text
    assert "Support status: `supported`" in text


def test_source_freshness_uses_worst_mixed_dependency_state(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from test_v013_statement_evidence import _prepared_v3_run

    root, grant_id, run_id, _publication, _statement = _prepared_v3_run(tmp_path)
    CompilationCoordinator(root).commit(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        source_revision_id = store.connection.execute(
            "SELECT source_revision_id FROM source_compilation_runs_v1 "
            "WHERE compilation_run_id = ?",
            (run_id,),
        ).fetchone()["source_revision_id"]
        dependency = store.connection.execute(
            "SELECT * FROM knowledge_dependencies_v1 "
            "WHERE source_revision_id = ? LIMIT 1",
            (source_revision_id,),
        ).fetchone()
        assert dependency is not None
        store.connection.execute(
            "UPDATE knowledge_dependencies_v1 SET freshness = 'stale' "
            "WHERE dependency_id = ?",
            (dependency["dependency_id"],),
        )
        now = store._next_transaction_time()
        store.connection.execute(
            """
            INSERT INTO knowledge_dependencies_v1(
                dependency_id, compilation_run_id, consumer_kind, consumer_object_id,
                consumer_revision_id, source_revision_id, fragment_id, dependency_kind,
                freshness, reason, recorded_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("dependency", run_id, "mixed-invalidated"),
                dependency["compilation_run_id"],
                dependency["consumer_kind"],
                dependency["consumer_object_id"],
                dependency["consumer_revision_id"],
                dependency["source_revision_id"],
                "fragment_mixed_invalidated",
                dependency["dependency_kind"],
                "invalidated",
                "page-family mixed freshness fixture",
                now,
                now,
            ),
        )
        store.connection.commit()
        rebuild_living_wiki(store)
    source_page = next((root / "wiki" / "sources").glob("sourcerev_*.md"))
    text = source_page.read_text(encoding="utf-8")
    assert "freshness: invalidated" in text


def test_v2_duty_applicability_is_explicitly_unrecorded_and_v3_is_exact(
    tmp_path: Path,
) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from test_v013_statement_evidence import _prepared_v3_run

    root, grant_id, run_id, _publication, _statement = _prepared_v3_run(tmp_path)
    CompilationCoordinator(root).commit(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        rebuild_living_wiki(store)
    source_page = next((root / "wiki" / "sources").glob("sourcerev_*.md"))
    v3_text = source_page.read_text(encoding="utf-8")
    assert "applicability=`not_applicable`" in v3_text

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.connection.execute(
            "UPDATE source_compilation_runs_v1 SET compiler_profile_version = '2' "
            "WHERE compilation_run_id = ?",
            (run_id,),
        )
        reports = store.connection.execute(
            "SELECT duty_type, report_json FROM semantic_duty_reports_v1 "
            "WHERE compilation_run_id = ?",
            (run_id,),
        ).fetchall()
        for report in reports:
            value = json.loads(report["report_json"])
            value.pop("applicability", None)
            store.connection.execute(
                "UPDATE semantic_duty_reports_v1 SET report_json = ? "
                "WHERE compilation_run_id = ? AND duty_type = ?",
                (json.dumps(value, sort_keys=True), run_id, report["duty_type"]),
            )
        store.connection.commit()
        rebuild_living_wiki(store)
    v2_text = source_page.read_text(encoding="utf-8")
    assert "applicability=`not_recorded_in_v2`" in v2_text
