from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from deeplaw.api import KnowledgeOS
from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, _validate_contract
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.retrieval import PurposeAwareRetrievalService
from deeplaw.retrieval.query_v6 import _source_evidence, _source_key
from deeplaw.util import canonical_json, sha256_bytes, stable_id, strict_json_loads
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


def test_v6_source_evidence_contract_requires_exact_revision_locator_and_quote(
    tmp_path: Path,
) -> None:
    root = _committed_vault(tmp_path)
    with KnowledgeOS.open(root) as knowledge_os:
        context = knowledge_os.context.compile(
            task="A durable source statement.",
            purpose="verify",
            confirm_no_case_data=True,
        )
    evidence = context["provider_capsule"]["capsule"]["evidence"][0]
    assert evidence["source_revision_id"] == evidence["source_refs"][0][
        "source_revision_id"
    ]
    assert evidence["fragment_id"] == evidence["source_refs"][0]["fragment_id"]
    assert evidence["content_sha256"] == evidence["source_refs"][0]["quote_sha256"]
    assert evidence["source_refs"][0]["locator"]

    missing_locator = deepcopy(context["provider_capsule"])
    del missing_locator["capsule"]["evidence"][0]["source_refs"][0]["locator"]
    with pytest.raises(ValueError, match=r"provider-knowledge-capsule\.v2"):
        _validate_contract("provider-knowledge-capsule.v2.schema.json", missing_locator)

    opaque_evidence = deepcopy(context["provider_capsule"]["capsule"])
    opaque_evidence["evidence"][0] = {"source_revision_id": evidence["source_revision_id"]}
    with pytest.raises(ValueError, match=r"knowledge-capsule-projection\.v1"):
        _validate_contract("knowledge-capsule-projection.v1.schema.json", opaque_evidence)


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


