from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from deeplaw.context_compiler import compile_context, verify_capsule
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_file

if __package__:
    from .benchlib import write_json
else:
    from benchlib import write_json

DATASET_NAME = "LongMemEval-S cleaned"
DATASET_REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
DATASET_SHA256 = "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
SCHEMA_VERSION = "deeplaw.external-dev-diagnostic/v1"


def _value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _case_markdown(case: dict[str, Any]) -> str:
    session_ids = case["haystack_session_ids"]
    sessions = case["haystack_sessions"]
    dates = case["haystack_dates"]
    if not (
        isinstance(session_ids, list)
        and isinstance(sessions, list)
        and isinstance(dates, list)
        and len(session_ids) == len(sessions) == len(dates)
    ):
        raise ValueError(f"case {case.get('question_id')} has invalid session arrays")
    lines = [f"# LongMemEval case {case['question_id']}"]
    for session_id, session_date, messages in zip(
        session_ids,
        dates,
        sessions,
        strict=True,
    ):
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("LongMemEval session ID must be non-empty")
        lines.extend(["", f"## Session {session_id}", "", f"Date: {session_date}"])
        if not isinstance(messages, list):
            raise ValueError(f"LongMemEval session {session_id} must be a message list")
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError(f"LongMemEval session {session_id} has an invalid message")
            lines.extend(
                [
                    "",
                    f"{_value_text(message.get('role', 'unknown')).upper()}:",
                    _value_text(message.get("content", "")),
                ]
            )
    return "\n".join(lines).strip() + "\n"


def _session_id(title: str, session_ids: set[str]) -> str | None:
    if not title.startswith("Session "):
        return None
    value = title.removeprefix("Session ").rsplit(" · part ", 1)[0]
    return value if value in session_ids else None


def _retrieval_metrics(
    retrieved_ids: list[str],
    relevant_ids: list[str],
) -> dict[str, Any]:
    relevant = set(relevant_ids)
    unique_retrieved = list(dict.fromkeys(retrieved_ids))
    first_hit = next(
        (
            rank
            for rank, session_id in enumerate(retrieved_ids, start=1)
            if session_id in relevant
        ),
        None,
    )
    unique_hits = relevant.intersection(unique_retrieved)
    return {
        "returned": len(retrieved_ids),
        "unique_returned": len(unique_retrieved),
        "duplicate_count": len(retrieved_ids) - len(unique_retrieved),
        "hit1": bool(retrieved_ids and retrieved_ids[0] in relevant),
        "recall5": len(unique_hits) / len(relevant),
        "mrr": 1.0 / first_hit if first_hit is not None else 0.0,
        "irrelevant_rate": (
            sum(session_id not in relevant for session_id in retrieved_ids)
            / len(retrieved_ids)
            if retrieved_ids
            else 0.0
        ),
    }


