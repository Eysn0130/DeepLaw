from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPOSITORY / "contracts/v013-quality-metric-catalog.v1.schema.json"
CATALOG_PATH = REPOSITORY / "benchmarks/v013/quality-metric-catalog-v1.json"

EXPECTED = {
    "retrieval": [
        "Recall@K",
        "User-visible Precision@K",
        "Target Identity Precision",
        "MRR",
        "nDCG",
        "Redundancy",
        "Context Coverage",
        "Duty Coverage",
    ],
    "grounding": [
        "Statement Evidence Binding",
        "Citation Validity",
        "Unsupported Statement Rate",
        "Source Coverage",
        "Exact Quote Validity",
    ],
    "living_wiki": [
        "Page Coverage",
        "Link Completeness",
        "Backlink Completeness",
        "Orphan Rate",
        "Gap Accuracy",
        "Freshness Accuracy",
        "Incremental Update Correctness",
        "Projection Reproducibility",
    ],
    "context": [
        "Compiled Hit",
        "Targeted Fallback",
        "Raw Fallback",
        "Duplicate Evidence",
        "RelevantChars/ContextChars",
        "Provider Payload",
        "Token Savings",
        "Repeated-query Reuse",
    ],
    "agent": [
        "Task Accuracy",
        "Manual Correction",
        "Tool Calls",
        "Time",
        "Tokens",
        "Failure/Recovery",
        "Cross-session Reuse",
    ],
    "authoritative": [
        "Definition",
        "Exception",
        "Proviso",
        "Temporal",
        "Cross-reference",
        "Wrong Version",
        "Correct Gap",
        "False Authority Admission",
    ],
}


def _load() -> tuple[dict, dict]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    catalogue = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return schema, catalogue


def test_catalogue_validates_against_draft_2020_12_schema() -> None:
    schema, catalogue = _load()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(catalogue)


def test_closed_44_name_inventory_and_category_counts() -> None:
    _, catalogue = _load()
    assert catalogue["metric_count"] == 44
    assert catalogue["category_order"] == list(EXPECTED)
    assert catalogue["category_counts"] == {key: len(value) for key, value in EXPECTED.items()}
    actual = {
        category: [
            metric["display_name"]
            for metric in catalogue["metrics"]
            if metric["category"] == category
        ]
        for category in EXPECTED
    }
    assert actual == EXPECTED
    assert len({metric["metric_id"] for metric in catalogue["metrics"]}) == 44
    assert all(
        re.fullmatch(r"[a-z][a-z0-9_]{2,99}", metric["metric_id"])
        for metric in catalogue["metrics"]
    )


def test_each_metric_has_denominator_scope_gold_direction_and_unexecuted_semantics() -> None:
    _, catalogue = _load()
    assert Counter(metric["category"] for metric in catalogue["metrics"]) == Counter(
        {category: len(names) for category, names in EXPECTED.items()}
    )
    allowed_gold = {
        "human_confirmed",
        "expert_confirmed",
        "public_source_free",
        "synthetic",
        "absent",
        "review_pending",
    }
    for metric in catalogue["metrics"]:
        denominator = metric["denominator"]
        assert denominator["unit"]
        assert denominator["definition"]
        assert denominator["zero_denominator_semantics"]
        assert set(metric["scope"]) == {"corpus", "task", "host", "projection", "authority_plane"}
        assert all(metric["scope"][key] for key in metric["scope"])
        assert metric["gold_status"] in allowed_gold
        assert metric["gold_status"] == "absent"
        assert metric["direction"] in {"higher", "lower"}
        assert metric["measurement_status"] in {"not_executed", "review_pending"}
        assert metric["measurement_status"] == "not_executed"
        assert metric["not_executed_semantics"]


def test_claim_policy_and_result_fields_fail_closed() -> None:
    _, catalogue = _load()
    assert catalogue["source_free"] is True
    assert catalogue["claim_eligible"] is False
    assert catalogue["competitive_claim_eligible"] is False
    assert catalogue["status"] == "definition_only"
    assert catalogue["measurement_policy"]["allowed_measurement_statuses"] == [
        "not_executed",
        "review_pending",
    ]
    for metric in catalogue["metrics"]:
        assert "score" not in metric
        assert "value" not in metric
        assert metric["measurement_status"] != "pass"


def test_product_code_does_not_import_catalogue_or_benchmark_artifacts() -> None:
    forbidden = re.compile(
        r"(?:benchmarks(?:[./]|$)|quality-metric-catalog-v1|v013-quality-metric-catalog)"
    )
    offenders: list[str] = []
    for path in (REPOSITORY / "src/deeplaw").rglob("*.py"):
        if forbidden.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(REPOSITORY)))
    assert offenders == []
