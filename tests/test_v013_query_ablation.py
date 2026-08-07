from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.v013.query_ablation import (
    _FIXTURE_ASSETS,
    CORPUS_FILENAME,
    SCHEMA_VERSION,
    build_query_ablation_report,
    verify_query_ablation_corpus,
    verify_query_ablation_report,
)
from deeplaw.util import normalize_query_text

REPOSITORY = Path(__file__).resolve().parents[1]
CORPUS_PATH = REPOSITORY / "benchmarks/v013" / CORPUS_FILENAME
SCHEMA_PATH = REPOSITORY / "contracts/query-ablation-report.v1.schema.json"
RUNNER_PATH = REPOSITORY / "benchmarks/v013/query_ablation.py"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_source_free_corpus_is_closed_and_has_no_v2_alias_terms() -> None:
    corpus = _read_json(CORPUS_PATH)
    receipt = verify_query_ablation_corpus(corpus)

    assert receipt == {
        "corpus_id": "v013-heldout-query-ablation-v1",
        "corpus_sha256": corpus["corpus_sha256"],
        "query_count": 8,
        "positive_query_count": 5,
        "negative_query_count": 3,
    }
    assert corpus["source_free"] is True
    assert all(item["negative"] is (not item["expected_ids"]) for item in corpus["queries"])


def test_held_out_queries_are_paraphrases_not_fixture_text_substrings() -> None:
    corpus = _read_json(CORPUS_PATH)
    fixture_texts = {
        normalize_query_text(text).casefold()
        for fixture in _FIXTURE_ASSETS
        for text in (fixture["title"], fixture["statement"])
    }
    for item in corpus["queries"]:
        query = normalize_query_text(item["query"]).casefold()
        assert query not in fixture_texts
        assert all(query not in fixture_text for fixture_text in fixture_texts)


def test_report_is_schema_valid_source_free_and_explicit_about_channel_statuses() -> None:
    report = build_query_ablation_report()
    schema = _read_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["competitive_claim_eligible"] is False
    assert report["corpus"]["source_free"] is True
    assert "headline metric deltas are zero" in " ".join(report["limitations"])
    assert verify_query_ablation_report(report)["valid"] is True

    variants = {item["variant_id"]: item for item in report["variants"]}
    assert set(variants) == {
        "expansion_on",
        "expansion_off",
        "lexical_only",
        "dense_only",
        "graph_only",
        "hybrid",
        "compiled_first",
        "targeted_evidence_fallback",
    }
    assert variants["expansion_on"]["status"] == "executed"
    assert variants["expansion_off"]["status"] == "executed"
    assert (
        variants["expansion_on"]["metrics"]["recall_at_k"]
        == variants["expansion_off"]["metrics"]["recall_at_k"]
    )
    assert (
        variants["expansion_on"]["metrics"]["precision_at_k"]
        == variants["expansion_off"]["metrics"]["precision_at_k"]
    )
    assert variants["expansion_on"]["metrics"]["throughput_qps"]["mean"] > 0
    assert variants["hybrid"]["execution_status"] == "degraded"
    assert variants["hybrid"]["degraded_reasons"]
    assert "dense" not in variants["hybrid"]["observed_channels"]
    assert "graph" not in variants["hybrid"]["observed_channels"]
    assert variants["graph_only"]["status"] == "not_executed"
    assert "not counted as graph execution" in variants["graph_only"]["not_executed_reason"]
    for variant_id in ("dense_only", "graph_only", "compiled_first", "targeted_evidence_fallback"):
        variant = variants[variant_id]
        assert variant["status"] == "not_executed"
        assert variant["execution_status"] == "not_executed"
        assert variant["not_executed_reason"]
        assert variant["per_query"] == []
        assert variant["metrics"]["token_proxy"]["method"] == "not_executed"
    assert variants["compiled_first"]["calibration"]["execution_status"] == "executed"
    assert variants["compiled_first"]["calibration"]["observed_channels"] == [
        "compiled_knowledge"
    ]
    assert variants["targeted_evidence_fallback"]["calibration"]["observed_channels"] == [
        "raw_fragment_fallback",
        "source_evidence",
    ]
    for variant in variants.values():
        assert set(variant["metrics"]) >= {
            "recall_at_k",
            "precision_at_k",
            "false_positive_rate",
            "latency_ms",
            "token_proxy",
        }
        assert "execution_status" in variant
        assert "observed_channels" in variant


def test_report_verifier_rejects_tampering_and_unknown_fields() -> None:
    report = build_query_ablation_report()

    tampered = dict(report)
    tampered["competitive_claim_eligible"] = True
    assert verify_query_ablation_report(tampered)["valid"] is False

    unknown = dict(report)
    unknown["unexpected"] = True
    assert verify_query_ablation_report(unknown)["valid"] is False


def test_cli_verify_reopens_the_written_report(tmp_path: Path) -> None:
    from benchmarks.v013.query_ablation import main

    output = tmp_path / "query-ablation.json"
    assert main(["--output", str(output), "--verify"]) == 0
    persisted = _read_json(output)
    assert verify_query_ablation_report(persisted)["valid"] is True


def test_runner_imports_only_product_public_retrieval_and_fixture_support() -> None:
    tree = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    assert "deeplaw.retrieval_fabric" in imports
    assert "deeplaw.knowledge_store" in imports
    assert not any(
        forbidden in module
        for module in imports
        for forbidden in ("Gold", "scorer", "benchmark", "source", "model")
    )


@pytest.mark.parametrize("field", ["report_sha256", "corpus_sha256"])
def test_digest_fields_are_hex_sha256(field: str) -> None:
    report = build_query_ablation_report()
    value = report["report_sha256"] if field == "report_sha256" else report["corpus"][field]
    assert isinstance(value, str)
    assert len(value) == 64
    assert all(character in "0123456789abcdef" for character in value)
