from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.util import canonical_json, sha256_bytes
from deeplaw.wiki_coverage import (
    CANONICAL_PAGE_FAMILIES,
    CANONICAL_SEMANTIC_DUTIES,
    COVERAGE_GAP_SCHEMA,
    COVERAGE_SPEC_SCHEMA,
    CoverageSpecError,
    compute_coverage_gaps,
    validate_coverage_gap,
    validate_coverage_spec,
)

ZERO = "0" * 64
TIMESTAMP = "2026-08-08T00:00:00Z"


def _duties(required: dict[str, str] | None = None) -> list[dict[str, object]]:
    required = required or {}
    return [
        (
            {
                "duty": duty,
                "applicability": "required",
                "page_families": [required[duty]],
            }
            if duty in required
            else {
                "duty": duty,
                "applicability": "not_applicable",
                "reason": "not_requested",
            }
        )
        for duty in CANONICAL_SEMANTIC_DUTIES
    ]


def _spec(
    *,
    page_families: list[str] | None = None,
    duties: list[dict[str, object]] | None = None,
    max_pages: int = 32,
    max_bytes: int = 4096,
    scopes: list[str] | None = None,
) -> dict[str, object]:
    families = page_families or ["concepts"]
    return {
        "schema_version": COVERAGE_SPEC_SCHEMA,
        "spec_id": "coverage_spec_1",
        "revision_id": "coverage_revision_1",
        "status": "owner_confirmed",
        "generated_at": TIMESTAMP,
        "transaction_head": ZERO,
        "audit_head": ZERO,
        "owner_confirmation": {
            "receipt_id": "owner_receipt_1",
            "confirmed_at": TIMESTAMP,
            "confirmed_by": "owner_1",
            "grants_authority": False,
        },
        "scopes": scopes or ["project"],
        "topics": ["alpha"],
        "page_families": families,
        "hierarchy": {"roots": families[:1], "edges": []},
        "guided_tours": [],
        "codemap": {
            "enabled": "codemap" in families,
            "required_input_revision_refs": [],
            "required_edge_ids": [],
        },
        "duties": duties or _duties(),
        "max_pages": max_pages,
        "max_bytes": max_bytes,
        "shard_bounds": {
            "max_shards": 4,
            "max_pages_per_shard": 32,
            "max_bytes_per_shard": 4096,
        },
    }


def _page(
    page_id: str,
    family: str,
    *,
    revision_id: str | None = None,
    byte_size: int = 8,
    input_revision_refs: list[str] | None = None,
    edge_ids: list[str] | None = None,
    content_role: str = "knowledge",
    origin: str = "agent_derived",
    legal_authority: bool = False,
    verified: bool = True,
    committed: bool = True,
    registered: bool = True,
) -> dict[str, object]:
    return {
        "page_id": page_id,
        "revision_id": revision_id or f"revision_{page_id}",
        "family": family,
        "topic": "alpha",
        "scope": "project",
        "canonical_page_path": f"wiki/{page_id}.md",
        "byte_size": byte_size,
        "verified": verified,
        "committed": committed,
        "registered": registered,
        "input_revision_refs": input_revision_refs or [],
        "edge_ids": edge_ids or [],
        "content_role": content_role,
        "origin": origin,
        "legal_authority": legal_authority,
    }


def _input(
    revision_id: str,
    *,
    path: str = "src/input.py",
    verified: bool = True,
    committed: bool = True,
    registered: bool = True,
) -> dict[str, object]:
    return {
        "revision_id": revision_id,
        "kind": "source_revision",
        "topic": "alpha",
        "scope": "project",
        "canonical_path": path,
        "byte_size": 4,
        "verified": verified,
        "committed": committed,
        "registered": registered,
    }


def _edge(
    edge_id: str,
    *,
    verified: bool = True,
    committed: bool = True,
    registered: bool = True,
) -> dict[str, object]:
    return {
        "edge_id": edge_id,
        "source_revision_id": "source_revision",
        "target_revision_id": "target_revision",
        "canonical_path": "src/input.py",
        "verified": verified,
        "committed": committed,
        "registered": registered,
    }


def _inventory(
    pages: list[dict[str, object]] | None = None,
    *,
    inputs: list[dict[str, object]] | None = None,
    edges: list[dict[str, object]] | None = None,
    audit_head: str = ZERO,
) -> dict[str, object]:
    return {
        "schema_version": "deeplaw.living-wiki-coverage-inventory/v1",
        "audit_head": audit_head,
        "generated_at": TIMESTAMP,
        "pages": pages or [],
        "inputs": inputs or [],
        "edges": edges or [],
    }


