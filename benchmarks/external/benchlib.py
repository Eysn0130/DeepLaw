from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA_CASE = "deeplaw.external-retrieval-case/v1"
SCHEMA_RUN = "deeplaw.external-retrieval-run/v1"
SCHEMA_REPORT = "deeplaw.external-retrieval-report/v1"
SCHEMA_METRIC_CASE = "deeplaw.external-metric-case/v1"
SCHEMA_METRIC_REPORT = "deeplaw.external-metric-report/v1"
SCHEMA_COMPARISON = "deeplaw.external-paired-comparison/v1"

MAX_CASES = 100_000
MAX_RELEVANT_IDS = 1_000
MAX_RETRIEVED_IDS = 1_000


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_loads(value: str) -> Any:
    return json.loads(
        value,
        object_pairs_hook=_unique_object,
        parse_constant=lambda constant: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number is not allowed: {constant}")
        ),
    )


def read_json(path: Path) -> Any:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"expected a regular JSON file: {path}")
    return strict_json_loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"expected a regular JSONL file: {path}")
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        value = strict_json_loads(raw_line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        records.append(value)
        if len(records) > MAX_CASES:
            raise ValueError(f"{path} exceeds the {MAX_CASES}-record safety bound")
    if not records:
        raise ValueError(f"{path} contains no records")
    return records


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _bounded_id_list(
    value: Any,
    *,
    field: str,
    maximum: int,
    allow_empty: bool,
) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > maximum
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ValueError(f"{field} must be a bounded list of non-empty strings")
    if not allow_empty and not value:
        raise ValueError(f"{field} must not be empty")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} must not contain duplicate IDs")
    return value


def _case_index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("schema_version") != SCHEMA_CASE:
            raise ValueError("retrieval case uses an unsupported schema")
        case_id = record.get("case_id")
        answerable = record.get("answerable")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("retrieval case_id must be a non-empty string")
        if case_id in cases:
            raise ValueError(f"duplicate retrieval case_id: {case_id}")
        if not isinstance(answerable, bool):
            raise ValueError(f"retrieval case {case_id} answerable must be boolean")
        relevant_ids = _bounded_id_list(
            record.get("relevant_ids"),
            field=f"retrieval case {case_id} relevant_ids",
            maximum=MAX_RELEVANT_IDS,
            allow_empty=not answerable,
        )
        if not answerable and relevant_ids:
            raise ValueError(f"unanswerable retrieval case {case_id} has relevant IDs")
        cases[case_id] = {
            "case_id": case_id,
            "answerable": answerable,
            "relevant_ids": relevant_ids,
            "group": record.get("group"),
        }
    return cases


def _run_index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("schema_version") != SCHEMA_RUN:
            raise ValueError("retrieval run item uses an unsupported schema")
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("retrieval run case_id must be a non-empty string")
        if case_id in runs:
            raise ValueError(f"duplicate retrieval run case_id: {case_id}")
        retrieved = record.get("retrieved")
        if not isinstance(retrieved, list) or len(retrieved) > MAX_RETRIEVED_IDS:
            raise ValueError(f"retrieval run {case_id} has invalid retrieved items")
        normalized_items: list[dict[str, Any]] = []
        for item in retrieved:
            if not isinstance(item, dict) or set(item) != {
                "id",
                "chars",
                "provenance_valid",
            }:
                raise ValueError(f"retrieval run {case_id} has an invalid retrieved item")
            item_id = item.get("id")
            chars = item.get("chars")
            provenance_valid = item.get("provenance_valid")
            if (
                not isinstance(item_id, str)
                or not item_id
                or isinstance(chars, bool)
                or not isinstance(chars, int)
                or chars < 0
                or not isinstance(provenance_valid, bool)
            ):
                raise ValueError(f"retrieval run {case_id} has invalid item fields")
            normalized_items.append(dict(item))
        latency_ms = record.get("latency_ms")
        task_success = record.get("task_success")
        if (
            isinstance(latency_ms, bool)
            or not isinstance(latency_ms, (int, float))
            or not math.isfinite(latency_ms)
            or latency_ms < 0
        ):
            raise ValueError(f"retrieval run {case_id} latency_ms must be finite and >= 0")
        if task_success is not None and not isinstance(task_success, bool):
            raise ValueError(f"retrieval run {case_id} task_success must be boolean or null")
        runs[case_id] = {
            "case_id": case_id,
            "retrieved": normalized_items,
            "latency_ms": float(latency_ms),
            "task_success": task_success,
        }
    return runs