def test_v6_quote_returns_gap_instead_of_truncated_source_passage(tmp_path: Path) -> None:
    long_passage = " ".join(f"budget-token-{index}" for index in range(80))
    root, grant_id, run_id, _publication, _statement_value = _prepared_v3_run(
        tmp_path,
        source_text=f"# Source\n{long_passage}",
        semantic_key="statement:bounded-exact-passage",
    )
    CompilationCoordinator(root).commit(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    result = PurposeAwareRetrievalService(root).query(
        "budget-token-40",
        purpose="quote",
        query_plan_version="6",
        max_chars=200,
    )
    assert result["evidence"] == []
    assert any(
        gap["code"] == "duty_unresolved" and gap["duty"] == "source_evidence"
        for gap in result["gaps"]
    )
    with (
        KnowledgeVault(root, read_only=True) as evidence_store,
        AutonomousKnowledgeStore(root, read_only=True) as knowledge_store,
    ):
        statement_json = knowledge_store.connection.execute(
            "SELECT statement_json FROM knowledge_statements_v1 LIMIT 1"
        ).fetchone()["statement_json"]
        suppressions: list[dict[str, str]] = []
        selected, _ = _source_evidence(
            evidence_store,
            knowledge_store,
            references=strict_json_loads(statement_json)["source_refs"],
            scope="project",
            max_sensitivity="private",
            max_sources=1,
            max_chars=200,
            reason="test_exact_source_passage_budget",
            seen={},
            represented_keys=set(),
            deduplications=[],
            suppressions=suppressions,
        )
    assert selected == []
    assert suppressions[0]["reason"] == "exact_source_passage_budget"


def test_v6_evidence_admission_normalizes_and_deduplicates_fragment_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _committed_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        statement_row = store.connection.execute(
            "SELECT statement_json FROM knowledge_statements_v1 LIMIT 1"
        ).fetchone()
        assert statement_row is not None
        exact_reference = strict_json_loads(statement_row["statement_json"])["source_refs"][0]
    missing_locator = {
        key: value for key, value in exact_reference.items() if key != "locator"
    }
    assert _source_key(exact_reference) != _source_key(missing_locator)

    service = PurposeAwareRetrievalService(root)
    original_evidence = service._evidence
    card = {
        "source_refs": [exact_reference],
        "excerpt": "A durable source statement.",
        "content_sha256": exact_reference["quote_sha256"],
    }

    def duplicate_evidence(*args: object, **kwargs: object) -> object:
        selection = original_evidence(*args, **kwargs)
        return replace(
            selection,
            cards=[{**card, "source_refs": [missing_locator]}, card],
        )

    monkeypatch.setattr(service, "_evidence", duplicate_evidence)
    result = service.query(
        "A durable source statement.",
        purpose="verify",
        query_plan_version="6",
    )

    assert len(result["evidence"]) == 1
    evidence = result["evidence"][0]
    assert evidence["evidence_id"] == stable_id(
        "queryevidence",
        exact_reference["source_revision_id"],
        exact_reference["fragment_id"],
    )
    assert evidence["source_refs"] == [exact_reference]
    evidence_ids = result["query_plan"]["selection"]["evidence_ids"]
    assert evidence_ids == list(dict.fromkeys(evidence_ids))
    assert result["query_plan"]["deduplicated_evidence_count"] >= 1
    assert any(
        item["reason"] == "duplicate_source_reference"
        for item in result["local_audit"]["deduplications"]
    )


def test_v6_evidence_admission_deduplicates_exact_content_within_source_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_revision_id = "sourcerev_" + "a" * 24
    text_sha256 = sha256_bytes(b"Same exact source passage.")
    references = [
        {
            "source_revision_id": source_revision_id,
            "fragment_id": f"fragment_{suffix * 24}",
            "locator": f"section:{index}",
            "quote_sha256": text_sha256,
        }
        for index, suffix in enumerate(("b", "c"), start=1)
    ]

    def canonical_reference(_store: object, reference: dict) -> tuple[dict, dict]:
        return reference, {
            "text": "Same exact source passage.",
            "text_sha256": text_sha256,
        }

    class BoundStore:
        @staticmethod
        def _source_reference_is_bound(*_args: object, **_kwargs: object) -> bool:
            return True

    monkeypatch.setattr(
        "deeplaw.retrieval.query_v6._canonical_source_reference",
        canonical_reference,
    )
    deduplications: list[dict[str, str]] = []
    selected, _ = _source_evidence(
        object(),  # type: ignore[arg-type]
        BoundStore(),  # type: ignore[arg-type]
        references=references,
        scope="project",
        max_sensitivity="private",
        max_sources=8,
        max_chars=8_000,
        reason="test_content_deduplication",
        seen={},
        represented_keys=set(),
        deduplications=deduplications,
        suppressions=[],
    )

    assert len(selected) == 1
    assert deduplications == [
        {
            "source_key": _source_key(references[1]),
            "reason": "duplicate_source_reference",
        }
    ]


def test_v6_evidence_admission_preserves_exact_content_across_source_revisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text_sha256 = sha256_bytes(b"Same text from two independently governed sources.")
    references = [
        {
            "source_revision_id": f"sourcerev_{source_suffix * 24}",
            "fragment_id": f"fragment_{fragment_suffix * 24}",
            "locator": "section:1",
            "quote_sha256": text_sha256,
        }
        for source_suffix, fragment_suffix in (("a", "b"), ("c", "d"))
    ]

    def canonical_reference(_store: object, reference: dict) -> tuple[dict, dict]:
        return reference, {
            "text": "Same text from two independently governed sources.",
            "text_sha256": text_sha256,
        }

    class BoundStore:
        @staticmethod
        def _source_reference_is_bound(*_args: object, **_kwargs: object) -> bool:
            return True

    monkeypatch.setattr(
        "deeplaw.retrieval.query_v6._canonical_source_reference",
        canonical_reference,
    )
    deduplications: list[dict[str, str]] = []
    selected, _ = _source_evidence(
        object(),  # type: ignore[arg-type]
        BoundStore(),  # type: ignore[arg-type]
        references=references,
        scope="project",
        max_sensitivity="private",
        max_sources=8,
        max_chars=8_000,
        reason="test_cross_source_content_preservation",
        seen={},
        represented_keys=set(),
        deduplications=deduplications,
        suppressions=[],
    )

    assert len(selected) == 2
    assert {item["source_revision_id"] for item in selected} == {
        reference["source_revision_id"] for reference in references
    }
    assert deduplications == []


@pytest.mark.parametrize("quote", [None, "0" * 64])
def test_v6_evidence_admission_rejects_missing_or_wrong_quote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quote: str | None,
) -> None:
    root = _committed_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        statement_row = store.connection.execute(
            "SELECT statement_json FROM knowledge_statements_v1 LIMIT 1"
        ).fetchone()
        assert statement_row is not None
        exact_reference = strict_json_loads(statement_row["statement_json"])["source_refs"][0]
    invalid_reference = (
        {
            **exact_reference,
            "quote_sha256": quote,
        }
        if quote is not None
        else {
            key: value
            for key, value in exact_reference.items()
            if key != "quote_sha256"
        }
    )
    service = PurposeAwareRetrievalService(root)
    original_evidence = service._evidence
    card = {
        "source_refs": [invalid_reference],
        "excerpt": "A durable source statement.",
        "content_sha256": exact_reference["quote_sha256"],
    }

    def invalid_evidence(*args: object, **kwargs: object) -> object:
        selection = original_evidence(*args, **kwargs)
        return replace(selection, cards=[card])

    monkeypatch.setattr(service, "_evidence", invalid_evidence)
    with pytest.raises(
        RuntimeError,
        match="statement source reference does not match its fragment",
    ):
        service.query(
            "A durable source statement.",
            purpose="verify",
            query_plan_version="6",
        )


