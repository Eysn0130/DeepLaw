from __future__ import annotations

import argparse
import random
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Literal

from jsonschema import Draft202012Validator

from benchmarks.semantic.compare_query_runs import (
    HIGHER_IS_BETTER,
    SAFETY_ZERO,
    _load,
    _repository,
    _validate_query,
)
from deeplaw.util import canonical_json, sha256_bytes, stable_id

Role = Literal["baseline", "candidate"]
Direction = Literal["higher", "lower"]

EXECUTION_ORDER: tuple[Role, ...] = (
    "baseline",
    "candidate",
    "candidate",
    "baseline",
    "baseline",
    "candidate",
)
PAIR_POSITIONS = ((0, 1), (3, 2), (4, 5))
BOOTSTRAP_RESAMPLES = 10_000
CONFIDENCE_LEVEL = 0.95

STRICT_LOWER_IS_BETTER = ("provider_bytes_per_matched_target",)
PERFORMANCE_STATISTICS: tuple[
    tuple[str, str, Callable[[Sequence[int]], float]], ...
] = (
    ("cold_latency_p50_ms", "cold_latency_ms", lambda values: float(median(values))),
    ("cold_latency_p95_ms", "cold_latency_ms", lambda values: float(_percentile(values, 0.95))),
    ("warm_latency_p50_ms", "warm_latency_ms", lambda values: float(median(values))),
    ("warm_latency_p95_ms", "warm_latency_ms", lambda values: float(_percentile(values, 0.95))),
    ("peak_rss_bytes", "peak_rss_bytes", lambda values: float(max(values))),
)


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise ValueError("a percentile requires at least one observation")
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, int((len(ordered) - 1) * fraction + 0.5)),
    )
    return float(ordered[index])


