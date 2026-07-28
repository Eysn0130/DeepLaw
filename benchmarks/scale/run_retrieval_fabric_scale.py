from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from deeplaw import __version__
from deeplaw.context_compiler import verify_capsule
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.retrieval_fabric import recall, retrieve
from deeplaw.util import sha256_file

SCHEMA_VERSION = "deeplaw.retrieval-fabric-scale-diagnostic/v1"
_MAX_ASSETS_PER_SOURCE = 100_000
_COLD_CLI_TIMEOUT_SECONDS = 900


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * percentile)]


def _peak_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _write_corpus(path: Path, *, start: int, count: int) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for index in range(start, start + count):
            stream.write(
                f"# Knowledge {index:07d}\n"
                f"Operational record {index:07d} binds unique token "
                f"glyph{index:07d} to verified procedure step {index % 997:03d}.\n"
            )


def _write_lifecycle_probe(path: Path, *, updated: bool) -> None:
    update_marker = (
        "lifecycleemerald7777777" if updated else "lifecycleamber7777777"
    )
    path.write_text(
        "# Knowledge update probe\n"
        f"The deployment profile uses marker {update_marker}.\n\n"
        "# Knowledge forgetting probe\n"
        "The obsolete preference uses marker lifecycleviolet7777777.\n",
        encoding="utf-8",
    )


