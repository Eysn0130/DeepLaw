from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts.run_living_wiki_host_harness import _not_executed_report

REPOSITORY = Path(__file__).resolve().parents[1]


def _validate(schema_name: str, value_path: Path) -> dict:
    schema = json.loads(
        (REPOSITORY / "contracts" / schema_name).read_text(encoding="utf-8")
    )
    value = json.loads(value_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    ).validate(value)
    return value


def test_living_wiki_benchmark_protocol_is_closed_and_not_claim_eligible() -> None:
    protocol = _validate(
        "living-wiki-benchmark-protocol.v1.schema.json",
        REPOSITORY / "benchmarks/living_wiki/protocol-v1.json",
    )
    assert protocol["status"] == "preregistered_not_executed"
    assert protocol["claim_policy"]["competitive_claim_eligible"] is False
    assert {item["comparator_id"] for item in protocol["comparators"]} == {
        "guanlan",
        "deeplaw_compiled_first",
        "deeplaw_source_fragment_fallback",
        "traditional_rag",
        "pure_embedding_retrieval",
        "graph_rag",
        "tolaria_exact_agent",
        "obsidian_exact_ai_plugin",
    }
    assert all(item["status"] == "not_executed" for item in protocol["comparators"])


def test_living_wiki_fixture_categories_match_the_protocol() -> None:
    protocol = json.loads(
        (REPOSITORY / "benchmarks/living_wiki/protocol-v1.json").read_text(
            encoding="utf-8"
        )
    )
    fixtures = _validate(
        "living-wiki-benchmark-fixtures.v1.schema.json",
        REPOSITORY / "benchmarks/living_wiki/fixtures-v1.json",
    )
    assert {item["category"] for item in fixtures["cases"]} == set(
        protocol["fixture_categories"]
    )
    assert len({item["case_id"] for item in fixtures["cases"]}) == len(fixtures["cases"])


def test_real_host_harness_reports_unavailable_tasks_as_not_executed() -> None:
    report = _not_executed_report(
        host="claude_code",
        host_version="unavailable",
        model_identity="unavailable",
        source_revision_id="sourcerev_0123456789abcdef01234567",
        network_policy="offline",
        reason="The external host was not invoked in core CI.",
    )
    schema = json.loads(
        (
            REPOSITORY
            / "contracts/real-host-compile-report.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    ).validate(report)
    assert report["status"] == "not_executed"
    assert report["executed"] is False
    assert report["competitive_claim_eligible"] is False
