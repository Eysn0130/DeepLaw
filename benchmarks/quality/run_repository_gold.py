from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

from deeplaw.knowledge_intelligence import (
    LOCAL_DENSE_MODEL,
    LOCAL_RERANKER_MODEL,
    rerank_candidates,
    semantic_similarity,
)
from deeplaw.util import canonical_json, search_terms, sha256_bytes, sha256_file, strict_json_loads

SUITE_SCHEMA = "deeplaw.repository-gold-set/v1"
SUITE_SCHEMA_V2 = "deeplaw.repository-gold-set/v2"
REPORT_SCHEMA = "deeplaw.repository-gold-report/v1"
REPORT_SCHEMA_V2 = "deeplaw.repository-gold-report/v2"
CATEGORIES = ("chinese", "english", "code", "legal", "long_document")
MODES = ("lexical", "dense", "hybrid")
_MAX_SUITE_BYTES = 4 * 1024 * 1024
_MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
_MAX_DOCUMENTS = 100
_MAX_CASES = 1_000


def _bounded_text(value: Any, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"{field} is not bounded canonical text")
    return value


def _load_suite(path: Path, *, repository: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    resolved_suite = path.resolve(strict=True)
    payload = resolved_suite.read_bytes()
    if not 1 <= len(payload) <= _MAX_SUITE_BYTES:
        raise ValueError("repository Gold Set exceeds its byte bound")
    suite = strict_json_loads(payload)
    if not isinstance(suite, dict):
        raise ValueError("repository Gold Set does not match its closed contract")
    schema_version = suite.get("schema_version")
    expected_keys = {
        "schema_version",
        "status",
        "claim_eligible",
        "frozen_at",
        "categories",
        "quality_gate",
        "documents",
        "cases",
    }
    if schema_version == SUITE_SCHEMA_V2:
        expected_keys |= {"split", "freeze_policy"}
    if set(suite) != expected_keys:
        raise ValueError("repository Gold Set does not match its closed contract")
    if schema_version not in {SUITE_SCHEMA, SUITE_SCHEMA_V2}:
        raise ValueError("repository Gold Set schema is unsupported")
    expected_status = (
        "curated_development_fixture"
        if schema_version == SUITE_SCHEMA
        else "public_time_frozen_holdout"
    )
    if (
        suite["status"] != expected_status
        or suite["claim_eligible"] is not False
        or tuple(suite["categories"]) != CATEGORIES
    ):
        raise ValueError("repository Gold Set governance is invalid")
    if schema_version == SUITE_SCHEMA_V2:
        if suite["split"] != "public_time_frozen_holdout":
            raise ValueError("repository Gold Set split is invalid")
        freeze_policy = suite["freeze_policy"]
        if not isinstance(freeze_policy, dict) or freeze_policy != {
            "visibility": "public",
            "labels_visible": True,
            "secret": False,
            "contamination_claim_eligible": False,
            "reuse_policy": "immutable_until_protocol_version_change",
        }:
            raise ValueError("repository Gold Set freeze policy is invalid")
    _bounded_text(suite["frozen_at"], field="frozen_at", maximum=40)
    quality_gate = suite["quality_gate"]
    if not isinstance(quality_gate, dict) or set(quality_gate) != set(MODES):
        raise ValueError("repository Gold Set quality gate is invalid")
    for mode, thresholds in quality_gate.items():
        if not isinstance(thresholds, dict) or set(thresholds) != {
            "minimum_hit_at_1",
            "minimum_useful_context_recall",
            "maximum_irrelevant_context_rate",
            "maximum_forbidden_admission_count",
        }:
            raise ValueError(f"repository Gold Set {mode} quality gate is invalid")
        for field in (
            "minimum_hit_at_1",
            "minimum_useful_context_recall",
            "maximum_irrelevant_context_rate",
        ):
            value = thresholds[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 1
            ):
                raise ValueError(f"repository Gold Set {mode} {field} is invalid")
        forbidden = thresholds["maximum_forbidden_admission_count"]
        if isinstance(forbidden, bool) or not isinstance(forbidden, int) or forbidden < 0:
            raise ValueError(
                f"repository Gold Set {mode} forbidden-admission gate is invalid"
            )
    documents = suite["documents"]
    if not isinstance(documents, list) or not 5 <= len(documents) <= _MAX_DOCUMENTS:
        raise ValueError("repository Gold Set document inventory is invalid")
    repository = repository.resolve(strict=True)
    indexed: list[dict[str, Any]] = []
    document_ids: set[str] = set()
    observed_categories: set[str] = set()
    for item in documents:
        if not isinstance(item, dict) or set(item) != {
            "document_id",
            "category",
            "path",
            "sha256",
            "title",
            "anchor",
        }:
            raise ValueError("repository Gold Set document does not match its closed contract")
        document_id = _bounded_text(item["document_id"], field="document_id", maximum=100)
        if document_id in document_ids:
            raise ValueError("repository Gold Set contains a duplicate document ID")
        document_ids.add(document_id)
        category = item["category"]
        if category not in CATEGORIES:
            raise ValueError("repository Gold Set document category is invalid")
        observed_categories.add(category)
        relative = Path(_bounded_text(item["path"], field="document path", maximum=500))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("repository Gold Set document path is unsafe")
        unresolved = repository / relative
        resolved = unresolved.resolve(strict=True)
        try:
            resolved.relative_to(repository)
        except ValueError as error:
            raise ValueError("repository Gold Set document escapes the repository") from error
        if resolved != unresolved or resolved.is_symlink() or not resolved.is_file():
            raise ValueError("repository Gold Set document path is not canonical")
        if not 1 <= resolved.stat().st_size <= _MAX_DOCUMENT_BYTES:
            raise ValueError("repository Gold Set document exceeds its byte bound")
        digest = item["sha256"]
        if not isinstance(digest, str) or sha256_file(resolved) != digest:
            raise ValueError(f"repository Gold Set source hash changed: {relative.as_posix()}")
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("repository Gold Set source is not UTF-8") from error
        anchor = _bounded_text(item["anchor"], field="document anchor", maximum=2_000)
        if anchor not in content:
            raise ValueError(f"repository Gold Set anchor is absent: {document_id}")
        indexed.append(
            {
                "knowledge_id": document_id,
                "revision_id": digest,
                "title": _bounded_text(item["title"], field="document title", maximum=500),
                "body": content,
                "semantic_key": category,
                "category": category,
                "path_sha256": sha256_bytes(relative.as_posix().encode("utf-8")),
            }
        )
    if observed_categories != set(CATEGORIES):
        raise ValueError("repository Gold Set does not cover every required category")
    cases = suite["cases"]
    if not isinstance(cases, list) or not 10 <= len(cases) <= _MAX_CASES:
        raise ValueError("repository Gold Set case inventory is invalid")
    case_ids: set[str] = set()
    case_categories: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "case_id",
            "category",
            "query",
            "expected_document_ids",
            "forbidden_document_ids",
            "top_k",
        }:
            raise ValueError("repository Gold Set case does not match its closed contract")
        case_id = _bounded_text(case["case_id"], field="case_id", maximum=100)
        if case_id in case_ids:
            raise ValueError("repository Gold Set contains a duplicate case ID")
        case_ids.add(case_id)
        if case["category"] not in CATEGORIES:
            raise ValueError("repository Gold Set case category is invalid")
        case_categories.add(case["category"])
        _bounded_text(case["query"], field="case query", maximum=2_000)
        for field, allow_empty in (
            ("expected_document_ids", False),
            ("forbidden_document_ids", True),
        ):
            values = case[field]
            if (
                not isinstance(values, list)
                or (not allow_empty and not values)
                or len(values) > 20
                or len(set(values)) != len(values)
                or any(value not in document_ids for value in values)
            ):
                raise ValueError(f"repository Gold Set {field} is invalid")
        if set(case["expected_document_ids"]) & set(case["forbidden_document_ids"]):
            raise ValueError("repository Gold Set expected and forbidden documents overlap")
        if (
            isinstance(case["top_k"], bool)
            or not isinstance(case["top_k"], int)
            or not 1 <= case["top_k"] <= 10
        ):
            raise ValueError("repository Gold Set top_k is invalid")
    if case_categories != set(CATEGORIES):
        raise ValueError("repository Gold Set cases do not cover every required category")
    return suite, indexed


