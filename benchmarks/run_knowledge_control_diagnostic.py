from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import Any

from deeplaw import __version__
from deeplaw.context_compiler import compile_context
from deeplaw.knowledge_compiler import compile_directory, compile_source
from deeplaw.knowledge_feedback import (
    create_run_receipt,
    record_structured_feedback,
    replay_feedback,
)
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.knowledge_models import utc_now
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_bytes, sha256_file

_FIXTURE_SOURCE_COUNT = 24
_QUERY_COUNT = 20


def _source_tree_sha256(repository: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((repository / "src" / "deeplaw").rglob("*.py")):
        digest.update(path.relative_to(repository).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _latencies(values: list[float]) -> dict[str, Any]:
    return {
        "samples": len(values),
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "p99_ms": round(_percentile(values, 0.99), 3),
        "max_ms": round(max(values, default=0.0), 3),
    }


def _timed(call: Callable[[], Any]) -> tuple[Any, float]:
    started = perf_counter()
    result = call()
    return result, (perf_counter() - started) * 1_000


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _selected_items(capsule: dict[str, Any]) -> list[dict[str, Any]]:
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


def _maximum_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def run_diagnostic(repository: Path) -> dict[str, Any]:
    queries = [f"benchentity{index:02d}" for index in range(_QUERY_COUNT)]
    fixture_records = [
        {
            "name": f"source-{index:02d}.md",
            "title": "Architecture",
            "token": f"benchentity{index:02d}",
            "text": (
                f"Project {index:02d} architecture keeps benchentity{index:02d} "
                f"and review-bound provenance."
            ),
        }
        for index in range(_FIXTURE_SOURCE_COUNT)
    ]
    fixture_records[0]["text"] += " Shared comparison marker crimsonalpha."
    fixture_records[1]["text"] += " Shared comparison marker cobaltbeta."
    fixture_sha256 = sha256_bytes(canonical_json(fixture_records).encode("utf-8"))
    query_sha256 = sha256_bytes(canonical_json(queries).encode("utf-8"))

    with tempfile.TemporaryDirectory(prefix="deeplaw-control-diagnostic-") as temporary:
        root = Path(temporary)
        source_root = root / "sources"
        source_root.mkdir()
        for record in fixture_records:
            (source_root / record["name"]).write_text(
                f"# {record['title']}\n{record['text']}\n",
                encoding="utf-8",
            )
        vault_root = root / "vault"
        initialize_knowledge_vault(vault_root, name="control-diagnostic", scope="project")

        with KnowledgeVault(vault_root, read_only=False) as vault:
            directory_result, ingest_ms = _timed(
                lambda: compile_directory(
                    vault,
                    source_root,
                    recursive=True,
                    include=("*.md",),
                    confirm_no_case_data=True,
                    typed_extraction="off",
                )
            )
            review_latencies: list[float] = []
            for source_id in directory_result["source_ids"]:
                manifest = vault.source_review_manifest(source_id)
                _, elapsed = _timed(
                    lambda source_id=source_id, manifest=manifest: vault.approve_source_assets(
                        source_id,
                        confirm_reviewed=True,
                        review_manifest_sha256=manifest["review_manifest_sha256"],
                        reviewer_id="diagnostic-operator",
                        review_reason="Synthetic diagnostic fixture review.",
                    )
                )
                review_latencies.append(elapsed)
            integrity_after_review = vault.verify_integrity()["valid"]

        persistent_search: list[float] = []
        persistent_context: list[float] = []
        hit1 = 0
        source_ref_coverage: list[float] = []
        for query in queries:
            response, elapsed = _timed(
                lambda query=query: handle_knowledge_support(
                    operation="search",
                    query=query,
                    vault_path=vault_root,
                )
            )
            persistent_search.append(elapsed)
            results = response["result"]["results"]
            hit1 += int(bool(results) and query in results[0]["excerpt"])
            context_response, elapsed = _timed(
                lambda query=query: handle_knowledge_support(
                    operation="context",
                    task=f"Use {query} while preserving review-bound provenance.",
                    confirm_no_case_data=True,
                    vault_path=vault_root,
                )
            )
            persistent_context.append(elapsed)
            items = _selected_items(context_response["result"])
            source_bound = [item for item in items if item["source_refs"]]
            source_ref_coverage.append(
                1.0
                if not source_bound
                else sum(bool(item["source_refs"]) for item in source_bound) / len(source_bound)
            )

        cold_cli_search: list[float] = []
        for query in queries[:5]:
            started = perf_counter()
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "deeplaw",
                    "knowledge",
                    "search",
                    "--vault",
                    str(vault_root),
                    "--query",
                    query,
                ],
                cwd=repository,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            cold_cli_search.append((perf_counter() - started) * 1_000)
            if completed.returncode != 0:
                raise RuntimeError(f"cold CLI diagnostic failed: {completed.stderr[:500]}")

        with KnowledgeVault(vault_root, read_only=True) as vault:
            dual = compile_context(
                vault,
                task="Compare crimsonalpha with cobaltbeta architecture evidence.",
                confirm_no_case_data=True,
                max_items=5,
                max_chars=4_000,
            )
            dual_items = _selected_items(dual)
            dual_sources = {
                reference["source_id"] for item in dual_items for reference in item["source_refs"]
            }
        same_title_cross_source_preserved = len(dual_sources) >= 2

        update_path = source_root / fixture_records[0]["name"]
        update_path.write_text(
            "# Architecture\n"
            "Project 00 architecture keeps benchentity00, review-bound provenance, "
            "and replacement marker emeraldgamma.\n",
            encoding="utf-8",
        )
        with KnowledgeVault(vault_root, read_only=False) as vault:
            old_source = vault.source_info(directory_result["source_ids"][0])
            successor, update_compile_ms = _timed(
                lambda: compile_source(
                    vault,
                    update_path,
                    source_kind="document",
                    source_key=old_source["source_key"],
                    confirm_no_case_data=True,
                )
            )
            source_diff = vault.source_diff(
                old_source["source_id"],
                successor["source"]["source_id"],
            )
            old_visible_before_review = bool(vault.search("crimsonalpha").results)
            successor_manifest = vault.source_review_manifest(successor["source"]["source_id"])
            _, update_review_ms = _timed(
                lambda: vault.approve_source_assets(
                    successor["source"]["source_id"],
                    confirm_reviewed=True,
                    review_manifest_sha256=successor_manifest["review_manifest_sha256"],
                    reviewer_id="diagnostic-operator",
                    review_reason="Synthetic successor review.",
                )
            )
            old_visible_after_review = bool(vault.search("crimsonalpha").results)
            new_visible_after_review = bool(vault.search("emeraldgamma").results)

            capsule = compile_context(
                vault,
                task="Use emeraldgamma and identify the missing rollback owner.",
                confirm_no_case_data=True,
            )
            capsule_path = root / "capsule.json"
            capsule_path.write_text(
                json.dumps(capsule, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            run, run_receipt_ms = _timed(
                lambda: create_run_receipt(
                    vault,
                    capsule_path=capsule_path,
                    status="partial",
                    host_name="diagnostic",
                    host_version=__version__,
                )
            )
            feedback, feedback_ms = _timed(
                lambda: record_structured_feedback(
                    vault,
                    run_id=run["run_id"],
                    outcome="partial",
                    missing_knowledge=("The rollback owner is not documented.",),
                    observation="The reviewed successor was available.",
                    recommended_action="Review a source-bound rollback owner decision.",
                )
            )
            replay, replay_ms = _timed(
                lambda: replay_feedback(
                    vault,
                    feedback_id=feedback["feedback_id"],
                    capsule_path=capsule_path,
                )
            )
            final_integrity = vault.verify_integrity()["valid"]
            inspection = vault.inspect()
            database_bytes = vault.database.stat().st_size

        git_status = _git(repository, "status", "--porcelain")
        report = {
            "schema_version": "deeplaw.knowledge-control-diagnostic/v1",
            "recorded_at": utc_now(),
            "claim_eligible": False,
            "claim_ineligibility_reason": (
                "Synthetic development fixture; not a secret held-out or independent "
                f"evaluation. Tracked working tree dirty={bool(git_status)}."
            ),
            "implementation": {
                "package_version": __version__,
                "git_head": _git(repository, "rev-parse", "HEAD"),
                "working_tree_dirty": bool(git_status),
                "pyproject_sha256": sha256_file(repository / "pyproject.toml"),
                "uv_lock_sha256": sha256_file(repository / "uv.lock"),
                "python_source_tree_sha256": _source_tree_sha256(repository),
            },
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "machine": platform.machine(),
                "processor": platform.processor() or None,
                "logical_cpu_count": os.cpu_count(),
            },
            "fixture": {
                "synthetic": True,
                "source_count": _FIXTURE_SOURCE_COUNT,
                "query_count": _QUERY_COUNT,
                "fixture_sha256": fixture_sha256,
                "query_sha256": query_sha256,
            },
            "quality": {
                "exact_hit1": hit1 / len(queries),
                "source_ref_coverage": sum(source_ref_coverage) / len(source_ref_coverage),
                "same_title_cross_source_preserved": same_title_cross_source_preserved,
                "source_update": {
                    "diff_changed_count": source_diff["changed_count"],
                    "old_visible_before_review": old_visible_before_review,
                    "old_visible_after_review": old_visible_after_review,
                    "new_visible_after_review": new_visible_after_review,
                    "atomic_switch_passed": (
                        old_visible_before_review
                        and not old_visible_after_review
                        and new_visible_after_review
                    ),
                },
                "run_receipt_valid": run["valid"],
                "feedback_valid": feedback["valid"],
                "replay_task_success_inferred": replay["task_success_inferred"],
                "integrity_after_review": integrity_after_review,
                "final_integrity": final_integrity,
            },
            "performance": {
                "directory_ingest_ms": round(ingest_ms, 3),
                "source_reviews": _latencies(review_latencies),
                "persistent_mcp_search": _latencies(persistent_search),
                "persistent_mcp_context": _latencies(persistent_context),
                "cold_cli_search": _latencies(cold_cli_search),
                "source_update_compile_ms": round(update_compile_ms, 3),
                "source_update_review_ms": round(update_review_ms, 3),
                "run_receipt_ms": round(run_receipt_ms, 3),
                "structured_feedback_ms": round(feedback_ms, 3),
                "feedback_replay_ms": round(replay_ms, 3),
            },
            "resources": {
                "database_bytes": database_bytes,
                "peak_process_rss_bytes": _maximum_rss_bytes(),
                "asset_count": inspection["integrity"]["state"]["asset_count"],
                "source_count": inspection["integrity"]["state"]["source_count"],
                "fragment_count": inspection["integrity"]["state"]["fragment_count"],
                "review_receipt_count": inspection["integrity"]["state"]["review_receipt_count"],
                "run_receipt_count": inspection["integrity"]["state"]["run_receipt_count"],
                "feedback_count": inspection["integrity"]["state"]["feedback_count"],
            },
            "limits": [
                "Synthetic exact-token fixture does not establish semantic retrieval quality.",
                "Persistent MCP measurements exercise the in-process MCP handler and integrity "
                "cache, not transport or concurrent multi-client load.",
                "Cold CLI samples include process startup and are machine-specific.",
                "No optional discovery model, external baseline, hidden label, or evaluator "
                "signature was used.",
            ],
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    report = run_diagnostic(repository)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
        return
    output = args.output.expanduser().absolute()
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise FileExistsError("diagnostic output must be a regular non-symlink path")
    if output.exists() and not args.replace:
        raise FileExistsError("diagnostic output exists; pass --replace to replace it atomically")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
