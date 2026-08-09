"""Claim-ineligible v0.13 scale and performance diagnostic.

The harness is deliberately conservative.  It creates only a synthetic, temporary Vault,
records every frozen operation at every requested scale, and refuses to substitute a smaller
fixture for a requested 10k/100k run.  A diagnostic report is evidence about construction
mechanics only; it is never eligible for a competitive or user-quality claim.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker
from mcp import types
from mcp.server.lowlevel.server import RequestContext, request_ctx

from deeplaw.api.knowledge_os import KnowledgeOS
from deeplaw.context_compiler import verify_capsule
from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_mcp_server import create_knowledge_mcp_server
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_bytes

SCHEMA_VERSION = "deeplaw.v013-scale-performance-report/v1"
SCALE_CHOICES = (1_000, 10_000, 100_000)
DEFAULT_SCALES = SCALE_CHOICES
SCALES = SCALE_CHOICES
OPERATION_INVENTORY = (
    "exact_get",
    "wiki_page",
    "backlinks",
    "outlinks",
    "compiled_first",
    "evidence_first",
    "context",
    "verify",
    "source_update",
    "incremental_projection",
    "full_rebuild",
    "mcp_cold",
    "mcp_warm",
    "concurrent_read",
    "rss_stability_10000_requests",
    "cache_invalidation_after_source_update",
    "storage_sqlite_bytes",
    "storage_file_count",
    "storage_canvas_count",
    "provider_payload_bytes",
)
OPERATIONS = OPERATION_INVENTORY
STATUS_VALUES = ("executed", "pass", "fail", "degraded", "not_executed")
PROVIDER_HARD_LIMIT_BYTES = 65_536
FROZEN_RSS_REQUESTS = 10_000
MAX_LATENCY_SAMPLES = 1_000
PROJECTION_PROFILE = "standard"
QUERY_PLAN_VERSION = "6"

REFERENCE_TARGETS: dict[str, Any] = {
    "scale_1000": {
        "compiled_first_p95_ms": {"operator": "<=", "value": 500, "unit": "ms"},
        "wiki_page_p95_ms": {"operator": "<=", "value": 150, "unit": "ms"},
        "backlinks_p95_ms": {"operator": "<=", "value": 150, "unit": "ms"},
    },
    "scale_10000": {
        "exact_get_p95_ms": {"operator": "<=", "value": 100, "unit": "ms"},
        "compiled_first_p95_ms": {"operator": "<=", "value": 1_000, "unit": "ms"},
        "context_p95_ms": {"operator": "<=", "value": 1_200, "unit": "ms"},
    },
    "scale_100000": {
        "compiled_first_p95_ms": {"operator": "<=", "value": 2_000, "unit": "ms"},
        "no_full_filesystem_scan": {"operator": "==", "value": False, "unit": "boolean"},
        "no_per_request_full_verify": {
            "operator": "==",
            "value": False,
            "unit": "boolean",
        },
    },
    "rss_stability_10000_requests": {
        "rss_growth_percent": {"operator": "<=", "value": 10, "unit": "%"}
    },
    "concurrent_readers": {"successful_readers": {"operator": ">=", "value": 8, "unit": "readers"}},
    "cache_invalidation_after_source_update": {
        "stale_cache_served": {"operator": "==", "value": False, "unit": "boolean"}
    },
    "provider_hard_limit_violations": {
        "violations": {"operator": "==", "value": 0, "unit": "count"}
    },
}

_LOCAL_PATH = re.compile(
    r"(?:/Users/|/home/|/private/var/|/tmp/|/var/folders/|[A-Za-z]:[\\/]|\\\\)"
)


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "contracts" / (
        "v013-scale-performance-report.v1.schema.json"
    )


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[round((len(ordered) - 1) * percentile)], 6)


def _latency_summary(values: Sequence[float]) -> dict[str, Any]:
    selected = [round(float(value), 6) for value in values[:MAX_LATENCY_SAMPLES]]
    return {
        "samples": selected,
        "p50": _percentile(selected, 0.50),
        "p95": _percentile(selected, 0.95),
        "p99": _percentile(selected, 0.99),
        "max": round(max(selected), 6) if selected else None,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sanitize_reason(value: str) -> str:
    """Keep exception attribution useful without recording local paths or user names."""

    value = _LOCAL_PATH.sub("<local-path>", value)
    value = re.sub(r"(?:[A-Za-z0-9._-]+/){2,}[A-Za-z0-9._-]+", "<local-path>", value)
    return value[:500]


def _git_metadata() -> tuple[str | None, bool, str | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return None, False, f"git metadata unavailable: {type(error).__name__}"
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return None, dirty, "git returned a non-40-character commit identity"
    return commit, dirty, None


def _total_ram_bytes() -> tuple[int | None, str | None]:
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        page_count = int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, NotImplementedError, OSError, TypeError, ValueError):
        return None, "portable total-RAM probe is unavailable"
    if page_size <= 0 or page_count <= 0:
        return None, "OS returned an invalid total-RAM value"
    return page_size * page_count, None


def _filesystem_metadata(path: Path) -> dict[str, Any]:
    try:
        stats = os.statvfs(path)
        block_size = int(stats.f_frsize or stats.f_bsize)
    except (AttributeError, OSError, TypeError, ValueError):
        return {
            "kind": "unknown",
            "block_size": None,
            "reason": "filesystem statistics probe failed",
        }
    # Python's portable statvfs API exposes capacity, not a trustworthy filesystem type.
    return {
        "kind": "unknown",
        "block_size": block_size if block_size > 0 else None,
        "reason": "Python statvfs does not expose a portable filesystem type",
    }


def _environment(root: Path) -> dict[str, Any]:
    commit, dirty, git_reason = _git_metadata()
    ram, ram_reason = _total_ram_bytes()
    return {
        "cpu": {
            "logical_count": max(1, int(os.cpu_count() or 1)),
            "model": platform.processor() or "unknown",
        },
        "ram": {"total_bytes": ram, "reason": ram_reason},
        "os": {
            "name": platform.system() or "unknown",
            "release": platform.release() or "unknown",
            "machine": platform.machine() or "unknown",
        },
        "python": platform.python_version(),
        "filesystem": _filesystem_metadata(root),
        "git_commit": commit,
        "working_tree_dirty": dirty,
        "git_reason": git_reason,
    }


@contextmanager
def _temporary_workspace(workspace: Path | None) -> Iterator[Path]:
    if workspace is None:
        with tempfile.TemporaryDirectory(prefix="deeplaw-v013-scale-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            yield root
        return
    root = workspace.expanduser().absolute()
    if root.exists() and any(root.iterdir()):
        raise ValueError(
            "workspace must be absent or empty; the harness never reads an existing Vault"
        )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    yield root


class _Fixture:
    def __init__(self, root: Path, scale: int) -> None:
        self.root = root
        self.scale = scale
        self.vault = root / "vault"
        self.asset_ids: list[str] = []
        self.knowledge_ids: list[str] = []
        self.revision_ids: list[str] = []
        self.evidence_knowledge_id: str | None = None
        self.source_id: str | None = None
        self.source_path = root / "synthetic-evidence.md"
        self.wiki_path = ""
        self.projection_error: str | None = None
        self.knowledge_os: KnowledgeOS | None = None

    def create(self) -> None:
        self.root.mkdir(parents=True, exist_ok=False, mode=0o700)
        # One heading plus one body line per synthetic Asset keeps the exact
        # 100k lane at 200k lines. The previous blank-separated layout expanded
        # 100k Assets to 300k source lines and failed before exercising scale.
        source_text = _synthetic_source_text(self.scale)
        self.source_path.write_text(source_text, encoding="utf-8", newline="\n")
        initialize_knowledge_vault(
            self.vault,
            name="DeepLaw v0.13 scale construction diagnostic",
            scope="project",
        )
        # Compile one source before installing the autonomous core so that the fixture has an
        # immutable evidence revision available to the evidence-first operation.
        with KnowledgeVault(self.vault, read_only=False) as legacy:
            compiled = compile_source(
                legacy,
                self.source_path,
                source_kind="document",
                sensitivity="public",
                confirm_no_case_data=True,
                logical_path="synthetic-evidence.md",
            )
            review_manifest = legacy.source_review_manifest(compiled["source"]["source_id"])
            legacy.approve_source_assets(
                compiled["source"]["source_id"],
                confirm_reviewed=True,
                review_manifest_sha256=review_manifest["review_manifest_sha256"],
            )
        self.source_id = compiled["source"]["source_id"]
        self.asset_ids = list(compiled.get("asset_ids", []))
        initialize_autonomous_core(self.vault)
        with AutonomousKnowledgeStore(self.vault, read_only=False) as store:
            grant = store.enable_grant(
                writer_id=f"v013-scale-{self.scale}",
                operations=("save_claim", "upsert_concept"),
                max_mutations_per_minute=120,
                max_objects=100_000,
            )
            evidence = store.remember(
                grant_id=grant["grant_id"],
                idempotency_key=f"scale-{self.scale}-evidence",
                title="Synthetic evidence probe",
                body=(
                    "Synthetic compiled evidence marker glyph000000 is bound to the "
                    "temporary source revision."
                ),
                kind="claim",
                operation="save_claim",
                source_refs=[{"source_id": self.source_id}],
                confirm_no_case_data=True,
            )
            concept = store.remember(
                grant_id=grant["grant_id"],
                idempotency_key=f"scale-{self.scale}-compiled",
                title="Synthetic compiled probe",
                body=(
                    "Synthetic compiled object marker glyph000001 is bounded for "
                    "construction diagnostics."
                ),
                kind="concept",
                operation="upsert_concept",
                semantic_key="v013-scale-glyph:000001",
                confirm_no_case_data=True,
            )
            self.evidence_knowledge_id = evidence["knowledge_id"]
            self.knowledge_ids = [concept["knowledge_id"]]
            self.revision_ids = [concept["revision_id"]]
            self.wiki_path = f"wiki/concepts/{self.knowledge_ids[0]}.md"
            try:
                store.rebuild_derived(projection_profile=PROJECTION_PROFILE)
            except Exception as error:
                # A large generated v2 manifest can exceed the current projection byte bound.
                # Keep the canonical fixture usable for operations whose prerequisites exist and
                # let Wiki/projection records carry the exact unavailable-capability reason.
                self.projection_error = _sanitize_reason(
                    f"projection unavailable: {type(error).__name__}"
                )
        # Keep one explicit Python facade alive for all read operations.  Its lazy
        # PersistentReadRuntime is warmed before per-operation instrumentation so startup
        # verification is not misclassified as request work.
        self.knowledge_os = KnowledgeOS.open(self.vault)

    def close(self) -> None:
        # ``TemporaryDirectory`` owns cleanup.  Keeping this method explicit makes the lifecycle
        # visible to callers and prevents accidental reuse of a fixture after a run.
        if self.knowledge_os is not None:
            self.knowledge_os.close()
            self.knowledge_os = None


def _synthetic_source_text(scale: int) -> str:
    return "\n".join(
        line
        for index in range(scale)
        for line in (
            f"# Synthetic object {index:06d}",
            f"Synthetic compiled evidence marker glyph{index:06d} is bounded "
            "for construction diagnostics.",
        )
    ) + "\n"


_MCP_QUERY_TEXT = "Synthetic compiled object marker glyph000001"


class _McpLifespan:
    """Synchronous bridge for one real low-level MCP lifespan on one event loop."""

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server: Any = None
        self.runtime: Any = None
        self._lifespan: Any = None
        self._request_id = 0

    def __enter__(self) -> _McpLifespan:
        self.loop = asyncio.new_event_loop()
        try:
            self.server = create_knowledge_mcp_server(vault_path=self.vault_path)
            self._lifespan = self.server.lifespan(self.server)
            self.runtime = self.loop.run_until_complete(self._lifespan.__aenter__())
        except BaseException:
            self._close_after_failed_enter()
            raise
        return self

    def _close_after_failed_enter(self) -> None:
        loop = self.loop
        self._lifespan = None
        self.runtime = None
        self.server = None
        self.loop = None
        if loop is not None and not loop.is_closed():
            loop.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> bool:
        loop = self.loop
        lifespan = self._lifespan
        try:
            if loop is not None and lifespan is not None:
                return bool(
                    loop.run_until_complete(
                        lifespan.__aexit__(exc_type, exc_value, traceback)
                    )
                )
            return False
        finally:
            self._lifespan = None
            self.runtime = None
            self.server = None
            self.loop = None
            if loop is not None and not loop.is_closed():
                loop.close()

    async def _call_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        server = self.server
        runtime = self.runtime
        if server is None or runtime is None:
            raise RuntimeError("MCP lifespan is not open")
        handler = server.request_handlers[types.CallToolRequest]
        token = request_ctx.set(
            RequestContext(
                request_id=self._request_id,
                meta=None,
                session=None,
                lifespan_context=runtime,
            )
        )
        try:
            response = await handler(
                types.CallToolRequest(
                    params=types.CallToolRequestParams(
                        name="knowledge_support",
                        arguments=arguments,
                    )
                )
            )
        finally:
            request_ctx.reset(token)
        if response.root.isError:
            raise RuntimeError("knowledge_support returned a safe error response")
        structured = response.root.structuredContent
        if not isinstance(structured, dict):
            raise RuntimeError("knowledge_support returned no structured response")
        if len(canonical_json(structured).encode("utf-8")) > PROVIDER_HARD_LIMIT_BYTES:
            raise RuntimeError("knowledge_support output exceeded its hard 64 KiB budget")
        return structured

    def call(self) -> dict[str, Any]:
        loop = self.loop
        if loop is None or loop.is_closed():
            raise RuntimeError("MCP lifespan is not open")
        self._request_id += 1
        return loop.run_until_complete(
            self._call_async(
                {
                    "operation": "query",
                    "query": _MCP_QUERY_TEXT,
                    # Keep the request on the v6 default query path.  The handler applies
                    # bounded defaults for admission and selection fields.
                    "query_plan_version": QUERY_PLAN_VERSION,
                }
            )
        )


def _mcp_cold_sample(vault_path: Path) -> dict[str, Any]:
    # A fresh server, event loop, and lifespan are created and closed for every sample.
    with _McpLifespan(vault_path) as lifespan:
        return lifespan.call()


def _threshold(scale: int, operation: str) -> dict[str, Any]:
    key = f"scale_{scale}"
    candidates = REFERENCE_TARGETS.get(key, {})
    target_name = {
        "compiled_first": "compiled_first_p95_ms",
        "wiki_page": "wiki_page_p95_ms",
        "backlinks": "backlinks_p95_ms",
        "exact_get": "exact_get_p95_ms",
        "context": "context_p95_ms",
    }.get(operation)
    if target_name in candidates:
        target = candidates[target_name]
        return {
            "applies": True,
            "metric": "p95",
            "operator": target["operator"],
            "value": target["value"],
            "unit": target["unit"],
            "reference": f"scale_{scale}.{target_name}",
            "reason": None,
        }
    if operation == "rss_stability_10000_requests":
        target = REFERENCE_TARGETS["rss_stability_10000_requests"]["rss_growth_percent"]
        return {
            "applies": scale == 10_000,
            "metric": "rss_growth_percent",
            "operator": target["operator"],
            "value": target["value"],
            "unit": target["unit"],
            "reference": "rss_stability_10000_requests.rss_growth_percent",
            "reason": None if scale == 10_000 else "RSS target is frozen for the 10k scale report",
        }
    if operation == "concurrent_read":
        target = REFERENCE_TARGETS["concurrent_readers"]["successful_readers"]
        return {
            "applies": True,
            "metric": "successful_readers",
            "operator": target["operator"],
            "value": target["value"],
            "unit": target["unit"],
            "reference": "concurrent_readers.successful_readers",
            "reason": None,
        }
    if operation == "cache_invalidation_after_source_update":
        target = REFERENCE_TARGETS["cache_invalidation_after_source_update"]["stale_cache_served"]
        return {
            "applies": True,
            "metric": "stale_cache_served",
            "operator": target["operator"],
            "value": target["value"],
            "unit": target["unit"],
            "reference": "cache_invalidation_after_source_update.stale_cache_served",
            "reason": None,
        }
    if operation == "provider_payload_bytes":
        target = REFERENCE_TARGETS["provider_hard_limit_violations"]["violations"]
        return {
            "applies": True,
            "metric": "violations",
            "operator": target["operator"],
            "value": target["value"],
            "unit": target["unit"],
            "reference": "provider_hard_limit_violations.violations",
            "reason": None,
        }
    if scale == 100_000 and operation in {"full_rebuild", "verify"}:
        metric = (
            "full_filesystem_scan"
            if operation == "full_rebuild"
            else "per_request_full_verify"
        )
        target = REFERENCE_TARGETS["scale_100000"][
            "no_full_filesystem_scan"
            if operation == "full_rebuild"
            else "no_per_request_full_verify"
        ]
        return {
            "applies": True,
            "metric": metric,
            "operator": target["operator"],
            "value": target["value"],
            "unit": target["unit"],
            "reference": f"scale_100000.{metric}",
            "reason": None,
        }
    return {
        "applies": False,
        "metric": "not_applicable",
        "operator": "none",
        "value": None,
        "unit": "none",
        "reference": None,
        "reason": "no frozen reference target is defined for this scale/operation pair",
    }


def _empty_measurement() -> dict[str, Any]:
    return {
        "value": None,
        "unit": None,
        "valid": None,
        "successful_readers": None,
        "rss_growth_percent": None,
        "stale_cache_served": None,
        "full_filesystem_scan": None,
        "per_request_full_verify": None,
        "provider_payload_bytes": None,
        "provider_hard_limit_violations": None,
        "file_count": None,
        "canvas_count": None,
    }


def _measurement_from_result(operation: str, result: Any) -> dict[str, Any]:
    measurement = _empty_measurement()
    if result is None:
        return measurement
    if operation in {"storage_sqlite_bytes", "storage_file_count", "storage_canvas_count"}:
        measurement["value"] = int(result)
        measurement["unit"] = {
            "storage_sqlite_bytes": "bytes",
            "storage_file_count": "files",
            "storage_canvas_count": "canvas_files",
        }[operation]
        if operation == "storage_file_count":
            measurement["file_count"] = int(result)
        if operation == "storage_canvas_count":
            measurement["canvas_count"] = int(result)
    elif operation == "provider_payload_bytes":
        payload_bytes = int(result)
        measurement["value"] = payload_bytes
        measurement["unit"] = "bytes"
        measurement["provider_payload_bytes"] = payload_bytes
        measurement["provider_hard_limit_violations"] = int(
            payload_bytes > PROVIDER_HARD_LIMIT_BYTES
        )
    elif operation == "concurrent_read" and isinstance(result, Mapping):
        readers = result.get("successful_readers")
        if isinstance(readers, int) and not isinstance(readers, bool):
            measurement["value"] = readers
            measurement["unit"] = "readers"
            measurement["successful_readers"] = readers
    elif operation == "verify" and isinstance(result, Mapping):
        valid = result.get("valid") is True
        measurement["value"] = int(valid)
        measurement["unit"] = "boolean"
        measurement["valid"] = valid
        full_verify = result.get("per_request_full_verify")
        if isinstance(full_verify, bool):
            measurement["per_request_full_verify"] = full_verify
    elif operation == "full_rebuild" and isinstance(result, Mapping):
        full_scan = result.get("full_filesystem_scan")
        if isinstance(full_scan, bool):
            measurement["full_filesystem_scan"] = full_scan
        measurement["unit"] = "boolean"
    return measurement


def _compare(value: Any, threshold: dict[str, Any]) -> bool:
    if value is None or not threshold["applies"]:
        return False
    operator = threshold["operator"]
    target = threshold["value"]
    if operator == "<=":
        return bool(value <= target)
    if operator == ">=":
        return bool(value >= target)
    if operator == "==":
        return bool(value == target)
    return False


def _judgment_value(
    operation: str,
    threshold: dict[str, Any],
    latency: dict[str, Any],
    measurement: dict[str, Any],
) -> Any:
    if threshold["metric"] == "p95":
        return latency["p95"]
    if threshold["metric"] == "successful_readers":
        return measurement["successful_readers"]
    if threshold["metric"] == "rss_growth_percent":
        return measurement["rss_growth_percent"]
    if threshold["metric"] == "stale_cache_served":
        return measurement["stale_cache_served"]
    if threshold["metric"] == "violations":
        return measurement["provider_hard_limit_violations"]
    if threshold["metric"] in {"full_filesystem_scan", "per_request_full_verify"}:
        return measurement[threshold["metric"]]
    return None


def _operation_record(
    *,
    scale: int,
    operation: str,
    query_runs: int,
    warmup_runs: int,
    runner: Callable[[], Any] | None,
    threshold: dict[str, Any],
    not_executed_reason: str | None = None,
) -> dict[str, Any]:
    latency_values: list[float] = []
    errors: list[str] = []
    last_result: Any = None
    warmup_completed = 0
    run_completed = 0
    if runner is not None:
        for _ in range(warmup_runs):
            try:
                runner()
                warmup_completed += 1
            except Exception as error:  # pragma: no cover - exercised by unavailable APIs
                errors.append(f"warmup {type(error).__name__}")
        for _ in range(query_runs):
            started = time.perf_counter()
            try:
                last_result = runner()
            except Exception as error:  # pragma: no cover - exercised by unavailable APIs
                errors.append(f"run {type(error).__name__}")
                continue
            latency_values.append((time.perf_counter() - started) * 1_000)
            run_completed += 1
    latency = _latency_summary(latency_values)
    measurement = _measurement_from_result(operation, last_result)
    if runner is None:
        status = "not_executed"
        judgment = "not_executed"
        reason = not_executed_reason or "operation was not selected for execution"
    elif not latency_values:
        status = "not_executed"
        judgment = "not_executed"
        reason = (
            not_executed_reason
            or "operation prerequisites were unavailable; no successful measurement was collected"
        )
    else:
        value = _judgment_value(operation, threshold, latency, measurement)
        if threshold["applies"]:
            passed = _compare(value, threshold)
            status = "pass" if passed else "fail"
            judgment = status
            reason = (
                None
                if passed
                else f"measured {threshold['metric']} did not satisfy the frozen reference target"
            )
        else:
            status = "executed"
            judgment = "not_applicable"
            reason = threshold["reason"]
        if errors:
            status = "degraded"
            judgment = "degraded"
            reason = f"{reason + '; ' if reason else ''}some runs failed: {', '.join(errors[:4])}"
    limitation = (
        "Synthetic construction diagnostic only; timing is host-local and claim-ineligible."
        if status != "not_executed"
        else "No timing claim is made because this operation was not executed."
    )
    return {
        "scale": scale,
        "operation": operation,
        "status": status,
        "sample_count": len(latency_values),
        "warmup_count": warmup_completed,
        "run_count": run_completed,
        "latency_ms": latency,
        "threshold": threshold,
        "judgment": judgment,
        "measured_value": _judgment_value(operation, threshold, latency, measurement),
        "measurement": measurement,
        "limitation": limitation,
        "reason": _sanitize_reason(reason or ""),
    }


@contextmanager
def _full_vault_scan_monitor(root: Path) -> Iterator[dict[str, bool]]:
    """Observe a recursive scan rooted at the entire Vault during one operation.

    Derived-owner staging/backup traversal is intentionally not classified as a
    full Vault scan. The old benchmark unconditionally reported every full rebuild
    as a filesystem scan without observing the implementation, forcing a false
    100k failure.
    """

    observation = {"full_filesystem_scan": False}
    original_rglob = Path.rglob

    def monitored_rglob(path: Path, pattern: str, *args: Any, **kwargs: Any) -> Any:
        if path == root:
            observation["full_filesystem_scan"] = True
        return original_rglob(path, pattern, *args, **kwargs)

    with patch.object(Path, "rglob", monitored_rglob):
        yield observation


def _fixture_operation_runners(fixture: _Fixture) -> dict[str, Callable[[], Any]]:
    root = fixture.vault
    knowledge_id = fixture.knowledge_ids[0]
    task = "Synthetic compiled object marker glyph000001"
    evidence_task = "Synthetic compiled evidence marker glyph000000"
    knowledge_os = fixture.knowledge_os
    if knowledge_os is None:
        raise RuntimeError("synthetic fixture Python facade is not open")

    # Warm the one handle once.  All measured context/Wiki calls below therefore use the same
    # verified snapshot and only the runtime's bounded identity observer on unchanged state.
    knowledge_os.context.compile(
        task=task,
        purpose="answer",
        policy="compiled-first-v1",
        scope="project",
        max_sensitivity="private",
        limit=5,
        max_chars=5_000,
        max_tokens=4_000,
        confirm_no_case_data=True,
    )

    def exact_get() -> Any:
        if not fixture.asset_ids:
            raise KeyError("synthetic legacy Asset unavailable")
        with KnowledgeVault(root, read_only=True) as store:
            return store.get_asset(fixture.asset_ids[0])

    def wiki_page() -> Any:
        return knowledge_os.wiki.page(fixture.wiki_path)

    def backlinks() -> Any:
        return knowledge_os.wiki.backlinks(fixture.wiki_path)

    def outlinks() -> Any:
        return knowledge_os.wiki.outlinks(fixture.wiki_path)

    def compiled_first() -> Any:
        return knowledge_os.context.compile(
            task=task,
            purpose="answer",
            policy="compiled-first-v1",
            scope="project",
            max_sensitivity="private",
            limit=5,
            max_chars=5_000,
            max_tokens=4_000,
            confirm_no_case_data=True,
        )

    def evidence_first() -> Any:
        return knowledge_os.context.compile(
            task=evidence_task,
            purpose="verify",
            policy="evidence-first-v1",
            scope="project",
            max_sensitivity="private",
            limit=5,
            max_chars=5_000,
            max_tokens=4_000,
            confirm_no_case_data=True,
        )

    def context() -> Any:
        return knowledge_os.context.compile(
            task=task,
            purpose="answer",
            policy="balanced-v1",
            scope="project",
            max_sensitivity="private",
            limit=5,
            max_chars=5_000,
            max_tokens=4_000,
            confirm_no_case_data=True,
        )

    def verify() -> Any:
        calls = 0
        original_verify = AutonomousKnowledgeStore.verify

        def counted_verify(
            store: AutonomousKnowledgeStore,
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return original_verify(store, *args, **kwargs)

        with patch.object(AutonomousKnowledgeStore, "verify", counted_verify):
            capsule = knowledge_os.context.compile(
                task=evidence_task,
                purpose="verify",
                policy="evidence-first-v1",
                scope="project",
                max_sensitivity="private",
                limit=5,
                max_chars=5_000,
                max_tokens=4_000,
                confirm_no_case_data=True,
            )
            verification = verify_capsule(capsule)
        verification["per_request_full_verify"] = calls > 0
        return verification

    def incremental_projection() -> Any:
        with AutonomousKnowledgeStore(root, read_only=False) as store:
            current = store.get_current(knowledge_id)
            if current is None:
                raise KeyError("synthetic Knowledge Object unavailable")
            grant = store.enable_grant(
                writer_id=f"v013-incremental-{time.time_ns()}",
                operations=("upsert_concept",),
                max_mutations_per_minute=120,
                max_objects=100_000,
            )
            store.remember(
                grant_id=grant["grant_id"],
                idempotency_key=f"incremental-{time.time_ns()}",
                knowledge_id=knowledge_id,
                expected_revision_id=current["revision_id"],
                title=current["title"],
                body=f"{current['body']} incremental marker.",
                kind="concept",
                operation="upsert_concept",
                semantic_key=f"v013-scale-glyph:incremental:{time.time_ns()}",
                confirm_no_case_data=True,
            )
            return store.rebuild_derived(projection_profile=PROJECTION_PROFILE)

    def full_rebuild() -> Any:
        with (
            _full_vault_scan_monitor(root) as observation,
            AutonomousKnowledgeStore(root, read_only=False) as store,
        ):
            store.rebuild_derived(projection_profile=PROJECTION_PROFILE)
        return observation

    def concurrent_read() -> Any:
        def read_once(_: int) -> bool:
            with AutonomousKnowledgeStore(root, read_only=True) as store:
                return store.verify()["valid"] is True

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(read_once, range(8)))
        return {"successful_readers": sum(results), "reader_count": len(results)}

    def storage_sqlite_bytes() -> int:
        databases = [root / "vault.sqlite3", root / ".deeplaw" / "ledger.sqlite3"]
        return sum(path.stat().st_size for path in databases if path.is_file())

    def storage_file_count() -> int:
        return sum(1 for path in root.rglob("*") if path.is_file())

    def storage_canvas_count() -> int:
        return sum(1 for path in (root / "canvas").glob("*.canvas") if path.is_file())

    def provider_payload_bytes() -> int:
        capsule = context()
        return len(canonical_json(capsule).encode("utf-8"))

    return {
        "exact_get": exact_get,
        "wiki_page": wiki_page,
        "backlinks": backlinks,
        "outlinks": outlinks,
        "compiled_first": compiled_first,
        "evidence_first": evidence_first,
        "context": context,
        "verify": verify,
        "incremental_projection": incremental_projection,
        "full_rebuild": full_rebuild,
        "concurrent_read": concurrent_read,
        "storage_sqlite_bytes": storage_sqlite_bytes,
        "storage_file_count": storage_file_count,
        "storage_canvas_count": storage_canvas_count,
        "provider_payload_bytes": provider_payload_bytes,
    }


def _special_record(
    *,
    scale: int,
    operation: str,
    query_runs: int,
    warmup_runs: int,
    runner: Callable[[], Any] | None,
    threshold: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return _operation_record(
        scale=scale,
        operation=operation,
        query_runs=query_runs,
        warmup_runs=warmup_runs,
        runner=runner,
        threshold=threshold,
        not_executed_reason=reason,
    )


def _run_scale(
    root: Path,
    scale: int,
    *,
    query_runs: int,
    warmup_runs: int,
    rss_requests: int,
    execute_expensive: bool,
) -> dict[str, Any]:
    if scale > 1_000 and not execute_expensive:
        reason = (
            f"scale {scale} is an expensive frozen workload; rerun with --execute-expensive "
            "to execute this scale. The harness did not substitute a smaller fixture."
        )
        records = [
            _special_record(
                scale=scale,
                operation=operation,
                query_runs=query_runs,
                warmup_runs=warmup_runs,
                runner=None,
                threshold=_threshold(scale, operation),
                reason=reason,
            )
            for operation in OPERATION_INVENTORY
        ]
        return {
            "scale": scale,
            "object_count": scale,
            "fixture_status": "not_executed",
            "fixture_reason": reason,
            "operations": records,
        }

    fixture_root = root / f"scale-{scale}"
    fixture = _Fixture(fixture_root, scale)
    try:
        fixture.create()
        runners = _fixture_operation_runners(fixture)
        records: list[dict[str, Any]] = []
        for operation in OPERATION_INVENTORY:
            threshold = _threshold(scale, operation)
            projection_operations = {
                "wiki_page",
                "backlinks",
                "outlinks",
                "incremental_projection",
                "full_rebuild",
                "storage_canvas_count",
            }
            if fixture.projection_error and operation in projection_operations:
                record = _special_record(
                    scale=scale,
                    operation=operation,
                    query_runs=query_runs,
                    warmup_runs=warmup_runs,
                    runner=None,
                    threshold=threshold,
                    reason=fixture.projection_error,
                )
            elif operation == "mcp_cold" and scale == 1_000:
                record = _operation_record(
                    scale=scale,
                    operation=operation,
                    query_runs=query_runs,
                    warmup_runs=warmup_runs,
                    runner=lambda: _mcp_cold_sample(fixture.vault),
                    threshold=threshold,
                )
            elif operation == "mcp_warm" and scale == 1_000:
                # Keep warmup and every measured request inside one actual MCP lifespan.
                # The context manager closes the persistent snapshot and event loop after the
                # operation record is complete, including when a request raises.
                with _McpLifespan(fixture.vault) as lifespan:
                    record = _operation_record(
                        scale=scale,
                        operation=operation,
                        query_runs=query_runs,
                        warmup_runs=warmup_runs,
                        runner=lifespan.call,
                        threshold=threshold,
                    )
            elif operation in runners:
                record = _operation_record(
                    scale=scale,
                    operation=operation,
                    query_runs=query_runs,
                    warmup_runs=warmup_runs,
                    runner=runners[operation],
                    threshold=threshold,
                )
            elif operation == "rss_stability_10000_requests":
                if rss_requests != FROZEN_RSS_REQUESTS:
                    reason = (
                        f"--rss-requests={rss_requests} is not the frozen 10000-request workload; "
                        "the harness refuses to treat a smaller run as the target"
                    )
                else:
                    reason = (
                        "RSS stability requires a dedicated child-process harness; this "
                        "construction runner does not claim a substitute measurement"
                    )
                record = _special_record(
                    scale=scale,
                    operation=operation,
                    query_runs=query_runs,
                    warmup_runs=warmup_runs,
                    runner=None,
                    threshold=threshold,
                    reason=reason,
                )
            elif operation in {"mcp_cold", "mcp_warm"}:
                record = _special_record(
                    scale=scale,
                    operation=operation,
                    query_runs=query_runs,
                    warmup_runs=warmup_runs,
                    runner=None,
                    threshold=threshold,
                    reason=(
                        "persistent MCP cold/warm diagnostics are frozen to the 1k synthetic "
                        "fixture; this scale was not executed"
                    ),
                )
            elif operation == "source_update":
                record = _special_record(
                    scale=scale,
                    operation=operation,
                    query_runs=query_runs,
                    warmup_runs=warmup_runs,
                    runner=None,
                    threshold=threshold,
                    reason=(
                        "source update mutates the canonical legacy read plane; this fixture "
                        "keeps its evidence and autonomous audit heads stable"
                    ),
                )
            elif operation == "cache_invalidation_after_source_update":
                record = _special_record(
                    scale=scale,
                    operation=operation,
                    query_runs=query_runs,
                    warmup_runs=warmup_runs,
                    runner=None,
                    threshold=threshold,
                    reason=(
                        "source_update was not executed, so stale-cache invalidation is "
                        "not inferred"
                    ),
                )
            else:
                record = _special_record(
                    scale=scale,
                    operation=operation,
                    query_runs=query_runs,
                    warmup_runs=warmup_runs,
                    runner=None,
                    threshold=threshold,
                    reason="the required public capability is unavailable in this fixture",
                )
            records.append(record)

        # RSS is a separate bounded workload.  It is deliberately not hidden behind a tiny
        # sample: a non-frozen request count is recorded as not_executed by the operation above.
        if rss_requests == FROZEN_RSS_REQUESTS and execute_expensive and scale == 10_000:
            records[OPERATION_INVENTORY.index("rss_stability_10000_requests")] = _special_record(
                scale=scale,
                operation="rss_stability_10000_requests",
                query_runs=query_runs,
                warmup_runs=warmup_runs,
                runner=None,
                threshold=_threshold(scale, "rss_stability_10000_requests"),
                reason=(
                    "RSS process isolation requires a dedicated child-process harness; this "
                    "local construction runner does not claim a substitute measurement"
                ),
            )
        return {
            "scale": scale,
            "object_count": scale,
            "fixture_status": "executed",
            "fixture_reason": "real temporary synthetic Vault created and torn down by the harness",
            "operations": records,
        }
    except Exception as error:
        reason = _sanitize_reason(
            f"fixture construction failed closed: {type(error).__name__}: {error}"
        )
        records = [
            _special_record(
                scale=scale,
                operation=operation,
                query_runs=query_runs,
                warmup_runs=warmup_runs,
                runner=None,
                threshold=_threshold(scale, operation),
                reason=reason,
            )
            for operation in OPERATION_INVENTORY
        ]
        return {
            "scale": scale,
            "object_count": scale,
            "fixture_status": "not_executed",
            "fixture_reason": reason,
            "operations": records,
        }
    finally:
        fixture.close()


def _validate_args(
    scales: Sequence[int],
    query_runs: int,
    warmup_runs: int,
    rss_requests: int,
) -> tuple[int, ...]:
    selected = tuple(dict.fromkeys(scales))
    if not selected or any(scale not in SCALE_CHOICES for scale in selected):
        raise ValueError("scale must contain only the frozen choices 1000, 10000, or 100000")
    if isinstance(query_runs, bool) or not 1 <= query_runs <= MAX_LATENCY_SAMPLES:
        raise ValueError("query-runs must be between 1 and 1000")
    if isinstance(warmup_runs, bool) or not 0 <= warmup_runs <= MAX_LATENCY_SAMPLES:
        raise ValueError("warmup-runs must be between 0 and 1000")
    if isinstance(rss_requests, bool) or not 1 <= rss_requests <= FROZEN_RSS_REQUESTS:
        raise ValueError("rss-requests must be between 1 and 10000")
    return tuple(scale for scale in SCALE_CHOICES if scale in selected)


def _overall(scale_reports: Sequence[dict[str, Any]]) -> dict[str, Any]:
    records = [record for report in scale_reports for record in report["operations"]]
    counts = {
        status: sum(record["status"] == status for record in records)
        for status in STATUS_VALUES
    }
    not_executed = [
        {
            "scale": record["scale"],
            "operation": record["operation"],
            "reason": record["reason"],
        }
        for record in records
        if record["status"] == "not_executed"
    ]
    return {
        "status": "not_released",
        "release_gate_passed": False,
        "operation_count": len(records),
        "pass_count": counts["pass"],
        "fail_count": counts["fail"],
        "degraded_count": counts["degraded"],
        "executed_count": len(records) - counts["not_executed"],
        "not_executed_count": counts["not_executed"],
        "not_executed": not_executed,
        "limitations": [
            "This construction diagnostic cannot satisfy the v0.13 release gate.",
            "Any not_executed operation remains an unmet gate and is never counted as pass.",
        ],
    }


def _digest_body(report: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(report)
    body.pop("report_sha256", None)
    return body


def build_scale_performance_report(
    *,
    scales: Sequence[int] = DEFAULT_SCALES,
    query_runs: int = 3,
    warmup_runs: int = 1,
    rss_requests: int = FROZEN_RSS_REQUESTS,
    execute_expensive: bool = False,
    workspace: Path | None = None,
) -> dict[str, Any]:
    selected_scales = _validate_args(scales, query_runs, warmup_runs, rss_requests)
    with _temporary_workspace(workspace) as root:
        environment = _environment(root)
        scale_reports = [
            _run_scale(
                root,
                scale,
                query_runs=query_runs,
                warmup_runs=warmup_runs,
                rss_requests=rss_requests,
                execute_expensive=execute_expensive,
            )
            for scale in selected_scales
        ]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "claim_eligible": False,
        "claim_ineligibility_reason": (
            "Deterministic synthetic temporary Vault construction diagnostic; it is not real-user "
            "quality evidence, a competitor run, or a release claim."
        ),
        "profile": "construction_diagnostic",
        "generated_at_utc": _utc_now(),
        "release_gate_passed": False,
        "candidate": {
            "package_version": _package_version(),
            "runner": "benchmarks/v013/scale_performance.py",
            "runner_sha256": _runner_sha256(),
        },
        "environment": environment,
        "configuration": {
            "scales": list(selected_scales),
            "query_runs": query_runs,
            "warmup_runs": warmup_runs,
            "rss_requests": rss_requests,
            "execute_expensive": execute_expensive,
            "max_latency_samples": MAX_LATENCY_SAMPLES,
            "projection_profile": PROJECTION_PROFILE,
            "query_plan_version": QUERY_PLAN_VERSION,
            "semantic_profile": "not_executed",
        },
        "operation_inventory": list(OPERATION_INVENTORY),
        "reference_targets": REFERENCE_TARGETS,
        "scale_reports": scale_reports,
        "overall": _overall(scale_reports),
        "limitations": [
            "No network, model, Gold/scorer, user Vault, legal source, or case data is read.",
            "Deterministic synthetic identifiers measure mechanics only and are claim-ineligible.",
            "Scale counts are synthetic source-derived Asset records; autonomous probes are "
            "bounded separately and do not represent real-user knowledge quality.",
            "not_executed, degraded, and fail outcomes are retained and never converted to pass.",
            "10k/100k reruns require --execute-expensive; no smaller substitute is used.",
        ],
        "rerun_commands": [
            (
                "uv run python -m benchmarks.v013.scale_performance --output REPORT.json "
                f"--scale {scale} --query-runs {query_runs} --warmup-runs {warmup_runs} "
                f"--rss-requests {rss_requests}"
                + (" --execute-expensive" if scale > 1_000 else "")
            )
            for scale in selected_scales
        ],
    }
    report["report_sha256"] = sha256_bytes(
        canonical_json(_digest_body(report)).encode("utf-8")
    )
    return report


def build_report(*, scale: int | None = None, **options: Any) -> dict[str, Any]:
    """Short alias for callers that use the report-oriented benchmark convention."""

    if scale is not None:
        options["scales"] = (scale,)
    return build_scale_performance_report(**options)


def _package_version() -> str:
    try:
        from deeplaw import __version__
    except ImportError:  # pragma: no cover - package import is required in supported runs
        return "unknown"
    return str(__version__)


def _runner_sha256() -> str:
    try:
        return sha256_bytes(Path(__file__).read_bytes())
    except OSError:
        return "0" * 64


def run_diagnostic(
    *,
    scales: Sequence[int] = DEFAULT_SCALES,
    scale: int | None = None,
    query_runs: int = 3,
    warmup_runs: int = 1,
    rss_requests: int = FROZEN_RSS_REQUESTS,
    execute_expensive: bool = False,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Compatibility entry point used by benchmark callers and tests."""

    selected_scales = (scale,) if scale is not None else scales
    return build_scale_performance_report(
        scales=selected_scales,
        query_runs=query_runs,
        warmup_runs=warmup_runs,
        rss_requests=rss_requests,
        execute_expensive=execute_expensive,
        workspace=workspace,
    )