def test_schemas_are_valid_and_generated_gap_is_a_closed_instance() -> None:
    spec_schema = json.loads(Path("contracts/living-wiki-coverage-spec.v1.schema.json").read_text())
    gap_schema = json.loads(Path("contracts/living-wiki-coverage-gap.v1.schema.json").read_text())
    Draft202012Validator.check_schema(spec_schema)
    Draft202012Validator.check_schema(gap_schema)
    Draft202012Validator(spec_schema, format_checker=FormatChecker()).validate(_spec())

    spec = _spec(page_families=["concepts"], duties=_duties({"answer": "concepts"}))
    gaps = compute_coverage_gaps(_inventory(), spec)
    selected = [gap for gap in gaps if gap["duty"] == "answer"]
    assert len(selected) == 1
    Draft202012Validator(gap_schema, format_checker=FormatChecker()).validate(selected[0])
    assert selected[0]["schema_version"] == COVERAGE_GAP_SCHEMA


def test_spec_shape_is_closed_and_owner_confirmation_never_grants_authority() -> None:
    spec = _spec()
    assert validate_coverage_spec(spec)["owner_confirmation"]["grants_authority"] is False
    unknown = copy.deepcopy(spec)
    unknown["unexpected"] = True
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(unknown)
    granted = copy.deepcopy(spec)
    granted["owner_confirmation"]["grants_authority"] = True
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(granted)
    missing_owner = copy.deepcopy(spec)
    missing_owner["owner_confirmation"].pop("confirmed_by")
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(missing_owner)


def test_all_canonical_families_and_fifteen_duties_are_explicit() -> None:
    spec = _spec(page_families=list(CANONICAL_PAGE_FAMILIES))
    normalized = validate_coverage_spec(spec)
    assert set(normalized["page_families"]) == set(CANONICAL_PAGE_FAMILIES)
    assert {row["duty"] for row in normalized["duties"]} == set(CANONICAL_SEMANTIC_DUTIES)


def test_four_gap_classes_are_golden_and_distinct() -> None:
    cases = (
        ("concepts", "answer", "missing", 1),
        ("entities", "recommend", "not_applicable", 1),
        ("guides", "explain", "unavailable", 1),
        ("memory", "define", "over_budget", 0),
    )
    observed: set[str] = set()
    for family, duty, expected, max_pages in cases:
        duties = _duties({} if expected == "not_applicable" else {duty: family})
        spec = _spec(page_families=[family], duties=duties, max_pages=max_pages)
        gaps = compute_coverage_gaps(_inventory(), spec)
        selected = next(gap for gap in gaps if gap["duty"] == duty)
        assert selected["status"] == expected
        observed.add(selected["status"])
    assert observed == {"missing", "not_applicable", "unavailable", "over_budget"}


def test_source_evidence_and_source_summary_are_not_interchangeable() -> None:
    duties = _duties({"answer": "source_evidence", "recommend": "source_summary"})
    spec = _spec(page_families=["source_evidence", "source_summary"], duties=duties)
    inventory = _inventory(
        [
            _page(
                "summary",
                "source_summary",
                input_revision_refs=["source_revision"],
                content_role="agent_derived_summary",
                origin="agent_derived",
            )
        ],
        inputs=[_input("source_revision")],
    )
    gaps = compute_coverage_gaps(inventory, spec)
    by_family = {gap["page_family"]: gap for gap in gaps if gap["duty"] in {"answer", "recommend"}}
    assert by_family["source_evidence"]["status"] == "missing"
    assert "source_summary" not in by_family

    wrong_kind_inventory = copy.deepcopy(inventory)
    wrong_kind_inventory["inputs"][0]["kind"] = "knowledge_revision"
    wrong_kind_gaps = compute_coverage_gaps(wrong_kind_inventory, spec)
    assert any(
        gap["page_family"] == "source_summary"
        and gap["duty"] == "recommend"
        and gap["status"] == "unavailable"
        for gap in wrong_kind_gaps
    )


def test_guides_and_codemap_require_registered_verified_inputs_and_edges() -> None:
    duties = _duties({"explain": "guides", "define": "codemap"})
    spec = _spec(page_families=["guides", "codemap"], duties=duties)
    inventory = _inventory(
        [
            _page(
                "guide",
                "guides",
                input_revision_refs=["missing_revision"],
                content_role="navigation",
            ),
            _page(
                "map",
                "codemap",
                input_revision_refs=["missing_revision"],
                edge_ids=["missing_edge"],
                content_role="navigation",
            ),
        ]
    )
    gaps = compute_coverage_gaps(inventory, spec)
    assert {gap["status"] for gap in gaps if gap["duty"] in {"explain", "define"}} == {
        "unavailable"
    }


