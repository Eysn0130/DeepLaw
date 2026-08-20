"""Build and validate the exact-candidate Kernel Gate v9 evidence bundle.

The bundle is an inventory and integrity boundary, not a qualification report.
Every retained file is re-read from the supplied root and bound by byte size and
SHA-256.  Typed evidence is still validated by the public
``parse_typed_evidence`` seam; Host preflight and external process receipts are
retained as separate artifacts and never count as typed evidence passes.

This module never reads authentication material, ``.env`` files, model output,
or network state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release.typed_qualification_evidence import (
    SCHEMA_V3_VERSION as TYPED_SCHEMA_VERSION,
)
from benchmarks.release.typed_qualification_evidence import (
    TypedQualificationEvidenceError,
    parse_typed_evidence,
)

REPOSITORY = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPOSITORY / "contracts/kernel-qualification-bundle-manifest.v1.schema.json"
BUNDLE_MANIFEST_NAME = "bundle-manifest.json"
MANIFEST_FILENAME = BUNDLE_MANIFEST_NAME
ACTIVE_RELATIVE_PATH = "benchmarks/v013/active-qualification-v3.json"
CLASSIFICATION_RELATIVE_PATH = "benchmarks/release/v013-gate-classification-v9.json"
PROTOCOL_RELATIVE_PATH = "benchmarks/v013/qualification-protocol-v3.json"
ACTIVE_SCHEMA_FILENAME = "v013-active-qualification.v3.schema.json"
CLASSIFICATION_SCHEMA_FILENAME = "v013-release-gate-classification.v9.schema.json"
PROTOCOL_SCHEMA_FILENAME = "v013-qualification-protocol.v3.schema.json"
HOST_PREFLIGHT_SCHEMA_FILENAME = "host-preflight-receipt.v1.schema.json"
HOST_PROCESS_SCHEMA_FILENAME = "host-process-receipt.v1.schema.json"

TYPED_COUNTS: dict[str, int] = {
    "candidate_full_junit": 1,
    "candidate_platform_receipt": 1,
    "host_event_sequence": 6,
    "exact_wheel_execution": 1,
    "professional_evidence_rows": 1,
    "wiki_journey_rows": 1,
    "context_capsule_selection_usage": 1,
    "scale_report": 1,
    "retained_supply_chain": 1,
}
TYPED_KINDS = frozenset(TYPED_COUNTS)
CORPUS_ROLES = frozenset(
    {
        "candidate_full",
        "candidate_platform",
        "host_qualification",
        "professional_evidence",
        "living_wiki",
        "scale_10000",
        "supply_chain",
    }
)
CANDIDATE_WORKFLOW_KINDS = frozenset(
    {
        "candidate_full_junit",
        "candidate_platform_receipt",
        "retained_supply_chain",
        "scale_report",
    }
)
HOST_NAMES = frozenset({"codex", "opencode"})
HOST_PROCESS_SCHEMA_VERSION = "deeplaw.host-process-receipt/v1"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
REQUIRED_RUN_ID_FIELDS = (
    "candidate_run_id",
    "evidence_run_id",
    "qualification_run_id",
)
FORBIDDEN_FIELD_NAMES = frozenset(
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
    }
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_RE = re.compile(r"^[0-9a-f]{40}$")
DRIVE_RE = re.compile(r"^[A-Za-z]:")
HOST_TOKEN_RE = re.compile(r"(?<![a-z])(codex|opencode)(?![a-z])")
_FORBIDDEN_FILENAME = re.compile(
    r"(?:^|[._-])(?:auth|credential|credentials|secret|secrets|password|passwd|"
    r"api[_-]?key|private[_-]?key|token|prompt|transcript|reasoning|stdout|stderr)"
    r"(?:$|[._-])",
    re.IGNORECASE,
)
_BROKER_SOURCE_PATH = re.compile(
    r"^retained-broker-source/(?P<host>codex|opencode)\.launcher-source$"
)
_BROKER_SECRET_LITERAL = re.compile(
    rb"(?i)(?:api[_-]?key|access[_-]?token|authorization|bearer|password|secret)"
    rb"\s*[:=]\s*[\"']?[A-Za-z0-9+/_-]{20,}"
)


class KernelQualificationBundleError(ValueError):
    """Raised when an exact-candidate bundle is missing or unsafe."""


KernelQualificationBundleV1Error = KernelQualificationBundleError


def _fail(message: str) -> None:
    raise KernelQualificationBundleError(message)


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("strict JSON contains a duplicate key")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    _fail(f"strict JSON contains a non-finite constant: {value}")


def canonical_json(value: Any) -> bytes:
    """Return the bundle's canonical UTF-8 JSON bytes."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise KernelQualificationBundleError("value is not canonical JSON") from error


def record_sha256(value: Mapping[str, Any]) -> str:
    """Hash the canonical manifest body with ``record_sha256`` excluded."""

    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return hashlib.sha256(canonical_json(body)).hexdigest()


_record_digest = record_sha256


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except KernelQualificationBundleError:
        raise
    except (UnicodeError, TypeError, ValueError) as error:
        raise KernelQualificationBundleError(f"{label} must be strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    return value


def _schema(filename: str, *, label: str) -> dict[str, Any]:
    path = REPOSITORY / "contracts" / filename
    _reject_path_component(path, label=label)
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise KernelQualificationBundleError(f"{label} is unavailable") from error
    value = _strict_json(raw, label=label)
    try:
        Draft202012Validator.check_schema(value)
    except Exception as error:
        raise KernelQualificationBundleError(f"{label} is not a valid JSON Schema") from error
    return value


def _validate_schema(value: Mapping[str, Any], schema: Mapping[str, Any], *, label: str) -> None:
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(dict(value)),
        key=lambda error: list(error.path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].path) or "$"
        _fail(f"{label} schema validation failed at {location}")


