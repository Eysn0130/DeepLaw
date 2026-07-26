from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import resource
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import deeplaw.knowledge_discovery as discovery_module
from deeplaw import __version__
from deeplaw.knowledge_discovery import (
    DISCOVERY_MODEL_PROFILES,
    DiscoveryIndex,
    _write_index_with_embedder,
)
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.util import sha256_file

SCHEMA_VERSION = "deeplaw.discovery-scale-diagnostic/v1"
_TOKEN = re.compile(r"\bglyph(?P<ordinal>[0-9]{5})\b")


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _deterministic_vector(text: str, *, dimension: int) -> list[float]:
    match = _TOKEN.search(text)
    if match is None:
        raise ValueError("scale diagnostic text is missing its fixed glyph token")
    digest = hashlib.sha256(match.group("ordinal").encode("ascii")).digest()
    vector = [0.0] * dimension
    used: set[int] = set()
    cursor = 0
    while len(used) < 8:
        if cursor + 3 > len(digest):
            digest = hashlib.sha256(digest).digest()
            cursor = 0
        index = int.from_bytes(digest[cursor : cursor + 2], "big") % dimension
        sign = 1.0 if digest[cursor + 2] & 1 else -1.0
        cursor += 3
        if index in used:
            continue
        used.add(index)
        vector[index] = sign
    return vector


class _DeterministicScaleModel:
    def __init__(self, *_: object, **__: object) -> None:
        self.profile = DISCOVERY_MODEL_PROFILES["english"]

    def embed_query(self, value: str) -> list[float]:
        return _deterministic_vector(value, dimension=self.profile.dimension)


def run_diagnostic(
    vault_root: Path,
    output_root: Path,
    *,
    expected_assets: int,
    query_count: int,
) -> dict[str, Any]:
    profile = DISCOVERY_MODEL_PROFILES["english"]
    with KnowledgeVault(vault_root, read_only=True) as vault:
        if len(vault.all_assets(statuses=("active",))) != expected_assets:
            raise ValueError("scale vault active Asset count does not match --expected-assets")
        build_started = time.perf_counter()
        built = _write_index_with_embedder(
            vault,
            output_root,
            profile=profile,
            embed_documents=lambda values: [
                _deterministic_vector(value, dimension=profile.dimension)
                for value in values
            ],
            confirm_no_case_data=True,
        )
        build_seconds = time.perf_counter() - build_started
        original_model = discovery_module.OnnxDiscoveryModel
        discovery_module.OnnxDiscoveryModel = _DeterministicScaleModel
        try:
            open_started = time.perf_counter()
            index = DiscoveryIndex(output_root, vault=vault)
            open_seconds = time.perf_counter() - open_started
            latencies: list[float] = []
            hits = 0
            for ordinal in range(query_count):
                target = (ordinal * 9_973 + 17) % expected_assets
                started = time.perf_counter()
                results = index.search(f"find glyph{target:05d}", limit=5)
                latencies.append((time.perf_counter() - started) * 1_000)
                if not results:
                    continue
                asset = vault.get_asset(results[0]["asset_id"])
                hits += int(asset.title == f"Knowledge {target:05d}")
        finally:
            discovery_module.OnnxDiscoveryModel = original_model

    repository = Path(__file__).resolve().parents[2]
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_eligible": False,
        "claim_ineligibility_reason": (
            "This diagnostic uses deterministic sparse test vectors and exact synthetic "
            "identifiers. It tests bounded index mechanics, not semantic quality."
        ),
        "candidate": {
            "version": __version__,
            "index_id": built["index_id"],
            "asset_count": built["asset_count"],
            "dimension": profile.dimension,
            "dtype": built["vectors"]["dtype"],
            "derived": built["policy"]["derived"],
            "authoritative": built["policy"]["authoritative"],
            "default_runtime_enabled": built["policy"]["default_runtime_enabled"],
            "implementation_files": {
                path: sha256_file(repository / path)
                for path in (
                    "src/deeplaw/knowledge_discovery.py",
                    "benchmarks/scale/run_discovery_scale.py",
                )
            },
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "measurements": {
            "build_seconds": build_seconds,
            "verified_open_seconds": open_seconds,
            "query_count": query_count,
            "hit_at_1": hits / query_count,
            "query_p50_ms": statistics.median(latencies),
            "query_p95_ms": _percentile(latencies, 0.95),
            "index_bytes": sum(
                path.stat().st_size for path in output_root.iterdir() if path.is_file()
            ),
            "peak_rss_bytes": _peak_rss_bytes(),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a claim-ineligible 100k-scale diagnostic for the source-bound "
            "DeepLaw discovery index."
        )
    )
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--output-index", type=Path, required=True)
    parser.add_argument("--expected-assets", type=int, default=100_000)
    parser.add_argument("--query-count", type=int, default=100)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not 100 <= args.expected_assets <= 100_000:
        raise ValueError("expected-assets must be between 100 and 100000")
    if not 1 <= args.query_count <= 1_000:
        raise ValueError("query-count must be between 1 and 1000")
    report = run_diagnostic(
        args.vault.expanduser().absolute(),
        args.output_index.expanduser().absolute(),
        expected_assets=args.expected_assets,
        query_count=args.query_count,
    )
    output = args.report.expanduser().absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