def test_same_inputs_are_sorted_and_budget_boundaries_are_deterministic() -> None:
    duties = _duties({"answer": "concepts", "recommend": "entities"})
    spec = _spec(page_families=["concepts", "entities"], duties=duties, max_pages=1, max_bytes=8)
    inventory = _inventory([_page("concept", "concepts", byte_size=8)])
    left = compute_coverage_gaps(inventory, spec)
    right = compute_coverage_gaps(copy.deepcopy(inventory), copy.deepcopy(spec))
    assert left == right
    selected = next(gap for gap in left if gap["duty"] == "recommend")
    assert selected["status"] == "over_budget"
    assert left == sorted(
        left,
        key=lambda gap: (
            gap["topic"],
            gap["scope"],
            gap["page_family"],
            gap["duty"],
            gap["status"],
            gap["reason"],
            gap["gap_id"],
        ),
    )


def test_oversize_and_unknown_records_fail_closed() -> None:
    spec = _spec()
    oversized = copy.deepcopy(spec)
    oversized["topics"] = ["x" * 129]
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(oversized)
    inventory = _inventory([_page("one", "concepts")])
    inventory["pages"][0]["unknown"] = True
    with pytest.raises(CoverageSpecError):
        compute_coverage_gaps(inventory, spec)
    malformed_gap = compute_coverage_gaps(_inventory(), _spec())[0]
    malformed_gap["status"] = "missing"
    with pytest.raises(CoverageSpecError):
        validate_coverage_gap(malformed_gap)


def test_missing_required_fields_with_optional_present_raise_coverage_error() -> None:
    spec = _spec()
    del spec["audit_head"]
    spec["transaction_id"] = "tx_1"
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(spec)

    inventory = _inventory()
    del inventory["generated_at"]
    inventory["extra"] = "still-not-valid"
    with pytest.raises(CoverageSpecError):
        compute_coverage_gaps(inventory, _spec())

    for builder in (
        lambda: {**_page("p", "concepts"), **{"optional": True}},
        lambda: {**_input("r"), **{"optional": True}},
        lambda: {**_edge("e"), **{"optional": True}},
    ):
        row = builder()
        row.pop("verified", None)
        with pytest.raises(CoverageSpecError):
            compute_coverage_gaps(
                _inventory(
                    [row] if "page_id" in row else [],
                    inputs=[row]
                    if "revision_id" in row
                    and "page_id" not in row
                    and "edge_id" not in row
                    else [],
                    edges=[row] if "edge_id" in row else [],
                ),
                _spec(),
            )


def test_explicit_false_verification_flags_are_unavailable() -> None:
    spec = _spec(page_families=["concepts"], duties=_duties({"answer": "concepts"}))
    inventory = _inventory([_page("page", "concepts", verified=False)])
    gap = next(gap for gap in compute_coverage_gaps(inventory, spec) if gap["duty"] == "answer")
    assert gap["status"] == "unavailable"

    guide_spec = _spec(page_families=["guides"], duties=_duties({"explain": "guides"}))
    guide_inventory = _inventory(
        [_page("guide", "guides", input_revision_refs=["input"], content_role="navigation")],
        inputs=[_input("input", verified=False)],
    )
    guide_gap = next(
        gap
        for gap in compute_coverage_gaps(guide_inventory, guide_spec)
        if gap["duty"] == "explain"
    )
    assert guide_gap["status"] == "unavailable"

    codemap_spec = _spec(page_families=["codemap"], duties=_duties({"define": "codemap"}))
    codemap_inventory = _inventory(
        [
            _page(
                "map",
                "codemap",
                input_revision_refs=["input"],
                edge_ids=["edge"],
                content_role="navigation",
            )
        ],
        inputs=[_input("input")],
        edges=[_edge("edge", registered=False)],
    )
    codemap_gap = next(
        gap
        for gap in compute_coverage_gaps(codemap_inventory, codemap_spec)
        if gap["duty"] == "define"
    )
    assert codemap_gap["status"] == "unavailable"


def test_audit_mismatch_and_gap_binding_mismatch_fail_closed() -> None:
    with pytest.raises(CoverageSpecError):
        compute_coverage_gaps(_inventory(audit_head="1" * 64), _spec())
    gap = compute_coverage_gaps(_inventory(), _spec())[0]
    gap["audit_binding"]["inventory_audit_head"] = "1" * 64
    with pytest.raises(CoverageSpecError):
        validate_coverage_gap(gap)
    gap = compute_coverage_gaps(_inventory(), _spec())[0]
    gap["transaction_head"] = "1" * 64
    with pytest.raises(CoverageSpecError):
        validate_coverage_gap(gap)


def test_scope_is_canonical_and_scope_wildcard_is_rejected() -> None:
    invalid_spec = _spec(scopes=["*"])
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(invalid_spec)
    invalid_page = _inventory([_page("p", "concepts")])
    invalid_page["pages"][0]["scope"] = "*"
    with pytest.raises(CoverageSpecError):
        compute_coverage_gaps(invalid_page, _spec())