def _reject_path_component(path: Path, *, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        _fail(f"{label} must be a regular non-symlink file")


def _safe_relative(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or "\x00" in value:
        _fail(f"{label} is not a safe relative path")
    if "\\" in value or value.startswith("/") or DRIVE_RE.match(value):
        _fail(f"{label} is not a safe relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail(f"{label} is not a safe relative path")
    if PurePosixPath(value).as_posix() != value:
        _fail(f"{label} is not a normalized relative path")
    return value


def _root_directory(root: Path | str) -> Path:
    selected = Path(root).expanduser()
    if selected.is_symlink() or not selected.is_dir():
        _fail("bundle root must be a regular non-symlink directory")
    try:
        resolved = selected.resolve(strict=True)
    except OSError as error:
        raise KernelQualificationBundleError("bundle root is unavailable") from error
    if resolved.is_symlink() or not resolved.is_dir():
        _fail("bundle root must be a regular directory")
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


def _read_regular(path: Path, *, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise KernelQualificationBundleError(f"{label} is unavailable") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail(f"{label} must be a regular non-symlink file")
    if before.st_size < 1 or before.st_size > MAX_FILE_BYTES:
        _fail(f"{label} exceeds the closed file-size bound")
    try:
        raw = path.read_bytes()
        after = path.lstat()
    except OSError as error:
        raise KernelQualificationBundleError(f"{label} could not be read") from error
    if stat.S_ISLNK(after.st_mode) or not stat.S_ISREG(after.st_mode):
        _fail(f"{label} changed to a non-regular file")
    if _stat_signature(before) != _stat_signature(after) or len(raw) != before.st_size:
        _fail(f"{label} changed while it was read")
    return raw


def _scan_root(root: Path, *, excluded: str) -> dict[str, tuple[Path, bytes]]:
    files: dict[str, tuple[Path, bytes]] = {}
    total_bytes = 0
    try:
        entries = list(root.rglob("*"))
    except OSError as error:
        raise KernelQualificationBundleError("bundle root could not be enumerated") from error
    for path in entries:
        if path.is_symlink():
            _fail("bundle contains a symbolic link")
        try:
            is_dir = path.is_dir()
            is_file = path.is_file()
        except OSError as error:
            raise KernelQualificationBundleError("bundle entry could not be inspected") from error
        if is_dir:
            continue
        if not is_file:
            _fail("bundle contains a non-regular file")
        relative = _safe_relative(
            path.relative_to(root).as_posix(),
            label="bundle file path",
        )
        if relative == excluded:
            continue
        if any(
            part == ".env"
            or part.startswith(".env.")
            or _FORBIDDEN_FILENAME.search(part)
            for part in relative.split("/")
        ):
            _fail("bundle contains a forbidden secret or raw-content filename")
        raw = _read_regular(path, label="bundle file")
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_BYTES:
            _fail("bundle exceeds the closed total-byte bound")
        files[relative] = (path, raw)
    if len(files) != len(set(files)):
        _fail("bundle contains duplicate file paths")
    return files


def _resolve_relative(root: Path, relative: Any, *, label: str) -> Path:
    name = _safe_relative(relative, label=label)
    selected = root / PurePosixPath(name)
    current = root
    for part in name.split("/"):
        current /= part
        try:
            if current.is_symlink():
                _fail(f"{label} contains a symbolic link")
        except OSError as error:
            raise KernelQualificationBundleError(f"{label} is unavailable") from error
    try:
        resolved = selected.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise KernelQualificationBundleError(f"{label} escapes the bundle root") from error
    if resolved.is_symlink() or not resolved.is_file():
        _fail(f"{label} must identify a regular file")
    return resolved


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _fail(f"{label} is not a lowercase SHA-256 digest")
    return value


def _git(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or GIT_RE.fullmatch(value) is None:
        _fail(f"{label} is not an exact Git commit/tree digest")
    return value


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(f"{label} must be a positive integer")
    return value


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    return value


def _reject_competitive_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in FORBIDDEN_FIELD_NAMES:
                _fail("bundle contains a comparative/reference field")
            _reject_competitive_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_competitive_fields(item)


def _manifest_path(root: Path, manifest: Path | str | None) -> tuple[str, Path]:
    selected = Path(manifest) if manifest is not None else Path(BUNDLE_MANIFEST_NAME)
    if selected.is_absolute():
        try:
            selected = selected.resolve(strict=False).relative_to(root)
        except ValueError as error:
            raise KernelQualificationBundleError("bundle manifest is outside the root") from error
    relative = _safe_relative(selected.as_posix(), label="bundle manifest path")
    target = (
        _resolve_relative(root, relative, label="bundle manifest")
        if (root / relative).exists()
        else root / relative
    )
    if target.is_symlink() or (target.exists() and not target.is_file()):
        _fail("bundle manifest must be a regular file")
    return relative, target


def _file_reference(
    relative: str,
    raw: bytes,
    *,
    artifact_kind: str,
    evidence_kind: str | None,
) -> dict[str, Any]:
    return {
        "relative_path": relative,
        "byte_size": len(raw),
        "sha256": _sha256(raw),
        "artifact_kind": artifact_kind,
        "evidence_kind": evidence_kind,
    }


def _json_at(relative: str, files: Mapping[str, tuple[Path, bytes]]) -> dict[str, Any] | None:
    selected = files.get(relative)
    if selected is None or not relative.casefold().endswith(".json"):
        return None
    try:
        return _strict_json(selected[1], label=relative)
    except KernelQualificationBundleError:
        return None


def _host_from_value(value: Mapping[str, Any], *, relative: str) -> str | None:
    if "host" in value:
        host = value["host"]
        if isinstance(host, str) and host in HOST_NAMES:
            return host
        if isinstance(host, Mapping) and host.get("name") in HOST_NAMES:
            return str(host["name"])
        return None
    host_identity = value.get("host_identity")
    if isinstance(host_identity, Mapping):
        name = host_identity.get("name")
        if name in HOST_NAMES:
            return str(name)
        return None
    match = HOST_TOKEN_RE.search(relative.casefold())
    return match.group(1) if match else None


def _classify_file(
    relative: str,
    raw: bytes,
    *,
    binding_paths: frozenset[str],
) -> tuple[str, str | None, Mapping[str, Any] | None]:
    if relative in binding_paths:
        return "raw_source", None, None
    value = _json_at(relative, {relative: (Path(relative), raw)})
    if value is not None and value.get("schema_version") == TYPED_SCHEMA_VERSION:
        kind = value.get("kind")
        if kind not in TYPED_KINDS:
            _fail("bundle contains unsupported or comparative typed evidence")
        _reject_competitive_fields(value)
        return "typed_manifest", kind, value
    if value is not None and value.get("schema_version") == "deeplaw.host-preflight-receipt/v1":
        return "host_preflight_receipt", None, value
    if value is not None and value.get("schema_version") == HOST_PROCESS_SCHEMA_VERSION:
        return "host_process_receipt", None, value
    if _BROKER_SOURCE_PATH.fullmatch(relative) is not None:
        return "broker_source", None, None
    lowered = relative.casefold()
    if "process" in lowered or (
        value is not None
        and any(key in value for key in ("process_receipt", "process_id"))
    ):
        return "host_process_receipt", None, value
    if "candidate" in lowered and "inventory" in lowered:
        return "candidate_inventory", None, value
    return "raw_source", None, value


def _validate_file_references(
    manifest: Mapping[str, Any],
    *,
    files: Mapping[str, tuple[Path, bytes]],
) -> dict[str, Mapping[str, Any]]:
    references = manifest["files"]
    by_path: dict[str, Mapping[str, Any]] = {}
    for reference in references:
        item = _mapping(reference, label="bundle file reference")
        relative = _safe_relative(item["relative_path"], label="bundle file reference path")
        if relative in by_path:
            _fail("bundle manifest contains duplicate file paths")
        if relative == BUNDLE_MANIFEST_NAME:
            _fail("bundle manifest cannot reference itself")
        if relative not in files:
            _fail("bundle manifest references a missing file")
        raw = files[relative][1]
        if item["byte_size"] != len(raw) or item["sha256"] != _sha256(raw):
            _fail("bundle file binding differs from retained bytes")
        by_path[relative] = item
    if set(by_path) != set(files):
        _fail("bundle contains an orphan or unreferenced file")
    return by_path


def _load_bound_json(
    root: Path,
    files: Mapping[str, tuple[Path, bytes]],
    reference: Mapping[str, Any],
    *,
    expected_relative: str,
    expected_schema_version: str,
    schema_filename: str,
    label: str,
) -> dict[str, Any]:
    if reference["relative_path"] != expected_relative:
        _fail(f"{label} binding path differs")
    if reference["schema_version"] != expected_schema_version:
        _fail(f"{label} binding schema version differs")
    relative = _safe_relative(reference["relative_path"], label=f"{label} path")
    path = _resolve_relative(root, relative, label=label)
    selected = files.get(relative)
    if selected is None or selected[0].resolve(strict=True) != path:
        _fail(f"{label} is not part of the bundle")
    raw = selected[1]
    if reference["sha256"] != _sha256(raw):
        _fail(f"{label} hash differs from exact bytes")
    value = _strict_json(raw, label=label)
    if value.get("schema_version") != expected_schema_version:
        _fail(f"{label} document schema version differs")
    _validate_schema(value, _schema(schema_filename, label=f"{label} schema"), label=label)
    return value


def _candidate_from_active(active: Mapping[str, Any]) -> dict[str, str]:
    if active.get("status") != "frozen_exact_candidate_machine_evaluation_pending":
        _fail("active qualification is not a frozen exact candidate")
    if active.get("candidate_version") != "0.13.0":
        _fail("active qualification candidate version is not 0.13.0")
    candidate = _mapping(active.get("candidate_binding"), label="active candidate binding")
    result = {
        "commit": _git(candidate.get("source_commit"), label="candidate commit"),
        "tree": _git(candidate.get("source_tree"), label="candidate tree"),
        "lock_sha256": _sha(candidate.get("lock_sha256"), label="candidate lock"),
        "wheel_sha256": _sha(candidate.get("wheel_sha256"), label="candidate wheel"),
        "sdist_sha256": _sha(candidate.get("sdist_sha256"), label="candidate sdist"),
        "version": "0.13.0",
    }
    for field in ("wheel_filename", "sdist_filename"):
        if not isinstance(candidate.get(field), str) or not candidate[field]:
            _fail(f"candidate {field} is missing")
    if candidate["sdist_filename"] != "deeplaw-0.13.0.tar.gz":
        _fail("candidate sdist filename is not the exact release target")
    return result


def _validate_bindings(
    root: Path,
    files: Mapping[str, tuple[Path, bytes]],
    manifest: Mapping[str, Any],
) -> dict[str, str]:
    bindings = _mapping(manifest["bindings"], label="bundle bindings")
    active = _load_bound_json(
        root,
        files,
        _mapping(bindings["active_qualification"], label="active binding"),
        expected_relative=ACTIVE_RELATIVE_PATH,
        expected_schema_version="deeplaw.v013-active-qualification/v3",
        schema_filename=ACTIVE_SCHEMA_FILENAME,
        label="active qualification",
    )
    classification = _load_bound_json(
        root,
        files,
        _mapping(bindings["gate_classification"], label="classification binding"),
        expected_relative=CLASSIFICATION_RELATIVE_PATH,
        expected_schema_version="deeplaw.v013-release-gate-classification/v9",
        schema_filename=CLASSIFICATION_SCHEMA_FILENAME,
        label="Gate classification",
    )
    protocol = _load_bound_json(
        root,
        files,
        _mapping(bindings["qualification_protocol"], label="protocol binding"),
        expected_relative=PROTOCOL_RELATIVE_PATH,
        expected_schema_version="deeplaw.v013-qualification-protocol/v3",
        schema_filename=PROTOCOL_SCHEMA_FILENAME,
        label="qualification protocol",
    )
    if classification.get("profile") != "kernel_release_core":
        _fail("Gate classification profile differs")
    if classification.get("classification_id") != "deeplaw-v013-commercial-gates-v9":
        _fail("Gate classification identity differs")
    if protocol.get("profile") != "kernel_release_core":
        _fail("qualification protocol profile differs")
    protocol_classification = _mapping(
        protocol.get("classification_binding"), label="protocol classification binding"
    )
    class_binding = _mapping(
        _mapping(active.get("classification_binding"), label="active classification binding"),
        label="active classification binding",
    )
    active_protocol = _mapping(active.get("protocol_binding"), label="active protocol binding")
    for source, label, expected_path in (
        (
            class_binding,
            "active classification binding",
            CLASSIFICATION_RELATIVE_PATH,
        ),
        (active_protocol, "active protocol binding", PROTOCOL_RELATIVE_PATH),
        (
            protocol_classification,
            "protocol classification binding",
            CLASSIFICATION_RELATIVE_PATH,
        ),
    ):
        if source.get("relative_path") != expected_path:
            _fail(f"{label} path is not canonical")
        expected_schema = (
            "deeplaw.v013-release-gate-classification/v9"
            if expected_path == CLASSIFICATION_RELATIVE_PATH
            else "deeplaw.v013-qualification-protocol/v3"
        )
        if source.get("schema_version") != expected_schema:
            _fail(f"{label} schema version is not canonical")
        _sha(source.get("sha256"), label=f"{label} hash")
    class_raw = files[CLASSIFICATION_RELATIVE_PATH][1]
    protocol_raw = files[PROTOCOL_RELATIVE_PATH][1]
    if class_binding["sha256"] != _sha256(class_raw):
        _fail("active classification binding hash differs")
    if active_protocol["sha256"] != _sha256(protocol_raw):
        _fail("active protocol binding hash differs")
    if protocol_classification["sha256"] != _sha256(class_raw):
        _fail("protocol classification binding hash differs")
    candidate = _candidate_from_active(active)
    protocol_candidate = _mapping(
        protocol.get("candidate_binding"), label="protocol candidate binding"
    )
    if protocol_candidate.get("lock_sha256") != candidate["lock_sha256"]:
        _fail("qualification protocol lock binding differs from exact candidate")
    return candidate


def _run_ids(value: Any) -> dict[str, int]:
    item = _mapping(value, label="bundle run ids")
    if set(item) != set(REQUIRED_RUN_ID_FIELDS):
        _fail("bundle run ids are not closed")
    result = {
        field: _positive_int(item[field], label=f"run ids.{field}")
        for field in REQUIRED_RUN_ID_FIELDS
    }
    if len(set(result.values())) != len(result):
        _fail("candidate, evidence, and qualification run ids must be distinct")
    return result


def _typed_source_paths(
    value: Mapping[str, Any],
    *,
    manifest_relative: str,
) -> set[str]:
    parent = PurePosixPath(manifest_relative).parent
    paths: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            if set(item) == {"relative_path", "byte_size", "sha256", "media_type"}:
                relative = _safe_relative(item["relative_path"], label="typed source path")
                joined = PurePosixPath(parent, relative).as_posix()
                _safe_relative(joined, label="typed source path")
                paths.add(joined)
            else:
                for nested in item.values():
                    walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(value.get("payload"))
    return paths


def _validate_preflight(
    value: Mapping[str, Any],
    *,
    raw: bytes,
) -> dict[str, str]:
    if value.get("schema_version") != "deeplaw.host-preflight-receipt/v1":
        _fail("Host preflight receipt schema version differs")
    schema = _schema(HOST_PREFLIGHT_SCHEMA_FILENAME, label="Host preflight receipt schema")
    _validate_schema(value, schema, label="Host preflight receipt")
    host = _host_from_value(value, relative="")
    if host is None:
        _fail("Host preflight receipt lacks a closed Host identity")
    expected_hosts = {
        "codex": (
            "codex-cli 0.148.0-alpha.15",
            "7645c3caf5607e4528eb3a15b12496c284c2a918939aed34e863c760c1b421e7",
        ),
        "opencode": (
            "1.18.16",
            "a41776bf64c75786d6baf531b840ffb873c090d7c44793ae2dd4b1896de56a1f",
        ),
    }
    expected_version, expected_binary = expected_hosts[host]
    host_value = _mapping(value["host"], label="Host preflight identity")
    broker = _mapping(value["broker_source"], label="Host preflight broker source")
    if (
        value.get("status") != "passed"
        or value.get("stage") != "complete"
        or value.get("reason_code") != "preflight_passed"
        or host_value.get("version") != expected_version
        or host_value.get("sha256") != expected_binary
        or broker.get("repository_external") is not True
        or broker.get("owner_only_mode") is not True
        or not isinstance(broker.get("bytes"), int)
        or broker["bytes"] < 1
        or not isinstance(broker.get("sha256"), str)
        or SHA256_RE.fullmatch(str(broker["sha256"])) is None
        or (
            broker.get("expected_sha256") is not None
            and broker.get("expected_sha256") != broker.get("sha256")
        )
    ):
        _fail("Host preflight receipt did not pass exact closed admission")
    if not raw:
        _fail("Host preflight receipt is empty")
    return {"host": host, "broker_sha256": str(broker["sha256"])}


def _validate_process_receipt(
    value: Mapping[str, Any] | None, *, relative: str
) -> dict[str, str]:
    if value is None or value.get("schema_version") != HOST_PROCESS_SCHEMA_VERSION:
        _fail("Host process receipt must use the sanitized v1 schema")
    _validate_schema(
        value,
        _schema(HOST_PROCESS_SCHEMA_FILENAME, label="Host process receipt schema"),
        label="Host process receipt",
    )
    if value.get("record_sha256") != record_sha256(value):
        _fail("Host process receipt record digest differs")
    host = _host_from_value(value or {}, relative=relative)
    if host is None:
        _fail("Host process receipt lacks a closed Host identity")
    path_match = HOST_TOKEN_RE.search(relative.casefold())
    if path_match is not None and path_match.group(1) != host:
        _fail("Host process receipt path and Host identity differ")
    _reject_competitive_fields(value)
    return {
        "host": host,
        "task_case": str(value["task_case"]),
        "run_id": str(value["run_id"]),
        "broker_sha256": str(value["broker_source"]["sha256"]),
    }


def _validate_broker_source(relative: str, raw: bytes) -> dict[str, str]:
    match = _BROKER_SOURCE_PATH.fullmatch(relative)
    if match is None or not 1 <= len(raw) <= 256 * 1024 or b"\x00" in raw:
        _fail("retained broker source path or size is invalid")
    try:
        raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise KernelQualificationBundleError(
            "retained broker source is not UTF-8 source text"
        ) from error
    if _BROKER_SECRET_LITERAL.search(raw):
        _fail("retained broker source contains a credential literal")
    return {"host": match.group("host"), "sha256": _sha256(raw)}


def _validate_inventory(
    root: Path,
    files: Mapping[str, tuple[Path, bytes]],
    references: Mapping[str, Mapping[str, Any]],
    *,
    manifest: Mapping[str, Any],
    candidate: Mapping[str, str],
    run_ids: Mapping[str, int],
) -> dict[str, Any]:
    typed_paths: dict[str, list[str]] = defaultdict(list)
    typed_values: dict[str, Mapping[str, Any]] = {}
    preflight_receipts: list[dict[str, str]] = []
    process_receipts: list[dict[str, str]] = []
    process_tasks: dict[str, list[str]] = defaultdict(list)
    broker_sources: list[dict[str, str]] = []
    broker_source_paths: set[str] = set()
    referenced_source_paths: set[str] = set()
    receipt_paths: set[str] = set()
    for relative, reference in references.items():
        raw = files[relative][1]
        artifact_kind = reference["artifact_kind"]
        evidence_kind = reference["evidence_kind"]
        parsed = _json_at(relative, files)
        if artifact_kind == "typed_manifest":
            if evidence_kind not in TYPED_KINDS or parsed is None:
                _fail("typed manifest file declaration is invalid")
            if (
                parsed.get("schema_version") != TYPED_SCHEMA_VERSION
                or parsed.get("kind") != evidence_kind
            ):
                _fail("typed manifest file declaration differs from its bytes")
            _reject_competitive_fields(parsed)
            typed_paths[evidence_kind].append(relative)
            typed_values[relative] = parsed
            referenced_source_paths.update(
                _typed_source_paths(parsed, manifest_relative=relative)
            )
        elif artifact_kind == "host_preflight_receipt":
            if evidence_kind is not None or parsed is None:
                _fail("Host preflight receipt declaration is invalid")
            preflight_receipts.append(_validate_preflight(parsed, raw=raw))
            receipt_paths.add(relative)
        elif artifact_kind == "host_process_receipt":
            if evidence_kind is not None or parsed is None:
                _fail("Host process receipt cannot be typed evidence")
            process_receipt = _validate_process_receipt(parsed, relative=relative)
            process_receipts.append(process_receipt)
            process_tasks[process_receipt["host"]].append(process_receipt["task_case"])
            receipt_paths.add(relative)
        elif artifact_kind == "broker_source":
            if evidence_kind is not None or parsed is not None:
                _fail("retained broker source declaration is invalid")
            broker_sources.append(_validate_broker_source(relative, raw))
            broker_source_paths.add(relative)
        elif artifact_kind in {"raw_source", "candidate_inventory"}:
            if evidence_kind is not None:
                _fail("raw bundle artifact cannot declare a typed evidence kind")
        else:
            _fail("bundle contains an unsupported artifact kind")
    if {kind: len(paths) for kind, paths in typed_paths.items()} != TYPED_COUNTS:
        _fail("bundle typed evidence inventory does not match the exact Gate v9 counts")
    known_paths = {
        ACTIVE_RELATIVE_PATH,
        CLASSIFICATION_RELATIVE_PATH,
        PROTOCOL_RELATIVE_PATH,
        *receipt_paths,
        *broker_source_paths,
        *(path for paths in typed_paths.values() for path in paths),
        *referenced_source_paths,
    }
    for relative, reference in references.items():
        if relative in known_paths:
            continue
        if reference["artifact_kind"] != "candidate_inventory":
            _fail("bundle contains an unreferenced raw file")
    preflight_hosts = [item["host"] for item in preflight_receipts]
    process_hosts = [item["host"] for item in process_receipts]
    if len(preflight_receipts) != 6 or len(process_receipts) != 6:
        _fail("bundle requires exactly six Host preflight and six process receipts")
    for label, hosts in (("preflight", preflight_hosts), ("process", process_hosts)):
        if set(hosts) != HOST_NAMES or any(hosts.count(host) != 3 for host in HOST_NAMES):
            _fail(f"Host {label} receipts must contain Codex x3 and OpenCode x3")
    expected_host_tasks = ["continuity", "living_wiki", "professional_evidence"]
    if any(sorted(process_tasks[host]) != expected_host_tasks for host in HOST_NAMES):
        _fail("Host process receipts must cover the three frozen task cases per Host")
    broker_by_host = {item["host"]: item["sha256"] for item in broker_sources}
    if len(broker_sources) != 2 or set(broker_by_host) != HOST_NAMES:
        _fail("bundle must retain one exact source file for each Host broker")
    for receipt in (*preflight_receipts, *process_receipts):
        if receipt["broker_sha256"] != broker_by_host[receipt["host"]]:
            _fail("Host receipt broker hash differs from retained broker source")
    corpora = manifest["corpora"]
    corpus_by_role: dict[str, Mapping[str, Any]] = {}
    for corpus in corpora:
        item = _mapping(corpus, label="bundle corpus")
        role = item["role"]
        if role in corpus_by_role:
            _fail("bundle corpus roles must be unique")
        if role not in CORPUS_ROLES or item["frozen"] is not True:
            _fail("bundle corpus role is not a frozen Kernel role")
        _sha(item["sha256"], label=f"corpus {role} hash")
        corpus_by_role[role] = item
    if set(corpus_by_role) != CORPUS_ROLES:
        _fail("bundle must contain each Kernel corpus role exactly once")
    expected_candidate = {
        field: candidate[field]
        for field in ("commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256")
    }
    parsed_results: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for kind, paths in typed_paths.items():
        for relative in paths:
            value = typed_values[relative]
            if value.get("candidate_binding") != expected_candidate:
                _fail("typed evidence candidate binding differs from the exact candidate")
            corpus = _mapping(value.get("corpus"), label="typed evidence corpus")
            role = corpus.get("role")
            if role not in CORPUS_ROLES or role not in corpus_by_role:
                _fail("typed evidence uses a non-Kernel corpus role")
            if corpus.get("sha256") != corpus_by_role[role]["sha256"]:
                _fail("typed evidence corpus hash differs from the bundle corpus")
            run = _mapping(value.get("run_binding"), label="typed evidence run binding")
            workflow_id = run.get("workflow_run_id")
            expected_workflow = (
                run_ids["candidate_run_id"]
                if kind in CANDIDATE_WORKFLOW_KINDS
                else run_ids["evidence_run_id"]
            )
            if workflow_id != expected_workflow:
                _fail("typed evidence workflow run differs from the bundle run ids")
            parser_kwargs: dict[str, Any] = {
                "root": files[relative][0].parent,
                "expected_candidate": expected_candidate,
                "expected_workflow_run_id": expected_workflow,
                "expected_corpus_sha256": corpus["sha256"],
            }
            if kind == "exact_wheel_execution":
                parser_kwargs["expected_candidate_run_id"] = run_ids["candidate_run_id"]
            try:
                derived = parse_typed_evidence(files[relative][0], **parser_kwargs)
            except (OSError, TypedQualificationEvidenceError, ValueError) as error:
                raise KernelQualificationBundleError(
                    "public typed evidence parser rejected a bundle manifest"
                ) from error
            if (
                not isinstance(derived, Mapping)
                or derived.get("kind") != kind
                or derived.get("status") != "passed"
                or derived.get("evidence_record_sha256") != value.get("record_sha256")
            ):
                _fail("typed evidence did not derive a passed self-bound result")
            parsed_results[kind].append(dict(derived))
    # Host identities are taken from the public parser's derived metrics.  The
    # parser order is stable because ``paths`` is built from sorted references.
    host_derived = parsed_results["host_event_sequence"]
    host_names = [item.get("metrics", {}).get("host") for item in host_derived]
    if set(host_names) != HOST_NAMES or any(host_names.count(host) != 3 for host in HOST_NAMES):
        _fail("typed Host evidence must contain Codex x3 and OpenCode x3")
    typed_host_runs = {
        (
            str(item.get("metrics", {}).get("host")),
            str(item.get("metrics", {}).get("task_case")),
            str(item.get("metrics", {}).get("run_id")),
        )
        for item in host_derived
    }
    retained_process_runs = {
        (item["host"], item["task_case"], item["run_id"])
        for item in process_receipts
    }
    if len(typed_host_runs) != 6 or typed_host_runs != retained_process_runs:
        _fail("Host process receipts do not bind the six typed Host task runs")
    typed_counts = {kind: len(paths) for kind, paths in typed_paths.items()}
    return {
        "typed_counts": typed_counts,
        "typed_evidence_kind_counts": typed_counts,
        "preflight_receipt_count": len(preflight_hosts),
        "process_receipt_count": len(process_hosts),
        "broker_source_count": len(broker_sources),
        "corpus_roles": sorted(corpus_by_role),
        "candidate": dict(candidate),
        "run_ids": dict(run_ids),
    }


def _build_corpora(
    files: Mapping[str, tuple[Path, bytes]],
    references: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_role: dict[str, str] = {}
    for reference in references:
        if reference["artifact_kind"] != "typed_manifest":
            continue
        value = _json_at(reference["relative_path"], files)
        if value is None:
            _fail("typed evidence manifest is not strict JSON")
        corpus = _mapping(value.get("corpus"), label="typed evidence corpus")
        role = corpus.get("role")
        digest = _sha(corpus.get("sha256"), label="typed evidence corpus hash")
        if role not in CORPUS_ROLES:
            _fail("typed evidence uses a non-Kernel corpus role")
        if role in by_role and by_role[role] != digest:
            _fail("typed evidence corpus hashes disagree for one role")
        by_role[role] = digest
    if set(by_role) != CORPUS_ROLES:
        _fail("typed evidence does not cover every Kernel corpus role")
    candidate_roles = {"candidate_full", "candidate_platform", "supply_chain"}
    return [
        {
            "role": role,
            "source": "candidate_artifact" if role in candidate_roles else "repository_external",
            "sha256": by_role[role],
            "frozen": True,
        }
        for role in sorted(CORPUS_ROLES)
    ]


def _classify_files(
    files: Mapping[str, tuple[Path, bytes]],
    *,
    binding_paths: frozenset[str],
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str | None, Mapping[str, Any] | None]]]:
    references: list[dict[str, Any]] = []
    classified: dict[str, tuple[str, str | None, Mapping[str, Any] | None]] = {}
    for relative in sorted(files):
        raw = files[relative][1]
        artifact_kind, evidence_kind, value = _classify_file(
            relative,
            raw,
            binding_paths=binding_paths,
        )
        classified[relative] = (artifact_kind, evidence_kind, value)
        references.append(
            _file_reference(
                relative,
                raw,
                artifact_kind=artifact_kind,
                evidence_kind=evidence_kind,
            )
        )
    return references, classified


def _binding_reference(
    relative: str,
    *,
    raw: bytes,
    schema_version: str,
) -> dict[str, Any]:
    return {
        "relative_path": relative,
        "sha256": _sha256(raw),
        "schema_version": schema_version,
    }


def _candidate_input(value: Mapping[str, Any] | None, *, candidate: Mapping[str, str]) -> None:
    if value is None:
        return
    expected = {
        "commit": candidate["commit"],
        "tree": candidate["tree"],
        "lock_sha256": candidate["lock_sha256"],
        "wheel_sha256": candidate["wheel_sha256"],
        "sdist_sha256": candidate["sdist_sha256"],
        "version": candidate["version"],
    }
    if dict(value) != expected:
        _fail("explicit candidate binding differs from the frozen active candidate")


def _write_manifest(path: Path, value: Mapping[str, Any]) -> None:
    if (path.exists() or path.is_symlink()) and (path.is_symlink() or not path.is_file()):
        _fail("bundle manifest target is not a regular file")
    try:
        path.write_bytes(canonical_json(value) + b"\n")
    except OSError as error:
        raise KernelQualificationBundleError("bundle manifest could not be written") from error


def validate_bundle(
    root: Path | str,
    *,
    manifest: Path | str | None = None,
    active_qualification: Path | str | None = None,
    classification: Path | str | None = None,
    qualification_protocol: Path | str | None = None,
    expected_candidate: Mapping[str, Any] | None = None,
    expected_run_ids: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-read and validate one exact-candidate Kernel evidence bundle."""

    selected_root = _root_directory(root)
    manifest_relative, manifest_path = _manifest_path(selected_root, manifest)
    if not manifest_path.is_file() or manifest_path.is_symlink():
        _fail("bundle manifest is unavailable")
    manifest_value = _strict_json(
        _read_regular(manifest_path, label="bundle manifest"),
        label="bundle manifest",
    )
    _reject_competitive_fields(manifest_value)
    bundle_schema = _schema(
        "kernel-qualification-bundle-manifest.v1.schema.json",
        label="bundle manifest schema",
    )
    _validate_schema(manifest_value, bundle_schema, label="bundle manifest")
    if manifest_value["record_sha256"] != record_sha256(manifest_value):
        _fail("bundle manifest record digest differs")
    files = _scan_root(selected_root, excluded=manifest_relative)
    references = _validate_file_references(manifest_value, files=files)
    expected_paths = {
        ACTIVE_RELATIVE_PATH,
        CLASSIFICATION_RELATIVE_PATH,
        PROTOCOL_RELATIVE_PATH,
    }
    for provided, expected in (
        (active_qualification, ACTIVE_RELATIVE_PATH),
        (classification, CLASSIFICATION_RELATIVE_PATH),
        (qualification_protocol, PROTOCOL_RELATIVE_PATH),
    ):
        if provided is None:
            continue
        supplied = _resolve_relative(selected_root, _safe_relative(
            Path(provided).resolve(strict=True).relative_to(selected_root).as_posix()
            if Path(provided).is_absolute()
            else Path(provided).as_posix(),
            label="provided binding path",
        ), label="provided binding")
        if supplied.relative_to(selected_root).as_posix() != expected:
            _fail("provided binding path differs from canonical bundle binding")
    for path in expected_paths:
        if path not in files:
            _fail("bundle is missing a required binding file")
    candidate = _validate_bindings(selected_root, files, manifest_value)
    if manifest_value["candidate_binding"] != candidate:
        _fail("bundle candidate binding differs from the frozen active candidate")
    _candidate_input(expected_candidate, candidate=candidate)
    run_ids = _run_ids(manifest_value["run_ids"])
    if expected_run_ids is not None and run_ids != _run_ids(expected_run_ids):
        _fail("bundle run ids differ from the expected run ids")
    derived = _validate_inventory(
        selected_root,
        files,
        references,
        manifest=manifest_value,
        candidate=candidate,
        run_ids=run_ids,
    )
    return {
        "schema_version": "deeplaw.kernel-qualification-bundle-derived/v1",
        "status": "passed",
        "manifest_record_sha256": manifest_value["record_sha256"],
        "manifest_relative_path": manifest_relative,
        **derived,
    }


def build_bundle(
    root: Path | str,
    *,
    run_ids: Mapping[str, Any],
    manifest: Path | str | None = None,
    active_qualification: Path | str | None = None,
    classification: Path | str | None = None,
    qualification_protocol: Path | str | None = None,
    expected_candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build, persist, and immediately validate a bundle manifest."""

    selected_root = _root_directory(root)
    manifest_relative, manifest_path = _manifest_path(selected_root, manifest)
    active_relative = ACTIVE_RELATIVE_PATH
    classification_relative = CLASSIFICATION_RELATIVE_PATH
    protocol_relative = PROTOCOL_RELATIVE_PATH
    for provided, expected in (
        (active_qualification, active_relative),
        (classification, classification_relative),
        (qualification_protocol, protocol_relative),
    ):
        if provided is not None:
            selected = Path(provided)
            if selected.is_absolute():
                try:
                    selected = selected.resolve(strict=True).relative_to(selected_root)
                except (OSError, ValueError) as error:
                    raise KernelQualificationBundleError(
                        "provided binding is outside the bundle root"
                    ) from error
            if _safe_relative(selected.as_posix(), label="provided binding path") != expected:
                _fail("provided binding path differs from canonical bundle binding")
    files = _scan_root(selected_root, excluded=manifest_relative)
    binding_paths = frozenset(
        {active_relative, classification_relative, protocol_relative}
    )
    references, _classified = _classify_files(files, binding_paths=binding_paths)
    for expected in binding_paths:
        if expected not in files:
            _fail("bundle is missing a required binding file")
    candidate = _validate_bindings(
        selected_root,
        files,
        {
            "bindings": {
                "active_qualification": _binding_reference(
                    active_relative,
                    raw=files[active_relative][1],
                    schema_version="deeplaw.v013-active-qualification/v3",
                ),
                "gate_classification": _binding_reference(
                    classification_relative,
                    raw=files[classification_relative][1],
                    schema_version="deeplaw.v013-release-gate-classification/v9",
                ),
                "qualification_protocol": _binding_reference(
                    protocol_relative,
                    raw=files[protocol_relative][1],
                    schema_version="deeplaw.v013-qualification-protocol/v3",
                ),
            }
        },
    )
    _candidate_input(expected_candidate, candidate=candidate)
    normalized_run_ids = _run_ids(run_ids)
    corpora = _build_corpora(files, references)
    provisional: dict[str, Any] = {
        "schema_version": "deeplaw.kernel-qualification-bundle-manifest/v1",
        "profile": "kernel_release_core",
        "reference_provenance": "deterministic_expected_evidence",
        "human_authenticity": "not_claimed",
        "candidate_binding": candidate,
        "run_ids": normalized_run_ids,
        "bindings": {
            "active_qualification": _binding_reference(
                active_relative,
                raw=files[active_relative][1],
                schema_version="deeplaw.v013-active-qualification/v3",
            ),
            "gate_classification": _binding_reference(
                classification_relative,
                raw=files[classification_relative][1],
                schema_version="deeplaw.v013-release-gate-classification/v9",
            ),
            "qualification_protocol": _binding_reference(
                protocol_relative,
                raw=files[protocol_relative][1],
                schema_version="deeplaw.v013-qualification-protocol/v3",
            ),
        },
        "corpora": corpora,
        "files": references,
        "record_sha256": "0" * 64,
    }
    provisional["record_sha256"] = record_sha256(provisional)
    _write_manifest(manifest_path, provisional)
    validate_bundle(
        selected_root,
        manifest=manifest_relative,
        expected_candidate=expected_candidate,
        expected_run_ids=normalized_run_ids,
    )
    return provisional


build_kernel_qualification_bundle = build_bundle
validate_kernel_qualification_bundle = validate_bundle


def _load_cli_json(path: Path, *, label: str) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        _fail(f"{label} must be a regular non-symlink file")
    if _FORBIDDEN_FILENAME.search(path.name) or path.name == ".env":
        _fail(f"{label} filename is forbidden")
    return _strict_json(_read_regular(path, label=label), label=label)


def _cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build or validate the Kernel Gate v9 evidence bundle."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "validate"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--root", type=Path, required=True)
        sub.add_argument("--manifest", type=Path, default=None)
        sub.add_argument("--active-qualification", type=Path, default=None)
        sub.add_argument("--classification", type=Path, default=None)
        sub.add_argument("--qualification-protocol", type=Path, default=None)
        sub.add_argument("--candidate", type=Path, default=None)
        sub.add_argument("--run-ids", type=Path, default=None)
        sub.add_argument("--candidate-run-id", type=int, default=None)
        sub.add_argument("--evidence-run-id", type=int, default=None)
        sub.add_argument("--qualification-run-id", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        candidate = (
            _load_cli_json(args.candidate, label="candidate descriptor")
            if args.candidate is not None
            else None
        )
        supplied_run_ids = None
        if args.run_ids is not None:
            supplied_run_ids = _load_cli_json(args.run_ids, label="run id descriptor")
        elif any(
            item is not None
            for item in (args.candidate_run_id, args.evidence_run_id, args.qualification_run_id)
        ):
            if not all(
                item is not None
                for item in (args.candidate_run_id, args.evidence_run_id, args.qualification_run_id)
            ):
                _fail("all three run ids are required together")
            supplied_run_ids = {
                "candidate_run_id": args.candidate_run_id,
                "evidence_run_id": args.evidence_run_id,
                "qualification_run_id": args.qualification_run_id,
            }
        if args.command == "build":
            if supplied_run_ids is None:
                _fail("build requires run ids")
            result = build_bundle(
                args.root,
                manifest=args.manifest,
                active_qualification=args.active_qualification,
                classification=args.classification,
                qualification_protocol=args.qualification_protocol,
                expected_candidate=candidate,
                run_ids=supplied_run_ids,
            )
        else:
            result = validate_bundle(
                args.root,
                manifest=args.manifest,
                active_qualification=args.active_qualification,
                classification=args.classification,
                qualification_protocol=args.qualification_protocol,
                expected_candidate=candidate,
                expected_run_ids=supplied_run_ids,
            )
        sys.stdout.write(canonical_json(result).decode("utf-8") + "\n")
        return 0
    except (OSError, KernelQualificationBundleError, ValueError) as error:
        print(f"kernel qualification bundle rejected: {error}", file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    return _cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BUNDLE_MANIFEST_NAME",
    "CORPUS_ROLES",
    "MANIFEST_FILENAME",
    "TYPED_COUNTS",
    "KernelQualificationBundleError",
    "KernelQualificationBundleV1Error",
    "build_bundle",
    "build_kernel_qualification_bundle",
    "canonical_json",
    "main",
    "record_sha256",
    "validate_bundle",
    "validate_kernel_qualification_bundle",
]