def _report_sha256(value: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _recorded_at(value: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(value["recorded_at"].replace("Z", "+00:00"))


def _same_condition(runs: Sequence[dict[str, Any]]) -> dict[str, bool]:
    anchor = runs[0]
    comparisons = {
        key: all(run[key] == anchor[key] for run in runs[1:])
        for key in (
            "gold_sha256",
            "fixture_manifest_sha256",
            "source_revision_set_sha256",
            "query_set_sha256",
            "budget",
            "retrieval_configuration",
            "execution_environment",
        )
    }
    return {**comparisons, "all_equal": all(comparisons.values())}


def _median_metric(
    *,
    name: str,
    baseline: Sequence[float | int],
    candidate: Sequence[float | int],
    direction: Direction,
) -> dict[str, Any]:
    baseline_median = float(median(baseline))
    candidate_median = float(median(candidate))
    non_regression = (
        candidate_median >= baseline_median
        if direction == "higher"
        else candidate_median <= baseline_median
    )
    return {
        "metric": name,
        "direction": direction,
        "baseline_values": list(baseline),
        "candidate_values": list(candidate),
        "baseline_median": baseline_median,
        "candidate_median": candidate_median,
        "delta": round(candidate_median - baseline_median, 6),
        "non_regression": non_regression,
    }


def _bootstrap_metric(
    *,
    name: str,
    baseline: Sequence[int],
    candidate: Sequence[int],
    statistic: Callable[[Sequence[int]], float],
    seed_material: str,
) -> dict[str, Any]:
    if not baseline or len(baseline) != len(candidate):
        raise ValueError(f"{name} requires equally sized, non-empty paired observations")
    observed_baseline = statistic(baseline)
    observed_candidate = statistic(candidate)
    observed_delta = observed_candidate - observed_baseline
    seed = int(sha256_bytes(seed_material.encode("utf-8")), 16)
    randomizer = random.Random(seed)
    deltas: list[float] = []
    count = len(baseline)
    for _ in range(BOOTSTRAP_RESAMPLES):
        indexes = [randomizer.randrange(count) for _ in range(count)]
        baseline_sample = [baseline[index] for index in indexes]
        candidate_sample = [candidate[index] for index in indexes]
        deltas.append(statistic(candidate_sample) - statistic(baseline_sample))
    lower = _percentile(deltas, (1 - CONFIDENCE_LEVEL) / 2)
    upper = _percentile(deltas, 1 - (1 - CONFIDENCE_LEVEL) / 2)
    regression_detected = lower > 0
    return {
        "metric": name,
        "direction": "lower",
        "statistic": (
            "maximum" if name == "peak_rss_bytes" else name.removesuffix("_ms")
        ),
        "baseline_observed": observed_baseline,
        "candidate_observed": observed_candidate,
        "delta": round(observed_delta, 6),
        "paired_observation_count": count,
        "bootstrap_confidence_interval": {
            "confidence_level": CONFIDENCE_LEVEL,
            "resamples": BOOTSTRAP_RESAMPLES,
            "lower_delta": lower,
            "upper_delta": upper,
        },
        "regression_detected": regression_detected,
        "non_regression": not regression_detected,
    }


def _case_values(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    field: str,
) -> tuple[list[int], list[int]]:
    baseline_cases = {item["case_id"]: item for item in baseline["cases"]}
    candidate_cases = {item["case_id"]: item for item in candidate["cases"]}
    if baseline_cases.keys() != candidate_cases.keys():
        raise ValueError("paired query reports have different case inventories")
    baseline_values: list[int] = []
    candidate_values: list[int] = []
    if field != "peak_rss_bytes":
        for case_id in sorted(baseline_cases):
            baseline_values.append(int(baseline_cases[case_id][field]))
            candidate_values.append(int(candidate_cases[case_id][field]))
        return baseline_values, candidate_values
    for case_id in sorted(baseline_cases):
        for prefix in ("cold", "warm"):
            key = f"{prefix}_peak_rss_bytes"
            baseline_value = baseline_cases[case_id][key]
            candidate_value = candidate_cases[case_id][key]
            if baseline_value is None or candidate_value is None:
                if baseline_value != candidate_value:
                    raise ValueError("paired query reports have different RSS support")
                continue
            baseline_values.append(int(baseline_value))
            candidate_values.append(int(candidate_value))
    baseline_challenges = {
        item["challenge_id"]: item for item in baseline["challenges"]
    }
    candidate_challenges = {
        item["challenge_id"]: item for item in candidate["challenges"]
    }
    if baseline_challenges.keys() != candidate_challenges.keys():
        raise ValueError("paired query reports have different challenge inventories")
    for challenge_id in sorted(baseline_challenges):
        baseline_value = baseline_challenges[challenge_id]["peak_rss_bytes"]
        candidate_value = candidate_challenges[challenge_id]["peak_rss_bytes"]
        if baseline_value is None or candidate_value is None:
            if baseline_value != candidate_value:
                raise ValueError("paired challenge reports have different RSS support")
            continue
        baseline_values.append(int(baseline_value))
        candidate_values.append(int(candidate_value))
    return baseline_values, candidate_values


def _paired_performance(
    runs: Sequence[dict[str, Any]],
    *,
    report_digest: str,
) -> list[dict[str, Any]]:
    fields: dict[str, tuple[list[int], list[int]]] = {
        metric: ([], []) for metric, _, _ in PERFORMANCE_STATISTICS
    }
    for baseline_position, candidate_position in PAIR_POSITIONS:
        baseline = runs[baseline_position]
        candidate = runs[candidate_position]
        for metric, field, _ in PERFORMANCE_STATISTICS:
            baseline_values, candidate_values = _case_values(
                baseline,
                candidate,
                field,
            )
            fields[metric][0].extend(baseline_values)
            fields[metric][1].extend(candidate_values)
    return [
        _bootstrap_metric(
            name=metric,
            baseline=fields[metric][0],
            candidate=fields[metric][1],
            statistic=statistic,
            seed_material=f"{report_digest}:{metric}:paired-bootstrap-v1",
        )
        for metric, _, statistic in PERFORMANCE_STATISTICS
    ]


def compare(*, roles: Sequence[Role], runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if tuple(roles) != EXECUTION_ORDER or len(runs) != len(EXECUTION_ORDER):
        raise ValueError(
            "replicated comparison requires the frozen execution order: "
            + ",".join(EXECUTION_ORDER)
        )
    for run in runs:
        _validate_query(run)
    timestamps = [_recorded_at(run) for run in runs]
    if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
        raise ValueError("query reports must be supplied in strict recorded execution order")
    same_condition = _same_condition(runs)
    baseline_runs = [run for role, run in zip(roles, runs, strict=True) if role == "baseline"]
    candidate_runs = [run for role, run in zip(roles, runs, strict=True) if role == "candidate"]
    report_bindings = [
        {
            "position": position,
            "role": role,
            "pair_index": next(
                index
                for index, pair in enumerate(PAIR_POSITIONS, start=1)
                if position in pair
            ),
            "report_id": run["report_id"],
            "report_sha256": _report_sha256(run),
            "recorded_at": run["recorded_at"],
            "status": run["status"],
        }
        for position, (role, run) in enumerate(zip(roles, runs, strict=True))
    ]
    report_digest = sha256_bytes(canonical_json(report_bindings).encode("utf-8"))
    deterministic_metrics = [
        *(
            _median_metric(
                name=name,
                baseline=[run["metrics"][name] for run in baseline_runs],
                candidate=[run["metrics"][name] for run in candidate_runs],
                direction="higher",
            )
            for name in HIGHER_IS_BETTER
        ),
        *(
            _median_metric(
                name=name,
                baseline=[run["metrics"][name] for run in baseline_runs],
                candidate=[run["metrics"][name] for run in candidate_runs],
                direction="lower",
            )
            for name in STRICT_LOWER_IS_BETTER
        ),
    ]
    performance_metrics = _paired_performance(runs, report_digest=report_digest)
    safety = {
        name: {
            "baseline_values": [run["metrics"][name] for run in baseline_runs],
            "candidate_values": [run["metrics"][name] for run in candidate_runs],
            "candidate_zero": all(run["metrics"][name] == 0 for run in candidate_runs),
        }
        for name in SAFETY_ZERO
    }
    quality_non_regression = all(
        item["non_regression"] for item in deterministic_metrics
    )
    no_detected_performance_regression = all(
        not item["regression_detected"] for item in performance_metrics
    )
    safety_passed = all(item["candidate_zero"] for item in safety.values())
    candidate_reports_passed = all(run["status"] == "passed" for run in candidate_runs)
    status = (
        "passed"
        if same_condition["all_equal"]
        and candidate_reports_passed
        and quality_non_regression
        and no_detected_performance_regression
        and safety_passed
        else "failed"
    )
    body = {
        "schema_version": "deeplaw.semantic-query-replicate-comparison/v1",
        "status": status,
        "methodology": {
            "repetition_count_per_role": 3,
            "execution_order": list(EXECUTION_ORDER),
            "pair_positions": [list(pair) for pair in PAIR_POSITIONS],
            "deterministic_metric_aggregation": "median_of_three_run_level_values",
            "performance_metric_aggregation": (
                "paired_case_process_observations_with_two_sided_percentile_bootstrap"
            ),
            "confidence_level": CONFIDENCE_LEVEL,
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "performance_tolerance_fraction": 0,
        },
        "runs": report_bindings,
        "same_condition": same_condition,
        "deterministic_metric_comparisons": deterministic_metrics,
        "performance_metric_comparisons": performance_metrics,
        "safety_comparisons": safety,
        "candidate_reports_passed": candidate_reports_passed,
        "quality_non_regression": quality_non_regression,
        "no_statistically_detected_performance_regression": (
            no_detected_performance_regression
        ),
        "safety_passed": safety_passed,
        "competitive_claim_eligible": False,
    }
    return {
        "comparison_id": stable_id(
            "semanticreplicatecomparison",
            report_digest,
            status,
        ),
        **body,
    }


def _parse_run(value: str) -> tuple[Role, Path]:
    role_value, separator, path_value = value.partition("=")
    if separator != "=" or role_value not in {"baseline", "candidate"} or not path_value:
        raise argparse.ArgumentTypeError("--run must be baseline=PATH or candidate=PATH")
    return role_value, Path(path_value)  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare three paired Semantic query repetitions with a frozen method."
    )
    parser.add_argument("--run", action="append", type=_parse_run, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    roles = [role for role, _ in arguments.run]
    runs = [_load(path) for _, path in arguments.run]
    report = compare(roles=roles, runs=runs)
    schema = _load(
        _repository() / "contracts/semantic-query-replicate-comparison.v1.schema.json"
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(canonical_json(report) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
