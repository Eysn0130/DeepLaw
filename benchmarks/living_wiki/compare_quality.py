from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_VERSION = "deeplaw.living-wiki-quality-comparison/v1"
REPORT_SCHEMA_VERSION = "deeplaw.living-wiki-quality-report/v1"
FUNCTIONAL_METRICS = (
    "recall_at_k",
    "precision_at_k",
    "mrr",
    "ndcg",
    "citation_validity",
    "claim_evidence_binding_accuracy",
    "source_coverage",
    "stale_selection_prevention",
    "evidence_attachment_rate",
    "repeated_query_reuse_rate",
    "context_bytes_saved_vs_raw_ratio",
)
PERFORMANCE_METRICS = (
    ("retrieval", "cold_latency_ms_p50"),
    ("retrieval", "cold_latency_ms_p95"),
    ("retrieval", "warm_latency_ms_p50"),
    ("retrieval", "warm_latency_ms_p95"),
    ("compilation", "first_compilation_latency_ms"),
    ("compilation", "incremental_refresh_latency_ms"),
    ("compilation", "rebuild_latency_ms"),
)
SECURITY_COUNTERS = (
    "unauthorized_disclosure",
    "silent_fallback",
    "stale_prohibited_selection",
    "invalid_official_citation",
    "provider_hard_limit_violation",
    "authority_elevation_by_ranking_or_model",
)


class ComparisonError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _load_report(path: Path, schema: dict[str, Any]) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ComparisonError(
            "input_unavailable_or_invalid",
            f"quality report is unavailable or invalid: {path.name}",
        ) from error
    Draft202012Validator(schema).validate(report)
    body = {key: value for key, value in report.items() if key != "record_sha256"}
    if report.get("record_sha256") != _digest(body):
        raise ComparisonError(
            "input_digest_invalid",
            f"quality report digest is invalid: {path.name}",
        )
    return report


def _same_environment(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    fields = (
        "platform_system",
        "platform_release",
        "machine",
        "processor",
        "logical_cpu_count",
        "python_version",
        "sqlite_version",
        "network_policy",
    )
    return all(
        baseline["environment"].get(field) == candidate["environment"].get(field)
        for field in fields
    )


def _source_bytes_inventory_sha256(report: dict[str, Any]) -> str:
    records = report.get("corpus", {}).get("records")
    if not isinstance(records, list) or not records:
        raise ComparisonError(
            "source_inventory_missing",
            "quality report has no source-byte inventory",
        )
    bounded: list[dict[str, str]] = []
    for item in records:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(field), str)
            for field in ("label", "immutable_bytes_sha256", "media_type")
        ):
            raise ComparisonError(
                "source_inventory_invalid",
                "quality report source-byte inventory is invalid",
            )
        bounded.append(
            {
                "label": item["label"],
                "immutable_bytes_sha256": item["immutable_bytes_sha256"],
                "media_type": item["media_type"],
            }
        )
    return hashlib.sha256(
        _canonical_json(sorted(bounded, key=lambda item: item["label"])).encode(
            "utf-8"
        )
    ).hexdigest()


def _higher_comparison(
    metric: str,
    baseline: float,
    candidate: float,
) -> dict[str, Any]:
    passed = candidate >= baseline
    if not passed:
        raise ComparisonError(
            "functional_quality_regression",
            f"functional quality regressed: {metric}",
        )
    return {
        "metric": metric,
        "baseline": baseline,
        "candidate": candidate,
        "direction": "higher_or_equal",
        "tolerance": 0,
        "passed": True,
    }


def _latency_comparison(
    metric: str,
    baseline: float,
    candidate: float,
) -> dict[str, Any]:
    tolerance = max(100.0, baseline * 0.20)
    passed = candidate <= baseline + tolerance
    if not passed:
        raise ComparisonError(
            "performance_regression",
            f"performance regressed beyond the frozen tolerance: {metric}",
        )
    return {
        "metric": metric,
        "baseline": baseline,
        "candidate": candidate,
        "direction": "lower_or_equal",
        "tolerance": tolerance,
        "passed": True,
    }