def verify_scale_performance_report(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["report must be an object"]}
    report = dict(value)
    errors: list[str] = []
    schema_path = _schema_path()
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors.extend(error.message for error in Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).iter_errors(report))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        errors.append(f"schema validation unavailable: {type(error).__name__}")
    expected = report.get("report_sha256")
    if not isinstance(expected, str) or expected != sha256_bytes(
        canonical_json(_digest_body(report)).encode("utf-8")
    ):
        errors.append("report digest mismatch")
    if report.get("claim_eligible") is not False or report.get("release_gate_passed") is not False:
        errors.append("claim or release gate is not fail-closed")
    if report.get("operation_inventory") != list(OPERATION_INVENTORY):
        errors.append("operation inventory is not the frozen closed inventory")
    for scale_report in report.get("scale_reports", []):
        operations = scale_report.get("operations", []) if isinstance(scale_report, Mapping) else []
        names = [item.get("operation") for item in operations if isinstance(item, Mapping)]
        if names != list(OPERATION_INVENTORY):
            errors.append(f"scale {scale_report.get('scale')} operation inventory is not closed")
        for item in operations:
            if not isinstance(item, Mapping):
                continue
            if item.get("status") == "not_executed" and not item.get("reason"):
                errors.append(
                    f"{item.get('scale')}/{item.get('operation')} lacks not_executed reason"
                )
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if _LOCAL_PATH.search(serialized):
        errors.append("report contains a local absolute path")
    return {"valid": not errors, "errors": errors}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a claim-ineligible v0.13 scale/performance construction diagnostic."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scale", type=int, choices=SCALE_CHOICES, action="append")
    parser.add_argument("--query-runs", type=int, default=3)
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--rss-requests", type=int, default=FROZEN_RSS_REQUESTS)
    parser.add_argument("--execute-expensive", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    scales = tuple(args.scale) if args.scale else DEFAULT_SCALES
    report = build_scale_performance_report(
        scales=scales,
        query_runs=args.query_runs,
        warmup_runs=args.warmup_runs,
        rss_requests=args.rss_requests,
        execute_expensive=args.execute_expensive,
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