def test_source_role_origin_and_legal_authority_combinations_fail_closed() -> None:
    for page in (
        _page(
            "evidence",
            "source_evidence",
            input_revision_refs=["input"],
            content_role="agent_derived_summary",
            origin="agent_derived",
        ),
        _page(
            "summary",
            "source_summary",
            input_revision_refs=["input"],
            content_role="agent_derived_summary",
            origin="agent_derived",
            legal_authority=True,
        ),
        _page(
            "concept",
            "concepts",
            content_role="source_evidence",
            origin="official",
        ),
        _page("agent_concept", "concepts", legal_authority=True),
    ):
        inventory = _inventory([page], inputs=[_input("input")])
        spec = _spec(page_families=[page["family"]], duties=_duties({"answer": page["family"]}))
        gap = next(gap for gap in compute_coverage_gaps(inventory, spec) if gap["duty"] == "answer")
        assert gap["status"] == "unavailable"


def test_large_unavailable_page_sets_are_digest_bound_and_inline_truncated() -> None:
    pages = [
        _page(f"page_{index:05d}", "concepts", verified=False)
        for index in range(5_000)
    ]
    spec = _spec(page_families=["concepts"], duties=_duties({"answer": "concepts"}))
    first = compute_coverage_gaps(_inventory(pages), spec)
    second = compute_coverage_gaps(_inventory(list(reversed(pages))), spec)
    gap = next(gap for gap in first if gap["duty"] == "answer")
    expected_ids = sorted(page["page_id"] for page in pages)
    assert gap == next(gap for gap in second if gap["duty"] == "answer")
    assert gap["observed_page_count"] == 5_000
    assert gap["observed_page_ids"] == expected_ids[:512]
    assert gap["observed_page_ids_truncated"] is True
    assert gap["observed_page_ids_sha256"] == sha256_bytes(
        canonical_json(expected_ids).encode("utf-8")
    )
    schema = json.loads(Path("contracts/living-wiki-coverage-gap.v1.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(gap)
    not_truncated = copy.deepcopy(gap)
    not_truncated["observed_page_ids_truncated"] = False
    with pytest.raises(CoverageSpecError):
        validate_coverage_gap(not_truncated)


def test_disabled_codemap_cannot_be_applicable_and_duty_aliases_are_rejected() -> None:
    spec = _spec(page_families=["codemap"], duties=_duties({"define": "codemap"}))
    spec["codemap"]["enabled"] = False
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(spec)

    alias = _spec()
    alias["duties"][0] = {"duty": "answer", "applicability": "applicable"}
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(alias)
    reasonless = _spec()
    reasonless["duties"][0] = {"duty": "answer", "applicability": "not_applicable"}
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(reasonless)


def test_gap_identity_and_required_counts_are_content_bound() -> None:
    gap = next(
        item
        for item in compute_coverage_gaps(
            _inventory(),
            _spec(page_families=["concepts"], duties=_duties({"answer": "concepts"})),
        )
        if item["duty"] == "answer"
    )
    for field, replacement in (
        ("observed_page_ids_sha256", "1" * 64),
        ("observed_bytes", 1),
        ("required_page_count", 2),
    ):
        tampered = copy.deepcopy(gap)
        tampered[field] = replacement
        with pytest.raises(CoverageSpecError):
            validate_coverage_gap(tampered)


def test_spec_references_are_closed_and_hierarchy_is_acyclic() -> None:
    unselected_root = _spec(page_families=["concepts"])
    unselected_root["hierarchy"]["roots"].append("entities")
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(unselected_root)

    unselected_tour = _spec(page_families=["concepts"])
    unselected_tour["guided_tours"].append(
        {"tour_id": "tour_1", "page_families": ["entities"]}
    )
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(unselected_tour)

    unselected_topic = _spec(page_families=["concepts"])
    unselected_topic["duties"][0]["topics"] = ["unselected"]
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(unselected_topic)

    cyclic = _spec(page_families=["concepts", "entities"])
    cyclic["hierarchy"]["edges"] = [
        {"parent": "concepts", "child": "entities"},
        {"parent": "entities", "child": "concepts"},
    ]
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(cyclic)

    draft = _spec()
    draft["status"] = "draft"
    with pytest.raises(CoverageSpecError):
        validate_coverage_spec(draft)


def test_page_inventory_rejects_projection_file_over_256_kib() -> None:
    oversized = _inventory([_page("large", "concepts", byte_size=262_145)])
    with pytest.raises(CoverageSpecError):
        compute_coverage_gaps(oversized, _spec())
