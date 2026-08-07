from __future__ import annotations

from pathlib import Path

import pytest

from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
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
