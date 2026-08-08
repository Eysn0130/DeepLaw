from __future__ import annotations

from pathlib import Path

import pytest

from deeplaw.api import KnowledgeOS
from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.retrieval import PurposeAwareRetrievalService
from deeplaw.util import canonical_json, sha256_bytes
from tests.test_v013_statement_evidence import _prepared_v3_run


def _committed_vault(tmp_path: Path) -> Path:
    root, grant_id, run_id, _publication, _statement = _prepared_v3_run(tmp_path)
    CompilationCoordinator(root).commit(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    return root


def test_v6_statement_selective_and_receipt_deterministic(tmp_path: Path) -> None:
    root = _committed_vault(tmp_path)
    service = PurposeAwareRetrievalService(root)
    first = service.query(
        "A durable source statement.",
        query_plan_version="6",
        projection="standard",
    )
    second = service.query(
        "A durable source statement.",
        query_plan_version="6",
        projection="standard",
    )
    assert first["receipt_id"] == second["receipt_id"]
    assert len(first["query_plan"]["duties"]) == 12
    assert len(first["statements"]) == 1
    assert first["capsule"]["statements"][0]["authority"] == "agent_derived"
    assert first["capsule"]["statements"][0]["legal_authority"] is False
    assert first["capsule"]["statements"][0]["freshness"] == "fresh"
    # Compiled-first carries exact citations; raw excerpts are duty-targeted only.
    assert first["evidence"] == []
    assert len(first["statements"][0]["source_refs"]) == 1
    assert len(canonical_json(first["capsule"]).encode("utf-8")) <= 65536
    audit = first["local_audit"]
    assert audit["receipt_sha256"] == sha256_bytes(
        canonical_json(
            {key: value for key, value in audit.items() if key != "receipt_sha256"}
        ).encode("utf-8")
    )


def test_v6_context_reuses_statement_selection_and_provider_projection(
    tmp_path: Path,
) -> None:
    root = _committed_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        audit_head = store.audit_head
    with KnowledgeOS.open(root) as knowledge_os:
        context = knowledge_os.context.compile(
            task="A durable source statement.",
            purpose="verify",
            confirm_no_case_data=True,
        )
    assert context["schema_version"] == "deeplaw.knowledge-capsule/v3"
    assert [item["statement_text"] for item in context["statements"]] == [
        "A durable source statement."
    ]
    assert context["evidence"][0]["excerpt"] == "A durable source statement."
    assert context["provider_capsule"]["capsule"]["statements"] == context["statements"]
    assert context["provider_capsule"]["capsule"]["evidence"] == context["evidence"]
    assert context["provider_capsule"]["receipt"]["receipt_id"] == context["receipt_id"]

    mcp = handle_knowledge_support(
        operation="context",
        task="A durable source statement.",
        purpose="verify",
        limit=8,
        max_chars=8_000,
        max_tokens=6_000,
        max_sources=12,
        confirm_no_case_data=True,
        vault_path=root,
    )
    assert mcp["schema_version"] == "deeplaw.knowledge-support-output/v6"
    assert mcp["result"] == context["provider_capsule"]
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.audit_head == audit_head


def test_v6_projection_bounds_and_explicit_no_answer_gap(tmp_path: Path) -> None:
    root = _committed_vault(tmp_path)
    service = PurposeAwareRetrievalService(root)
    for projection in ("compact", "standard", "audit"):
        result = service.query(
            "unseen alpha zeta",
            query_plan_version="6",
            projection=projection,
        )
        assert result["gaps"]
        assert any(gap["code"] == "no_answer" for gap in result["gaps"])
        assert len(canonical_json(result["capsule"]).encode("utf-8")) <= 65536


def test_v6_targeted_fallback_is_limited_to_uncovered_duty(tmp_path: Path) -> None:
    root = _committed_vault(tmp_path)
    result = PurposeAwareRetrievalService(root).query(
        "A durable source statement.",
        query_plan_version="6",
        applicable_duties=("procedure", "unresolved_gap"),
    )
    events = result["query_plan"]["fallback"]["events"]
    assert {event["duty"] for event in events} <= {"procedure"}


def test_v6_verify_and_quote_materialize_exact_statement_evidence(
    tmp_path: Path,
) -> None:
    root = _committed_vault(tmp_path)
    service = PurposeAwareRetrievalService(root)
    for purpose in ("verify", "quote"):
        result = service.query(
            "A durable source statement.",
            purpose=purpose,
            query_plan_version="6",
        )
        assert result["evidence"]
        assert result["evidence"][0]["verification"] == "verified_source"
        assert result["evidence"][0]["excerpt"] == "A durable source statement."


def test_v6_temporal_duty_requires_temporal_coordinates(tmp_path: Path) -> None:
    root = _committed_vault(tmp_path)
    result = PurposeAwareRetrievalService(root).query(
        "A durable source statement.",
        purpose="freshness_check",
        query_plan_version="6",
    )
    temporal = next(
        item
        for item in result["query_plan"]["duties"]
        if item["duty"] == "temporal_freshness"
    )
    assert temporal["status"] == "unresolved"
    assert any(
        gap["duty"] == "temporal_freshness" for gap in result["gaps"]
    )


def test_v6_statement_and_map_tamper_fail_closed(tmp_path: Path) -> None:
    root = _committed_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.connection.execute(
            "UPDATE knowledge_statements_v1 SET statement_json = ?",
            ("{}",),
        )
        store.connection.commit()
    with pytest.raises(RuntimeError, match="knowledge vault integrity"):
        PurposeAwareRetrievalService(root).query(
            "A durable source statement.", query_plan_version="6"
        )

    root = _committed_vault(tmp_path / "map")
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.connection.execute(
            "UPDATE statement_evidence_maps_v1 SET map_json = ?",
            ("{}",),
        )
        store.connection.commit()
    with pytest.raises(RuntimeError, match="knowledge vault integrity"):
        PurposeAwareRetrievalService(root).query(
            "A durable source statement.", query_plan_version="6"
        )


def test_v6_identity_target_is_fail_closed_and_legal_stays_on_boundary(
    tmp_path: Path,
) -> None:
    root = _committed_vault(tmp_path)
    service = PurposeAwareRetrievalService(root)
    targeted = service.query(
        "unrelated wording",
        query_plan_version="6",
        query_target={"knowledge_id": "knowledge_" + "0" * 24, "text": "unrelated wording"},
    )
    assert targeted["statements"] == []
    legal = service.query(
        "A durable source statement.",
        purpose="legal",
        query_plan_version="6",
    )
    assert legal["schema_version"] == "deeplaw.purpose-aware-retrieval/v3"
    assert legal["statements"] == []
    assert legal["evidence"] == []
    assert any(gap["code"] == "law_support_required" for gap in legal["gaps"])


def test_v6_query_target_and_duty_contracts_match_runtime(tmp_path: Path) -> None:
    root = _committed_vault(tmp_path)
    service = PurposeAwareRetrievalService(root)
    with pytest.raises(ValueError, match="query_target contains unknown fields"):
        service.query("target", query_plan_version="6", query_target={})
    with pytest.raises(ValueError, match=r"query_target\.kind is invalid"):
        service.query(
            "target",
            query_plan_version="6",
            query_target={"kind": "not-a-kind"},
        )
    with pytest.raises(ValueError, match=r"query_target\.knowledge_id is invalid"):
        service.query(
            "target",
            query_plan_version="6",
            query_target={"knowledge_id": "knowledge_not-an-id"},
        )
    with pytest.raises(ValueError, match="applicable_duties contains an invalid duty"):
        service.query("target", query_plan_version="6", applicable_duties=())
