from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from deeplaw import __version__
from deeplaw.knowledge_discovery import (
    DISCOVERY_MODEL_PROFILES,
    DiscoveryIndex,
    build_discovery_index,
    verify_discovery_model,
)
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.util import sha256_file

if __package__:
    from .benchlib import write_json
    from .run_longmemeval_s_dev import (
        DATASET_SHA256,
        _retrieval_metrics,
        _selected_cases,
        _session_id,
        run_diagnostic,
    )
else:
    from benchlib import write_json
    from run_longmemeval_s_dev import (
        DATASET_SHA256,
        _retrieval_metrics,
        _selected_cases,
        _session_id,
        run_diagnostic,
    )

SCHEMA_VERSION = "deeplaw.discovery-development-ablation/v1"


def _aggregate(results: list[dict[str, Any]]) -> dict[str, float]:
    return {
        metric: statistics.fmean(float(case[metric]) for case in results)
        for metric in (
            "hit1",
            "recall5",
            "mrr",
            "irrelevant_rate",
            "duplicate_count",
            "unique_returned",
        )
    }


def _by_type(results: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        grouped[result["type"]].append(result["metrics"])
    return {
        question_type: _aggregate(rows)
        for question_type, rows in sorted(grouped.items())
    }


def _paired_outcomes(
    baseline_cases: dict[str, dict[str, Any]],
    discovery_cases: list[dict[str, Any]],
    *,
    metric: str,
) -> dict[str, int]:
    outcomes = {"improved": 0, "unchanged": 0, "regressed": 0}
    for discovery in discovery_cases:
        baseline = float(baseline_cases[discovery["id"]]["context"][metric])
        candidate = float(discovery["metrics"][metric])
        if candidate > baseline:
            outcomes["improved"] += 1
        elif candidate < baseline:
            outcomes["regressed"] += 1
        else:
            outcomes["unchanged"] += 1
    return outcomes


def run_ablation(
    dataset_path: Path,
    *,
    sample_per_type: int,
    limit: int,
    max_chars: int,
    workspace_root: Path,
    model_root: Path,
    profile: str,
    threads: int | None,
) -> dict[str, Any]:
    if sha256_file(dataset_path) != DATASET_SHA256:
        raise ValueError("LongMemEval-S cleaned dataset SHA-256 does not match the pin")
    baseline = run_diagnostic(
        dataset_path,
        sample_per_type=sample_per_type,
        limit=limit,
        max_chars=max_chars,
        workspace_root=workspace_root,
    )
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    selected = _selected_cases(cases, sample_per_type=sample_per_type)
    model = verify_discovery_model(profile, model_root=model_root)
    results: list[dict[str, Any]] = []
    for case in selected:
        question_id = case["question_id"]
        case_root = workspace_root / question_id
        vault_root = case_root / "vault"
        index_root = case_root / "discovery-index"
        with KnowledgeVault(vault_root, read_only=True) as vault:
            revision_before = vault.revision
            build_started = time.perf_counter()
            built = build_discovery_index(
                vault,
                index_root,
                profile_name=profile,
                model_root=model_root,
                confirm_no_case_data=True,
                threads=threads,
            )
            build_seconds = time.perf_counter() - build_started
            if vault.revision != revision_before:
                raise RuntimeError("derived discovery build mutated the canonical vault")
            index = DiscoveryIndex(
                index_root,
                vault=vault,
                model_root=model_root,
                threads=threads,
            )
            query_started = time.perf_counter()
            candidates = index.search(case["question"], limit=limit)
            query_ms = (time.perf_counter() - query_started) * 1_000
        session_ids = set(case["haystack_session_ids"])
        with KnowledgeVault(vault_root, read_only=True) as vault:
            retrieved_ids = [
                session_id
                for candidate in candidates
                if (
                    session_id := _session_id(
                        vault.get_asset(candidate["asset_id"]).title,
                        session_ids,
                    )
                )
                is not None
            ]
        metrics = _retrieval_metrics(retrieved_ids, case["answer_session_ids"])
        results.append(
            {
                "id": question_id,
                "type": case["question_type"],
                "relevant": case["answer_session_ids"],
                "retrieved_ids": retrieved_ids,
                "metrics": metrics,
                "build_s": build_seconds,
                "query_ms": query_ms,
                "asset_count": built["asset_count"],
                "index_bytes": sum(
                    path.stat().st_size for path in index_root.iterdir() if path.is_file()
                ),
                "index_id": built["index_id"],
            }
        )

    baseline_cases = {case["id"]: case for case in baseline["cases"]}
    repository = Path(__file__).resolve().parents[2]
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_eligible": False,
        "claim_ineligibility_reason": (
            "This public development sample was inspected and used to choose the "
            "projection, model, and pooling policy. It is an ablation, not held-out proof."
        ),
        "dataset": baseline["dataset"],
        "selection": baseline["selection"],
        "budget": baseline["budget"],
        "candidate": {
            "version": __version__,
            "surface": "explicit_derived_discovery_only",
            "default_runtime_enabled": False,
            "authoritative": False,
            "legal_authority": False,
            "case_data_allowed": False,
            "source_bound": True,
            "model": {
                **{
                    key: model[key]
                    for key in (
                        "profile",
                        "model_id",
                        "source_repository",
                        "source_revision",
                        "dimension",
                        "runtime",
                    )
                },
                "files": model["files"],
            },
        },
        "baseline": {
            "search": baseline["search"],
            "context": baseline["context"],
            "avg_selected_chars": baseline["avg_selected_chars"],
        },
        "discovery": {
            "overall": _aggregate([result["metrics"] for result in results]),
            "by_type": _by_type(results),
            "avg_build_s": statistics.fmean(result["build_s"] for result in results),
            "avg_query_ms": statistics.fmean(result["query_ms"] for result in results),
            "avg_index_bytes": statistics.fmean(
                result["index_bytes"] for result in results
            ),
        },
        "paired_context_comparison": {
            metric: _paired_outcomes(
                baseline_cases,
                results,
                metric=metric,
            )
            for metric in ("hit1", "recall5", "mrr")
        },
        "decision": {
            "default_activation": "rejected",
            "reason": (
                "A public development ablation cannot activate a derived candidate "
                "by itself. The surface remains explicit and removable until held-out "
                "task-success, irrelevant-context, provenance, cost, and safety gates "
                "all pass."
            ),
        },
        "implementation_files": {
            path: sha256_file(repository / path)
            for path in (
                "src/deeplaw/knowledge_discovery.py",
                "src/deeplaw/knowledge_compiler.py",
                "src/deeplaw/knowledge_store.py",
                "benchmarks/external/run_longmemeval_s_dev.py",
                "benchmarks/external/run_longmemeval_discovery_dev.py",
                "pyproject.toml",
                "uv.lock",
            )
        },
        "cases": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a claim-ineligible source-bound discovery ablation through the "
            "real DeepLaw compiler and vault."
        )
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--sample-per-type", type=int, default=10)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-chars", type=int, default=5_000)
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument(
        "--profile",
        choices=sorted(DISCOVERY_MODEL_PROFILES),
        default="english",
    )
    parser.add_argument("--threads", type=int)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 1 <= args.sample_per_type <= 50:
        raise ValueError("sample-per-type must be between 1 and 50")
    if not 1 <= args.limit <= 12:
        raise ValueError("limit must be between 1 and 12")
    if not 1 <= args.max_chars <= 12_000:
        raise ValueError("max-chars must be between 1 and 12000")
    if args.workspace_root is not None:
        args.workspace_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        report = run_ablation(
            args.dataset,
            sample_per_type=args.sample_per_type,
            limit=args.limit,
            max_chars=args.max_chars,
            workspace_root=args.workspace_root,
            model_root=args.model_root,
            profile=args.profile,
            threads=args.threads,
        )
    else:
        with tempfile.TemporaryDirectory(
            prefix="deeplaw-longmemeval-discovery-"
        ) as temporary:
            report = run_ablation(
                args.dataset,
                sample_per_type=args.sample_per_type,
                limit=args.limit,
                max_chars=args.max_chars,
                workspace_root=Path(temporary),
                model_root=args.model_root,
                profile=args.profile,
                threads=args.threads,
            )
    write_json(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
