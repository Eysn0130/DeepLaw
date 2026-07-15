from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from typing import Any

from .models import SearchRequest
from .search import DeepLaw
from .store import database_sha256
from .util import sha256_file


def evaluate_file(database: Path, cases_path: Path, *, limit: int = 5) -> dict[str, Any]:
    cases = [
        json.loads(line)
        for raw_line in cases_path.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
    ]
    if not cases:
        raise ValueError("evaluation file contains no cases")

    retrieval_successes = 0
    constraint_successes = 0
    overall_successes = 0
    top1_successes = 0
    ranked_cases = 0
    reciprocal_rank = 0.0
    total_chars = 0
    latencies: list[float] = []
    results: list[dict[str, Any]] = []
    release_id = ""
    source_manifest_sha256: str | None = None
    with DeepLaw(database) as law:
        release_id = law.release_id
        source_manifest_sha256 = law.info.get("release", {}).get("source_manifest_sha256")
        for case in cases:
            started = perf_counter()
            response = law.search(
                SearchRequest(
                    query=case["query"],
                    purpose=case.get("purpose", "auto"),
                    as_of=case.get("as_of"),
                    limit=limit,
                )
            )
            latency_ms = (perf_counter() - started) * 1000
            latencies.append(latency_ms)
            titles = [card.title for card in response.evidence]
            articles = [card.article_label for card in response.evidence]
            extraction_review_flags = [
                card.extraction_review_required for card in response.evidence
            ]
            expected_titles = set(case.get("expected_titles", []))
            expected_articles = set(case.get("expected_articles", []))
            expected_empty = bool(case.get("expected_empty", False))
            title_rank = next(
                (index for index, title in enumerate(titles, start=1) if title in expected_titles),
                None,
            )
            article_rank = next(
                (
                    index
                    for index, article in enumerate(articles, start=1)
                    if article in expected_articles
                ),
                None,
            )
            ranks = [rank for rank in (title_rank, article_rank) if rank is not None]
            rank = min(ranks) if ranks else None
            is_ranked_case = bool(expected_titles or expected_articles)
            ranked_cases += int(is_ranked_case)
            if expected_empty:
                retrieval_passed = not response.evidence
            else:
                title_passed = not expected_titles or title_rank is not None
                article_passed = not expected_articles or article_rank is not None
                retrieval_passed = title_passed and article_passed

            forbidden_titles = set(case.get("forbidden_titles", []))
            mode_passed = case.get("expected_mode") in {None, response.mode}
            evidence_bound = int(case.get("max_evidence", limit))
            excerpt_bound = int(case.get("max_excerpt_chars", 6000))
            expected_extraction_review = case.get("expected_extraction_review_required")
            extraction_review_passed = expected_extraction_review is None or (
                bool(extraction_review_flags)
                and extraction_review_flags[0] is bool(expected_extraction_review)
            )
            constraints_passed = (
                not (forbidden_titles & set(titles))
                and mode_passed
                and extraction_review_passed
                and len(response.evidence) <= evidence_bound
                and response.total_excerpt_chars <= excerpt_bound
            )
            passed = retrieval_passed and constraints_passed
            retrieval_successes += int(retrieval_passed)
            constraint_successes += int(constraints_passed)
            overall_successes += int(passed)
            top1_successes += int(is_ranked_case and rank == 1)
            if is_ranked_case:
                reciprocal_rank += 0.0 if rank is None else 1.0 / rank
            total_chars += response.total_excerpt_chars
            results.append(
                {
                    "id": case.get("id"),
                    "query": case["query"],
                    "passed": passed,
                    "retrieval_passed": retrieval_passed,
                    "constraints_passed": constraints_passed,
                    "rank": rank,
                    "returned_titles": titles,
                    "returned_articles": articles,
                    "returned_extraction_review_required": extraction_review_flags,
                    "mode": response.mode,
                    "evidence_count": len(response.evidence),
                    "excerpt_chars": response.total_excerpt_chars,
                    "latency_ms": round(latency_ms, 3),
                }
            )

    latencies.sort()
    p95_index = max(0, min(len(latencies) - 1, int(len(latencies) * 0.95) - 1))
    return {
        "schema_version": "deeplaw.eval-report/v1",
        "release_id": release_id,
        "database_sha256": database_sha256(database),
        "source_manifest_sha256": source_manifest_sha256,
        "cases_sha256": sha256_file(cases_path),
        "case_count": len(cases),
        "retrieval_pass_rate": retrieval_successes / len(cases),
        "constraint_pass_rate": constraint_successes / len(cases),
        "overall_pass_rate": overall_successes / len(cases),
        "ranked_case_count": ranked_cases,
        "hit_at_1": top1_successes / ranked_cases if ranked_cases else None,
        "mrr": reciprocal_rank / ranked_cases if ranked_cases else None,
        "average_excerpt_chars": round(total_chars / len(cases), 3),
        "p50_latency_ms": round(latencies[len(latencies) // 2], 3),
        "p95_latency_ms": round(latencies[p95_index], 3),
        "limit": limit,
        "results": results,
    }