def _dcg(binary_relevance: Iterable[int]) -> float:
    return sum(
        relevance / math.log2(rank + 1)
        for rank, relevance in enumerate(binary_relevance, start=1)
    )


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def score_retrieval(
    case_records: list[dict[str, Any]],
    run_records: list[dict[str, Any]],
    *,
    k: int,
    suite_id: str,
    system_id: str,
    cases_sha256: str,
    run_sha256: str,
    claim_eligible: bool,
    claim_ineligibility_reason: str | None,
) -> dict[str, Any]:
    if isinstance(k, bool) or not 1 <= k <= MAX_RETRIEVED_IDS:
        raise ValueError(f"k must be between 1 and {MAX_RETRIEVED_IDS}")
    if not suite_id or not system_id:
        raise ValueError("suite_id and system_id must be non-empty")
    cases = _case_index(case_records)
    runs = _run_index(run_records)
    missing = sorted(set(cases) - set(runs))
    extra = sorted(set(runs) - set(cases))
    if missing or extra:
        raise ValueError(
            f"run coverage mismatch: missing={missing[:10]}, extra={extra[:10]}"
        )
    per_case: list[dict[str, Any]] = []
    for case_id, case in cases.items():
        run = runs[case_id]
        top_items = run["retrieved"][:k]
        ranked_ids = [item["id"] for item in top_items]
        unique_ranked_ids = list(dict.fromkeys(ranked_ids))
        relevant = set(case["relevant_ids"])
        hits = [int(item_id in relevant) for item_id in ranked_ids]
        unique_hits = len(relevant.intersection(unique_ranked_ids))
        answerable = case["answerable"]
        first_hit = next(
            (rank for rank, item_id in enumerate(ranked_ids, start=1) if item_id in relevant),
            None,
        )
        ideal_hits = [1] * min(len(relevant), k)
        ndcg = (
            _dcg(hits) / _dcg(ideal_hits)
            if answerable and ideal_hits
            else float(not ranked_ids)
        )
        irrelevant_count = sum(item_id not in relevant for item_id in ranked_ids)
        provenance_valid = (
            sum(item["provenance_valid"] for item in top_items) / len(top_items)
            if top_items
            else 1.0
        )
        per_case.append(
            {
                "case_id": case_id,
                "group": case["group"],
                "answerable": answerable,
                "hit_at_k": float(bool(unique_hits)) if answerable else float(not ranked_ids),
                "recall_at_k": unique_hits / len(relevant) if answerable else float(not ranked_ids),
                "precision_at_k": unique_hits / len(unique_ranked_ids)
                if unique_ranked_ids
                else float(not answerable),
                "mrr": 1.0 / first_hit if first_hit is not None else 0.0,
                "ndcg_at_k": ndcg,
                "irrelevant_context_rate": irrelevant_count / len(ranked_ids)
                if ranked_ids
                else 0.0,
                "duplicate_count": len(ranked_ids) - len(unique_ranked_ids),
                "context_chars": sum(item["chars"] for item in top_items),
                "provenance_coverage": provenance_valid,
                "latency_ms": run["latency_ms"],
                "task_success": (
                    float(run["task_success"])
                    if run["task_success"] is not None
                    else None
                ),
            }
        )
    metric_names = (
        "hit_at_k",
        "recall_at_k",
        "precision_at_k",
        "mrr",
        "ndcg_at_k",
        "irrelevant_context_rate",
        "duplicate_count",
        "context_chars",
        "provenance_coverage",
        "latency_ms",
    )
    aggregate = {
        metric: statistics.fmean(float(item[metric]) for item in per_case)
        for metric in metric_names
    }
    aggregate["latency_p50_ms"] = _percentile(
        [item["latency_ms"] for item in per_case],
        0.50,
    )
    aggregate["latency_p95_ms"] = _percentile(
        [item["latency_ms"] for item in per_case],
        0.95,
    )
    task_scores = [
        float(item["task_success"])
        for item in per_case
        if item["task_success"] is not None
    ]
    aggregate["task_success"] = statistics.fmean(task_scores) if task_scores else None
    return {
        "schema_version": SCHEMA_REPORT,
        "suite_id": suite_id,
        "system_id": system_id,
        "k": k,
        "case_count": len(per_case),
        "cases_sha256": cases_sha256,
        "run_sha256": run_sha256,
        "complete": True,
        "claim_eligible": claim_eligible,
        "claim_ineligibility_reason": claim_ineligibility_reason,
        "aggregate": aggregate,
        "per_case": per_case,
    }


