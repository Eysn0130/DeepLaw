from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts.run_living_wiki_host_harness import _safe_command
from benchmarks.semantic.review_gold import validate_candidate
from deeplaw.util import canonical_json, sha256_bytes, stable_id

BUDGET = {
    "max_items": 8,
    "max_sources": 12,
    "max_chars": 8_000,
    "max_tokens": 6_000,
    "max_sensitivity": "public",
}


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _validate(name: str, value: dict[str, Any]) -> None:
    schema = _load(_repository() / "contracts" / name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction + 0.5)))
    return ordered[index]


def _query(
    prefix: list[str],
    *,
    vault: Path,
    query: str,
    purpose: str,
    as_of: str | None,
) -> tuple[dict[str, Any], int]:
    temporal_arguments = ["--as-of", as_of] if as_of is not None else []
    started = time.monotonic()
    completed = subprocess.run(
        [
            *prefix,
            "knowledge",
            "--format",
            "json",
            "query",
            "--vault",
            str(vault),
            "--query",
            query,
            "--purpose",
            purpose,
            *temporal_arguments,
            "--scope",
            "personal",
            "--max-sensitivity",
            "public",
            "--limit",
            str(BUDGET["max_items"]),
            "--max-chars",
            str(BUDGET["max_chars"]),
            "--max-tokens",
            str(BUDGET["max_tokens"]),
            "--max-sources",
            str(BUDGET["max_sources"]),
            "--graph-hops",
            "1",
            "--retrieval-mode",
            "hybrid",
            "--query-plan-version",
            "5",
        ],
        cwd=_repository(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=120,
    )
    elapsed_ms = round((time.monotonic() - started) * 1000)
    if completed.returncode != 0:
        summary = completed.stderr.decode("utf-8", errors="replace")[-2_000:]
        raise RuntimeError(f"first-party semantic query failed: {summary}")
    if len(completed.stdout) > 512 * 1024:
        raise RuntimeError("first-party semantic query output exceeded 512 KiB")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("first-party semantic query returned a non-object")
    return value, elapsed_ms


def _selected(value: dict[str, Any]) -> tuple[list[str], list[str]]:
    revisions: set[str] = set()
    source_revisions: set[str] = set()
    for item in [*value.get("compiled", []), *value.get("evidence", [])]:
        if not isinstance(item, dict):
            continue
        revision_id = item.get("revision_id")
        if isinstance(revision_id, str) and revision_id.startswith("knowledgerev_"):
            revisions.add(revision_id)
        direct_source = item.get("source_revision_id")
        if isinstance(direct_source, str) and direct_source.startswith("sourcerev_"):
            source_revisions.add(direct_source)
        for reference in item.get("source_refs", []):
            if not isinstance(reference, dict):
                continue
            source_revision_id = reference.get("source_revision_id")
            if isinstance(source_revision_id, str) and source_revision_id.startswith(
                "sourcerev_"
            ):
                source_revisions.add(source_revision_id)
    return sorted(revisions), sorted(source_revisions)


def _case_result(
    *,
    case: dict[str, Any],
    cold: dict[str, Any],
    warm: dict[str, Any],
    cold_latency_ms: int,
    warm_latency_ms: int,
    source_ids: dict[str, str],
) -> dict[str, Any]:
    compiled_ids, selected_sources = _selected(warm)
    cold_compiled_ids, cold_selected_sources = _selected(cold)
    gaps = [item for item in warm.get("gaps", []) if isinstance(item, dict)]
    gap_codes = sorted(
        {str(item["code"]) for item in gaps if isinstance(item.get("code"), str)}
    )
    explicit_gap = bool(
        {"evidence_gap", "retrieval_gap", "uncompiled_source", "stale_knowledge"}
        & set(gap_codes)
    )
    fallback_used = bool(warm.get("query_plan", {}).get("fallback", {}).get("used"))
    source_fallback_visible = not fallback_used or "source_fallback" in gap_codes
    provider_bytes = int(warm.get("metrics", {}).get("provider_payload_bytes", 65_537))
    hard_limit_valid = 0 <= provider_bytes <= 65_536
    repeat_reused = bool(
        compiled_ids == cold_compiled_ids
        and selected_sources == cold_selected_sources
        and warm.get("metrics", {}).get("repeated_query_reused_compilation", False)
    )
    task_type = case["task_type"]
    semantic_pass = bool(compiled_ids or warm.get("evidence"))
    failure_reason: str | None = None
    if task_type == "unanswerable":
        semantic_pass = bool(
            explicit_gap and not compiled_ids and not warm.get("evidence")
        )
        if not semantic_pass:
            failure_reason = "unanswerable query did not return only an explicit Gap"
    elif task_type == "source_withdrawal":
        semantic_pass = source_ids["retention-a"] not in selected_sources
        if not semantic_pass:
            failure_reason = "withdrawn Source Revision was selected"
    elif task_type in {"source_successor_update", "overview_refresh"}:
        semantic_pass = bool(
            source_ids["update-v2"] in selected_sources
            and source_ids["update-v1"] not in selected_sources
        )
        if not semantic_pass:
            failure_reason = "query did not select only the active Atlas successor"
    elif task_type == "source_conflict":
        semantic_pass = bool(warm.get("contradictions"))
        if not semantic_pass:
            failure_reason = "conflicting policies were not exposed as a contradiction"
    elif not semantic_pass:
        failure_reason = "query returned neither compiled Knowledge nor evidence"
    safety_pass = bool(
        warm.get("write_performed") is False
        and warm.get("authority_changed_by_ranking") is False
        and source_fallback_visible
        and hard_limit_valid
    )
    passed = bool(semantic_pass and safety_pass)
    if not safety_pass and failure_reason is None:
        failure_reason = "query safety or provider-bound invariant failed"
    return {
        "case_id": case["case_id"],
        "task_type": task_type,
        "query_sha256": sha256_bytes(case["query"].encode("utf-8")),
        "purpose": case["purpose"],
        "status": "passed" if passed else "failed",
        "cold_latency_ms": cold_latency_ms,
        "warm_latency_ms": warm_latency_ms,
        "provider_payload_bytes": max(provider_bytes, 0),
        "compiled_revision_ids": compiled_ids,
        "selected_source_revision_ids": selected_sources,
        "gap_codes": gap_codes,
        "explicit_gap": explicit_gap,
        "repeat_reused": repeat_reused,
        "write_performed": bool(warm.get("write_performed", True)),
        "authority_changed_by_ranking": bool(
            warm.get("authority_changed_by_ranking", True)
        ),
        "source_fallback_visible": source_fallback_visible,
        "provider_hard_limit_valid": hard_limit_valid,
        "failure_reason": failure_reason,
    }


def run(
    *,
    gold: dict[str, Any],
    host_report: dict[str, Any],
    corpus: dict[str, Any],
    vault: Path,
    command: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_candidate(gold, repository=_repository())
    if host_report.get("status") != "passed":
        raise ValueError("semantic query suite requires a passed real-host report")
    if host_report.get("gold_id") != gold["gold_id"]:
        raise ValueError("semantic query suite host report does not bind Gold")
    if corpus.get("gold_id") != gold["gold_id"]:
        raise ValueError("semantic query suite corpus does not bind Gold")
    prefix = _safe_command(command)
    source_ids = {
        item["source_key"]: item["source_revision_id"] for item in corpus["sources"]
    }
    query_set = [
        {
            "case_id": case["case_id"],
            "query": case["query"],
            "purpose": case["purpose"],
            "as_of": case.get("as_of"),
            "budget": BUDGET,
        }
        for case in gold["cases"]
    ]
    query_set_sha256 = sha256_bytes(canonical_json(query_set).encode("utf-8"))
    cases = []
    raw_fragment_baseline_bytes = 0
    source_sizes = {
        item["source_key"]: (
            _repository() / item["relative_path"]
        ).stat().st_size
        for item in gold["sources"]
    }
    for case in gold["cases"]:
        raw_fragment_baseline_bytes += sum(
            source_sizes[source_key] for source_key in case["source_keys"]
        )
        cold, cold_latency = _query(
            prefix,
            vault=vault,
            query=case["query"],
            purpose=case["purpose"],
            as_of=case.get("as_of"),
        )
        warm, warm_latency = _query(
            prefix,
            vault=vault,
            query=case["query"],
            purpose=case["purpose"],
            as_of=case.get("as_of"),
        )
        cases.append(
            _case_result(
                case=case,
                cold=cold,
                warm=warm,
                cold_latency_ms=cold_latency,
                warm_latency_ms=warm_latency,
                source_ids=source_ids,
            )
        )
    provider_bytes = sum(item["provider_payload_bytes"] for item in cases)
    cold_latencies = [item["cold_latency_ms"] for item in cases]
    warm_latencies = [item["warm_latency_ms"] for item in cases]
    passed_count = sum(item["status"] == "passed" for item in cases)
    metrics = {
        "query_count": len(cases),
        "execution_count": len(cases) * 2,
        "passed_count": passed_count,
        "provider_payload_bytes": provider_bytes,
        "raw_fragment_baseline_bytes": raw_fragment_baseline_bytes,
        "bytes_saved_ratio": round(
            1 - provider_bytes / raw_fragment_baseline_bytes, 6
        ) if raw_fragment_baseline_bytes else 0.0,
        "cold_latency_p50_ms": round(median(cold_latencies)),
        "cold_latency_p95_ms": _percentile(cold_latencies, 0.95),
        "warm_latency_p50_ms": round(median(warm_latencies)),
        "warm_latency_p95_ms": _percentile(warm_latencies, 0.95),
        "repeated_query_reuse_rate": round(
            sum(item["repeat_reused"] for item in cases) / len(cases), 6
        ),
        "provider_hard_limit_violations": sum(
            not item["provider_hard_limit_valid"] for item in cases
        ),
        "unauthorized_writes": sum(item["write_performed"] for item in cases),
        "authority_elevations": sum(
            item["authority_changed_by_ranking"] for item in cases
        ),
        "silent_fallbacks": sum(not item["source_fallback_visible"] for item in cases),
        "stale_prohibited_selections": sum(
            item["task_type"]
            in {"source_withdrawal", "source_successor_update", "overview_refresh"}
            and item["status"] == "failed"
            for item in cases
        ),
    }
    passed = bool(
        passed_count == len(cases)
        and metrics["provider_hard_limit_violations"] == 0
        and metrics["unauthorized_writes"] == 0
        and metrics["authority_elevations"] == 0
        and metrics["silent_fallbacks"] == 0
        and metrics["stale_prohibited_selections"] == 0
    )
    recorded_at = _timestamp()
    report = {
        "schema_version": "deeplaw.semantic-query-run/v1",
        "report_id": stable_id(
            "semanticqueryrun",
            gold["gold_id"],
            host_report["report_id"],
            query_set_sha256,
            recorded_at,
        ),
        "status": "passed" if passed else "failed",
        "gold_id": gold["gold_id"],
        "host_report_id": host_report["report_id"],
        "query_set_sha256": query_set_sha256,
        "first_party_command_sha256": sha256_bytes(
            canonical_json(command).encode("utf-8")
        ),
        "budget": BUDGET,
        "cases": cases,
        "metrics": metrics,
        "recorded_at": recorded_at,
        "competitive_claim_eligible": False,
    }
    cost = {
        "schema_version": "deeplaw.semantic-query-cost/v1",
        "gold_id": gold["gold_id"],
        "host_report_id": host_report["report_id"],
        "query_set_sha256": query_set_sha256,
        "first_party_command": "deeplaw knowledge query",
        "query_count": len(cases),
        "total_query_tokens": provider_bytes,
        "total_context_bytes": provider_bytes,
        "raw_fragment_baseline_bytes": raw_fragment_baseline_bytes,
        "measurement_method": "utf8_bytes_proxy",
        "budget": {**BUDGET, "cold_or_warm": "warm"},
        "measured_at": recorded_at,
    }
    _validate("semantic-query-run.v1.schema.json", report)
    _validate("semantic-query-cost.v1.schema.json", cost)
    return report, cost


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the frozen Semantic Gold query set twice through the first-party CLI."
    )
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--host-report", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--deeplaw-command", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cost-output", type=Path, required=True)
    arguments = parser.parse_args()
    report, cost = run(
        gold=_load(arguments.gold),
        host_report=_load(arguments.host_report),
        corpus=_load(arguments.corpus),
        vault=arguments.vault,
        command=_load(arguments.deeplaw_command),
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.cost_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(canonical_json(report) + "\n", encoding="utf-8")
    arguments.cost_output.write_text(canonical_json(cost) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
