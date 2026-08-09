from __future__ import annotations

import pytest

from benchmarks.semantic.run_query_suite import _v3_context_retrieval_view


def _statement(
    *,
    knowledge_id: str,
    revision_id: str,
    statement_text: str,
    source_revision_id: str,
) -> dict[str, object]:
    return {
        "knowledge_revision_id": revision_id,
        "statement_text": statement_text,
        "source_refs": [{"source_revision_id": source_revision_id}],
        "object_summary": {
            "knowledge_id": knowledge_id,
            "revision_id": revision_id,
            "title": "Atlas retention policy",
            "kind": "claim",
            "semantic_key": "claim:atlas-retention",
        },
    }


def test_v3_statements_project_without_dropping_same_revision_statements() -> None:
    first = _statement(
        knowledge_id="knowledge_aaaaaaaaaaaaaaaaaaaaaaaa",
        revision_id="knowledgerev_bbbbbbbbbbbbbbbbbbbbbbbb",
        statement_text="Atlas retains diagnostic logs for 60 days.",
        source_revision_id="sourcerev_cccccccccccccccccccccccc",
    )
    duplicate = _statement(
        knowledge_id="knowledge_aaaaaaaaaaaaaaaaaaaaaaaa",
        revision_id="knowledgerev_bbbbbbbbbbbbbbbbbbbbbbbb",
        statement_text="A second Statement from the same revision remains independently visible.",
        source_revision_id="sourcerev_dddddddddddddddddddddddd",
    )
    capsule = {
        "schema_version": "deeplaw.knowledge-capsule/v3",
        "statements": [first, duplicate],
        "evidence": [
            {
                "evidence_id": "queryevidence_eeeeeeeeeeeeeeeeeeeeeeee",
                "source_revision_id": "sourcerev_cccccccccccccccccccccccc",
                "source_refs": [],
            }
        ],
        "gaps": [
            {"code": "retrieval_gap", "message": "one gap"},
            {"code": "retrieval_gap", "message": "duplicate code"},
        ],
    }

    view = _v3_context_retrieval_view(capsule)

    assert len(view["compiled"]) == 2
    assert view["compiled"][0] == {
        "knowledge_id": "knowledge_aaaaaaaaaaaaaaaaaaaaaaaa",
        "revision_id": "knowledgerev_bbbbbbbbbbbbbbbbbbbbbbbb",
        "title": "Atlas retention policy",
        "kind": "claim",
        "semantic_key": "claim:atlas-retention",
        "content": "Atlas retains diagnostic logs for 60 days.",
        "source_refs": [
            {"source_revision_id": "sourcerev_cccccccccccccccccccccccc"}
        ],
    }
    assert view["compiled"][1]["content"] == (
        "A second Statement from the same revision remains independently visible."
    )
    assert view["compiled"][1]["source_refs"] == [
        {"source_revision_id": "sourcerev_dddddddddddddddddddddddd"}
    ]
    assert len(view["evidence"]) == 1
    assert view["gaps"] == [{"code": "retrieval_gap"}]


def test_v3_projection_rejects_legacy_sections_and_malformed_gaps() -> None:
    with pytest.raises(ValueError, match="v3 schema"):
        _v3_context_retrieval_view(
            {
                "schema_version": "deeplaw.knowledge-capsule/v2",
                "sections": {"agent_derived_knowledge": [{"knowledge_id": "old"}]},
            }
        )

    with pytest.raises(ValueError, match="gap 0 has no code"):
        _v3_context_retrieval_view(
            {
                "schema_version": "deeplaw.knowledge-capsule/v3",
                "statements": [],
                "evidence": [],
                "gaps": [{"message": "missing code"}],
            }
        )
