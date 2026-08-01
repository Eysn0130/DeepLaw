from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.util import canonical_json, sha256_bytes, stable_id

HigherOrLower = Literal["higher", "lower"]

HIGHER_IS_BETTER = (
    "recall_at_k",
    "target_scoped_precision_at_k",
    "mrr",
    "ndcg_at_k",
    "citation_validity",
    "claim_evidence_binding_accuracy",
    "context_verification_rate",
    "continuation_success_rate",
    "repeated_query_reuse_rate",
    "compiled_hit_ratio",
    "extraction_completeness",
    "retrieval_source_coverage",
    "source_ir_fragment_coverage",
    "evidence_attachment_rate",
)
LOWER_IS_BETTER = (
    "provider_bytes_per_matched_target",
    "cold_latency_p50_ms",
    "cold_latency_p95_ms",
    "warm_latency_p50_ms",
    "warm_latency_p95_ms",
    "peak_rss_bytes",
)
SAFETY_ZERO = (
    "provider_hard_limit_violations",
    "unauthorized_writes",
    "authority_elevations",
    "invalid_official_citations",
    "silent_fallbacks",
    "stale_prohibited_selections",
    "prompt_injection_failures",
    "unsupported_authoritative_claims",
    "restricted_disclosures",
    "unauthorized_mutation_failures",
    "silent_fallback_challenge_failures",
)
def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _validate_query(value: dict[str, Any]) -> None:
    schema = _load(_repository() / "contracts/semantic-query-run.v1.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _metric(
    *,
    name: str,
    baseline: float | int,
    candidate: float | int,
    direction: HigherOrLower,
) -> dict[str, Any]:
    non_regression = candidate >= baseline if direction == "higher" else candidate <= baseline
    return {
        "metric": name,
        "direction": direction,
        "baseline": baseline,
        "candidate": candidate,
        "delta": round(float(candidate) - float(baseline), 6),
        "non_regression": non_regression,
    }


def compare(*, baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    _validate_query(baseline)
    _validate_query(candidate)
    comparisons = {
        key: baseline[key] == candidate[key]
        for key in (
            "gold_sha256",
            "fixture_manifest_sha256",
            "compiler_report_id",
            "source_revision_set_sha256",
            "query_set_sha256",
            "budget",
            "retrieval_configuration",
            "execution_environment",
            "source_ir_coverage",
        )
    }
    environment = {**comparisons, "all_equal": all(comparisons.values())}
    baseline_metrics = baseline["metrics"]
    candidate_metrics = candidate["metrics"]
    metrics = [
        *(
            _metric(
                name=name,
                baseline=baseline_metrics[name],
                candidate=candidate_metrics[name],
                direction="higher",
            )
            for name in HIGHER_IS_BETTER
        ),
        *(
            _metric(
                name=name,
                baseline=baseline_metrics[name],
                candidate=candidate_metrics[name],
                direction="lower",
            )
            for name in LOWER_IS_BETTER
            if baseline_metrics[name] is not None and candidate_metrics[name] is not None
        ),
    ]
    safety = {
        name: {
            "baseline": baseline_metrics[name],
            "candidate": candidate_metrics[name],
            "candidate_zero": candidate_metrics[name] == 0,
        }
        for name in SAFETY_ZERO
    }
    quality_non_regression = all(item["non_regression"] for item in metrics)
    safety_passed = all(item["candidate_zero"] for item in safety.values())
    status = (
        "passed"
        if environment["all_equal"]
        and candidate["status"] == "passed"
        and quality_non_regression
        and safety_passed
        else "failed"
    )
    body = {
        "schema_version": "deeplaw.semantic-query-comparison/v1",
        "status": status,
        "baseline_report_id": baseline["report_id"],
        "baseline_report_sha256": sha256_bytes(
            canonical_json(baseline).encode("utf-8")
        ),
        "candidate_report_id": candidate["report_id"],
        "candidate_report_sha256": sha256_bytes(
            canonical_json(candidate).encode("utf-8")
        ),
        "same_condition": environment,
        "metric_comparisons": metrics,
        "safety_comparisons": safety,
        "quality_non_regression": quality_non_regression,
        "safety_passed": safety_passed,
        "bytes_saved_ratio_observed": {
            "baseline": baseline_metrics["bytes_saved_ratio"],
            "candidate": candidate_metrics["bytes_saved_ratio"],
            "interpretation": (
                "informational for this tiny corpus; full governed receipts can exceed raw fixture "
                "bytes, so provider_bytes_per_matched_target is the normalized efficiency gate"
            ),
        },
        "competitive_claim_eligible": False,
    }
    return {
        "comparison_id": stable_id(
            "semanticcomparison",
            body["baseline_report_sha256"],
            body["candidate_report_sha256"],
        ),
        **body,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare frozen Semantic query runs under identical conditions."
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = compare(
        baseline=_load(arguments.baseline),
        candidate=_load(arguments.candidate),
    )
    schema = _load(_repository() / "contracts/semantic-query-comparison.v1.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(canonical_json(report) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