@pytest.mark.parametrize("card_hash", ["valid", "wrong"])
def test_v6_historical_evidence_uses_and_validates_card_hash_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    card_hash: str,
) -> None:
    root = _committed_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        statement_row = store.connection.execute(
            "SELECT statement_json FROM knowledge_statements_v1 LIMIT 1"
        ).fetchone()
        assert statement_row is not None
        exact_reference = strict_json_loads(statement_row["statement_json"])["source_refs"][0]
    missing_quote = {
        key: value
        for key, value in exact_reference.items()
        if key != "quote_sha256"
    }
    service = PurposeAwareRetrievalService(root)
    original_evidence = service._evidence
    card = {
        "source_refs": [missing_quote],
        "excerpt": "A durable source statement.",
        "content_sha256": (
            exact_reference["quote_sha256"] if card_hash == "valid" else "0" * 64
        ),
    }

    def historical_evidence(*args: object, **kwargs: object) -> object:
        selection = original_evidence(*args, **kwargs)
        return replace(selection, cards=[card])

    monkeypatch.setattr(service, "_evidence", historical_evidence)
    query = {
        "purpose": "historical",
        "as_of": "2999-01-01T00:00:00Z",
        "query_plan_version": "6",
        "applicable_duties": ("source_evidence", "unresolved_gap"),
    }
    if card_hash == "wrong":
        with pytest.raises(
            RuntimeError,
            match="statement source reference does not match its fragment",
        ):
            service.query("A durable source statement.", **query)
        return
    result = service.query("A durable source statement.", **query)
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["source_refs"] == [exact_reference]


def test_v6_historical_targeted_fallback_does_not_repeat_admitted_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _committed_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        statement_row = store.connection.execute(
            "SELECT statement_json FROM knowledge_statements_v1 LIMIT 1"
        ).fetchone()
        assert statement_row is not None
        exact_reference = strict_json_loads(statement_row["statement_json"])["source_refs"][0]
    service = PurposeAwareRetrievalService(root)
    original_evidence = service._evidence
    card = {
        "source_refs": [exact_reference],
        "excerpt": "A durable source statement.",
        "content_sha256": exact_reference["quote_sha256"],
    }

    def repeated_evidence(*args: object, **kwargs: object) -> object:
        selection = original_evidence(*args, **kwargs)
        return replace(selection, cards=[card])

    monkeypatch.setattr(service, "_evidence", repeated_evidence)
    result = service.query(
        "A durable source statement.",
        purpose="historical",
        as_of="2999-01-01T00:00:00Z",
        query_plan_version="6",
        applicable_duties=("source_evidence", "contradiction", "unresolved_gap"),
    )

    assert len(result["evidence"]) == 1
    assert len(result["query_plan"]["selection"]["evidence_ids"]) == 1
    assert any(
        item["reason"] == "duplicate_source_reference"
        for item in result["local_audit"]["deduplications"]
    )


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
