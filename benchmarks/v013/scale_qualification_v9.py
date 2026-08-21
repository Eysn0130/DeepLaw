"""Machine-only v0.13 Kernel Release Core scale qualification.

This runner is intentionally a new, versioned seam.  The older
``scale_performance`` and ``query_graph_scale`` modules remain historical
diagnostics and are never imported here.  A qualification report is accepted
only when one exact candidate is bound to one run and all scale obligations
are observed from the public Source/KnowledgeOS query and context surfaces.

The default command does not execute a 10,000-object workload.  The expensive
lane requires ``--execute-10k`` and complete wheel/sdist/candidate metadata;
there is no smaller-fixture substitution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.compilation.models import MAX_COMPILATION_REQUEST_BYTES
from deeplaw.util import canonical_json

SCHEMA_VERSION = "deeplaw.v013-scale-qualification-report/v9"
PROFILE = "kernel_release_core"
RUNNER_RELATIVE_PATH = "benchmarks/v013/scale_qualification_v9.py"
SCHEMA_RELATIVE_PATH = "contracts/v013-scale-qualification-report.v9.schema.json"
ACTIVE_GOVERNED_OBJECT_TARGET = 10_000
SOURCE_BATCH_COUNT = 250
FRAGMENTS_PER_SOURCE = 40
WARM_SAMPLE_TARGET = 30
PROVIDER_HARD_LIMIT_BYTES = 65_536
DEFERRED_100000 = "v0.14"
HARD_FAILURE_IDS = (
    "active_governed_object_count_mismatch",
    "experimental_over_10000_claimed_qualified",
    "100000_not_deferred_to_v0.14",
    "warm_samples_below_30",
    "missing_p50",
    "missing_p95",
    "missing_max",
    "rss_missing",
    "storage_missing",
    "file_count_missing",
    "build_duration_missing",
    "rebuild_duration_missing",
    "full_incremental_noop_mismatch",
    "user_bytes_unbounded",
    "provider_bound_exceeded",
)


class ScaleQualificationError(ValueError):
    """Raised when a scale report or candidate binding is unsafe."""


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / SCHEMA_RELATIVE_PATH


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ScaleQualificationError("scale report is not canonical JSON") from error


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _finite_nonnegative(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value >= 0
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ScaleQualificationError("latency samples are empty")
    ordered = sorted(float(value) for value in values)
    index = round((len(ordered) - 1) * percentile)
    return round(ordered[index], 6)


def _latency(values: Sequence[float]) -> dict[str, Any]:
    samples = [float(value) for value in values]
    if not samples or any(not _finite_nonnegative(value) for value in samples):
        raise ScaleQualificationError("latency samples must be finite non-negative numbers")
    return {
        "samples_ms": [round(value, 6) for value in samples],
        "sample_count": len(samples),
        "p50_ms": _percentile(samples, 0.50),
        "p95_ms": _percentile(samples, 0.95),
        "max_ms": round(max(samples), 6),
    }


def _change_counts(receipt: Mapping[str, Any]) -> dict[str, int]:
    living_wiki = receipt.get("living_wiki")
    change_set = living_wiki.get("change_set") if isinstance(living_wiki, Mapping) else None
    if not isinstance(change_set, Mapping):
        raise ScaleQualificationError("rebuild receipt has no Living Wiki change set")
    counts: dict[str, int] = {}
    for field in ("created", "updated", "deleted", "unchanged"):
        values = change_set.get(field)
        if not isinstance(values, list):
            raise ScaleQualificationError(f"rebuild change set has no {field} list")
        counts[field] = len(values)
    return counts


def _rebuild_modes_valid(
    rebuild: Mapping[str, Any],
    equivalence: Mapping[str, Any],
) -> bool:
    if set(rebuild) != {"full", "minimal", "incremental", "no_op"}:
        return False
    expected = {
        "full": ("full", "standard"),
        "minimal": ("minimal", "minimal"),
        "incremental": ("incremental", "standard"),
        "no_op": ("no_op", "standard"),
    }
    for name, (mode, profile) in expected.items():
        entry = rebuild[name]
        if not isinstance(entry, Mapping) or set(entry) != {
            "mode",
            "projection_profile",
            "change_counts",
            "stable_identity_sha256",
        }:
            return False
        if entry["mode"] != mode or entry["projection_profile"] != profile:
            return False
        counts = entry["change_counts"]
        if not isinstance(counts, Mapping) or set(counts) != {
            "created",
            "updated",
            "deleted",
            "unchanged",
        }:
            return False
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts.values()
        ):
            return False
        digest = entry["stable_identity_sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            return False
    if (
        sum(
            rebuild["full"]["change_counts"][field]
            for field in ("created", "updated", "deleted")
        )
        < 1
    ):
        return False
    if sum(
        rebuild["incremental"]["change_counts"][field]
        for field in ("created", "updated", "deleted")
    ) < 1:
        return False
    if sum(
        rebuild["no_op"]["change_counts"][field]
        for field in ("created", "updated", "deleted")
    ) != 0:
        return False
    for mode, digest_key in (
        ("full", "full"),
        ("incremental", "incremental"),
        ("no_op", "no_op"),
    ):
        if rebuild[mode]["stable_identity_sha256"] != equivalence[digest_key]["sha256"]:
            return False
    return (
        rebuild["full"]["stable_identity_sha256"]
        == rebuild["incremental"]["stable_identity_sha256"]
        == rebuild["no_op"]["stable_identity_sha256"]
    )


def _semantic_batches_valid(
    batches: Sequence[Mapping[str, Any]],
    *,
    expected_count: int,
) -> bool:
    if not batches:
        return False
    ordered = sorted(batches, key=lambda item: item.get("batch_index", -1))
    offset = 0
    asset_count = 0
    for index, batch in enumerate(ordered):
        required = {
            "batch_index",
            "global_offset",
            "target_object_count",
            "grant_max_objects",
            "grant_id",
            "compilation_run_id",
            "source_revision_id",
            "asset_count",
            "asset_ids_sha256",
            "publication_request_bytes",
            "publication_request_sha256",
            "publication_request_limit_bytes",
            "published_object_count",
            "committed_object_count",
            "committed_relation_count",
        }
        if set(batch) != required:
            return False
        if batch["batch_index"] != index or batch["global_offset"] != offset:
            return False
        target = batch["target_object_count"]
        if (
            isinstance(target, bool)
            or not isinstance(target, int)
            or target < 1
            or target > FRAGMENTS_PER_SOURCE
            or batch["grant_max_objects"] != offset + target
            or batch["asset_count"] != target
            or batch["published_object_count"] != target
            or batch["committed_object_count"] != target
            or batch["committed_relation_count"] != 0
        ):
            return False
        if (
            not isinstance(batch["publication_request_bytes"], int)
            or batch["publication_request_bytes"] < 1
            or batch["publication_request_bytes"] > MAX_COMPILATION_REQUEST_BYTES
            or batch["publication_request_limit_bytes"] != MAX_COMPILATION_REQUEST_BYTES
        ):
            return False
        for field in ("asset_ids_sha256", "publication_request_sha256"):
            digest = batch[field]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                return False
        for field in ("grant_id", "compilation_run_id", "source_revision_id"):
            if not isinstance(batch[field], str) or not batch[field]:
                return False
        offset += target
        asset_count += batch["asset_count"]
    return offset == expected_count and asset_count == expected_count


def _artifact(value: Mapping[str, Any], *, suffix: str) -> dict[str, Any]:
    required = {"filename", "sha256", "size_bytes"}
    if set(value) != required:
        raise ScaleQualificationError(f"candidate {suffix} artifact keys are not closed")
    filename = value["filename"]
    digest = value["sha256"]
    size = value["size_bytes"]
    if (
        not isinstance(filename, str)
        or not filename.startswith("deeplaw-0.13.0")
        or "/" in filename
        or "\\" in filename
        or (suffix == "wheel" and not filename.endswith(".whl"))
        or (suffix == "sdist" and not filename.endswith(".tar.gz"))
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or digest == "0" * 64
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 1
    ):
        raise ScaleQualificationError(f"candidate {suffix} artifact binding is invalid")
    return dict(value)


def _candidate_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {"commit", "tree", "version", "lock_sha256", "wheel", "sdist"}
    if set(value) != required:
        raise ScaleQualificationError("candidate binding keys are not closed")
    if value["version"] != "0.13.0":
        raise ScaleQualificationError("scale qualification requires candidate version 0.13.0")
    for field in ("commit", "tree"):
        digest = value[field]
        if (
            not isinstance(digest, str)
            or len(digest) != 40
            or any(character not in "0123456789abcdef" for character in digest)
            or digest == "0" * 40
        ):
            raise ScaleQualificationError(f"candidate {field} binding is invalid")
    lock = value["lock_sha256"]
    if (
        not isinstance(lock, str)
        or len(lock) != 64
        or any(character not in "0123456789abcdef" for character in lock)
        or lock == "0" * 64
    ):
        raise ScaleQualificationError("candidate lock binding is invalid")
    return {
        **{field: value[field] for field in ("commit", "tree", "version", "lock_sha256")},
        "wheel": _artifact(value["wheel"], suffix="wheel"),
        "sdist": _artifact(value["sdist"], suffix="sdist"),
    }


def _run_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "run_id",
        "workflow_run_id",
        "started_at_utc",
        "finished_at_utc",
        "platform",
        "python_version",
        "runner",
        "runner_sha256",
        "command",
    }
    if set(value) != required:
        raise ScaleQualificationError("run binding keys are not closed")
    if value["runner"] != RUNNER_RELATIVE_PATH:
        raise ScaleQualificationError("run binding points at a different runner")
    if not isinstance(value["run_id"], str) or not value["run_id"]:
        raise ScaleQualificationError("run_id is invalid")
    if (
        isinstance(value["workflow_run_id"], bool)
        or not isinstance(value["workflow_run_id"], int)
        or value["workflow_run_id"] < 1
    ):
        raise ScaleQualificationError("workflow_run_id is invalid")
    digest = value["runner_sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or digest == "0" * 64
    ):
        raise ScaleQualificationError("runner hash binding is invalid")
    if not isinstance(value["platform"], str) or not value["platform"].strip():
        raise ScaleQualificationError("platform binding is invalid")
    if not isinstance(value["python_version"], str) or not value["python_version"].strip():
        raise ScaleQualificationError("python version binding is invalid")
    for field in ("started_at_utc", "finished_at_utc"):
        if not isinstance(value[field], str):
            raise ScaleQualificationError(f"{field} is invalid")
        try:
            datetime.fromisoformat(value[field].replace("Z", "+00:00"))
        except ValueError as error:
            raise ScaleQualificationError(f"{field} is invalid") from error
    if not isinstance(value["command"], str) or not value["command"].strip():
        raise ScaleQualificationError("qualification command is missing")
    return dict(value)


def _failure_ids(report: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    vault = report["vault"]
    if vault["active_governed_object_count"] != ACTIVE_GOVERNED_OBJECT_TARGET:
        failures.append("active_governed_object_count_mismatch")
    if vault["above_10000_status"] != "experimental_unqualified":
        failures.append("experimental_over_10000_claimed_qualified")
    if vault["deferred_100000"] != DEFERRED_100000:
        failures.append("100000_not_deferred_to_v0.14")
    warm = report["warm_samples"]
    for kind in ("query", "context"):
        entry = warm[kind]
        if (
            entry["sample_count"] < WARM_SAMPLE_TARGET
            or len(entry["samples_ms"]) < WARM_SAMPLE_TARGET
        ):
            failures.append("warm_samples_below_30")
        for metric, field in (("p50", "p50_ms"), ("p95", "p95_ms"), ("max", "max_ms")):
            if field not in entry or entry[field] is None:
                failures.append(f"missing_{metric}")
    metrics = report["metrics"]
    rss = metrics.get("rss")
    if not isinstance(rss, Mapping) or any(
        rss.get(field, 0) < 1 for field in ("start_bytes", "peak_bytes", "end_bytes")
    ):
        failures.append("rss_missing")
    if not isinstance(metrics.get("storage_bytes"), int) or metrics["storage_bytes"] < 1:
        failures.append("storage_missing")
    if not isinstance(metrics.get("file_count"), int) or metrics["file_count"] < 1:
        failures.append("file_count_missing")
    if not _finite_nonnegative(metrics.get("build_duration_ms")):
        failures.append("build_duration_missing")
    if not _finite_nonnegative(metrics.get("rebuild_duration_ms")):
        failures.append("rebuild_duration_missing")
    equivalence = report["equivalence"]
    if not (
        equivalence["full_incremental_equal"]
        and equivalence["incremental_noop_equal"]
        and equivalence["exact"]
        and equivalence["full"]["sha256"]
        == equivalence["incremental"]["sha256"]
        == equivalence["no_op"]["sha256"]
    ):
        failures.append("full_incremental_noop_mismatch")
    if not _rebuild_modes_valid(report["rebuild"], equivalence):
        failures.append("full_incremental_noop_mismatch")
    source_compile = report["source_compile"]
    if (
        source_compile["source_file_count"] != SOURCE_BATCH_COUNT
        or source_compile["fragments_per_source"] != FRAGMENTS_PER_SOURCE
        or source_compile["query_plan_version"] != "5"
        or source_compile["asset_count"] != ACTIVE_GOVERNED_OBJECT_TARGET
        or source_compile["unique_asset_count"] != ACTIVE_GOVERNED_OBJECT_TARGET
        or source_compile["expected_asset_count"] != ACTIVE_GOVERNED_OBJECT_TARGET
        or source_compile["exact"] is not True
        or not _semantic_batches_valid(
            report["semantic_batches"], expected_count=ACTIVE_GOVERNED_OBJECT_TARGET
        )
    ):
        failures.append("active_governed_object_count_mismatch")
    batches = report["semantic_batches"]
    if isinstance(batches, list) and any(
        isinstance(batch, Mapping)
        and batch.get("publication_request_bytes", 0) > MAX_COMPILATION_REQUEST_BYTES
        for batch in batches
    ):
        failures.append("provider_bound_exceeded")
    user_bytes = report["user_bytes"]
    files = user_bytes.get("files")
    if (
        not isinstance(files, list)
        or user_bytes.get("file_count") != len(files)
        or not files
        or user_bytes.get("all_unchanged") is not True
        or any(
            item.get("size_before") != item.get("size_after")
            or item.get("sha256_before") != item.get("sha256_after")
            or item.get("unchanged") is not True
            for item in files
        )
    ):
        failures.append("user_bytes_unbounded")
    provider = report["provider"]
    if (
        provider["hard_limit_bytes"] != PROVIDER_HARD_LIMIT_BYTES
        or provider["max_bytes"] > PROVIDER_HARD_LIMIT_BYTES
        or provider["violation_count"] != 0
        or provider["violation"] is not False
    ):
        failures.append("provider_bound_exceeded")
    return list(dict.fromkeys(failures))


def build_scale_qualification_report(
    *,
    candidate_binding: Mapping[str, Any],
    run_binding: Mapping[str, Any],
    active_governed_object_count: int,
    query_samples_ms: Sequence[float],
    context_samples_ms: Sequence[float],
    rss: Mapping[str, int],
    storage_bytes: int,
    file_count: int,
    build_duration_ms: float,
    rebuild_duration_ms: float,
    equivalence: Mapping[str, Any],
    rebuild: Mapping[str, Any],
    source_compile: Mapping[str, Any],
    semantic_batches: Sequence[Mapping[str, Any]],
    user_files: Sequence[Mapping[str, Any]],
    provider_sample_bytes: Sequence[int],
    status: str = "executed",
    above_10000_status: str = "experimental_unqualified",
    deferred_100000: str = DEFERRED_100000,
) -> dict[str, Any]:
    """Build one canonical report from observed, non-derived measurements.

    Callers must pass the actual measurements.  In particular, the function
    does not turn a requested object count, a synthetic smaller sample, or a
    claimed equivalence into observed evidence.
    """

    if status not in {"executed", "failed", "not_executed"}:
        raise ScaleQualificationError("scale status is invalid")
    candidate = _candidate_binding(candidate_binding)
    run = _run_binding(run_binding)
    if (
        isinstance(active_governed_object_count, bool)
        or not isinstance(active_governed_object_count, int)
        or active_governed_object_count < 0
    ):
        raise ScaleQualificationError("active governed object count is invalid")
    if not isinstance(rss, Mapping) or set(rss) != {"start_bytes", "peak_bytes", "end_bytes"}:
        raise ScaleQualificationError("RSS measurement keys are not closed")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in rss.values()
    ):
        raise ScaleQualificationError("RSS measurements are invalid")
    if isinstance(storage_bytes, bool) or not isinstance(storage_bytes, int) or storage_bytes < 1:
        raise ScaleQualificationError("storage measurement is invalid")
    if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count < 1:
        raise ScaleQualificationError("file count measurement is invalid")
    if not _finite_nonnegative(build_duration_ms) or not _finite_nonnegative(rebuild_duration_ms):
        raise ScaleQualificationError("build/rebuild durations are invalid")
    if set(equivalence) != {
        "full",
        "incremental",
        "no_op",
        "full_incremental_equal",
        "incremental_noop_equal",
        "exact",
    }:
        raise ScaleQualificationError("equivalence keys are not closed")
    normalized_equivalence = dict(equivalence)
    for label in ("full", "incremental", "no_op"):
        item = normalized_equivalence[label]
        if not isinstance(item, Mapping) or set(item) != {"sha256"}:
            raise ScaleQualificationError(f"equivalence {label} digest is invalid")
        digest = item["sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ScaleQualificationError(f"equivalence {label} digest is invalid")
    if (
        not isinstance(user_files, Sequence)
        or isinstance(user_files, (str, bytes))
        or not user_files
    ):
        raise ScaleQualificationError("at least one protected user file is required")
    normalized_user_files = [dict(item) for item in user_files]
    if (
        not isinstance(provider_sample_bytes, Sequence)
        or isinstance(provider_sample_bytes, (str, bytes))
        or not provider_sample_bytes
    ):
        raise ScaleQualificationError("provider samples are required")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in provider_sample_bytes
    ):
        raise ScaleQualificationError("provider sample bytes are invalid")
    if not isinstance(rebuild, Mapping) or not isinstance(source_compile, Mapping):
        raise ScaleQualificationError("scale rebuild/source measurements are invalid")
    if not isinstance(semantic_batches, Sequence) or isinstance(semantic_batches, (str, bytes)):
        raise ScaleQualificationError("semantic batch measurements are invalid")
    normalized_rebuild = {key: dict(value) for key, value in rebuild.items()}
    normalized_source_compile = dict(source_compile)
    normalized_batches = [dict(value) for value in semantic_batches]
    if not _semantic_batches_valid(
        normalized_batches,
        expected_count=ACTIVE_GOVERNED_OBJECT_TARGET,
    ):
        raise ScaleQualificationError("semantic batches do not cover the exact 10k target")
    if not _rebuild_modes_valid(
        normalized_rebuild,
        normalized_equivalence,
    ):
        raise ScaleQualificationError(
            "rebuild modes are not a real full/minimal/incremental/no-op path"
        )
    if (
        normalized_source_compile.get("source_file_count") != SOURCE_BATCH_COUNT
        or normalized_source_compile.get("fragments_per_source") != FRAGMENTS_PER_SOURCE
        or normalized_source_compile.get("query_plan_version") != "5"
        or normalized_source_compile.get("asset_count") != ACTIVE_GOVERNED_OBJECT_TARGET
        or normalized_source_compile.get("unique_asset_count")
        != ACTIVE_GOVERNED_OBJECT_TARGET
        or normalized_source_compile.get("expected_asset_count")
        != ACTIVE_GOVERNED_OBJECT_TARGET
        or normalized_source_compile.get("exact") is not True
    ):
        raise ScaleQualificationError("source assets do not cover the exact 10k target")
    max_provider_bytes = max(provider_sample_bytes)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "status": status,
        "release_gate_passed": False,
        "candidate_binding": candidate,
        "run_binding": run,
        "vault": {
            "name": "Vault",
            "active_governed_object_count": active_governed_object_count,
            "target_active_governed_object_count": ACTIVE_GOVERNED_OBJECT_TARGET,
            "above_10000_status": above_10000_status,
            "deferred_100000": deferred_100000,
        },
        "warm_samples": {
            "minimum_samples": WARM_SAMPLE_TARGET,
            "query": _latency(query_samples_ms),
            "context": _latency(context_samples_ms),
        },
        "metrics": {
            "rss": dict(rss),
            "storage_bytes": storage_bytes,
            "file_count": file_count,
            "build_duration_ms": round(float(build_duration_ms), 6),
            "rebuild_duration_ms": round(float(rebuild_duration_ms), 6),
        },
        "equivalence": normalized_equivalence,
        "rebuild": normalized_rebuild,
        "source_compile": normalized_source_compile,
        "semantic_batches": normalized_batches,
        "user_bytes": {
            "file_count": len(normalized_user_files),
            "files": normalized_user_files,
            "all_unchanged": all(item.get("unchanged") is True for item in normalized_user_files),
        },
        "provider": {
            "hard_limit_bytes": PROVIDER_HARD_LIMIT_BYTES,
            "sample_count": len(provider_sample_bytes),
            "max_bytes": max_provider_bytes,
            "violation_count": sum(
                value > PROVIDER_HARD_LIMIT_BYTES for value in provider_sample_bytes
            ),
            "violation": max_provider_bytes > PROVIDER_HARD_LIMIT_BYTES,
        },
        "scope": {
            "above_10000": "experimental_unqualified",
            "hundred_thousand": "deferred_v0.14",
            "claim_eligible": False,
        },
        "hard_failures": [],
        "report_sha256": "0" * 64,
    }
    failures = _failure_ids(report)
    report["hard_failures"] = failures
    report["release_gate_passed"] = status == "executed" and not failures
    report["scope"]["claim_eligible"] = report["release_gate_passed"]
    report["report_sha256"] = _sha256_bytes(
        _canonical_bytes(
            {key: value for key, value in report.items() if key != "report_sha256"}
        )
    )
    return report


def _schema_errors(report: Mapping[str, Any]) -> list[str]:
    validator = Draft202012Validator(
        json.loads(_schema_path().read_text(encoding="utf-8")),
        format_checker=FormatChecker(),
    )
    return [
        error.message
        for error in sorted(validator.iter_errors(report), key=lambda item: list(item.path))
    ]


def verify_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Fail-closed structural and semantic verification of a scale report."""

    if not isinstance(report, Mapping):
        return {"valid": False, "errors": ["report must be an object"]}
    errors = _schema_errors(report)
    if errors:
        return {"valid": False, "errors": errors}
    try:
        declared_digest = report["report_sha256"]
        observed_digest = _sha256_bytes(
            _canonical_bytes(
                {key: value for key, value in report.items() if key != "report_sha256"}
            )
        )
        if declared_digest != observed_digest:
            errors.append("report_sha256 does not match canonical report bytes")
        for kind in ("query", "context"):
            latency = report["warm_samples"][kind]
            expected = _latency(latency["samples_ms"])
            for field in ("sample_count", "p50_ms", "p95_ms", "max_ms"):
                if latency[field] != expected[field]:
                    errors.append(f"{kind} {field} does not match raw samples")
        try:
            started = datetime.fromisoformat(
                report["run_binding"]["started_at_utc"].replace("Z", "+00:00")
            )
            finished = datetime.fromisoformat(
                report["run_binding"]["finished_at_utc"].replace("Z", "+00:00")
            )
            if started.tzinfo is None or finished.tzinfo is None or finished < started:
                errors.append("run timestamps are not an ordered UTC interval")
        except ValueError:
            errors.append("run timestamps are invalid")
        expected_failures = _failure_ids(report)
        if report["hard_failures"] != expected_failures:
            errors.append("hard_failures do not match observed scale conditions")
        expected_release = report["status"] == "executed" and not expected_failures
        if report["release_gate_passed"] is not expected_release:
            errors.append("release_gate_passed is inconsistent with status and failures")
        if report["scope"]["claim_eligible"] is not expected_release:
            errors.append("claim_eligible is inconsistent with status and failures")
    except (KeyError, TypeError, ValueError, ScaleQualificationError) as error:
        errors.append(f"semantic verification failed: {error}")
    return {"valid": not errors, "errors": errors}