def compare(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    baseline_source_bytes = _source_bytes_inventory_sha256(baseline)
    candidate_source_bytes = _source_bytes_inventory_sha256(candidate)
    if (
        baseline.get("schema_version") != REPORT_SCHEMA_VERSION
        or candidate.get("schema_version") != REPORT_SCHEMA_VERSION
        or baseline.get("candidate", {}).get("role") != "baseline"
        or candidate.get("candidate", {}).get("role")
        not in {"candidate", "fresh_wheel", "formal_release"}
        or candidate.get("passed") is not True
        or candidate.get("failures") != []
        or candidate.get("competitive_claim_eligible") is not False
        or baseline.get("competitive_claim_eligible") is not False
        or baseline.get("suite", {}).get("suite_sha256")
        != candidate.get("suite", {}).get("suite_sha256")
        or baseline.get("suite", {}).get("runner_sha256")
        != candidate.get("suite", {}).get("runner_sha256")
        or baseline_source_bytes != candidate_source_bytes
        or baseline.get("configuration") != candidate.get("configuration")
        or not _same_environment(baseline, candidate)
    ):
        raise ComparisonError(
            "frozen_experiment_mismatch",
            "baseline and candidate are not the same frozen quality experiment"
        )
    functional = [
        _higher_comparison(
            metric,
            float(baseline["retrieval"][metric]),
            float(candidate["retrieval"][metric]),
        )
        for metric in FUNCTIONAL_METRICS
    ]
    performance = [
        _latency_comparison(
            metric,
            float(baseline[section][metric]),
            float(candidate[section][metric]),
        )
        for section, metric in PERFORMANCE_METRICS
    ]
    candidate_security = candidate["security"]
    if (
        any(candidate_security.get(field) != 0 for field in SECURITY_COUNTERS)
        or candidate_security.get("unauthorized_write_rejected") is not True
        or not all(
            value is True
            for value in candidate.get("retrieval", {})
            .get("gate_checks", {})
            .values()
        )
    ):
        raise ComparisonError(
            "candidate_gate_incomplete",
            "candidate security or quality gate is incomplete",
        )
    report = {
        "schema_version": SCHEMA_VERSION,
        "suite": {
            "suite_id": candidate["suite"]["suite_id"],
            "suite_sha256": candidate["suite"]["suite_sha256"],
            "runner_sha256": candidate["suite"]["runner_sha256"],
            "source_bytes_inventory_sha256": candidate_source_bytes,
        },
        "environment_match": True,
        "baseline": {
            "candidate": baseline["candidate"],
            "report_record_sha256": baseline["record_sha256"],
            "passed": baseline["passed"],
            "failure_codes": sorted(
                str(item.get("code")) for item in baseline["failures"]
            ),
        },
        "candidate": {
            "candidate": candidate["candidate"],
            "report_record_sha256": candidate["record_sha256"],
            "passed": True,
        },
        "functional_comparisons": functional,
        "performance_comparisons": performance,
        "security": {
            **{field: candidate_security[field] for field in SECURITY_COUNTERS},
            "unauthorized_write_rejected": True,
        },
        "quality_regression": False,
        "performance_regression": False,
        "passed": True,
        "competitive_claim_eligible": False,
    }
    report["record_sha256"] = _digest(report)
    return report


def _comparison_row(
    metric: str,
    baseline: float,
    candidate: float,
    *,
    direction: str,
) -> dict[str, Any]:
    tolerance = 0.0 if direction == "higher_or_equal" else max(100.0, baseline * 0.20)
    passed = (
        candidate >= baseline
        if direction == "higher_or_equal"
        else candidate <= baseline + tolerance
    )
    return {
        "metric": metric,
        "baseline": baseline,
        "candidate": candidate,
        "direction": direction,
        "tolerance": tolerance,
        "passed": passed,
    }


def _failed_comparison_receipt(
    *,
    error: ComparisonError,
    baseline_path: Path,
    candidate_path: Path,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    functional = [
        _comparison_row(
            metric,
            float(baseline["retrieval"][metric]),
            float(candidate["retrieval"][metric]),
            direction="higher_or_equal",
        )
        for metric in FUNCTIONAL_METRICS
    ]
    performance = [
        _comparison_row(
            metric,
            float(baseline[section][metric]),
            float(candidate[section][metric]),
            direction="lower_or_equal",
        )
        for section, metric in PERFORMANCE_METRICS
    ]

    def input_descriptor(role: str, path: Path, report: dict[str, Any]) -> dict[str, Any]:
        payload = path.read_bytes()
        return {
            "role": role,
            "relative_path": path.name,
            "byte_size": len(payload),
            "file_sha256": hashlib.sha256(payload).hexdigest(),
            "report_record_sha256": report["record_sha256"],
        }

    receipt = {
        "schema_version": "deeplaw.living-wiki-quality-comparison-failure/v1",
        "failure_code": error.code,
        "inputs": [
            input_descriptor("baseline", baseline_path, baseline),
            input_descriptor("candidate", candidate_path, candidate),
        ],
        "functional_comparisons": functional,
        "performance_comparisons": performance,
        "quality_regression": any(not row["passed"] for row in functional),
        "performance_regression": any(not row["passed"] for row in performance),
        "passed": False,
        "competitive_claim_eligible": False,
    }
    receipt["record_sha256"] = _digest(receipt)
    return receipt


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Compare same-condition Living Wiki baseline and candidate reports."
    )
    parser.add_argument("--repository", type=Path, default=repository)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    baseline_report: dict[str, Any] | None = None
    candidate_report: dict[str, Any] | None = None
    try:
        selected_repository = arguments.repository.resolve(strict=True)
        report_schema = json.loads(
            (
                selected_repository
                / "contracts/living-wiki-quality-report.v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        comparison_schema = json.loads(
            (
                selected_repository
                / "contracts/living-wiki-quality-comparison.v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(report_schema)
        Draft202012Validator.check_schema(comparison_schema)
        baseline_path = arguments.baseline.resolve(strict=True)
        candidate_path = arguments.candidate.resolve(strict=True)
        baseline_report = _load_report(baseline_path, report_schema)
        candidate_report = _load_report(candidate_path, report_schema)
        report = compare(baseline_report, candidate_report)
        Draft202012Validator(comparison_schema).validate(report)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except ComparisonError as error:
        if baseline_report is not None and candidate_report is not None:
            failure_schema = json.loads(
                (
                    selected_repository
                    / "contracts/living-wiki-quality-comparison-failure.v1.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(failure_schema)
            receipt = _failed_comparison_receipt(
                error=error,
                baseline_path=baseline_path,
                candidate_path=candidate_path,
                baseline=baseline_report,
                candidate=candidate_report,
            )
            Draft202012Validator(failure_schema).validate(receipt)
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(str(error), file=sys.stderr)
        return 1
    except (
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