def score_metrics(
    records: list[dict[str, Any]],
    *,
    suite_id: str,
    system_id: str,
    input_sha256: str,
    cases_sha256: str,
    claim_eligible: bool,
    claim_ineligibility_reason: str | None,
) -> dict[str, Any]:
    if not suite_id or not system_id:
        raise ValueError("suite_id and system_id must be non-empty")
    if len(cases_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in cases_sha256
    ):
        raise ValueError("cases_sha256 must be lowercase SHA-256")
    per_case: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    expected_metrics: set[str] | None = None
    for record in records:
        if record.get("schema_version") != SCHEMA_METRIC_CASE:
            raise ValueError("external metric case uses an unsupported schema")
        if set(record) != {"schema_version", "case_id", "group", "metrics"}:
            raise ValueError("external metric case does not match its closed shape")
        case_id = record.get("case_id")
        metrics = record.get("metrics")
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise ValueError("external metric case_id must be non-empty and unique")
        if not isinstance(metrics, dict) or not metrics:
            raise ValueError(f"external metric case {case_id} has no metrics")
        normalized_metrics: dict[str, float] = {}
        for metric, value in metrics.items():
            if (
                not isinstance(metric, str)
                or not metric
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"external metric case {case_id} has invalid metrics")
            normalized_metrics[metric] = float(value)
        metric_names = set(normalized_metrics)
        if expected_metrics is None:
            expected_metrics = metric_names
        elif metric_names != expected_metrics:
            raise ValueError("every external metric case must expose the same metrics")
        seen_ids.add(case_id)
        per_case.append(
            {
                "case_id": case_id,
                "group": record.get("group"),
                "metrics": normalized_metrics,
            }
        )
    if not per_case or expected_metrics is None:
        raise ValueError("external metric report requires at least one case")
    aggregate = {
        metric: statistics.fmean(item["metrics"][metric] for item in per_case)
        for metric in sorted(expected_metrics)
    }
    return {
        "schema_version": SCHEMA_METRIC_REPORT,
        "suite_id": suite_id,
        "system_id": system_id,
        "case_count": len(per_case),
        "input_sha256": input_sha256,
        "cases_sha256": cases_sha256,
        "complete": True,
        "claim_eligible": claim_eligible,
        "claim_ineligibility_reason": claim_ineligibility_reason,
        "aggregate": aggregate,
        "per_case": per_case,
    }


def _case_metric(item: dict[str, Any], metric: str) -> Any:
    value = item.get(metric)
    if value is not None:
        return value
    metrics = item.get("metrics")
    return metrics.get(metric) if isinstance(metrics, dict) else None


