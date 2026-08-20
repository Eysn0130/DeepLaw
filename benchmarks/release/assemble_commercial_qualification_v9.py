"""Assemble the current exact-candidate Kernel Release Core Gate v9 evidence.

The bundle validator is the admission boundary.  After admission this module
reopens the bundle's exact bytes, invokes the public v3 typed-evidence parser,
and writes only v5 Gate results and the v6 evidence report.  Capability and
competitive/research claims are retained as explicit ``not_executed`` records;
they never authorize the Kernel Release Core.

No Host, provider, network, credential, comparative reference, scorer, panel,
arbiter, release-manifest, or Ledger operation is performed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release import kernel_qualification_bundle_v1
from benchmarks.release.typed_qualification_evidence import parse_typed_evidence

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACTS = REPOSITORY / "contracts"
BUNDLE_MANIFEST_NAME = "bundle-manifest.json"
ACTIVE_RELATIVE_PATH = "benchmarks/v013/active-qualification-v3.json"
CLASSIFICATION_RELATIVE_PATH = "benchmarks/release/v013-gate-classification-v9.json"
PROTOCOL_RELATIVE_PATH = "benchmarks/v013/qualification-protocol-v3.json"
TYPED_SCHEMA_VERSION = "deeplaw.typed-qualification-evidence/v3"
GATE_SCHEMA_VERSION = "deeplaw.provenance-bound-gate-result/v5"
REPORT_SCHEMA_VERSION = "deeplaw.commercial-evidence-report/v6"
CLASSIFICATION_SCHEMA_VERSION = "deeplaw.v013-release-gate-classification/v9"
CLASSIFICATION_ID = "deeplaw-v013-commercial-gates-v9"
PROFILE = "kernel_release_core"
REFERENCE_PROVENANCE = "deterministic_expected_evidence"
HUMAN_AUTHENTICITY = "not_claimed"
TYPED_DERIVED_SCHEMA_VERSION = "deeplaw.typed-qualification-derived/v3"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024

CORE_GATE_IDS = (
    "canonical_integrity",
    "migration_recovery",
    "secret_host_isolation",
    "bounded_context",
    "source_citation_locator",
    "living_wiki",
    "scale_performance",
    "supported_platforms",
    "reproducible_supply_chain",
    "codex",
    "opencode",
    "selective_forget",
    "timeline",
)
CAPABILITY_GATE_IDS = (
    "official_legal_pack",
    "semantic_restore",
    "claude",
    "gui_desktop_interoperability",
)
COMPETITIVE_GATE_IDS = (
    "machine_reference_isolation",
    "qualification_comparative_holdout",
    "final_blind_comparative_holdout",
    "agent_review_panel",
    "scorer_a",
    "scorer_b",
    "arbiter",
    "comparative_incremental_benefit",
    "superiority",
    "sota",
)
ALL_GATE_IDS = frozenset((*CORE_GATE_IDS, *CAPABILITY_GATE_IDS, *COMPETITIVE_GATE_IDS))
HOST_TASK_CASES = frozenset(
    {"continuity", "living_wiki", "professional_evidence"}
)
CANDIDATE_WORKFLOW_KINDS = frozenset(
    {
        "candidate_full_junit",
        "candidate_platform_receipt",
        "retained_supply_chain",
        "scale_report",
    }
)
TYPED_KINDS = frozenset(kernel_qualification_bundle_v1.TYPED_COUNTS)
CORPUS_ROLES = frozenset(kernel_qualification_bundle_v1.CORPUS_ROLES)
EXPECTED_CORE_KINDS = {
    "canonical_integrity": "exact_wheel_execution",
    "migration_recovery": "candidate_full_junit",
    "secret_host_isolation": "host_event_sequence",
    "bounded_context": "context_capsule_selection_usage",
    "source_citation_locator": "professional_evidence_rows",
    "living_wiki": "wiki_journey_rows",
    "scale_performance": "scale_report",
    "supported_platforms": "candidate_platform_receipt",
    "reproducible_supply_chain": "retained_supply_chain",
    "codex": "host_event_sequence",
    "opencode": "host_event_sequence",
    "selective_forget": "host_event_sequence",
    "timeline": "host_event_sequence",
}
EXPECTED_CORE_ROLES = {
    "canonical_integrity": ("candidate_full",),
    "migration_recovery": ("candidate_full",),
    "secret_host_isolation": ("host_qualification",),
    "bounded_context": ("host_qualification",),
    "source_citation_locator": ("professional_evidence",),
    "living_wiki": ("living_wiki",),
    "scale_performance": ("scale_10000",),
    "supported_platforms": ("candidate_platform",),
    "reproducible_supply_chain": ("supply_chain",),
    "codex": ("host_qualification",),
    "opencode": ("host_qualification",),
    "selective_forget": ("host_qualification",),
    "timeline": ("host_qualification",),
}
FORBIDDEN_KEYS = frozenset(
    {
        "comparative_reference",
        "machine_reference",
        "machine_reference_scorer",
        "qualification_holdout",
        "qualification_comparative_holdout",
        "final_blind",
        "final_blind_comparative_holdout",
        "agent_review_panel",
        "agent_consensus",
        "panel",
        "scorer_a",
        "scorer_b",
        "scorer_panel",
        "arbiter",
        "machine_reference_isolation",
        "comparative_incremental_benefit",
        "superiority",
        "sota",
        "auth",
        "authentication",
        "credential",
        "credentials",
        "secret",
        "secrets",
        "password",
        "api_key",
        "apikey",
        "private_key",
        "prompt",
        "transcript",
        "reasoning",
        "stdout",
        "stderr",
        "raw_output",
    }
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT = re.compile(r"^[0-9a-f]{40}$")
DRIVE = re.compile(r"^[A-Za-z]:")
ABSOLUTE_PRIVATE_PATH = re.compile(
    r"(?:^|[\s=:\"'])/(?:Users|home|private|tmp|var|root|etc|opt|Volumes|workspace)(?:/|$)"
    r"|(?:^|[\s=:\"'(])[A-Za-z]:[\\/]",
    re.IGNORECASE,
)
FORBIDDEN_FILENAME = re.compile(
    r"(?:^|[._-])(?:auth|credential|credentials|secret|secrets|password|passwd|"
    r"api[_-]?key|private[_-]?key|token|prompt|transcript|reasoning|stdout|stderr)"
    r"(?:$|[._-])",
    re.IGNORECASE,
)


class CommercialQualificationAssemblerError(ValueError):
    """Raised when current Gate v9 evidence is absent, unsafe, or inconsistent."""


CommercialQualificationV9Error = CommercialQualificationAssemblerError


class _TypedRecord:
    __slots__ = ("derived", "kind", "manifest", "path", "raw", "relative")

    def __init__(
        self,
        *,
        kind: str,
        manifest: Mapping[str, Any],
        path: Path,
        raw: bytes,
        relative: str,
        derived: Mapping[str, Any],
    ) -> None:
        self.kind = kind
        self.manifest = manifest
        self.path = path
        self.raw = raw
        self.relative = relative
        self.derived = derived


def _fail(message: str) -> None:
    raise CommercialQualificationAssemblerError(message)


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("strict JSON contains a duplicate key")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    _fail(f"strict JSON contains a non-finite constant: {value}")


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except CommercialQualificationAssemblerError:
        raise
    except (UnicodeError, TypeError, ValueError) as error:
        raise CommercialQualificationAssemblerError(
            f"{label} must be strict UTF-8 JSON"
        ) from error
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    return value


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise CommercialQualificationAssemblerError(
            "qualification value is not canonical JSON"
        ) from error


def record_sha256(value: Mapping[str, Any], *, field: str = "record_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _schema(filename: str, *, label: str) -> dict[str, Any]:
    path = CONTRACTS / filename
    raw = _regular_bytes(path, label=label)
    value = _strict_json(raw, label=label)
    try:
        Draft202012Validator.check_schema(value)
    except Exception as error:
        raise CommercialQualificationAssemblerError(
            f"{label} is not a valid JSON Schema"
        ) from error
    return value


def _validate_schema(value: Mapping[str, Any], schema: Mapping[str, Any], *, label: str) -> None:
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(dict(value)),
        key=lambda error: list(error.path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].path) or "$"
        _fail(f"{label} schema validation failed at {location}")


def _safe_relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or "\x00" in value:
        _fail(f"{label} is not a safe relative path")
    if "\\" in value or value.startswith("/") or DRIVE.match(value):
        _fail(f"{label} is not a safe relative path")
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        _fail(f"{label} is not a safe relative path")
    if PurePosixPath(value).as_posix() != value:
        _fail(f"{label} is not a normalized relative path")
    return value


def _root_directory(root: Path | str) -> Path:
    selected = Path(root).expanduser()
    if selected.is_symlink() or not selected.is_dir():
        _fail("bundle or output root must be a regular directory")
    try:
        resolved = selected.resolve(strict=True)
    except OSError as error:
        raise CommercialQualificationAssemblerError("root is unavailable") from error
    if resolved.is_symlink() or not resolved.is_dir():
        _fail("root must be a regular directory")
    return resolved


def _stat_signature(details: os.stat_result) -> tuple[Any, ...]:
    return (
        getattr(details, "st_ino", None),
        getattr(details, "st_size", None),
        getattr(details, "st_mode", None),
        getattr(details, "st_uid", None),
        getattr(details, "st_nlink", None),
        getattr(details, "st_mtime_ns", getattr(details, "st_mtime", None)),
        getattr(details, "st_ctime_ns", getattr(details, "st_ctime", None)),
    )


def _regular_bytes(path: Path, *, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise CommercialQualificationAssemblerError(f"{label} is unavailable") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail(f"{label} must be a regular non-symlink file")
    if before.st_size > MAX_FILE_BYTES:
        _fail(f"{label} exceeds its byte bound")
    try:
        raw = path.read_bytes()
        after = path.lstat()
    except OSError as error:
        raise CommercialQualificationAssemblerError(f"{label} could not be read") from error
    if stat.S_ISLNK(after.st_mode) or not stat.S_ISREG(after.st_mode):
        _fail(f"{label} changed to a non-regular file")
    if _stat_signature(before) != _stat_signature(after) or len(raw) != before.st_size:
        _fail(f"{label} changed while it was read")
    if len(raw) > MAX_FILE_BYTES:
        _fail(f"{label} exceeds its byte bound")
    return raw


def _reject_forbidden_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() in FORBIDDEN_KEYS:
                _fail("qualification evidence contains a comparative/reference field")
            _reject_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_keys(item)


def _safe_generated_value(value: Any, *, label: str, depth: int = 0) -> None:
    if depth > 32:
        _fail(f"{label} exceeds its depth bound")
    if isinstance(value, str):
        if value.startswith("/") or ABSOLUTE_PRIVATE_PATH.search(value):
            _fail(f"{label} contains an absolute private path")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(f"{label} contains a non-string field")
            _safe_generated_value(item, label=f"{label}.{key}", depth=depth + 1)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _safe_generated_value(item, label=f"{label}[{index}]", depth=depth + 1)
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not __import__("math").isfinite(value):
            _fail(f"{label} contains a non-finite number")
        return
    _fail(f"{label} contains an unsupported value")


def _scan_root(root: Path, *, excluded: str) -> dict[str, tuple[Path, bytes]]:
    files: dict[str, tuple[Path, bytes]] = {}
    total_bytes = 0
    try:
        entries = list(root.rglob("*"))
    except OSError as error:
        raise CommercialQualificationAssemblerError(
            "bundle root could not be enumerated"
        ) from error
    for path in entries:
        if path.is_symlink():
            _fail("bundle contains a symbolic link")
        try:
            is_dir = path.is_dir()
            is_file = path.is_file()
        except OSError as error:
            raise CommercialQualificationAssemblerError(
                "bundle entry could not be inspected"
            ) from error
        if is_dir:
            continue
        if not is_file:
            _fail("bundle contains a non-regular file")
        relative = _safe_relative(path.relative_to(root).as_posix(), label="bundle file path")
        if relative == excluded:
            continue
        if any(
            part == ".env"
            or part.startswith(".env.")
            or FORBIDDEN_FILENAME.search(part)
            for part in relative.split("/")
        ):
            _fail("bundle contains a forbidden Secret or raw-content filename")
        raw = _regular_bytes(path, label="bundle file")
        total_bytes += len(raw)
        if total_bytes > MAX_BUNDLE_BYTES:
            _fail("bundle exceeds its total byte bound")
        files[relative] = (path, raw)
    return files


def _manifest_location(root: Path, manifest: Path | str | None) -> tuple[str, Path]:
    selected = Path(manifest) if manifest is not None else Path(BUNDLE_MANIFEST_NAME)
    if selected.is_absolute():
        try:
            selected = selected.resolve(strict=False).relative_to(root)
        except ValueError as error:
            raise CommercialQualificationAssemblerError(
                "bundle manifest is outside the bundle root"
            ) from error
    relative = _safe_relative(selected.as_posix(), label="bundle manifest path")
    target = root / PurePosixPath(relative)
    if target.exists():
        if target.is_symlink():
            _fail("bundle manifest must be a regular file")
        try:
            target.resolve(strict=True).relative_to(root)
        except ValueError as error:
            raise CommercialQualificationAssemblerError(
                "bundle manifest escapes the bundle root"
            ) from error
    if target.is_symlink() or (target.exists() and not target.is_file()):
        _fail("bundle manifest must be a regular file")
    return relative, target


def _load_bundle(root: Path, *, manifest: Path | str | None) -> tuple[
    dict[str, Any], bytes, dict[str, tuple[Path, bytes]], dict[str, Mapping[str, Any]]
]:
    manifest_relative, manifest_path = _manifest_location(root, manifest)
    manifest_raw = _regular_bytes(manifest_path, label="bundle manifest")
    manifest_value = _strict_json(manifest_raw, label="bundle manifest")
    _reject_forbidden_keys(manifest_value)
    _validate_schema(
        manifest_value,
        _schema(
            "kernel-qualification-bundle-manifest.v1.schema.json",
            label="bundle manifest schema",
        ),
        label="bundle manifest",
    )
    if manifest_value["record_sha256"] != record_sha256(manifest_value):
        _fail("bundle manifest record digest differs")
    files = _scan_root(root, excluded=manifest_relative)
    references: dict[str, Mapping[str, Any]] = {}
    for reference in manifest_value["files"]:
        if not isinstance(reference, Mapping):
            _fail("bundle file reference is invalid")
        relative = _safe_relative(
            reference.get("relative_path"), label="bundle file reference path"
        )
        if relative in references or relative == manifest_relative or relative not in files:
            _fail("bundle file references are not closed")
        raw = files[relative][1]
        if reference["byte_size"] != len(raw) or reference["sha256"] != _sha256(raw):
            _fail("bundle file binding differs from reopened bytes")
        references[relative] = reference
    if set(references) != set(files):
        _fail("bundle contains an orphan or unreferenced file")
    return manifest_value, manifest_raw, files, references


def _binding_path_argument(root: Path, value: Path | str | None, *, expected: str) -> None:
    if value is None:
        return
    supplied = Path(value)
    if supplied.is_absolute():
        try:
            supplied = supplied.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise CommercialQualificationAssemblerError(
                "provided binding is outside the bundle root"
            ) from error
    if _safe_relative(supplied.as_posix(), label="provided binding path") != expected:
        _fail("provided binding path differs from the canonical bundle binding")


def _candidate_from_active(active: Mapping[str, Any]) -> dict[str, str]:
    if active.get("status") != "frozen_exact_candidate_machine_evaluation_pending":
        _fail("active qualification is not the frozen exact candidate")
    if active.get("candidate_version") != "0.13.0":
        _fail("active qualification candidate version differs")
    binding = active.get("candidate_binding")
    if not isinstance(binding, Mapping):
        _fail("active candidate binding is missing")
    result = {
        "commit": binding.get("source_commit"),
        "tree": binding.get("source_tree"),
        "lock_sha256": binding.get("lock_sha256"),
        "wheel_sha256": binding.get("wheel_sha256"),
        "sdist_sha256": binding.get("sdist_sha256"),
        "version": "0.13.0",
    }
    if not GIT.fullmatch(str(result["commit"])) or not GIT.fullmatch(str(result["tree"])):
        _fail("active candidate commit/tree is invalid")
    for field in ("lock_sha256", "wheel_sha256", "sdist_sha256"):
        if not isinstance(result[field], str) or SHA256.fullmatch(result[field]) is None:
            _fail(f"active candidate {field} is invalid")
    return result  # type: ignore[return-value]


def _run_ids(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != {
        "candidate_run_id",
        "evidence_run_id",
        "qualification_run_id",
    }:
        _fail("bundle run ids are not closed")
    result: dict[str, int] = {}
    for field in ("candidate_run_id", "evidence_run_id", "qualification_run_id"):
        item = value[field]
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            _fail(f"bundle {field} is invalid")
        result[field] = item
    if len(set(result.values())) != 3:
        _fail("bundle run ids must be distinct")
    return result


def _load_bound_documents(
    root: Path,
    files: Mapping[str, tuple[Path, bytes]],
    references: Mapping[str, Mapping[str, Any]],
    bindings: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes, dict[str, Any], bytes]:
    documents: list[tuple[str, str, str, str, str]] = [
        (
            ACTIVE_RELATIVE_PATH,
            "deeplaw.v013-active-qualification/v3",
            "v013-active-qualification.v3.schema.json",
            "active qualification",
            "active_qualification",
        ),
        (
            CLASSIFICATION_RELATIVE_PATH,
            CLASSIFICATION_SCHEMA_VERSION,
            "v013-release-gate-classification.v9.schema.json",
            "Gate classification",
            "gate_classification",
        ),
        (
            PROTOCOL_RELATIVE_PATH,
            "deeplaw.v013-qualification-protocol/v3",
            "v013-qualification-protocol.v3.schema.json",
            "qualification protocol",
            "qualification_protocol",
        ),
    ]
    loaded: list[tuple[dict[str, Any], bytes]] = []
    for relative, schema_version, schema_filename, label, binding_key in documents:
        if relative not in files or relative not in references:
            _fail(f"bundle is missing {label}")
        reference = references[relative]
        if (
            reference.get("artifact_kind") != "raw_source"
            or reference.get("relative_path") != relative
        ):
            _fail(f"{label} binding is not canonical")
        raw = files[relative][1]
        binding = bindings.get(binding_key)
        if (
            not isinstance(binding, Mapping)
            or binding.get("relative_path") != relative
            or binding.get("schema_version") != schema_version
            or binding.get("sha256") != _sha256(raw)
        ):
            _fail(f"{label} manifest binding differs from reopened bytes")
        value = _strict_json(raw, label=label)
        if value.get("schema_version") != schema_version:
            _fail(f"{label} schema version differs")
        _validate_schema(value, _schema(schema_filename, label=f"{label} schema"), label=label)
        loaded.append((value, raw))
    return (*loaded[0], *loaded[1], *loaded[2])


def _validate_document_cross_bindings(
    active: Mapping[str, Any],
    classification: Mapping[str, Any],
    protocol: Mapping[str, Any],
    *,
    classification_raw: bytes,
    protocol_raw: bytes,
    candidate: Mapping[str, str],
) -> None:
    if (
        classification.get("profile") != PROFILE
        or classification.get("classification_id") != CLASSIFICATION_ID
    ):
        _fail("Gate classification identity differs")
    if protocol.get("profile") != PROFILE:
        _fail("qualification protocol profile differs")
    class_hash = _sha256(classification_raw)
    protocol_hash = _sha256(protocol_raw)
    active_class = active.get("classification_binding")
    active_protocol = active.get("protocol_binding")
    protocol_class = protocol.get("classification_binding")
    if (
        not isinstance(active_class, Mapping)
        or not isinstance(active_protocol, Mapping)
        or not isinstance(protocol_class, Mapping)
    ):
        _fail("active/protocol classification bindings are missing")
    if (
        dict(active_class).get("relative_path") != CLASSIFICATION_RELATIVE_PATH
        or dict(active_class).get("schema_version") != CLASSIFICATION_SCHEMA_VERSION
        or dict(active_class).get("sha256") != class_hash
        or dict(active_protocol).get("relative_path") != PROTOCOL_RELATIVE_PATH
        or dict(active_protocol).get("schema_version") != "deeplaw.v013-qualification-protocol/v3"
        or dict(active_protocol).get("sha256") != protocol_hash
        or dict(protocol_class).get("relative_path") != CLASSIFICATION_RELATIVE_PATH
        or dict(protocol_class).get("schema_version") != CLASSIFICATION_SCHEMA_VERSION
        or dict(protocol_class).get("sha256") != class_hash
    ):
        _fail("active/protocol document hashes differ from reopened bytes")
    protocol_candidate = protocol.get("candidate_binding")
    if (
        not isinstance(protocol_candidate, Mapping)
        or protocol_candidate.get("lock_sha256") != candidate["lock_sha256"]
    ):
        _fail("qualification protocol candidate lock differs")


def _validate_host_receipts(
    files: Mapping[str, tuple[Path, bytes]],
    references: Mapping[str, Mapping[str, Any]],
) -> set[tuple[str, str, str]]:
    preflight_hosts: list[str] = []
    process_hosts: list[str] = []
    process_runs: list[tuple[str, str, str]] = []
    preflight_schema = _schema(
        "host-preflight-receipt.v1.schema.json", label="Host preflight schema"
    )
    process_schema = _schema(
        "host-process-receipt.v1.schema.json", label="Host process schema"
    )
    for relative, reference in references.items():
        artifact_kind = reference.get("artifact_kind")
        if artifact_kind not in {"host_preflight_receipt", "host_process_receipt"}:
            continue
        value = _strict_json(files[relative][1], label=f"Host receipt {relative}")
        if artifact_kind == "host_preflight_receipt":
            _validate_schema(value, preflight_schema, label="Host preflight receipt")
            host = value.get("host", {}).get("name")
            preflight_hosts.append(host)
        else:
            _validate_schema(value, process_schema, label="Host process receipt")
            if value.get("record_sha256") != record_sha256(value):
                _fail("Host process receipt record digest differs")
            host = value.get("host")
            task_case = value.get("task_case")
            run_id = value.get("run_id")
            process_hosts.append(host)
            process_runs.append((host, task_case, run_id))
    for label, hosts in (("preflight", preflight_hosts), ("process", process_hosts)):
        if len(hosts) != 6 or set(hosts) != {"codex", "opencode"}:
            _fail(f"Host {label} receipt inventory is incomplete")
        if any(hosts.count(host) != 3 for host in ("codex", "opencode")):
            _fail(f"Host {label} receipts must contain Codex x3 and OpenCode x3")
    if len(set(process_runs)) != len(process_runs):
        _fail("Host process receipts contain duplicate task runs")
    for host in ("codex", "opencode"):
        host_tasks = {
            task_case
            for item_host, task_case, _ in process_runs
            if item_host == host
        }
        if host_tasks != HOST_TASK_CASES:
            _fail(f"{host} Host process receipts do not cover the frozen task cases")
    return set(process_runs)


def _classification_inventory(
    classification: Mapping[str, Any],
) -> tuple[list[str], dict[str, Mapping[str, Any]], dict[str, str]]:
    categories = classification.get("categories")
    gates = classification.get("gates")
    if not isinstance(categories, list) or not isinstance(gates, list):
        _fail("Gate classification inventory is missing")
    category_ids: dict[str, list[str]] = {}
    for category in categories:
        if not isinstance(category, Mapping) or not isinstance(category.get("category_id"), str):
            _fail("Gate classification category is invalid")
        listed = category.get("gate_ids")
        if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
            _fail("Gate classification category Gate list is invalid")
        category_ids[category["category_id"]] = listed
    if set(category_ids) != {"core", "capability", "competitive_research"}:
        _fail("Gate classification categories are not closed")
    core_ids = category_ids["core"]
    if tuple(core_ids) != CORE_GATE_IDS:
        _fail("Gate classification Core inventory is not exactly 13 Gates")
    if (
        set(category_ids["capability"]) != set(CAPABILITY_GATE_IDS)
        or set(category_ids["competitive_research"]) != set(COMPETITIVE_GATE_IDS)
    ):
        _fail("Gate classification optional inventories are not closed")
    by_id: dict[str, Mapping[str, Any]] = {}
    for gate in gates:
        if not isinstance(gate, Mapping) or not isinstance(gate.get("gate_id"), str):
            _fail("Gate classification entry is invalid")
        gate_id = gate["gate_id"]
        if gate_id in by_id or gate_id not in ALL_GATE_IDS:
            _fail("Gate classification contains a duplicate or unknown Gate")
        by_id[gate_id] = gate
    if set(by_id) != ALL_GATE_IDS:
        _fail("Gate classification does not contain exactly 27 Gates")
    categories_by_gate = {
        **{gate_id: "Core" for gate_id in CORE_GATE_IDS},
        **{gate_id: "Capability" for gate_id in CAPABILITY_GATE_IDS},
        **{gate_id: "Competitive/Research Claim" for gate_id in COMPETITIVE_GATE_IDS},
    }
    for gate_id, category in categories_by_gate.items():
        definition = by_id[gate_id]
        if definition.get("category") != category:
            _fail(f"Gate {gate_id} category differs from its classification category")
        if category == "Core":
            if definition.get("artifact_kinds") != [EXPECTED_CORE_KINDS[gate_id]]:
                _fail(f"Gate {gate_id} typed mapping differs from the closed classification")
            if tuple(definition.get("required_corpus_roles", [])) != EXPECTED_CORE_ROLES[gate_id]:
                _fail(f"Gate {gate_id} corpus mapping differs from the closed classification")
            if (
                definition.get("accepted_input_schema_versions") != [TYPED_SCHEMA_VERSION]
                or definition.get("output_schema_versions") != [GATE_SCHEMA_VERSION]
            ):
                _fail(f"Gate {gate_id} schema bindings differ from current v9")
            if (
                definition.get("validator_id") != "deeplaw-typed-qualification-v3"
                or str(definition.get("validator_version")) != "3"
            ):
                _fail(f"Gate {gate_id} validator identity differs from current v3")
        else:
            if (
                definition.get("status") != "not_executed"
                or definition.get("passed") is not False
                or definition.get("claim") is not False
                or definition.get("artifact_kinds") != []
                or definition.get("accepted_input_schema_versions") != []
                or definition.get("required_corpus_roles") != []
            ):
                _fail(f"optional Gate {gate_id} is not a closed not-executed claim")
    return list(core_ids), by_id, categories_by_gate


def _threshold_binding(definition: Mapping[str, Any]) -> dict[str, Any]:
    gate_id = str(definition["gate_id"])
    body = {
        "classification_id": CLASSIFICATION_ID,
        "gate_id": gate_id,
        "thresholds": definition.get("thresholds"),
        "hard_zero_derivation": definition.get("hard_zero_derivation"),
    }
    return {
        "threshold_id": f"deeplaw-v013-thresholds-v9:{gate_id}",
        "threshold_sha256": _sha256(canonical_json(body)),
        "frozen": True,
    }


def _global_threshold_binding(definitions: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    body = {
        "classification_id": CLASSIFICATION_ID,
        "schema_version": CLASSIFICATION_SCHEMA_VERSION,
        "gates": [
            {
                "gate_id": gate_id,
                "thresholds": definitions[gate_id].get("thresholds"),
                "hard_zero_derivation": definitions[gate_id].get("hard_zero_derivation"),
            }
            for gate_id in CORE_GATE_IDS
        ],
    }
    return {
        "threshold_id": "deeplaw-v013-thresholds-v9",
        "threshold_sha256": _sha256(canonical_json(body)),
        "frozen": True,
    }


def _load_typed_records(
    root: Path,
    files: Mapping[str, tuple[Path, bytes]],
    references: Mapping[str, Mapping[str, Any]],
    *,
    candidate: Mapping[str, str],
    run_ids: Mapping[str, int],
    corpora: Mapping[str, Mapping[str, Any]],
) -> list[_TypedRecord]:
    typed: list[_TypedRecord] = []
    for relative in sorted(references):
        reference = references[relative]
        if reference.get("artifact_kind") != "typed_manifest":
            continue
        raw = files[relative][1]
        manifest = _strict_json(raw, label=f"typed evidence {relative}")
        _reject_forbidden_keys(manifest)
        _safe_generated_value(manifest, label=f"typed evidence {relative}")
        kind = manifest.get("kind")
        if (
            manifest.get("schema_version") != TYPED_SCHEMA_VERSION
            or kind not in TYPED_KINDS
            or reference.get("evidence_kind") != kind
        ):
            _fail("typed evidence file declaration differs from its exact bytes")
        if manifest.get("record_sha256") != record_sha256(manifest):
            _fail("typed evidence record digest differs")
        if manifest.get("candidate_binding") != {
            field: candidate[field]
            for field in ("commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256")
        }:
            _fail("typed evidence candidate binding differs from the exact candidate")
        corpus = manifest.get("corpus")
        run = manifest.get("run_binding")
        if not isinstance(corpus, Mapping) or not isinstance(run, Mapping):
            _fail("typed evidence corpus or run binding is missing")
        role = corpus.get("role")
        if (
            role not in CORPUS_ROLES
            or role not in corpora
            or corpus.get("sha256") != corpora[role].get("sha256")
        ):
            _fail("typed evidence corpus binding differs from the bundle corpus")
        workflow_id = (
            run_ids["candidate_run_id"]
            if kind in CANDIDATE_WORKFLOW_KINDS
            else run_ids["evidence_run_id"]
        )
        if run.get("workflow_run_id") != workflow_id:
            _fail("typed evidence workflow run differs from the bundle run ids")
        parser_kwargs: dict[str, Any] = {
            "root": files[relative][0].parent,
            "expected_candidate": manifest["candidate_binding"],
            "expected_workflow_run_id": workflow_id,
            "expected_corpus_sha256": corpus["sha256"],
        }
        if kind == "exact_wheel_execution":
            parser_kwargs["expected_candidate_run_id"] = run_ids["candidate_run_id"]
        try:
            derived = parse_typed_evidence(files[relative][0], **parser_kwargs)
        except Exception as error:
            raise CommercialQualificationAssemblerError(
                "public typed v3 parser rejected a bundle manifest"
            ) from error
        if not isinstance(derived, Mapping):
            _fail("typed parser did not return a derived object")
        derived_value = dict(derived)
        if (
            derived_value.get("schema_version") != TYPED_DERIVED_SCHEMA_VERSION
            or derived_value.get("kind") != kind
            or derived_value.get("status") not in {"passed", "failed"}
            or derived_value.get("evidence_record_sha256") != manifest.get("record_sha256")
        ):
            _fail("typed parser returned an unbound derived result")
        _reject_forbidden_keys(derived_value)
        metrics = derived_value.get("metrics")
        failures = derived_value.get("hard_failure_counts")
        if not isinstance(metrics, Mapping) or not isinstance(failures, Mapping):
            _fail("typed parser derived metrics or hard failures are missing")
        positive_failures = 0
        for failure_id, count in failures.items():
            if (
                not isinstance(failure_id, str)
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
            ):
                _fail("typed parser hard failure count is invalid")
            positive_failures += count
        expected_status = "passed" if positive_failures == 0 else "failed"
        if derived_value["status"] != expected_status:
            _fail("typed parser status does not derive from hard failures")
        typed.append(
            _TypedRecord(
                kind=kind,
                manifest=manifest,
                path=files[relative][0],
                raw=raw,
                relative=relative,
                derived=derived_value,
            )
        )
    counts = {kind: sum(record.kind == kind for record in typed) for kind in TYPED_KINDS}
    if counts != kernel_qualification_bundle_v1.TYPED_COUNTS:
        _fail("typed evidence inventory does not match exact Gate v9 counts")
    run_ids_seen = [record.manifest["run_binding"]["run_id"] for record in typed]
    if len(run_ids_seen) != len(set(run_ids_seen)):
        _fail("typed evidence run bindings contain duplicate run ids")
    return typed


def _input_reference(record: _TypedRecord, *, input_id: str) -> dict[str, Any]:
    return {
        "input_id": input_id,
        "relative_path": _safe_relative(record.relative, label="Gate input path"),
        "byte_size": len(record.raw),
        "file_sha256": _sha256(record.raw),
        "schema_version": TYPED_SCHEMA_VERSION,
        "record_sha256": record.manifest["record_sha256"],
        "artifact_kind": "typed-qualification-evidence",
        "evidence_kind": record.kind,
        "derived_record_sha256": _sha256(canonical_json(record.derived)),
    }


def _source_binding(path: Path, *, label: str) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(REPOSITORY).as_posix()
    except ValueError as error:
        raise CommercialQualificationAssemblerError(
            f"{label} is outside the repository"
        ) from error
    raw = _regular_bytes(resolved, label=label)
    return {
        "relative_path": _safe_relative(relative, label=f"{label} path"),
        "byte_size": len(raw),
        "file_sha256": _sha256(raw),
    }


def _execution(record: _TypedRecord, *, input_id: str) -> dict[str, Any]:
    run = record.manifest.get("run_binding")
    if not isinstance(run, Mapping):
        _fail("typed execution run binding is missing")
    return {
        "run_id": run.get("run_id"),
        "workflow_run_id": run.get("workflow_run_id"),
        "input_refs": [input_id],
        "evidence_kind": record.kind,
    }


def _corpora_for_gate(
    definition: Mapping[str, Any],
    corpora: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    roles = definition.get("required_corpus_roles")
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        _fail("Gate corpus roles are invalid")
    return [dict(corpora[role]) for role in roles if role in corpora]


def _gate_result(
    definition: Mapping[str, Any],
    records: Sequence[_TypedRecord],
    *,
    candidate: Mapping[str, str],
    run_ids: Mapping[str, int],
    classification_raw: bytes,
    protocol: Mapping[str, Any],
    protocol_raw: bytes,
    corpora: Mapping[str, Mapping[str, Any]],
    validator_source: Mapping[str, Any],
    validator_executable: Mapping[str, Any],
) -> dict[str, Any]:
    gate_id = str(definition["gate_id"])
    expected_kind = EXPECTED_CORE_KINDS[gate_id]
    selected = list(records)
    if not selected or any(record.kind != expected_kind for record in selected):
        _fail(f"Core Gate {gate_id} typed evidence mapping is incomplete")
    required_roles = set(definition.get("required_corpus_roles", []))
    if any(
        record.manifest.get("corpus", {}).get("role") not in required_roles
        for record in selected
    ):
        _fail(f"Core Gate {gate_id} typed corpus role differs from classification")
    input_records: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    hard_failures: list[dict[str, Any]] = []
    gate_run_ids: list[str] = []
    required_failure_ids = set(
        definition.get("hard_zero_derivation", {}).get("failure_ids", [])
    )
    thresholds = definition.get("thresholds")
    if not required_failure_ids or not isinstance(thresholds, list) or not thresholds:
        _fail(f"Core Gate {gate_id} classification derivation is incomplete")
    for index, record in enumerate(selected, start=1):
        input_id = f"input:{gate_id}:{index}"
        input_records.append(_input_reference(record, input_id=input_id))
        executions.append(_execution(record, input_id=input_id))
        run_id = record.manifest["run_binding"]["run_id"]
        if not isinstance(run_id, str) or not run_id:
            _fail(f"Core Gate {gate_id} has an invalid typed run id")
        gate_run_ids.append(run_id)
        if not required_failure_ids <= set(record.derived["hard_failure_counts"]):
            _fail(f"Core Gate {gate_id} typed hard-failure coverage is incomplete")
        for metric_name, observed in sorted(record.derived["metrics"].items()):
            metrics.append(
                {
                    "metric": f"{input_id}:{metric_name}",
                    "observed": observed,
                    "input_refs": [input_id],
                }
            )
        for failure_id, count in sorted(record.derived["hard_failure_counts"].items()):
            hard_failures.append(
                {
                    "failure_id": f"{input_id}:{failure_id}",
                    "count": count,
                    "maximum_allowed": 0,
                    "input_refs": [input_id],
                }
            )
    threshold_violation = False
    all_input_refs = [item["input_id"] for item in input_records]
    for threshold in thresholds:
        metric_name = threshold.get("metric") if isinstance(threshold, Mapping) else None
        if not isinstance(metric_name, str) or not metric_name:
            _fail(f"Core Gate {gate_id} threshold metric is invalid")
        values: list[float] = []
        for record in selected:
            observed = record.derived["metrics"].get(metric_name)
            if (
                isinstance(observed, bool)
                or not isinstance(observed, (int, float))
                or not math.isfinite(float(observed))
            ):
                _fail(f"Core Gate {gate_id} typed threshold metric is missing")
            values.append(float(observed))
        minimum = threshold.get("minimum")
        maximum = threshold.get("maximum")
        violations = sum(
            (minimum is not None and value < minimum)
            or (maximum is not None and value > maximum)
            for value in values
        )
        threshold_violation = threshold_violation or violations > 0
        aggregate = min(values) if minimum is not None else max(values)
        metrics.append(
            {
                "metric": metric_name,
                "observed": aggregate,
                "input_refs": all_input_refs,
            }
        )
        hard_failures.append(
            {
                "failure_id": f"threshold:{metric_name}",
                "count": violations,
                "maximum_allowed": 0,
                "input_refs": all_input_refs,
            }
        )
    if len(gate_run_ids) != len(set(gate_run_ids)):
        _fail(f"Core Gate {gate_id} contains duplicate typed run ids")
    candidate_binding = {
        "candidate_commit": candidate["commit"],
        "candidate_tree": candidate["tree"],
        "candidate_wheel_sha256": candidate["wheel_sha256"],
        "candidate_sdist_sha256": candidate["sdist_sha256"],
    }
    protocol_binding = {
        "protocol_id": protocol.get("protocol_id"),
        "protocol_sha256": _sha256(protocol_raw),
        "frozen": True,
    }
    result: dict[str, Any] = {
        "schema_version": GATE_SCHEMA_VERSION,
        "profile": PROFILE,
        "reference_provenance": REFERENCE_PROVENANCE,
        "human_authenticity": HUMAN_AUTHENTICITY,
        "qualification_run_id": run_ids["qualification_run_id"],
        "gate_id": gate_id,
        "category": "Core",
        "validator_id": definition["validator_id"],
        "validator_version": str(definition["validator_version"]),
        "validator_source": dict(validator_source),
        "validator_executable": dict(validator_executable),
        "classification_binding": {
            "classification_id": CLASSIFICATION_ID,
            "classification_schema_version": CLASSIFICATION_SCHEMA_VERSION,
            "classification_sha256": _sha256(classification_raw),
        },
        "candidate_binding": candidate_binding,
        "protocol_binding": protocol_binding,
        "threshold_binding": _threshold_binding(definition),
        "corpora": _corpora_for_gate(definition, corpora),
        "status": (
            "failed"
            if threshold_violation
            or any(record.derived["status"] == "failed" for record in selected)
            else "passed"
        ),
        "executions": executions,
        "run_ids": gate_run_ids,
        "metrics": metrics,
        "hard_failures": hard_failures,
        "inputs": input_records,
    }
    _safe_generated_value(result, label=f"Gate {gate_id}")
    result["result_sha256"] = record_sha256(result, field="result_sha256")
    _validate_schema(
        result,
        _schema("provenance-bound-gate-result.v5.schema.json", label="Gate result schema"),
        label=f"Gate {gate_id}",
    )
    return result


def _optional_gate_result(
    definition: Mapping[str, Any],
    *,
    candidate: Mapping[str, str],
    run_ids: Mapping[str, int],
    classification_raw: bytes,
    protocol: Mapping[str, Any],
    protocol_raw: bytes,
    validator_source: Mapping[str, Any],
    validator_executable: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": GATE_SCHEMA_VERSION,
        "profile": PROFILE,
        "reference_provenance": "not_applicable",
        "human_authenticity": HUMAN_AUTHENTICITY,
        "qualification_run_id": run_ids["qualification_run_id"],
        "gate_id": definition["gate_id"],
        "category": definition["category"],
        "validator_id": definition["validator_id"],
        "validator_version": str(definition["validator_version"]),
        "validator_source": dict(validator_source),
        "validator_executable": dict(validator_executable),
        "classification_binding": {
            "classification_id": CLASSIFICATION_ID,
            "classification_schema_version": CLASSIFICATION_SCHEMA_VERSION,
            "classification_sha256": _sha256(classification_raw),
        },
        "candidate_binding": {
            "candidate_commit": candidate["commit"],
            "candidate_tree": candidate["tree"],
            "candidate_wheel_sha256": candidate["wheel_sha256"],
            "candidate_sdist_sha256": candidate["sdist_sha256"],
        },
        "protocol_binding": {
            "protocol_id": protocol.get("protocol_id"),
            "protocol_sha256": _sha256(protocol_raw),
            "frozen": True,
        },
        "threshold_binding": _threshold_binding(definition),
        "corpora": [],
        "status": "not_executed",
        "executions": [],
        "run_ids": [],
        "metrics": [],
        "hard_failures": [],
        "inputs": [],
    }
    _safe_generated_value(result, label=f"Gate {definition['gate_id']}")
    result["result_sha256"] = record_sha256(result, field="result_sha256")
    _validate_schema(
        result,
        _schema("provenance-bound-gate-result.v5.schema.json", label="Gate result schema"),
        label=f"Gate {definition['gate_id']}",
    )
    return result


def _output_root(root: Path | str) -> Path:
    selected = Path(root).expanduser()
    if selected.exists() and selected.is_symlink():
        _fail("output root must not be a symbolic link")
    selected.mkdir(parents=True, exist_ok=True)
    return _root_directory(selected)


def _write_json(root: Path, relative: str, value: Mapping[str, Any]) -> tuple[bytes, int, str]:
    relative = _safe_relative(relative, label="output path")
    _safe_generated_value(value, label=relative)
    target = root / PurePosixPath(relative)
    current = root
    for component in relative.split("/"):
        current /= component
        if current.is_symlink():
            _fail("output path contains a symbolic link")
    if target.exists() and not target.is_file():
        _fail("output target is not a regular file")
    raw = canonical_json(value) + b"\n"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    except OSError as error:
        raise CommercialQualificationAssemblerError("output could not be written") from error
    reopened = _regular_bytes(target, label="output file")
    if reopened != raw:
        _fail("output bytes changed while writing")
    return raw, len(raw), _sha256(raw)


def _verify_output_gate(
    root: Path,
    relative: str,
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    binding = reference.get("result")
    if not isinstance(binding, Mapping):
        _fail("Gate output reference is incomplete")
    path = root / PurePosixPath(_safe_relative(relative, label="Gate output path"))
    raw = _regular_bytes(path, label="Gate output")
    value = _strict_json(raw, label="Gate output")
    _validate_schema(
        value,
        _schema("provenance-bound-gate-result.v5.schema.json", label="Gate result schema"),
        label="Gate output",
    )
    if (
        value.get("gate_id") != reference.get("gate_id")
        or value.get("category") != reference.get("category")
        or binding.get("byte_size") != len(raw)
        or binding.get("file_sha256") != _sha256(raw)
        or binding.get("record_sha256") != value.get("result_sha256")
        or value.get("result_sha256") != record_sha256(value, field="result_sha256")
    ):
        _fail("Gate output binding differs from reopened bytes")
    return value


def assemble_commercial_qualification(
    *,
    bundle_root: Path | str,
    output_root: Path | str | None = None,
    assets_root: Path | str | None = None,
    manifest: Path | str | None = None,
    active_qualification: Path | str | None = None,
    classification: Path | str | None = None,
    protocol: Path | str | None = None,
    expected_candidate: Mapping[str, Any] | None = None,
    expected_run_ids: Mapping[str, Any] | None = None,
    candidate_run_id: int | None = None,
    evidence_run_id: int | None = None,
    qualification_run_id: int | None = None,
) -> dict[str, Any]:
    """Validate one Kernel bundle and write Gate v5 plus report v6 evidence."""

    validator_kwargs: dict[str, Any] = {}
    if manifest is not None:
        validator_kwargs["manifest"] = manifest
    for name, value in (
        ("active_qualification", active_qualification),
        ("classification", classification),
        ("qualification_protocol", protocol),
    ):
        if value is not None:
            validator_kwargs[name] = value
    if expected_candidate is not None:
        validator_kwargs["expected_candidate"] = expected_candidate
    direct_run_ids = (candidate_run_id, evidence_run_id, qualification_run_id)
    if any(value is not None for value in direct_run_ids):
        if not all(value is not None for value in direct_run_ids):
            _fail("all three direct run ids are required together")
        supplied = {
            "candidate_run_id": candidate_run_id,
            "evidence_run_id": evidence_run_id,
            "qualification_run_id": qualification_run_id,
        }
        if expected_run_ids is not None and dict(expected_run_ids) != supplied:
            _fail("expected run ids differ from direct run ids")
        expected_run_ids = supplied
    if expected_run_ids is not None:
        validator_kwargs["expected_run_ids"] = expected_run_ids

    # This is intentionally the first input operation.  The bundle validator
    # admits the exact root before any assembler-owned document is reopened.
    try:
        admitted = kernel_qualification_bundle_v1.validate_bundle(
            bundle_root, **validator_kwargs
        )
    except (
        OSError,
        kernel_qualification_bundle_v1.KernelQualificationBundleError,
        ValueError,
    ) as error:
        raise CommercialQualificationAssemblerError(
            "Kernel qualification bundle v1 rejected"
        ) from error
    if not isinstance(admitted, Mapping) or admitted.get("status") != "passed":
        _fail("Kernel qualification bundle v1 did not derive a passed admission")

    source_root = _root_directory(bundle_root)
    _binding_path_argument(source_root, active_qualification, expected=ACTIVE_RELATIVE_PATH)
    _binding_path_argument(source_root, classification, expected=CLASSIFICATION_RELATIVE_PATH)
    _binding_path_argument(source_root, protocol, expected=PROTOCOL_RELATIVE_PATH)
    manifest_value, _manifest_raw, files, references = _load_bundle(
        source_root,
        manifest=manifest,
    )
    (
        active,
        _active_raw,
        classification_value,
        classification_raw,
        protocol_value,
        protocol_raw,
    ) = _load_bound_documents(
        source_root,
        files,
        references,
        manifest_value["bindings"],
    )
    process_runs = _validate_host_receipts(files, references)
    candidate = _candidate_from_active(active)
    _validate_document_cross_bindings(
        active,
        classification_value,
        protocol_value,
        classification_raw=classification_raw,
        protocol_raw=protocol_raw,
        candidate=candidate,
    )
    if manifest_value.get("candidate_binding") != candidate:
        _fail("bundle candidate binding differs from reopened active candidate")
    run_ids = _run_ids(manifest_value.get("run_ids"))
    if expected_run_ids is not None and run_ids != _run_ids(expected_run_ids):
        _fail("bundle run ids differ from expected run ids")
    if expected_candidate is not None:
        expected = dict(expected_candidate)
        if expected != candidate:
            _fail("bundle candidate differs from expected exact candidate")

    _core_ids, definitions, categories_by_gate = _classification_inventory(classification_value)
    corpus_by_role: dict[str, Mapping[str, Any]] = {}
    for corpus in manifest_value.get("corpora", []):
        if not isinstance(corpus, Mapping):
            _fail("bundle corpus is invalid")
        role = corpus.get("role")
        if role in corpus_by_role or role not in CORPUS_ROLES or corpus.get("frozen") is not True:
            _fail("bundle corpus inventory is not closed")
        corpus_by_role[role] = corpus
    if set(corpus_by_role) != CORPUS_ROLES:
        _fail("bundle corpus roles are incomplete")
    records = _load_typed_records(
        source_root,
        files,
        references,
        candidate=candidate,
        run_ids=run_ids,
        corpora=corpus_by_role,
    )
    by_kind: dict[str, list[_TypedRecord]] = defaultdict(list)
    for record in records:
        by_kind[record.kind].append(record)
    host_records = by_kind["host_event_sequence"]
    if len(host_records) != 6:
        _fail("Host typed evidence must contain exactly six records")
    hosts: dict[str, list[_TypedRecord]] = defaultdict(list)
    for record in host_records:
        metrics = record.derived["metrics"]
        host = metrics.get("host")
        task_case = metrics.get("task_case")
        run_id = metrics.get("run_id")
        manifest_run_id = record.manifest.get("run_binding", {}).get("run_id")
        if (
            host not in {"codex", "opencode"}
            or task_case not in HOST_TASK_CASES
            or not isinstance(run_id, str)
            or run_id != manifest_run_id
        ):
            _fail("Host typed evidence has an invalid derived identity")
        if (host, task_case, run_id) not in process_runs:
            _fail("Host typed evidence is not bound to a retained process receipt")
        hosts[host].append(record)
    if set(hosts) != {"codex", "opencode"} or any(len(hosts[host]) != 3 for host in hosts):
        _fail("Host typed evidence requires Codex x3 and OpenCode x3")
    for host in ("codex", "opencode"):
        if {record.derived["metrics"]["task_case"] for record in hosts[host]} != HOST_TASK_CASES:
            _fail(f"{host} Host typed evidence does not cover the frozen task cases")

    validator_source = _source_binding(
        REPOSITORY / "benchmarks/release/typed_qualification_evidence.py",
        label="typed v3 parser source",
    )
    validator_executable = _source_binding(
        Path(__file__),
        label="Gate v9 assembler source",
    )
    selected_output = output_root if output_root is not None else assets_root
    if selected_output is None:
        _fail("an output root is required")
    if (
        output_root is not None
        and assets_root is not None
        and Path(output_root).expanduser() != Path(assets_root).expanduser()
    ):
        _fail("output_root and assets_root differ")
    output = _output_root(selected_output)
    gate_values: dict[str, dict[str, Any]] = {}
    gate_references: list[dict[str, Any]] = []
    for gate_id in (*CORE_GATE_IDS, *CAPABILITY_GATE_IDS, *COMPETITIVE_GATE_IDS):
        definition = definitions[gate_id]
        if gate_id in EXPECTED_CORE_KINDS:
            selected = list(by_kind[EXPECTED_CORE_KINDS[gate_id]])
            if gate_id == "codex":
                selected = hosts["codex"]
            elif gate_id == "opencode":
                selected = hosts["opencode"]
            elif gate_id in {"secret_host_isolation", "timeline"}:
                selected = host_records
            elif gate_id == "selective_forget":
                selected = [
                    record
                    for record in host_records
                    if record.derived["metrics"]["task_case"] == "continuity"
                ]
            result = _gate_result(
                definition,
                selected,
                candidate=candidate,
                run_ids=run_ids,
                classification_raw=classification_raw,
                protocol=protocol_value,
                protocol_raw=protocol_raw,
                corpora=corpus_by_role,
                validator_source=validator_source,
                validator_executable=validator_executable,
            )
        else:
            result = _optional_gate_result(
                definition,
                candidate=candidate,
                run_ids=run_ids,
                classification_raw=classification_raw,
                protocol=protocol_value,
                protocol_raw=protocol_raw,
                validator_source=validator_source,
                validator_executable=validator_executable,
            )
        relative = f"evidence/gate-results/{gate_id}.json"
        raw, size, file_sha = _write_json(output, relative, result)
        if len(raw) != size or _sha256(raw) != file_sha:
            _fail(f"Gate {gate_id} output hash changed")
        gate_values[gate_id] = result
        gate_references.append(
            {
                "gate_id": gate_id,
                "category": categories_by_gate[gate_id],
                "result": {
                    "relative_path": relative,
                    "byte_size": size,
                    "file_sha256": file_sha,
                    "schema_version": GATE_SCHEMA_VERSION,
                    "record_sha256": result["result_sha256"],
                    "artifact_kind": "provenance-bound-gate-result",
                },
            }
        )
    core_passed = all(gate_values[gate_id]["status"] == "passed" for gate_id in CORE_GATE_IDS)
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "profile": PROFILE,
        "reference_provenance": REFERENCE_PROVENANCE,
        "human_authenticity": HUMAN_AUTHENTICITY,
        "report_kind": "v013_kernel_release_gate_collection",
        "report_id": f"kernel-v013-{run_ids['qualification_run_id']}",
        "qualification_run_id": run_ids["qualification_run_id"],
        "candidate_binding": {
            "candidate_commit": candidate["commit"],
            "candidate_tree": candidate["tree"],
            "candidate_wheel_sha256": candidate["wheel_sha256"],
            "candidate_sdist_sha256": candidate["sdist_sha256"],
        },
        "protocol_binding": {
            "protocol_id": protocol_value.get("protocol_id"),
            "protocol_sha256": _sha256(protocol_raw),
            "frozen": True,
        },
        "threshold_binding": _global_threshold_binding(definitions),
        "corpora": [dict(corpus_by_role[role]) for role in sorted(corpus_by_role)],
        "classification_binding": {
            "classification_id": CLASSIFICATION_ID,
            "classification_schema_version": CLASSIFICATION_SCHEMA_VERSION,
            "classification_sha256": _sha256(classification_raw),
        },
        "gate_results": gate_references,
        "kernel_release_core_passed": core_passed,
        "release_ready": core_passed,
        "human_attested_claim_eligible": False,
        "competitive_claim_eligible": False,
    }
    _safe_generated_value(report, label="commercial evidence report")
    report["report_sha256"] = record_sha256(report, field="report_sha256")
    _validate_schema(
        report,
        _schema("commercial-evidence-report.v6.schema.json", label="report schema"),
        label="commercial evidence report",
    )
    report_relative = "evidence/commercial-evidence-report.json"
    report_raw, report_size, report_file_sha = _write_json(output, report_relative, report)
    if len(report_raw) != report_size or _sha256(report_raw) != report_file_sha:
        _fail("report output hash changed")
    for reference in gate_references:
        _verify_output_gate(output, reference["result"]["relative_path"], reference)
    reopened_report = _strict_json(
        _regular_bytes(output / PurePosixPath(report_relative), label="commercial evidence report"),
        label="commercial evidence report",
    )
    if (
        reopened_report.get("report_sha256") != report["report_sha256"]
        or reopened_report.get("report_sha256")
        != record_sha256(reopened_report, field="report_sha256")
    ):
        _fail("report self-hash differs from reopened bytes")
    return {
        "schema_version": "deeplaw.commercial-qualification-derived/v9",
        "status": "passed" if core_passed else "failed",
        "kernel_release_core_passed": core_passed,
        "release_ready": core_passed,
        "qualification_run_id": run_ids["qualification_run_id"],
        "gate_count": len(gate_references),
        "core_gate_count": len(CORE_GATE_IDS),
        "optional_gate_count": len(CAPABILITY_GATE_IDS) + len(COMPETITIVE_GATE_IDS),
        "report_path": report_relative,
        "report_sha256": report["report_sha256"],
        "report_file_sha256": report_file_sha,
        "gate_statuses": {gate_id: gate_values[gate_id]["status"] for gate_id in gate_values},
    }


assemble_kernel_qualification_v9 = assemble_commercial_qualification
assemble_commercial_qualification_v9 = assemble_commercial_qualification


def _cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        result = assemble_commercial_qualification(
            bundle_root=args.bundle_root,
            output_root=args.output_root,
            manifest=args.manifest,
        )
    except (OSError, CommercialQualificationAssemblerError, ValueError) as error:
        print(f"commercial qualification v9 assembly failed: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(canonical_json(result).decode("utf-8") + "\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return _cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CommercialQualificationAssemblerError",
    "CommercialQualificationV9Error",
    "assemble_commercial_qualification",
    "assemble_commercial_qualification_v9",
    "assemble_kernel_qualification_v9",
    "canonical_json",
    "main",
    "record_sha256",
]
