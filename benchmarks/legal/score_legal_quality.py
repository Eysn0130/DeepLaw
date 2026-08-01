from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from benchmarks.legal.review_held_out import validate_candidate
from deeplaw.util import canonical_json, stable_id

THRESHOLDS = {
    "available_duty_recall": 0.90,
    "false_covered_duty_rate_max": 0.0,
    "correct_gap_precision": 1.0,
    "correct_gap_recall": 1.0,
    "exception_recall": 0.90,
    "definition_recall": 0.90,
    "temporal_challenge_recall": 0.90,
    "cross_reference_recall": 0.90,
    "exact_citation_pass_rate": 1.0,
    "receipt_verification_rate": 1.0,
    "false_authority_admission_rate_max": 0.0,
    "temporal_false_inclusion_rate_max": 0.0,
    "temporal_false_exclusion_rate_max": 0.0,
}


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _validate_run(value: dict[str, Any]) -> None:
    contract_dir = _repository() / "contracts"
    schema = _load(contract_dir / "legal-quality-run.v1.schema.json")
    Draft202012Validator.check_schema(schema)
    citation = _load(contract_dir / "citation-audit.v1.schema.json")
    registry = Registry().with_resource(
        citation["$id"], Resource.from_contents(citation)
    )
    Draft202012Validator(
        schema, registry=registry, format_checker=FormatChecker()
    ).validate(value)


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator / denominator), 6) if denominator else 1.0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 6)


