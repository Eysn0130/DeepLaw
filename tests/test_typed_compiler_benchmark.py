from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.typed_compiler.score import score_suite
from deeplaw.util import canonical_json, sha256_bytes


def test_typed_compiler_dev_fixture_scores_every_required_metric() -> None:
    repository = Path(__file__).resolve().parents[1]
    report = score_suite(repository / "benchmarks/typed_compiler/dev-fixture-v1.json")
    checked_report = json.loads(
        (
            repository
            / "benchmarks/typed_compiler/dev-fixture-report-2026-07-28.json"
        ).read_text()
    )
    schema = json.loads(
        (repository / "contracts/typed-compiler-benchmark.v1.schema.json").read_text()
    )
    input_schema = json.loads(
        (
            repository
            / "contracts/typed-compiler-benchmark-input.v1.schema.json"
        ).read_text()
    )
    suite = json.loads(
        (repository / "benchmarks/typed_compiler/dev-fixture-v1.json").read_text()
    )
    Draft202012Validator(input_schema).validate(suite)
    Draft202012Validator(schema).validate(report)
    assert report == checked_report

    assert report["case_count"] == 2
    assert report["counts"]["gold_claims"] == 2
    assert report["counts"]["predicted_claims"] == 3
    assert report["metrics"] == {
        "precision": pytest.approx(2 / 3),
        "recall": 1.0,
        "f1": pytest.approx(0.8),
        "hallucinated_claim_rate": pytest.approx(1 / 3),
        "unsupported_claim_rate": pytest.approx(1 / 3),
        "source_span_correctness": 1.0,
        "duplicate_claim_rate": 0.0,
        "review_acceptance_rate": pytest.approx(2 / 3),
        "cross_document_synthesis_correctness": 1.0,
    }
    body = {key: value for key, value in report.items() if key != "report_sha256"}
    assert report["report_sha256"] == sha256_bytes(canonical_json(body).encode())
    assert report["claim_eligible"] is False


def test_typed_compiler_scorer_rejects_cross_document_label_without_two_sources(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    value = json.loads(
        (repository / "benchmarks/typed_compiler/dev-fixture-v1.json").read_text()
    )
    value["cases"][0]["gold_claims"][0]["cross_document"] = True
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="cross_document conflicts"):
        score_suite(path)