def verify_scale_qualification_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Compatibility-friendly alias for callers that name the report explicitly."""

    return verify_report(report)


def _git_binding() -> tuple[str, str]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ScaleQualificationError("candidate Git binding is unavailable") from error
    if len(commit) != 40 or len(tree) != 40:
        raise ScaleQualificationError("candidate Git binding is invalid")
    return commit, tree


def _rss_bytes() -> int:
    """Return current RSS without using monotonic ``ru_maxrss``."""

    if platform.system() == "Linux":
        fields = Path("/proc/self/statm").read_text(encoding="ascii").split()
        return int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
    if platform.system() == "Darwin":
        output = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        return int(output) * 1024
    raise ScaleQualificationError("current RSS is unavailable on this platform")


def _storage(root: Path) -> tuple[int, int]:
    total = 0
    files = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            files += 1
            total += path.stat().st_size
    return total, files


def _equivalence_digest(receipt: Mapping[str, Any]) -> str:
    """Hash only stable derived-state identity, excluding timestamps and snapshots."""

    living_wiki = receipt.get("living_wiki")
    change_set = living_wiki.get("change_set") if isinstance(living_wiki, Mapping) else None
    if not isinstance(change_set, Mapping):
        change_set = {}
    stable = {
        "input_audit_head": receipt.get("input_audit_head"),
        "legacy_audit_head": receipt.get("legacy_audit_head"),
        "generator": receipt.get("generator"),
        "generator_version": receipt.get("generator_version"),
        "configuration": receipt.get("configuration"),
        "fts_rows_sha256": receipt.get("fts_rows_sha256"),
        "dense_manifest_sha256": receipt.get("dense_manifest_sha256"),
        "knowledge_revision_count": receipt.get("knowledge_revision_count"),
        "knowledge_revision_ids_sha256": receipt.get("knowledge_revision_ids_sha256"),
        "relation_revision_count": receipt.get("relation_revision_count"),
        "relation_revision_ids_sha256": receipt.get("relation_revision_ids_sha256"),
        "living_wiki_manifest_sha256": (
            living_wiki.get("manifest_sha256") if isinstance(living_wiki, Mapping) else None
        ),
        "new_manifest_sha256": change_set.get("new_manifest_sha256"),
        "new_v3_manifest_sha256": change_set.get("new_v3_manifest_sha256"),
        "new_inventory_sha256": change_set.get("new_inventory_sha256"),
    }
    return _sha256_bytes(_canonical_bytes(stable))


def _rebuild_mode_receipt(
    receipt: Mapping[str, Any],
    *,
    mode: str,
    projection_profile: str,
    stable_identity_sha256: str,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "projection_profile": projection_profile,
        "change_counts": _change_counts(receipt),
        "stable_identity_sha256": stable_identity_sha256,
    }


@contextmanager
def _temporary_vault(workspace: Path | None) -> Iterator[Path]:
    if workspace is None:
        with tempfile.TemporaryDirectory(prefix="deeplaw-v013-scale-qualification-") as temporary:
            yield Path(temporary)
        return
    root = workspace.expanduser().absolute()
    if root.exists() and any(root.iterdir()):
        raise ScaleQualificationError("qualification workspace must be absent or empty")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        yield root
    finally:
        if root.exists():
            shutil.rmtree(root)


def _source_fixture(root: Path, count: int) -> tuple[Path, Path]:
    """Create source and protected user bytes; no input outside this workspace is read."""

    source = root / "source.md"
    user_file = root / "user-owned.md"
    source.write_text(
        "\n".join(
            f"# Object {index:05d}\nBounded scale evidence {index:05d}."
            for index in range(count)
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    user_file.write_text(
        "owner bytes must survive full and incremental rebuilds\n",
        encoding="utf-8",
        newline="\n",
    )
    return source, user_file


def _source_batch_fixture(
    root: Path,
    *,
    source_count: int = SOURCE_BATCH_COUNT,
    fragments_per_source: int = FRAGMENTS_PER_SOURCE,
) -> tuple[list[Path], Path]:
    """Create bounded Source files so every semantic run stays request-bounded."""

    if source_count < 1 or fragments_per_source < 1:
        raise ScaleQualificationError("source batch fixture dimensions are invalid")
    sources: list[Path] = []
    for source_index in range(source_count):
        source = root / f"source-{source_index:03d}.md"
        source.write_text(
            "\n".join(
                f"# Object {source_index:03d}-{fragment_index:03d}\n"
                f"Bounded scale evidence {source_index:03d}-{fragment_index:03d}."
                for fragment_index in range(fragments_per_source)
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        sources.append(source)
    user_file = root / "user-owned.md"
    user_file.write_text(
        "owner bytes must survive full, minimal, incremental and no-op rebuilds\n",
        encoding="utf-8",
        newline="\n",
    )
    return sources, user_file


def _public_semantic_compile(
    vault: Path,
    source_result: Mapping[str, Any],
    *,
    target: int,
    global_offset: int = 0,
    batch_index: int = 0,
    knowledge_os_handle: Any | None = None,
) -> dict[str, Any]:
    """Publish one bounded Source IR batch through the public compiler seam."""

    from deeplaw.api.knowledge_os import KnowledgeOS
    from deeplaw.compilation.models import SEMANTIC_COMPILER_GRANT_OPERATIONS
    from deeplaw.compilation.semantic import SemanticCompilationService
    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore

    identity = source_result.get("identity")
    if not isinstance(identity, Mapping):
        raise ScaleQualificationError("source identity is unavailable")
    source_revision_id = identity.get("source_revision_id")
    if not isinstance(source_revision_id, str):
        raise ScaleQualificationError("source revision identity is unavailable")
    asset_ids = source_result.get("asset_ids")
    if (
        not isinstance(asset_ids, list)
        or len(asset_ids) != target
        or len(set(asset_ids)) != target
    ):
        raise ScaleQualificationError("source batch does not contain the exact asset target")
    if (
        isinstance(global_offset, bool)
        or not isinstance(global_offset, int)
        or global_offset < 0
        or isinstance(batch_index, bool)
        or not isinstance(batch_index, int)
        or batch_index < 0
    ):
        raise ScaleQualificationError("source batch offset or index is invalid")
    knowledge_os_context = (
        nullcontext(knowledge_os_handle)
        if knowledge_os_handle is not None
        else KnowledgeOS.open(vault)
    )
    with knowledge_os_context as knowledge_os:
        profile = knowledge_os.compilations.profile(version="3")
        with AutonomousKnowledgeStore(vault, read_only=False) as store:
            grant_id = store.enable_grant(
                writer_id=f"v013-scale-qualification-v9:batch:{batch_index:03d}",
                operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
                max_request_bytes=MAX_COMPILATION_REQUEST_BYTES,
                max_mutations_per_minute=120,
                # ``max_objects`` is the domain's cumulative Vault capacity,
                # so each batch grants exactly the post-commit total while the
                # staged/publication action count remains bounded by ``target``.
                max_objects=global_offset + target,
            )["grant_id"]
        run = knowledge_os.compilations.begin(
            grant_id=grant_id,
            source_revision_id=source_revision_id,
            compiler_profile=profile["compiler_profile"],
            compiler_profile_version=profile["compiler_profile_version"],
            host_identity="v013-scale-qualification-v9",
            model_identity=None,
            prompt_template_id=profile["prompt_template_id"],
            prompt_config_sha256=profile["prompt_config_sha256"],
            plan_configuration_sha256=profile["plan_configuration_sha256"],
            packet_max_fragments=min(target, 128),
            confirm_no_case_data=True,
        )
        packet_plans: list[dict[str, Any]] = []
        dispositions: list[dict[str, Any]] = []
        observed_count = 0
        while packet := run.next_packet():
            object_actions: list[dict[str, Any]] = []
            observations: list[dict[str, Any]] = []
            for _ordinal, fragment in enumerate(packet["fragments"], start=1):
                if observed_count >= target:
                    raise ScaleQualificationError(
                        "Source IR emitted more than the exact object target"
                    )
                source_ref = {
                    "source_revision_id": packet["source_revision_id"],
                    "fragment_id": fragment["fragment_id"],
                    "locator": fragment["locator"],
                    "quote_sha256": fragment["text_sha256"],
                }
                ordinal = global_offset + observed_count
                semantic_key = f"v013-scale-qualification-v9:{ordinal:05d}"
                title = str(fragment.get("title") or f"Scale Object {ordinal:05d}")
                body = str(fragment["text"]).strip()
                observation = {
                    "packet_id": packet["packet_id"],
                    "semantic_key_candidate": semantic_key,
                    "kind": "claim",
                    "title_candidate": title,
                    "body_candidate": body,
                    "aliases": [],
                    "source_refs": [source_ref],
                    "assertion": None,
                    "applicability": None,
                    "tags": ["v013-scale-qualification"],
                    "reason": "Exact v0.13 scale qualification source-bound object.",
                }
                observation["observation_id"] = SemanticCompilationService.observation_id(
                    compilation_run_id=packet["compilation_run_id"],
                    packet_id=packet["packet_id"],
                    observation=observation,
                )
                observations.append(observation)
                object_actions.append(
                    {
                        "action": "create",
                        "kind": "claim",
                        "semantic_key": semantic_key,
                        "knowledge_id": None,
                        "expected_revision_id": None,
                        "title": title,
                        "body": body,
                        "aliases": [],
                        "epistemic_state": "supported",
                        "source_refs": [source_ref],
                        "assertion": None,
                        "tags": ["v013-scale-qualification"],
                        "valid_from": None,
                        "valid_to": None,
                        "applicability": {
                            "description": "Exact source-bound scale object.",
                            "scopes": [],
                            "conditions": [],
                            "exclusions": [],
                        },
                        "synthesis_inputs": None,
                        "reason": "Publish one exact active governed Knowledge Object.",
                    }
                )
                dispositions.append(
                    {
                        "observation_id": observation["observation_id"],
                        "disposition": "published",
                        "target_ref": semantic_key,
                        "reason": "Publish the exact source-bound scale object.",
                    }
                )
                observed_count += 1
            run.stage_observations(
                {
                    "schema_version": "deeplaw.source-compilation-observation-plan/v2",
                    "compilation_run_id": packet["compilation_run_id"],
                    "source_revision_id": packet["source_revision_id"],
                    "packet_id": packet["packet_id"],
                    "expected_audit_head": packet["input_audit_head"],
                    "observations": observations,
                    "coverage": {
                        "packet_fragment_count": len(packet["fragments"]),
                        "covered_fragment_ids": [
                            fragment["fragment_id"] for fragment in packet["fragments"]
                        ],
                        "omitted_fragments": [],
                        "ratio": 1.0,
                    },
                    "warnings": [],
                },
                confirm_no_case_data=True,
            )
            packet_plans.append(
                {
                    "schema_version": "deeplaw.source-compilation-plan/v1",
                    "source_revision_id": packet["source_revision_id"],
                    "packet_id": packet["packet_id"],
                    "expected_audit_head": packet["input_audit_head"],
                    "object_actions": object_actions,
                    "relation_actions": [],
                    "identity_actions": [],
                    "unresolved_identities": [],
                    "contradictions": [],
                    "coverage": {
                        "packet_fragment_count": len(packet["fragments"]),
                        "covered_fragment_ids": [
                            fragment["fragment_id"] for fragment in packet["fragments"]
                        ],
                        "omitted_fragment_ids": [],
                        "ratio": 1.0,
                        "completeness": "complete",
                    },
                    "skipped_fragments": [],
                    "warnings": [],
                }
            )
        if observed_count != target:
            raise ScaleQualificationError(
                f"Source IR emitted {observed_count} objects; expected {target}"
            )
        inventory = run.semantic_inventory(confirm_no_case_data=True)
        finalization = run.finalization_packet()
        finalization_provider_bytes = len(canonical_json(finalization).encode("utf-8"))
        if finalization_provider_bytes > PROVIDER_HARD_LIMIT_BYTES:
            raise ScaleQualificationError(
                "semantic finalization packet exceeds the provider hard bound"
            )
        duty_reports: list[dict[str, Any]] = []
        for duty in finalization["duties"]:
            applicable = duty["applicability"]
            duty_reports.append(
                {
                    "duty_id": duty["duty_id"],
                    "duty_type": duty["duty_type"],
                    "required": duty["required"],
                    "applicability": applicable,
                    "status": (
                        "omitted_with_reason"
                        if applicable == "not_applicable"
                        else "unresolved"
                    ),
                    "output_refs": [],
                    "evidence_refs": [],
                    "reason": "Scale fixture has no independent semantic duty witness.",
                    "unresolved_items": (
                        []
                        if applicable == "not_applicable"
                        else ["Scale qualification does not claim semantic duty coverage."]
                    ),
                    "omission_reason": (
                        "No deterministic witness in this scale fixture."
                        if applicable == "not_applicable"
                        else None
                    ),
                    "deterministic_basis": duty["deterministic_basis"],
                }
            )
        publication = {
            "schema_version": "deeplaw.semantic-publication-plan/v3",
            "compiler_profile_version": "3",
            "compilation_run_id": run.compilation_run_id,
            "source_revision_id": source_revision_id,
            "expected_audit_head": run.begin_receipt()["input_audit_head"],
            "inventory_sha256": inventory["inventory_sha256"],
            "finalization_packet_id": finalization["finalization_packet_id"],
            "applicability_policy_sha256": finalization["applicability_policy_sha256"],
            "applicability_digest": finalization["applicability_digest"],
            "packet_plans": packet_plans,
            "statement_plans": [],
            "observation_dispositions": dispositions,
            "duty_reports": duty_reports,
            "semantic_status": "partial",
            "warnings": ["Exact scale fixture; no semantic quality claim."],
        }
        publication_bytes = len(canonical_json(publication).encode("utf-8"))
        if publication_bytes > MAX_COMPILATION_REQUEST_BYTES:
            raise ScaleQualificationError(
                "semantic publication plan exceeds the public request byte bound"
            )
        publication_sha256 = _sha256_bytes(canonical_json(publication).encode("utf-8"))
        run.stage_publication(publication, confirm_no_case_data=True)
        if run.validate(confirm_no_case_data=True)["valid"] is not True:
            raise ScaleQualificationError("public semantic compilation validation failed")
        quality_receipt = run.commit(confirm_no_case_data=True)
        if quality_receipt.get("observation_count") != target:
            raise ScaleQualificationError(
                "semantic quality receipt does not cover the exact object target"
            )
        return {
            **quality_receipt,
            "batch_index": batch_index,
            "global_offset": global_offset,
            "target_object_count": target,
            "grant_max_objects": global_offset + target,
            "grant_id": grant_id,
            "compilation_run_id": run.compilation_run_id,
            "source_revision_id": source_revision_id,
            "asset_count": len(asset_ids),
            "asset_ids_sha256": _sha256_bytes(canonical_json(asset_ids).encode("utf-8")),
            "publication_request_bytes": publication_bytes,
            "publication_request_sha256": publication_sha256,
            "publication_request_limit_bytes": MAX_COMPILATION_REQUEST_BYTES,
            "finalization_provider_bytes": finalization_provider_bytes,
            "published_object_count": observed_count,
            "committed_object_count": observed_count,
            "committed_relation_count": 0,
        }


def _user_file_receipt(path: Path, before: bytes) -> dict[str, Any]:
    after = path.read_bytes()
    return {
        "relative_path": "user-owned.md",
        "size_before": len(before),
        "size_after": len(after),
        "sha256_before": _sha256_bytes(before),
        "sha256_after": _sha256_bytes(after),
        "unchanged": before == after,
    }


def _artifact_binding(path: Path) -> dict[str, Any]:
    return {
        "filename": path.name,
        "sha256": _sha256_path(path),
        "size_bytes": path.stat().st_size,
    }


def run_scale_qualification(
    *,
    candidate_binding: Mapping[str, Any],
    wheel_path: Path,
    sdist_path: Path,
    lock_sha256: str,
    workflow_run_id: int,
    workspace: Path | None = None,
    execute_10k: bool = False,
    command: str | None = None,
) -> dict[str, Any]:
    """Execute the exact 10k lane using the first-party source/query/context paths.

    ``execute_10k`` is deliberately mandatory.  The runner refuses an artifact
    mismatch before creating a Vault, so a development tree cannot accidentally
    produce qualification evidence.
    """

    if not execute_10k:
        raise ScaleQualificationError("exact 10k qualification requires --execute-10k")
    wheel = Path(wheel_path).expanduser().absolute()
    sdist = Path(sdist_path).expanduser().absolute()
    if not wheel.is_file() or not sdist.is_file():
        raise ScaleQualificationError("exact candidate wheel and sdist are required")
    # Keep the caller-supplied candidate as the report binding.  Observed
    # values are an independent comparison target; silently replacing a
    # caller error with the observed artifact would turn a wrong candidate
    # into an apparently valid qualification.
    candidate = _candidate_binding(candidate_binding)
    try:
        installed_version = package_version("deeplaw")
    except PackageNotFoundError as error:
        raise ScaleQualificationError("candidate deeplaw package is not installed") from error
    if installed_version != candidate["version"]:
        raise ScaleQualificationError(
            "installed deeplaw package does not match candidate version"
        )
    commit, tree = _git_binding()
    observed_candidate = _candidate_binding(
        {
            "commit": commit,
            "tree": tree,
            "version": installed_version,
            "lock_sha256": lock_sha256,
            "wheel": _artifact_binding(wheel),
            "sdist": _artifact_binding(sdist),
        }
    )
    for field in ("commit", "tree", "version", "lock_sha256", "wheel", "sdist"):
        if candidate[field] != observed_candidate[field]:
            raise ScaleQualificationError(
                f"candidate {field} binding does not match the observed candidate"
            )
    runner_path = Path(__file__).resolve()
    started = _utc_now()
    run_id = f"scale-v9-{uuid.uuid4().hex[:24]}"
    query_times: list[float] = []
    context_times: list[float] = []
    provider_bytes: list[int] = []
    with _temporary_vault(workspace) as root:
        source_files, user_file = _source_batch_fixture(root)
        if len(source_files) != SOURCE_BATCH_COUNT:
            raise ScaleQualificationError(
                f"source fixture does not have {SOURCE_BATCH_COUNT} files"
            )
        rss_start = _rss_bytes()
        rss_samples = [rss_start]
        build_start = time.perf_counter()
        # Importing these first-party services here keeps the module import-only
        # for contract/unit tests and ensures execution uses the public domain seam.
        from deeplaw.api.knowledge_os import KnowledgeOS
        from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
        from deeplaw.knowledge_compiler import compile_source
        from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault

        vault = root / "Vault"
        initialize_knowledge_vault(vault, name="v0.13 exact scale qualification", scope="project")
        user_bytes = user_file.read_bytes()
        user_file = vault / user_file.name
        user_file.write_bytes(user_bytes)
        user_before = user_file.read_bytes()
        source_results: list[Mapping[str, Any]] = []
        asset_ids: list[str] = []
        with KnowledgeVault(vault, read_only=False) as legacy:
            for source_index, source in enumerate(source_files):
                compiled = compile_source(
                    legacy,
                    source,
                    source_kind="document",
                    sensitivity="public",
                    confirm_no_case_data=True,
                    logical_path=f"source-{source_index:03d}.md",
                )
                batch_asset_ids = compiled.get("asset_ids")
                if (
                    not isinstance(batch_asset_ids, list)
                    or len(batch_asset_ids) != FRAGMENTS_PER_SOURCE
                    or len(set(batch_asset_ids)) != FRAGMENTS_PER_SOURCE
                ):
                    raise ScaleQualificationError(
                        "public source compilation did not admit one exact source batch"
                    )
                source_results.append(compiled)
                asset_ids.extend(batch_asset_ids)
        if (
            len(asset_ids) != ACTIVE_GOVERNED_OBJECT_TARGET
            or len(set(asset_ids)) != ACTIVE_GOVERNED_OBJECT_TARGET
        ):
            raise ScaleQualificationError(
                "public source compilation did not admit exact unique assets"
            )
        initialize_autonomous_core(vault)
        semantic_batches: list[dict[str, Any]] = []
        with KnowledgeOS.open(vault) as compilation_knowledge_os:
            for batch_index, source_result in enumerate(source_results):
                semantic_commit = _public_semantic_compile(
                    vault,
                    source_result,
                    target=FRAGMENTS_PER_SOURCE,
                    global_offset=batch_index * FRAGMENTS_PER_SOURCE,
                    batch_index=batch_index,
                    knowledge_os_handle=compilation_knowledge_os,
                )
                semantic_batches.append(
                    {
                        field: semantic_commit[field]
                        for field in (
                            "batch_index",
                            "global_offset",
                            "target_object_count",
                            "grant_max_objects",
                            "grant_id",
                            "compilation_run_id",
                            "source_revision_id",
                            "asset_count",
                            "asset_ids_sha256",
                            "publication_request_bytes",
                            "publication_request_sha256",
                            "publication_request_limit_bytes",
                            "published_object_count",
                            "committed_object_count",
                            "committed_relation_count",
                        )
                    }
                )
                rss_samples.append(_rss_bytes())
        build_duration_ms = (time.perf_counter() - build_start) * 1000
        full_start = time.perf_counter()
        with AutonomousKnowledgeStore(vault, read_only=False) as store:
            full_result = store.rebuild_derived(projection_profile="standard")
        full_duration_ms = (time.perf_counter() - full_start) * 1000
        active_count = int(full_result.get("knowledge_count", -1))
        if (
            active_count != ACTIVE_GOVERNED_OBJECT_TARGET
            or sum(item["committed_object_count"] for item in semantic_batches)
            != ACTIVE_GOVERNED_OBJECT_TARGET
        ):
            raise ScaleQualificationError(
                "public semantic compilation did not commit the exact active object target"
            )
        rss_samples.append(_rss_bytes())
        minimal_start = time.perf_counter()
        with AutonomousKnowledgeStore(vault, read_only=False) as store:
            minimal_result = store.rebuild_derived(projection_profile="minimal")
        minimal_duration_ms = (time.perf_counter() - minimal_start) * 1000
        rss_samples.append(_rss_bytes())
        incremental_start = time.perf_counter()
        with AutonomousKnowledgeStore(vault, read_only=False) as store:
            incremental_result = store.rebuild_derived(projection_profile="standard")
        incremental_duration_ms = (time.perf_counter() - incremental_start) * 1000
        rss_samples.append(_rss_bytes())
        noop_start = time.perf_counter()
        with AutonomousKnowledgeStore(vault, read_only=False) as store:
            noop_result = store.rebuild_derived(projection_profile="standard")
        noop_duration_ms = (time.perf_counter() - noop_start) * 1000
        rss_samples.append(_rss_bytes())
        with KnowledgeOS.open(vault) as knowledge_os:
            query_text = "Bounded scale evidence 000-000"
            for _ in range(WARM_SAMPLE_TARGET):
                start = time.perf_counter()
                result = knowledge_os.retrieval.query(
                    query_text,
                    query_plan_version="5",
                    purpose="answer",
                    scope="project",
                    max_sensitivity="public",
                    limit=8,
                    max_chars=8_000,
                    max_tokens=4_000,
                )
                query_times.append((time.perf_counter() - start) * 1000)
                compiled = result.get("compiled")
                if (
                    not isinstance(compiled, list)
                    or not compiled
                    or not any(
                        item.get("semantic_key")
                        == "v013-scale-qualification-v9:00000"
                        for item in compiled
                        if isinstance(item, Mapping)
                    )
                ):
                    raise ScaleQualificationError(
                        "warm public query did not select the exact scale identity"
                    )
                query_provider_bytes = result.get("metrics", {}).get(
                    "provider_payload_bytes"
                )
                if (
                    isinstance(query_provider_bytes, bool)
                    or not isinstance(query_provider_bytes, int)
                    or query_provider_bytes < 1
                    or result.get("delivery", {}).get("provider_visible_bytes")
                    != query_provider_bytes
                ):
                    raise ScaleQualificationError(
                        "warm public query Provider byte receipt is invalid"
                    )
                provider_bytes.append(query_provider_bytes)
                start = time.perf_counter()
                context = knowledge_os.context.compile(
                    task=query_text,
                    query_plan_version="5",
                    purpose="answer",
                    scope="project",
                    max_sensitivity="public",
                    limit=8,
                    max_chars=8_000,
                    max_tokens=4_000,
                    confirm_no_case_data=True,
                )
                context_times.append((time.perf_counter() - start) * 1000)
                if context.get("budget", {}).get("selected_items", 0) < 1:
                    raise ScaleQualificationError(
                        "warm public context did not select governed knowledge"
                    )
                provider_bytes.append(len(canonical_json(context).encode("utf-8")))
        rss_samples.append(_rss_bytes())
        # Rebuild receipts include timestamps and read-snapshot bytes.  The
        # stable identity digest compares only derived-state inputs/outputs,
        # so an equality claim cannot be manufactured from whole-receipt
        # serialization or from a claimed count.
        full_digest = _equivalence_digest(full_result)
        minimal_digest = _equivalence_digest(minimal_result)
        incremental_digest = _equivalence_digest(incremental_result)
        noop_digest = _equivalence_digest(noop_result)
        rebuild_modes = {
            "full": _rebuild_mode_receipt(
                full_result,
                mode="full",
                projection_profile="standard",
                stable_identity_sha256=full_digest,
            ),
            "minimal": _rebuild_mode_receipt(
                minimal_result,
                mode="minimal",
                projection_profile="minimal",
                stable_identity_sha256=minimal_digest,
            ),
            "incremental": _rebuild_mode_receipt(
                incremental_result,
                mode="incremental",
                projection_profile="standard",
                stable_identity_sha256=incremental_digest,
            ),
            "no_op": _rebuild_mode_receipt(
                noop_result,
                mode="no_op",
                projection_profile="standard",
                stable_identity_sha256=noop_digest,
            ),
        }
        rss_end = _rss_bytes()
        storage_bytes, file_count = _storage(vault)
        user_receipt = _user_file_receipt(user_file, user_before)
        source_compile = {
            "source_file_count": len(source_files),
            "fragments_per_source": FRAGMENTS_PER_SOURCE,
            "query_plan_version": "5",
            "expected_asset_count": ACTIVE_GOVERNED_OBJECT_TARGET,
            "asset_count": len(asset_ids),
            "unique_asset_count": len(set(asset_ids)),
            "asset_ids_sha256": _sha256_bytes(canonical_json(asset_ids).encode("utf-8")),
            "exact": len(asset_ids) == len(set(asset_ids)) == ACTIVE_GOVERNED_OBJECT_TARGET,
        }
    finished = _utc_now()
    report = build_scale_qualification_report(
        candidate_binding=candidate,
        run_binding={
            "run_id": run_id,
            "workflow_run_id": workflow_run_id,
            "started_at_utc": started,
            "finished_at_utc": finished,
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "runner": RUNNER_RELATIVE_PATH,
            "runner_sha256": _sha256_path(runner_path),
            "command": command
            or "uv run --frozen python -m benchmarks.v013.scale_qualification_v9 "
            "--execute-10k",
        },
        active_governed_object_count=active_count,
        query_samples_ms=query_times,
        context_samples_ms=context_times,
        rss={"start_bytes": rss_start, "peak_bytes": max(rss_samples), "end_bytes": rss_end},
        storage_bytes=storage_bytes,
        file_count=file_count,
        build_duration_ms=build_duration_ms,
        rebuild_duration_ms=(
            full_duration_ms
            + minimal_duration_ms
            + incremental_duration_ms
            + noop_duration_ms
        ),
        equivalence={
            "full": {"sha256": full_digest},
            "incremental": {"sha256": incremental_digest},
            "no_op": {"sha256": noop_digest},
            "full_incremental_equal": full_digest == incremental_digest,
            "incremental_noop_equal": incremental_digest == noop_digest,
            "exact": full_digest == incremental_digest == noop_digest,
        },
        rebuild=rebuild_modes,
        source_compile=source_compile,
        semantic_batches=semantic_batches,
        user_files=[user_receipt],
        provider_sample_bytes=provider_bytes,
    )
    return report


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Run the exact v0.13 10k scale qualification.")
    parser.add_argument("--execute-10k", action="store_true")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--lock-sha256", required=False)
    parser.add_argument("--candidate-commit", required=False)
    parser.add_argument("--candidate-tree", required=False)
    parser.add_argument("--candidate-version", required=False)
    parser.add_argument("--candidate-wheel-sha256", required=False)
    parser.add_argument("--candidate-sdist-sha256", required=False)
    parser.add_argument("--workflow-run-id", type=int, required=False)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not all(
        (
            args.execute_10k,
            args.wheel is not None,
            args.sdist is not None,
            bool(args.lock_sha256),
            bool(args.candidate_commit),
            bool(args.candidate_tree),
            bool(args.candidate_version),
            bool(args.candidate_wheel_sha256),
            bool(args.candidate_sdist_sha256),
            args.workflow_run_id is not None and args.workflow_run_id > 0,
        )
    ):
        raise SystemExit(
            "exact 10k execution and all exact candidate bindings are required"
        )
    wheel_binding = _artifact_binding(args.wheel)
    wheel_binding["sha256"] = args.candidate_wheel_sha256
    sdist_binding = _artifact_binding(args.sdist)
    sdist_binding["sha256"] = args.candidate_sdist_sha256
    report = run_scale_qualification(
        candidate_binding={
            "commit": args.candidate_commit,
            "tree": args.candidate_tree,
            "version": args.candidate_version,
            "lock_sha256": args.lock_sha256,
            "wheel": wheel_binding,
            "sdist": sdist_binding,
        },
        wheel_path=args.wheel,
        sdist_path=args.sdist,
        lock_sha256=args.lock_sha256,
        workflow_run_id=args.workflow_run_id,
        execute_10k=True,
        command=(
            "python -m benchmarks.v013.scale_qualification_v9 --execute-10k "
            "--exact-candidate-bindings"
        ),
    )
    result = verify_report(report)
    if not result["valid"]:
        raise SystemExit("scale report failed validation: " + "; ".join(result["errors"]))
    args.output.write_bytes(_canonical_bytes(report) + b"\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