def score(*, gold: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    gold_sha256 = validate_candidate(gold)
    if gold["status"] != "expert_confirmed":
        raise ValueError("formal legal scoring requires expert-confirmed held-out Gold")
    _validate_run(run)
    if run["gold_id"] != gold["gold_id"]:
        raise ValueError("legal quality run does not bind the selected Gold")
    expected = {case["case_id"]: case for case in gold["cases"]}
    actual = {case["case_id"]: case for case in run["cases"]}
    if set(actual) != set(expected):
        raise ValueError("legal quality run case set must exactly match Gold")

    available_required = 0
    available_covered = 0
    covered_total = 0
    falsely_covered = 0
    expected_gap_cases = 0
    reported_gap_cases = 0
    correct_gap_cases = 0
    category_required: dict[str, int] = {
        "exception": 0, "definition": 0, "temporal": 0, "cross_reference": 0
    }
    category_satisfied = dict.fromkeys(category_required, 0)
    citation_checks: list[dict[str, Any]] = []
    false_authority = 0
    authority_negative = 0
    temporal_should_exclude = 0
    temporal_false_inclusion = 0
    temporal_should_include = 0
    temporal_false_exclusion = 0
    hit_ranks: list[int | None] = []
    failures: list[str] = []

    category_mapping = {
        "exception": "exception",
        "proviso": "exception",
        "definition": "definition",
        "historical_version": "temporal",
        "effective_date": "temporal",
        "amendment": "temporal",
        "repeal": "temporal",
        "cross_reference": "cross_reference",
    }
    challenge_mapping = {
        "exception": "exception_challenge",
        "definition": "definition_challenge",
        "temporal": "temporal_challenge",
        "cross_reference": "cross_reference_challenge",
    }
    for case_id, gold_case in expected.items():
        result = actual[case_id]
        required = set(gold_case["expected"]["required_duties"])
        covered = set(result["covered_duties"])
        available = gold_case["answerability"] == "duty_evidence_available"
        if available:
            available_required += len(required)
            available_covered += len(required & covered)
        covered_total += len(covered)
        if not available:
            falsely_covered += len(covered)
        expected_gap = gold_case["answerability"] in {
            "duty_evidence_uncertain", "duty_not_in_corpus", "duty_not_applicable"
        }
        reported_gap = bool(result["gaps"])
        expected_gap_cases += int(expected_gap)
        reported_gap_cases += int(reported_gap)
        correct_gap_cases += int(expected_gap and reported_gap)
        if expected_gap and gold_case["expected"]["blocking_gap"] not in result["gaps"]:
            failures.append(f"{case_id}: expected blocking gap was not reported")
        if not expected_gap and reported_gap:
            failures.append(f"{case_id}: unexpected blocking gap was reported")

        quality_category = category_mapping.get(gold_case["category"])
        if quality_category and available:
            category_required[quality_category] += 1
            challenges = {
                item["challenge_type"]: item["result"]
                for item in result["challenge_results"]
            }
            if challenges.get(challenge_mapping[quality_category]) == "satisfied":
                category_satisfied[quality_category] += 1

        citation_checks.extend(result["citation_audits"])
        authority_negative += int(gold_case["category"] == "private_not_official")
        false_authority += int(
            gold_case["category"] == "private_not_official"
            and result["authority_admitted"]
        )
        if gold_case["category"] == "repeal":
            temporal_should_exclude += 1
            temporal_false_inclusion += int(result["temporal_included"])
        elif quality_category == "temporal" and available:
            temporal_should_include += 1
            temporal_false_exclusion += int(result["temporal_excluded"])
        if available:
            hit_ranks.append(result["hit_rank"])

    deterministic_passes = sum(
        item["deterministic_pass"] for item in citation_checks
    )
    receipt_passes = sum(
        item["checks"]["receipt"]["passed"] for item in citation_checks
    )
    hit_at_1 = _ratio(sum(rank == 1 for rank in hit_ranks), len(hit_ranks))
    mrr = _ratio(sum(0.0 if rank is None else 1.0 / rank for rank in hit_ranks), len(hit_ranks))
    total_relevant = sum(case["relevant_chars"] for case in actual.values())
    total_context = sum(case["context_chars"] for case in actual.values())
    metrics = {
        "available_duty_recall": _ratio(available_covered, available_required),
        "false_covered_duty_rate": _ratio(falsely_covered, covered_total),
        "correct_gap_precision": _ratio(correct_gap_cases, reported_gap_cases),
        "correct_gap_recall": _ratio(correct_gap_cases, expected_gap_cases),
        "exception_recall": _ratio(category_satisfied["exception"], category_required["exception"]),
        "definition_recall": _ratio(
            category_satisfied["definition"], category_required["definition"]
        ),
        "temporal_challenge_recall": _ratio(
            category_satisfied["temporal"], category_required["temporal"]
        ),
        "cross_reference_recall": _ratio(
            category_satisfied["cross_reference"],
            category_required["cross_reference"],
        ),
        "exact_citation_pass_rate": _ratio(deterministic_passes, len(citation_checks)),
        "receipt_verification_rate": _ratio(receipt_passes, len(citation_checks)),
        "false_authority_admission_rate": _ratio(false_authority, authority_negative),
        "temporal_false_inclusion_rate": _ratio(temporal_false_inclusion, temporal_should_exclude),
        "temporal_false_exclusion_rate": _ratio(temporal_false_exclusion, temporal_should_include),
        "relevant_chars_per_context_chars": _ratio(total_relevant, total_context),
        "hit_at_1": hit_at_1,
        "mrr": mrr,
        "latency_p95_ms": _p95([float(case["latency_ms"]) for case in actual.values()]),
    }
    for key, threshold in THRESHOLDS.items():
        if key.endswith("_max"):
            metric_key = key.removesuffix("_max")
            if metrics[metric_key] > threshold:
                failures.append(f"{metric_key} exceeds {threshold}")
        elif metrics[key] < threshold:
            failures.append(f"{key} is below {threshold}")
    run_sha256 = hashlib.sha256(canonical_json(run).encode("utf-8")).hexdigest()
    status = "passed" if not failures else "failed"
    return {
        "schema_version": "deeplaw.legal-quality-report/v1",
        "report_id": stable_id("legalreport", gold["gold_id"], run_sha256),
        "gold_id": gold["gold_id"],
        "gold_sha256": gold_sha256,
        "release_id": run["release_id"],
        "run_sha256": run_sha256,
        "status": status,
        "formal_release_eligible": status == "passed",
        "metrics": metrics,
        "thresholds": THRESHOLDS,
        "failures": sorted(set(failures)),
        "limitations": [
            "Semantic entailment is never treated as a deterministic citation check.",
            "A passed report binds one expert-confirmed Gold digest and one immutable release.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score an answerability-aware legal run")
    parser.add_argument("gold", type=Path)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = score(gold=_load(arguments.gold), run=_load(arguments.run))
    rendered = canonical_json(report) + "\n"
    if arguments.output:
        arguments.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