def _selected_cases(
    cases: list[dict[str, Any]],
    *,
    sample_per_type: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        question_type = case.get("question_type")
        question_id = case.get("question_id")
        if not isinstance(question_type, str) or not isinstance(question_id, str):
            raise ValueError("LongMemEval case lacks question_type or question_id")
        grouped[question_type].append(case)
    selected: list[dict[str, Any]] = []
    for question_type in sorted(grouped):
        ranked = sorted(
            grouped[question_type],
            key=lambda item: hashlib.sha256(item["question_id"].encode("utf-8")).hexdigest(),
        )
        selected.extend(ranked[:sample_per_type])
    return selected


def _capsule_items(capsule: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for group in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in capsule[group]
    ]


def run_diagnostic(
    dataset_path: Path,
    *,
    sample_per_type: int,
    limit: int,
    max_chars: int,
    workspace_root: Path,
) -> dict[str, Any]:
    if sha256_file(dataset_path) != DATASET_SHA256:
        raise ValueError("LongMemEval-S cleaned dataset SHA-256 does not match the pin")
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or len(cases) != 500:
        raise ValueError("LongMemEval-S cleaned dataset must contain exactly 500 cases")
    selected = _selected_cases(cases, sample_per_type=sample_per_type)
    results: list[dict[str, Any]] = []
    for case in selected:
        question_id = case["question_id"]
        case_root = workspace_root / question_id
        source_path = case_root / "source.md"
        case_root.mkdir(parents=True, exist_ok=False, mode=0o700)
        source_path.write_text(_case_markdown(case), encoding="utf-8")
        source_path.chmod(0o600)
        ingest_started = time.perf_counter()
        vault_root = case_root / "vault"
        initialize_knowledge_vault(
            vault_root,
            name=f"LongMemEval-S development case {question_id}",
            scope="project",
        )
        quarantined_assets = 0
        with KnowledgeVault(vault_root, read_only=False) as vault:
            compiled = compile_source(
                vault,
                source_path,
                source_kind="conversation",
                title=f"LongMemEval case {question_id}",
                origin_uri=f"benchmark://longmemeval-s/{question_id}",
                trust="untrusted",
                sensitivity="private",
                confirm_no_case_data=True,
            )
            for asset_id in compiled["asset_ids"]:
                asset = vault.get_asset(asset_id, include_inactive=True)
                quarantined = asset.status == "quarantined"
                quarantined_assets += int(quarantined)
                vault.approve_asset(
                    asset_id,
                    confirm_reviewed=True,
                    confirm_quarantined=quarantined,
                )
        ingest_seconds = time.perf_counter() - ingest_started
        session_ids = set(case["haystack_session_ids"])
        with KnowledgeVault(vault_root, read_only=True) as vault:
            search_started = time.perf_counter()
            search = vault.search(
                case["question"],
                limit=limit,
                max_chars=max_chars,
            )
            search_ms = (time.perf_counter() - search_started) * 1_000
            context_started = time.perf_counter()
            capsule = compile_context(
                vault,
                task=case["question"],
                confirm_no_case_data=True,
                max_items=limit,
                max_chars=max_chars,
            )
            context_ms = (time.perf_counter() - context_started) * 1_000
            verification = verify_capsule(capsule, vault=vault)
            if not verification["valid"]:
                raise RuntimeError(
                    f"case {question_id} produced an invalid Capsule: "
                    f"{canonical_json(verification)}"
                )
        search_ids = [
            session_id
            for card in search.results
            if (session_id := _session_id(card.title, session_ids)) is not None
        ]
        context_ids = [
            session_id
            for item in _capsule_items(capsule)
            if (session_id := _session_id(item["title"], session_ids)) is not None
        ]
        relevant = case["answer_session_ids"]
        results.append(
            {
                "id": question_id,
                "type": case["question_type"],
                "relevant": relevant,
                "search_ids": search_ids,
                "context_ids": context_ids,
                "search": _retrieval_metrics(search_ids, relevant),
                "context": _retrieval_metrics(context_ids, relevant),
                "selected_chars": capsule["budget"]["selected_chars"],
                "payload_chars": len(canonical_json(capsule)),
                "ingest_s": ingest_seconds,
                "search_ms": search_ms,
                "context_ms": context_ms,
                "quarantined_assets": quarantined_assets,
            }
        )

    def aggregate(surface: str) -> dict[str, float]:
        return {
            metric: statistics.fmean(float(case[surface][metric]) for case in results)
            for metric in (
                "hit1",
                "recall5",
                "mrr",
                "irrelevant_rate",
                "duplicate_count",
                "unique_returned",
            )
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "claim_eligible": False,
        "reason": "public development sample selected and inspected before protocol freeze",
        "dataset": {
            "name": DATASET_NAME,
            "revision": DATASET_REVISION,
            "sha256": DATASET_SHA256,
        },
        "selection": "smallest sha256(question_id) values per question_type",
        "sample_per_question_type": sample_per_type,
        "case_count": len(results),
        "limit": limit,
        "max_chars": max_chars,
        "search": aggregate("search"),
        "context": aggregate("context"),
        "avg_selected_chars": statistics.fmean(
            float(case["selected_chars"]) for case in results
        ),
        "avg_payload_chars": statistics.fmean(
            float(case["payload_chars"]) for case in results
        ),
        "avg_ingest_s": statistics.fmean(float(case["ingest_s"]) for case in results),
        "avg_search_ms": statistics.fmean(float(case["search_ms"]) for case in results),
        "avg_context_ms": statistics.fmean(float(case["context_ms"]) for case in results),
        "cases": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the claim-ineligible LongMemEval-S development diagnostic "
            "through the real DeepLaw vault."
        )
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--sample-per-type", type=int, default=10)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-chars", type=int, default=5_000)
    parser.add_argument("--workspace-root", type=Path)
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
        report = run_diagnostic(
            args.dataset,
            sample_per_type=args.sample_per_type,
            limit=args.limit,
            max_chars=args.max_chars,
            workspace_root=args.workspace_root,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="deeplaw-longmemeval-s-") as temporary:
            report = run_diagnostic(
                args.dataset,
                sample_per_type=args.sample_per_type,
                limit=args.limit,
                max_chars=args.max_chars,
                workspace_root=Path(temporary),
            )
    write_json(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
