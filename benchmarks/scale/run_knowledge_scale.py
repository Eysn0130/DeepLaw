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
from deeplaw.context_compiler import compile_context, verify_capsule
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.util import canonical_json, sha256_file

SCHEMA_VERSION = "deeplaw.knowledge-scale-diagnostic/v1"


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


def _run_cli(*arguments: str) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "deeplaw", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError(
            f"DeepLaw CLI failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("DeepLaw CLI did not return a JSON object")
    return value, elapsed


def _write_corpus(path: Path, *, asset_count: int) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for index in range(asset_count):
            stream.write(
                f"# Knowledge {index:05d}\n"
                f"Operational record {index:05d} binds unique token "
                f"glyph{index:05d} to verified procedure step {index % 97:02d}.\n"
            )


def _peak_rss_bytes() -> int:
    if sys.platform == "win32":
        return _windows_peak_rss_bytes()

    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _windows_peak_rss_bytes() -> int:
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, "GetProcessMemoryInfo failed")
    return int(counters.PeakWorkingSetSize)


def run_diagnostic(
    workspace: Path,
    *,
    asset_count: int,
    query_count: int,
) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=False, mode=0o700)
    source = workspace / "corpus.md"
    vault_root = workspace / "vault"
    capsule_path = workspace / "cli-capsule.json"
    _write_corpus(source, asset_count=asset_count)
    source_sha256 = sha256_file(source)

    _, init_seconds = _run_cli(
        "knowledge",
        "init",
        "--vault",
        str(vault_root),
        "--name",
        "DeepLaw deterministic scale diagnostic",
        "--scope",
        "domain",
    )
    ingested, ingest_seconds = _run_cli(
        "knowledge",
        "ingest",
        "--vault",
        str(vault_root),
        "--source",
        str(source),
        "--source-kind",
        "document",
        "--sensitivity",
        "private",
        "--confirm-no-case-data",
    )
    source_id = ingested["source"]["source_id"]
    review_manifest, _ = _run_cli(
        "knowledge",
        "review",
        "manifest",
        "--vault",
        str(vault_root),
        "--source-id",
        source_id,
    )
    approved, approval_seconds = _run_cli(
        "knowledge",
        "approve-source",
        "--vault",
        str(vault_root),
        "--source-id",
        source_id,
        "--review-manifest-sha256",
        review_manifest["review_manifest_sha256"],
        "--confirm-reviewed",
    )

    prefix = " ".join(f"irrelevantprefix{index:02d}" for index in range(40))
    cli_target = (asset_count * 7 // 11) % asset_count
    cli_query = f"{prefix} glyph{cli_target:05d}"
    cli_search, cli_search_seconds = _run_cli(
        "knowledge",
        "search",
        "--vault",
        str(vault_root),
        "--query",
        cli_query,
        "--limit",
        "5",
        "--max-chars",
        "5000",
    )
    cli_context, cli_context_seconds = _run_cli(
        "knowledge",
        "context",
        "--vault",
        str(vault_root),
        "--task",
        cli_query,
        "--max-items",
        "5",
        "--max-chars",
        "5000",
        "--confirm-no-case-data",
        "--output",
        str(capsule_path),
    )
    cli_verification, cli_verify_seconds = _run_cli(
        "knowledge",
        "verify-capsule",
        "--vault",
        str(vault_root),
        "--capsule",
        str(capsule_path),
    )

    opened_at = time.perf_counter()
    vault = KnowledgeVault(vault_root, read_only=True)
    open_seconds = time.perf_counter() - opened_at
    try:
        verification_started = time.perf_counter()
        integrity = vault.verify_integrity()
        cold_integrity_seconds = time.perf_counter() - verification_started
        search_latencies: list[float] = []
        context_latencies: list[float] = []
        search_hits = 0
        capsule_hits = 0
        capsule_valid = 0
        selected_chars: list[int] = []
        payload_chars: list[int] = []
        for ordinal in range(query_count):
            target = (ordinal * 9_973 + 17) % asset_count
            target_title = f"Knowledge {target:05d}"
            query = f"{prefix} glyph{target:05d}"
            search_started = time.perf_counter()
            search = vault.search(query, limit=5, max_chars=5_000)
            search_latencies.append((time.perf_counter() - search_started) * 1_000)
            if search.results and search.results[0].title == target_title:
                search_hits += 1

            context_started = time.perf_counter()
            capsule = compile_context(
                vault,
                task=query,
                confirm_no_case_data=True,
                max_items=5,
                max_chars=5_000,
            )
            context_latencies.append((time.perf_counter() - context_started) * 1_000)
            selected_chars.append(capsule["budget"]["selected_chars"])
            payload_chars.append(len(canonical_json(capsule)))
            if any(
                item["title"] == target_title
                for group in (
                    "constraints",
                    "decisions",
                    "knowledge_assets",
                    "experiences",
                    "open_questions",
                )
                for item in capsule[group]
            ):
                capsule_hits += 1
            capsule_valid += int(verify_capsule(capsule, vault=vault)["valid"])
    finally:
        vault.close()

    repository = Path(__file__).resolve().parents[2]
    database = vault_root / "vault.sqlite3"
    cli_target_title = f"Knowledge {cli_target:05d}"
    cli_search_hit = bool(
        cli_search["results"]
        and cli_search["results"][0]["title"] == cli_target_title
    )
    cli_capsule_hit = any(
        item["title"] == cli_target_title
        for group in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in cli_context[group]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_eligible": False,
        "claim_ineligibility_reason": (
            "deterministic synthetic corpus created by the DeepLaw development team"
        ),
        "candidate": {
            "version": __version__,
            "implementation_files": {
                path: sha256_file(repository / path)
                for path in (
                    "src/deeplaw/util.py",
                    "src/deeplaw/knowledge_store.py",
                    "src/deeplaw/context_compiler.py",
                    "src/deeplaw/knowledge_cli.py",
                    "benchmarks/scale/run_knowledge_scale.py",
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
            "source_bytes": source.stat().st_size,
            "source_sha256": source_sha256,
            "database_bytes": database.stat().st_size,
            "source_id": source_id,
        },
        "cli_lifecycle": {
            "init_seconds": init_seconds,
            "ingest_seconds": ingest_seconds,
            "approval_seconds": approval_seconds,
            "approved_asset_count": approved["approved_asset_count"],
            "search_seconds": cli_search_seconds,
            "context_seconds": cli_context_seconds,
            "verify_seconds": cli_verify_seconds,
            "search_hit_at_1": cli_search_hit,
            "capsule_recall": cli_capsule_hit,
            "capsule_valid": cli_verification["valid"],
        },
        "persistent_reader": {
            "open_seconds": open_seconds,
            "cold_integrity_seconds": cold_integrity_seconds,
            "integrity_valid": integrity["valid"],
            "search_hit_at_1": search_hits / query_count,
            "capsule_recall": capsule_hits / query_count,
            "capsule_verification_rate": capsule_valid / query_count,
            "search_p50_ms": statistics.median(search_latencies),
            "search_p95_ms": _percentile(search_latencies, 0.95),
            "context_p50_ms": statistics.median(context_latencies),
            "context_p95_ms": _percentile(context_latencies, 0.95),
            "average_selected_chars": statistics.fmean(selected_chars),
            "average_payload_chars": statistics.fmean(payload_chars),
            "peak_rss_bytes": _peak_rss_bytes(),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a claim-ineligible DeepLaw CLI and persistent-reader scale diagnostic."
        )
    )
    parser.add_argument("--asset-count", type=int, default=10_000)
    parser.add_argument("--query-count", type=int, default=100)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 100 <= args.asset_count <= 100_000:
        raise ValueError("asset-count must be between 100 and 100000")
    if not 1 <= args.query_count <= 1_000:
        raise ValueError("query-count must be between 1 and 1000")
    if args.workspace is None:
        with tempfile.TemporaryDirectory(prefix="deeplaw-scale-") as temporary:
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
