"""Public graph completeness reproductions for the v0.13 selection boundary.

These tests pin the contract that candidate scanning and relation selection remain
separately observable.
They use only the public Knowledge sink/domain and Wiki read seams; no Ledger fixtures or
private SQL are used.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.subprocess_environment import _build_subprocess_environment


def _relation_fixture(
    tmp_path: Path,
    *,
    rejected_candidates: bool = False,
    relation_count: int = 3,
) -> tuple[Path, str]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013 graph completeness", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="v013-graph-completeness",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
            max_objects=100,
        )["grant_id"]

        def concept(key: str) -> dict[str, str]:
            result = store.remember(
                grant_id=grant_id,
                idempotency_key=f"graph-completeness-{key}",
                title=f"Graph completeness {key}",
                body=f"Synthetic graph completeness node {key}.",
                kind="concept",
                operation="upsert_concept",
                semantic_key=f"v013:graph-completeness:{key}",
                confirm_no_case_data=True,
            )
            return {
                "knowledge_id": str(result["knowledge_id"]),
                "revision_id": str(result["revision_id"]),
            }

        seed = concept("seed")
        evidence_anchor = concept("evidence-anchor")
        neighbors = [concept(f"neighbor-{index}") for index in range(relation_count)]
        for index, neighbor in enumerate(neighbors):
            evidence = (
                evidence_anchor
                if rejected_candidates and index > 0
                else seed
            )
            store.add_relation(
                grant_id=grant_id,
                idempotency_key=f"graph-completeness-relation-{index}",
                subject_knowledge_id=seed["knowledge_id"],
                predicate="related_to",
                object_knowledge_id=neighbor["knowledge_id"],
                evidence_refs=[{"revision_id": evidence["revision_id"]}],
                confirm_no_case_data=True,
            )
        if rejected_candidates:
            store.forget(
                grant_id=grant_id,
                idempotency_key="graph-completeness-forget-evidence-anchor",
                knowledge_id=evidence_anchor["knowledge_id"],
                expected_revision_id=evidence_anchor["revision_id"],
                reason="Leave governance-rejected relation candidates for the public scan.",
                confirm_no_case_data=True,
            )
        store.rebuild_derived(projection_profile="standard")
    return root, seed["knowledge_id"]


def test_store_graph_exposes_selection_cut_separately_from_candidate_scan(
    tmp_path: Path,
) -> None:
    root, seed_id = _relation_fixture(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        limited = store.graph(knowledge_id=seed_id, limit=1)
        complete = store.graph(knowledge_id=seed_id, limit=3)

    assert limited["budget"]["selected_relations"] == 1
    assert limited["budget"]["candidate_relations_scanned"] == 2
    assert limited["budget"]["candidate_scan_truncated"] is False
    assert limited["budget"].get("selection_truncated") is True
    assert len(limited["relations"]) == 1
    assert len(limited["nodes"]) == 2
    limitations = (
        limited.get("gaps")
        or limited.get("limitations")
        or limited["budget"].get("limitations")
    )
    assert limitations, "selection truncation must carry a bounded Gap or limitation"

    assert complete["budget"]["selected_relations"] == 3
    assert complete["budget"]["candidate_relations_scanned"] == 3
    assert complete["budget"].get("selection_truncated") is False
    assert complete["gaps"] == []


def test_wiki_local_graph_preserves_selection_truncation_semantics(tmp_path: Path) -> None:
    root, seed_id = _relation_fixture(tmp_path)
    with KnowledgeOS.open(root) as knowledge_os:
        limited = knowledge_os.wiki.local_graph(seed_id, limit=1)
        complete = knowledge_os.wiki.local_graph(seed_id, limit=3)

    assert limited["budget"]["selected_relations"] == 1
    assert limited["budget"]["candidate_relations_scanned"] == 2
    assert limited["budget"].get("selection_truncated") is True
    assert limited.get("gaps") or limited.get("limitations")
    assert complete["budget"]["selected_relations"] == 3
    assert complete["budget"].get("selection_truncated") is False
    assert complete["gaps"] == []


def test_cli_and_mcp_graph_share_bounded_completeness_signals(tmp_path: Path) -> None:
    root, seed_id = _relation_fixture(tmp_path)
    mcp = handle_knowledge_support(
        operation="graph",
        knowledge_id=seed_id,
        scope="project",
        max_sensitivity="private",
        limit=1,
        vault_path=root,
    )["result"]
    isolated_home = tmp_path / "cli-home"
    isolated_home.mkdir()
    repository = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "deeplaw",
            "knowledge",
            "autonomy",
            "graph",
            "--vault",
            str(root),
            "--knowledge-id",
            seed_id,
            "--scope",
            "project",
            "--max-sensitivity",
            "private",
            "--limit",
            "1",
        ],
        cwd=repository,
        env=_build_subprocess_environment(
            overrides={"HOME": str(isolated_home), "PYTHONPATH": str(repository)}
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    cli = json.loads(completed.stdout)

    for result in (cli, mcp):
        assert result["budget"] == {
            "max_relations": 1,
            "selected_relations": 1,
            "max_candidate_relations_scanned": 5_000,
            "candidate_relations_scanned": 2,
            "candidate_scan_truncated": False,
            "selection_truncated": True,
        }
        assert len(result["relations"]) == 1
        assert len(result["gaps"]) == 1
        assert "selection" in result["gaps"][0]


def test_governance_rejected_candidates_do_not_fake_selection_truncation(
    tmp_path: Path,
) -> None:
    root, seed_id = _relation_fixture(tmp_path, rejected_candidates=True)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        result = store.graph(knowledge_id=seed_id, limit=10)

    assert result["budget"]["candidate_relations_scanned"] == 3
    assert result["budget"]["candidate_scan_truncated"] is False
    assert result["budget"]["selected_relations"] == 1
    assert result["budget"].get("selection_truncated") is False
    assert len(result["rejected"]) == 2
    assert {item["reason"] for item in result["rejected"]} == {
        "relation_evidence_inactive"
    }
    assert result["gaps"] == []


def test_candidate_scan_truncation_has_independent_bounded_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("deeplaw.knowledge_autonomy._MAX_GRAPH_RELATION_SCAN", 3)
    root, seed_id = _relation_fixture(tmp_path, relation_count=4)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        result = store.graph(knowledge_id=seed_id, limit=10)

    assert result["budget"]["candidate_relations_scanned"] == 3
    assert result["budget"]["candidate_scan_truncated"] is True
    assert result["budget"]["selection_truncated"] is False
    assert len(result["relations"]) == 3
    assert len(result["gaps"]) == 1
    assert "candidate scan" in result["gaps"][0]
    assert "3-row bound" in result["gaps"][0]


def test_selection_early_exit_does_not_fake_candidate_scan_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("deeplaw.knowledge_autonomy._MAX_GRAPH_RELATION_SCAN", 3)
    root, seed_id = _relation_fixture(tmp_path, relation_count=4)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        result = store.graph(knowledge_id=seed_id, limit=1)

    assert result["budget"]["max_candidate_relations_scanned"] == 3
    assert result["budget"]["candidate_relations_scanned"] == 2
    assert result["budget"]["candidate_scan_truncated"] is False
    assert result["budget"]["selection_truncated"] is True
    assert len(result["gaps"]) == 1
    assert "selection" in result["gaps"][0]
    assert "candidate scan" not in result["gaps"][0]