def _exercise_lifecycle(workspace: Path, vault_root: Path) -> dict[str, Any]:
    source = workspace / "lifecycle-probe.md"
    _write_lifecycle_probe(source, updated=False)
    with KnowledgeVault(vault_root, read_only=False) as vault:
        initial_started = time.perf_counter()
        initial = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
            logical_path="scale/lifecycle-probe.md",
        )
        initial_manifest = vault.source_review_manifest(initial["source"]["source_id"])
        initial_approval = vault.approve_source_assets(
            initial["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=initial_manifest["review_manifest_sha256"],
        )
        initial_seconds = time.perf_counter() - initial_started
        initial_old = retrieve(
            vault,
            "lifecycleamber7777777",
            mode="hybrid",
            limit=5,
        )

        _write_lifecycle_probe(source, updated=True)
        update_started = time.perf_counter()
        updated = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
            logical_path="scale/lifecycle-probe.md",
        )
        before_approval_old = retrieve(
            vault,
            "lifecycleamber7777777",
            mode="hybrid",
            limit=5,
        )
        updated_manifest = vault.source_review_manifest(updated["source"]["source_id"])
        updated_approval = vault.approve_source_assets(
            updated["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=updated_manifest["review_manifest_sha256"],
        )
        update_seconds = time.perf_counter() - update_started
        after_approval_old = retrieve(
            vault,
            "lifecycleamber7777777",
            mode="hybrid",
            limit=5,
        )
        after_approval_new = retrieve(
            vault,
            "lifecycleemerald7777777",
            mode="hybrid",
            limit=5,
        )
        if not after_approval_new["results"]:
            raise RuntimeError("lifecycle update probe did not retrieve its new revision")
        updated_asset_id = after_approval_new["results"][0]["asset_id"]
        updated_lineage = vault.knowledge_lineage(asset_id=updated_asset_id)

        forgetting_candidate = retrieve(
            vault,
            "lifecycleviolet7777777",
            mode="hybrid",
            limit=5,
        )
        if not forgetting_candidate["results"]:
            raise RuntimeError("lifecycle forgetting probe is unavailable")
        forgotten_asset_id = forgetting_candidate["results"][0]["asset_id"]
        binding = vault.connection.execute(
            """
            SELECT knowledge_revisions_v2.knowledge_key
            FROM asset_revision_bindings_v2
            JOIN knowledge_revisions_v2 USING(asset_revision_id)
            WHERE legacy_asset_id = ?
            """,
            (forgotten_asset_id,),
        ).fetchone()
        if binding is None:
            raise RuntimeError("lifecycle forgetting probe has no Knowledge Identity v2 binding")
        forgetting_started = time.perf_counter()
        forgotten = vault.selectively_forget(
            knowledge_key=binding["knowledge_key"],
            reason="The scale diagnostic explicitly forgets its synthetic lifecycle probe.",
            confirm=True,
        )
        forgetting_seconds = time.perf_counter() - forgetting_started
        post_forgetting_started = time.perf_counter()
        after_forgetting = retrieve(
            vault,
            "lifecycleviolet7777777",
            mode="hybrid",
            limit=5,
        )
        post_forgetting_retrieval_and_integrity_seconds = (
            time.perf_counter() - post_forgetting_started
        )
        forgotten_lineage = vault.knowledge_lineage(
            knowledge_key=binding["knowledge_key"]
        )
    with KnowledgeVault(vault_root, read_only=True) as final_vault:
        final_integrity_started = time.perf_counter()
        final_integrity = final_vault.verify_integrity()
        final_integrity_seconds = time.perf_counter() - final_integrity_started
        final_counts = {
            "stored_asset_count": final_vault.connection.execute(
                "SELECT COUNT(*) AS count FROM assets"
            ).fetchone()["count"],
            "active_asset_count": final_vault.connection.execute(
                "SELECT COUNT(*) AS count FROM assets WHERE status = 'active'"
            ).fetchone()["count"],
        }

    update_correct = bool(
        initial_old["results"]
        and before_approval_old["results"]
        and not after_approval_old["results"]
        and after_approval_new["results"]
        and any(
            transition["status"] == "modified"
            for transition in updated_lineage["transitions"]
        )
    )
    forgetting_correct = bool(
        not after_forgetting["results"]
        and forgotten["history_retained"]
        and forgotten["current_retrieval_eligible"] is False
        and forgotten_lineage["revisions"]
    )
    return {
        "initial_compile_and_approval_seconds": initial_seconds,
        "source_update_seconds": update_seconds,
        "selective_forgetting_seconds": forgetting_seconds,
        "post_forgetting_retrieval_and_integrity_seconds": (
            post_forgetting_retrieval_and_integrity_seconds
        ),
        "final_integrity_cache_lookup_seconds": final_integrity_seconds,
        "final_integrity_valid": final_integrity["valid"],
        "initial_source_id": initial["source"]["source_id"],
        "updated_source_id": updated["source"]["source_id"],
        "source_key_stable": (
            initial["identity"]["source_key"] == updated["identity"]["source_key"]
        ),
        "source_revision_changed": (
            initial["identity"]["source_revision_id"]
            != updated["identity"]["source_revision_id"]
        ),
        "initial_approved_asset_count": initial_approval["approved_asset_count"],
        "updated_approved_asset_count": updated_approval["approved_asset_count"],
        "update_correct": update_correct,
        "updated_asset_id": updated_asset_id,
        "updated_knowledge_key": updated_lineage["knowledge_key"],
        "forgetting_correct": forgetting_correct,
        "forgotten_asset_id": forgotten_asset_id,
        "forgotten_knowledge_key": forgotten["knowledge_key"],
        "history_retained": forgotten["history_retained"],
        **final_counts,
    }


def _git_state(repository: Path) -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return {
        "commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "tracked_or_untracked_worktree_dirty": bool(status.stdout.strip()),
    }


def run_diagnostic(
    workspace: Path,
    *,
    asset_count: int,
    query_count: int,
) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=False, mode=0o700)
    vault_root = workspace / "vault"
    initialize_knowledge_vault(
        vault_root,
        name="Retrieval Fabric scale diagnostic",
        scope="domain",
    )
    source_records: list[dict[str, Any]] = []
    build_started = time.perf_counter()
    with KnowledgeVault(vault_root, read_only=False) as vault:
        for chunk_start in range(0, asset_count, _MAX_ASSETS_PER_SOURCE):
            chunk_count = min(_MAX_ASSETS_PER_SOURCE, asset_count - chunk_start)
            source = workspace / f"corpus-{chunk_start:07d}.md"
            _write_corpus(source, start=chunk_start, count=chunk_count)
            compile_started = time.perf_counter()
            compiled = compile_source(
                vault,
                source,
                source_kind="document",
                confirm_no_case_data=True,
                collection_id=None,
                logical_path=f"scale/{source.name}",
            )
            compile_seconds = time.perf_counter() - compile_started
            review = vault.source_review_manifest(compiled["source"]["source_id"])
            approval_started = time.perf_counter()
            approved = vault.approve_source_assets(
                compiled["source"]["source_id"],
                confirm_reviewed=True,
                review_manifest_sha256=review["review_manifest_sha256"],
            )
            source_records.append(
                {
                    "source_id": compiled["source"]["source_id"],
                    "source_revision_id": compiled["source"]["source_revision_id"],
                    "content_sha256": sha256_file(source),
                    "asset_count": chunk_count,
                    "compile_seconds": compile_seconds,
                    "approval_seconds": time.perf_counter() - approval_started,
                    "approved_asset_count": approved["approved_asset_count"],
                }
            )
    build_seconds = time.perf_counter() - build_started

    cold_target = 17 % asset_count
    cold_query = (
        f"请查找唯一运行标识 glyph{cold_target:07d} 对应的 procedure record"
    )
    cold_cli_started = time.perf_counter()
    cold_cli = subprocess.run(
        [
            sys.executable,
            "-c",
            "from deeplaw.cli import main; main()",
            "recall",
            cold_query,
            "--vault",
            str(vault_root),
            "--mode",
            "hybrid",
            "--max-items",
            "5",
            "--max-tokens",
            "2048",
            "--confirm-no-case-data",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=_COLD_CLI_TIMEOUT_SECONDS,
    )
    cold_cli_process_ms = (time.perf_counter() - cold_cli_started) * 1_000
    if cold_cli.returncode != 0:
        raise RuntimeError(
            "cold Retrieval Fabric CLI failed: "
            + (cold_cli.stderr or cold_cli.stdout)[:2_000]
        )
    cold_cli_payload = json.loads(cold_cli.stdout)
    cold_cli_capsule_valid = bool(
        cold_cli_payload.get("capsule_verification", {}).get("valid")
    )
    if not cold_cli_capsule_valid:
        raise RuntimeError("cold Retrieval Fabric CLI returned an invalid Capsule")

    opened_at = time.perf_counter()
    vault = KnowledgeVault(vault_root, read_only=True)
    open_seconds = time.perf_counter() - opened_at
    try:
        integrity_started = time.perf_counter()
        integrity = vault.verify_integrity()
        cold_integrity_seconds = time.perf_counter() - integrity_started
        scale_asset_count = vault.connection.execute(
            "SELECT COUNT(*) AS count FROM assets"
        ).fetchone()["count"]
        lexical_latencies: list[float] = []
        hybrid_latencies: list[float] = []
        recall_latencies: list[float] = []
        lexical_hits = 0
        hybrid_hits = 0
        capsule_hits = 0
        capsule_valid = 0
        provenance_complete = 0
        selected_tokens: list[int] = []
        for ordinal in range(query_count):
            target = (ordinal * 99_991 + 17) % asset_count
            target_title = f"Knowledge {target:07d}"
            query = f"请查找唯一运行标识 glyph{target:07d} 对应的 procedure record"

            started = time.perf_counter()
            lexical = retrieve(vault, query, mode="lexical", limit=5, max_chars=5_000)
            lexical_latencies.append((time.perf_counter() - started) * 1_000)
            lexical_hits += int(
                bool(lexical["results"] and lexical["results"][0]["title"] == target_title)
            )

            started = time.perf_counter()
            hybrid = retrieve(vault, query, mode="hybrid", limit=5, max_chars=5_000)
            hybrid_latencies.append((time.perf_counter() - started) * 1_000)
            hybrid_hits += int(
                bool(hybrid["results"] and hybrid["results"][0]["title"] == target_title)
            )

            started = time.perf_counter()
            recalled = recall(
                vault,
                query,
                confirm_no_case_data=True,
                mode="hybrid",
                max_items=5,
                max_chars=5_000,
                max_tokens=2_048,
            )
            recall_latencies.append((time.perf_counter() - started) * 1_000)
            capsule = recalled["capsule"]
            capsule_hits += int(target_title in json.dumps(capsule, ensure_ascii=False))
            capsule_valid += int(verify_capsule(capsule, vault=vault)["valid"])
            selected = recalled["retrieval"]["results"]
            provenance_complete += int(
                bool(selected) and all(item["source_refs"] for item in selected)
            )
            selected_tokens.append(capsule["budget"]["selected_tokens"])

        no_answer = retrieve(
            vault,
            "zzyzx-absent-scale-evidence-999999999",
            mode="hybrid",
            limit=5,
        )
    finally:
        vault.close()

    lifecycle = _exercise_lifecycle(workspace, vault_root)

    lexical_p95 = _percentile(lexical_latencies, 0.95)
    hybrid_p95 = _percentile(hybrid_latencies, 0.95)
    recall_p95 = _percentile(recall_latencies, 0.95)
    database = vault_root / "vault.sqlite3"
    repository = Path(__file__).resolve().parents[2]
    formal_100k = asset_count == 100_000
    million_diagnostic = asset_count == 1_000_000
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_eligible": False,
        "claim_ineligibility_reason": (
            "deterministic exact-token synthetic corpus produced by the development team; "
            "not a natural-language or competitor benchmark"
        ),
        "candidate": {
            "candidate_line": "0.7.0-unreleased",
            "package_version": __version__,
            **_git_state(repository),
            "implementation_files": {
                path: sha256_file(repository / path)
                for path in (
                    "src/deeplaw/knowledge_store.py",
                    "src/deeplaw/retrieval_fabric.py",
                    "src/deeplaw/context_compiler.py",
                    "benchmarks/scale/run_retrieval_fabric_scale.py",
                )
            },
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "corpus": {
            "asset_count": asset_count,
            "query_count": query_count,
            "source_count": len(source_records),
            "stored_asset_count_before_lifecycle_probe": scale_asset_count,
            "stored_asset_count_after_lifecycle_probe": lifecycle["stored_asset_count"],
            "active_asset_count_after_lifecycle_probe": lifecycle["active_asset_count"],
            "database_bytes": database.stat().st_size,
            "sources": source_records,
        },
        "build": {
            "total_seconds": build_seconds,
            "open_seconds": open_seconds,
            "cold_integrity_seconds": cold_integrity_seconds,
            "integrity_valid": integrity["valid"] and lifecycle["final_integrity_valid"],
            "final_integrity_cache_lookup_seconds": lifecycle[
                "final_integrity_cache_lookup_seconds"
            ],
            "peak_rss_bytes": _peak_rss_bytes(),
            "cold_cli_process_ms": cold_cli_process_ms,
            "cold_cli_exit_code": cold_cli.returncode,
            "cold_cli_capsule_valid": cold_cli_capsule_valid,
        },
        "measurements": {
            "lexical_hit_at_1": lexical_hits / query_count,
            "hybrid_hit_at_1": hybrid_hits / query_count,
            "capsule_recall": capsule_hits / query_count,
            "capsule_verification_rate": capsule_valid / query_count,
            "provenance_coverage": provenance_complete / query_count,
            "no_answer_empty": not no_answer["results"],
            "lexical_p50_ms": statistics.median(lexical_latencies),
            "lexical_p95_ms": lexical_p95,
            "hybrid_p50_ms": statistics.median(hybrid_latencies),
            "hybrid_p95_ms": hybrid_p95,
            "recall_and_context_p50_ms": statistics.median(recall_latencies),
            "recall_and_context_p95_ms": recall_p95,
            "average_selected_tokens_estimated": statistics.fmean(selected_tokens),
            "token_count_mode": "estimated",
        },
        "lifecycle": lifecycle,
        "thresholds": {
            "formal_100k_run": formal_100k,
            "million_asset_diagnostic_run": million_diagnostic,
            "warm_lexical_p95_lt_50_ms": lexical_p95 < 50,
            "warm_hybrid_p95_lt_500_ms": hybrid_p95 < 500,
            "recall_and_context_p95_lt_750_ms": recall_p95 < 750,
            "quality_and_integrity_passed": all(
                (
                    integrity["valid"],
                    lifecycle["final_integrity_valid"],
                    scale_asset_count == asset_count,
                    lifecycle["source_key_stable"],
                    lifecycle["source_revision_changed"],
                    lifecycle["update_correct"],
                    lifecycle["forgetting_correct"],
                    lexical_hits == query_count,
                    hybrid_hits == query_count,
                    capsule_hits == query_count,
                    capsule_valid == query_count,
                    provenance_complete == query_count,
                    not no_answer["results"],
                    cold_cli_capsule_valid,
                )
            ),
        },
        "limitations": [
            "Unique tokens measure mechanical indexing and lifecycle correctness only.",
            "Hybrid ran without an optional Dense index or local reranker unless configured.",
            "A dirty worktree report cannot become a frozen release artifact.",
            "Only an actual one-million-Asset run may be described as the million diagnostic.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a claim-ineligible Retrieval Fabric scale diagnostic."
    )
    parser.add_argument("--asset-count", type=int, default=100_000)
    parser.add_argument("--query-count", type=int, default=100)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 100 <= args.asset_count <= 1_000_000:
        raise ValueError("asset-count must be between 100 and 1000000")
    if not 1 <= args.query_count <= 1_000:
        raise ValueError("query-count must be between 1 and 1000")
    if args.workspace is None:
        with tempfile.TemporaryDirectory(prefix="deeplaw-retrieval-scale-") as temporary:
            report = run_diagnostic(
                Path(temporary) / "workspace",
                asset_count=args.asset_count,
                query_count=args.query_count,
            )
    else:
        report = run_diagnostic(
            args.workspace.expanduser().absolute(),
            asset_count=args.asset_count,
            query_count=args.query_count,
        )
    output = args.output.expanduser().absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