def _lexical_score(query: str, candidate: dict[str, Any]) -> float:
    query_terms = set(search_terms(query, limit=128, cover_tail=True))
    document_terms = set(
        search_terms(
            f"{candidate['title']} {candidate['body']} {candidate['semantic_key']}",
            limit=20_000,
            cover_tail=True,
        )
    )
    return len(query_terms & document_terms) / max(1, len(query_terms))


def _rank(query: str, documents: list[dict[str, Any]], *, mode: str) -> list[str]:
    if mode == "hybrid":
        ranked = rerank_candidates(
            query,
            [
                {
                    **document,
                    "epistemic_state": "supported",
                    "feedback_utility": 0.0,
                }
                for document in documents
            ],
        )
        return [item["knowledge_id"] for item in ranked]
    scored: list[tuple[float, str]] = []
    for document in documents:
        text = f"{document['title']}\n{document['body']}\n{document['semantic_key']}"
        score = (
            _lexical_score(query, document)
            if mode == "lexical"
            else semantic_similarity(query, text)
        )
        scored.append((score, document["knowledge_id"]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [document_id for _score, document_id in scored]


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def run_suite(path: Path, *, repository: Path) -> dict[str, Any]:
    suite, documents = _load_suite(path, repository=repository)
    modes: dict[str, Any] = {}
    for mode in MODES:
        outcomes: list[dict[str, Any]] = []
        latencies: list[float] = []
        for case in suite["cases"]:
            started = time.perf_counter_ns()
            ranking = _rank(case["query"], documents, mode=mode)
            latencies.append((time.perf_counter_ns() - started) / 1_000_000)
            selected = ranking[: case["top_k"]]
            expected = set(case["expected_document_ids"])
            forbidden = set(case["forbidden_document_ids"])
            useful = len(expected & set(selected))
            irrelevant = len([item for item in selected if item not in expected])
            outcomes.append(
                {
                    "case_id": case["case_id"],
                    "category": case["category"],
                    "selected_document_ids": selected,
                    "hit_at_1": bool(selected and selected[0] in expected),
                    "useful_context_recall": useful / len(expected),
                    "irrelevant_context_rate": irrelevant / max(1, len(selected)),
                    "forbidden_admission_count": len(forbidden & set(selected)),
                }
            )
        category_metrics: dict[str, Any] = {}
        for category in CATEGORIES:
            selected_outcomes = [item for item in outcomes if item["category"] == category]
            category_metrics[category] = {
                "case_count": len(selected_outcomes),
                "hit_at_1": statistics.fmean(
                    float(item["hit_at_1"]) for item in selected_outcomes
                ),
                "useful_context_recall": statistics.fmean(
                    item["useful_context_recall"] for item in selected_outcomes
                ),
                "irrelevant_context_rate": statistics.fmean(
                    item["irrelevant_context_rate"] for item in selected_outcomes
                ),
            }
        modes[mode] = {
            "case_count": len(outcomes),
            "hit_at_1": statistics.fmean(float(item["hit_at_1"]) for item in outcomes),
            "useful_context_recall": statistics.fmean(
                item["useful_context_recall"] for item in outcomes
            ),
            "irrelevant_context_rate": statistics.fmean(
                item["irrelevant_context_rate"] for item in outcomes
            ),
            "forbidden_admission_count": sum(
                item["forbidden_admission_count"] for item in outcomes
            ),
            "latency_ms_p50": round(_percentile(latencies, 0.5), 6),
            "latency_ms_p95": round(_percentile(latencies, 0.95), 6),
            "category_metrics": category_metrics,
            "failures": [
                item
                for item in outcomes
                if (
                    not item["hit_at_1"]
                    or item["useful_context_recall"] < 1.0
                    or item["forbidden_admission_count"]
                )
            ],
            "case_results": outcomes,
        }
    gate_results: dict[str, Any] = {}
    for mode in MODES:
        thresholds = suite["quality_gate"][mode]
        metrics = modes[mode]
        checks = {
            "hit_at_1": metrics["hit_at_1"] >= thresholds["minimum_hit_at_1"],
            "useful_context_recall": metrics["useful_context_recall"]
            >= thresholds["minimum_useful_context_recall"],
            "irrelevant_context_rate": metrics["irrelevant_context_rate"]
            <= thresholds["maximum_irrelevant_context_rate"],
            "forbidden_admission_count": metrics["forbidden_admission_count"]
            <= thresholds["maximum_forbidden_admission_count"],
        }
        gate_results[mode] = {"passed": all(checks.values()), "checks": checks}
    source_inventory = [
        {
            "document_id": item["knowledge_id"],
            "sha256": item["revision_id"],
            "path_sha256": item["path_sha256"],
        }
        for item in documents
    ]
    report = {
        "schema_version": (
            REPORT_SCHEMA_V2
            if suite["schema_version"] == SUITE_SCHEMA_V2
            else REPORT_SCHEMA
        ),
        "suite_schema_version": suite["schema_version"],
        "suite_sha256": sha256_file(path.resolve(strict=True)),
        "source_inventory_sha256": sha256_bytes(
            canonical_json(source_inventory).encode("utf-8")
        ),
        "categories": list(CATEGORIES),
        "document_count": len(documents),
        "case_count": len(suite["cases"]),
        "models": {
            "dense": LOCAL_DENSE_MODEL,
            "reranker": LOCAL_RERANKER_MODEL,
            "network_policy": "offline",
        },
        "modes": modes,
        "quality_gate": {
            "thresholds": suite["quality_gate"],
            "mode_results": gate_results,
            "passed": all(result["passed"] for result in gate_results.values()),
        },
        "development_fixture": suite["schema_version"] == SUITE_SCHEMA,
        "secret_held_out": False,
        "independently_evaluated": False,
        "competitive_claim_eligible": False,
    }
    if suite["schema_version"] == SUITE_SCHEMA_V2:
        report.update(
            {
                "split": suite["split"],
                "visibility": suite["freeze_policy"]["visibility"],
                "labels_visible": suite["freeze_policy"]["labels_visible"],
                "contamination_claim_eligible": suite["freeze_policy"][
                    "contamination_claim_eligible"
                ],
            }
        )
    report["report_sha256"] = sha256_bytes(canonical_json(report).encode("utf-8"))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the repository-bound multilingual/code/legal/long-document Gold Set"
    )
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path("benchmarks/quality/repository-gold-v1.json"),
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run_suite(arguments.suite, repository=arguments.repository)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        if arguments.output.exists() or arguments.output.is_symlink():
            raise FileExistsError("quality report output already exists")
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0 if report["quality_gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