def paired_comparison(
    candidate: dict[str, Any],
    baseline: dict[str, Any],
    *,
    metric: str,
    direction: str,
    samples: int,
    confidence: float,
    seed: int,
    noninferiority_margin: float,
    minimum_effect: float,
) -> dict[str, Any]:
    if candidate.get("schema_version") not in {SCHEMA_REPORT, SCHEMA_METRIC_REPORT}:
        raise ValueError("candidate report schema is unsupported")
    if baseline.get("schema_version") not in {SCHEMA_REPORT, SCHEMA_METRIC_REPORT}:
        raise ValueError("baseline report schema is unsupported")
    if candidate.get("suite_id") != baseline.get("suite_id"):
        raise ValueError("paired reports must use the same suite")
    candidate_case_hash = candidate.get("cases_sha256")
    baseline_case_hash = baseline.get("cases_sha256")
    if candidate_case_hash != baseline_case_hash:
        raise ValueError("paired reports must use the same frozen case file")
    if direction not in {"higher", "lower"}:
        raise ValueError("direction must be higher or lower")
    if isinstance(samples, bool) or samples < 1_000:
        raise ValueError("paired bootstrap requires at least 1000 samples")
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be between 0.5 and 1")
    if noninferiority_margin < 0:
        raise ValueError("noninferiority_margin must be non-negative")
    if minimum_effect < 0:
        raise ValueError("minimum_effect must be non-negative")
    candidate_items = candidate.get("per_case", [])
    baseline_items = baseline.get("per_case", [])
    if not isinstance(candidate_items, list) or not isinstance(baseline_items, list):
        raise ValueError("paired reports must contain per_case lists")
    candidate_cases = {item["case_id"]: item for item in candidate_items}
    baseline_cases = {item["case_id"]: item for item in baseline_items}
    if len(candidate_cases) != len(candidate_items) or len(baseline_cases) != len(
        baseline_items
    ):
        raise ValueError("paired reports must not contain duplicate case IDs")
    if not candidate_cases or set(candidate_cases) != set(baseline_cases):
        raise ValueError("paired reports must contain the same non-empty case IDs")
    if (
        candidate.get("case_count") != len(candidate_cases)
        or baseline.get("case_count") != len(baseline_cases)
    ):
        raise ValueError("paired report case_count does not match per_case")
    ordered_ids = sorted(candidate_cases)
    deltas: list[float] = []
    for case_id in ordered_ids:
        candidate_value = _case_metric(candidate_cases[case_id], metric)
        baseline_value = _case_metric(baseline_cases[case_id], metric)
        if (
            isinstance(candidate_value, bool)
            or not isinstance(candidate_value, (int, float))
            or isinstance(baseline_value, bool)
            or not isinstance(baseline_value, (int, float))
        ):
            raise ValueError(f"metric {metric} is unavailable for paired comparison")
        deltas.append(float(candidate_value) - float(baseline_value))
    rng = random.Random(seed)
    bootstrap = [
        statistics.fmean(deltas[rng.randrange(len(deltas))] for _ in deltas)
        for _ in range(samples)
    ]
    alpha = (1.0 - confidence) / 2.0
    ci_low = _percentile(bootstrap, alpha)
    ci_high = _percentile(bootstrap, 1.0 - alpha)
    mean_delta = statistics.fmean(deltas)
    directional_deltas = (
        deltas if direction == "higher" else [-delta for delta in deltas]
    )
    shifted_deltas = [delta - minimum_effect for delta in directional_deltas]
    observed_shifted_effect = statistics.fmean(shifted_deltas)
    randomization = random.Random(seed ^ 0x5EED5EED)
    superiority_extreme_count = 0
    for _ in range(samples):
        null_effect = statistics.fmean(
            delta * (-1 if randomization.randrange(2) else 1)
            for delta in shifted_deltas
        )
        if null_effect >= observed_shifted_effect:
            superiority_extreme_count += 1
    superiority_p_value = (superiority_extreme_count + 1) / (samples + 1)
    superior = (
        ci_low >= minimum_effect
        if direction == "higher"
        else ci_high <= -minimum_effect
    )
    noninferior = (
        ci_low >= -noninferiority_margin
        if direction == "higher"
        else ci_high <= noninferiority_margin
    )
    return {
        "schema_version": SCHEMA_COMPARISON,
        "suite_id": candidate["suite_id"],
        "candidate_system_id": candidate["system_id"],
        "baseline_system_id": baseline["system_id"],
        "candidate_report_sha256": hashlib.sha256(
            canonical_json(candidate).encode("utf-8")
        ).hexdigest(),
        "baseline_report_sha256": hashlib.sha256(
            canonical_json(baseline).encode("utf-8")
        ).hexdigest(),
        "metric": metric,
        "direction": direction,
        "case_count": len(deltas),
        "samples": samples,
        "confidence": confidence,
        "seed": seed,
        "noninferiority_margin": noninferiority_margin,
        "minimum_effect": minimum_effect,
        "candidate_minus_baseline": mean_delta,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "superiority_p_value": superiority_p_value,
        "superior": superior,
        "noninferior": noninferior,
    }
