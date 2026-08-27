"""Thin, machine-only adapter for the v0.13 Gate v9 Host task matrix.

The adapter does not directly start Codex/OpenCode.  A real Host run is an
external qualification action with owner-controlled prerequisites.  This file
provides the frozen task plan, a direct owner-external broker IPC preflight,
public read seams, and validation of already-retained typed evidence.  It never
reads ambient authentication, invokes a model, or opens a private database API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts.codex_app_server_client import (
    CodexOwnerExternalBrokerError,
    build_codex_zero_model_preflight_request,
    consume_codex_zero_model_preflight,
)
from benchmarks.hosts.host_preflight_receipt import (
    HostIdentityValidationError,
    host_binary_identity,
    host_identity_sha256,
    inspect_broker_source,
    load_host_identity_input,
)
from benchmarks.hosts.pass13_orchestrator import (
    QualificationOrchestrationError,
    repository_binding,
)
from benchmarks.release.typed_qualification_evidence import parse_typed_evidence
from benchmarks.release.typed_qualification_evidence_v3_host_tasks import (
    TASK_DUTIES,
    TASK_OPERATIONS,
    TASK_WRONG_STATES,
)
from deeplaw.api.knowledge_os import KnowledgeOS
from deeplaw.read_services import SourceReadService, WikiReadService
from deeplaw.util import strict_json_loads

REPOSITORY = Path(__file__).resolve().parents[2]
CASES_RELATIVE_PATH = Path("benchmarks/hosts/v013-host-task-cases-v1.json")
SCHEMA_RELATIVE_PATH = Path("contracts/v013-host-task-evidence.v1.schema.json")
HANDOFF_SCHEMA_RELATIVE_PATH = Path("contracts/v013-host-task-handoff.v1.schema.json")
HOSTS = ("codex", "opencode")
TASK_CASES = ("continuity", "living_wiki", "professional_evidence")
DRIVER_KIND = "native_host_task_v3"
TYPED_SOURCE_SLOTS = (
    "event_source",
    "lifecycle_source",
    "usage_source",
    "expected_source",
    "continuity_source",
    "isolation_source",
)
CONTROL_RECEIPT_SLOTS = ("host_preflight_receipt", "host_process_receipt")
_TASK_SEED_SEAMS = {
    "continuity": (
        "task_continuity.start_task",
        "task_continuity.checkpoint_task",
        "task_continuity.bind_host_session",
        "task_continuity.resume_task",
        "task_continuity.forget_task",
    ),
    "living_wiki": (
        "KnowledgeOS.compilations.begin",
        "KnowledgeOS.compilations.next_packet",
        "KnowledgeOS.compilations.stage",
        "KnowledgeOS.compilations.validate",
        "KnowledgeOS.compilations.commit",
        "SourceReadService.execute",
        "WikiReadService.execute",
        "KnowledgeOS.context.compile",
    ),
    "professional_evidence": (
        "KnowledgeOS.compilations.begin",
        "KnowledgeOS.compilations.next_packet",
        "KnowledgeOS.compilations.stage",
        "KnowledgeOS.compilations.validate",
        "KnowledgeOS.compilations.commit",
        "SourceReadService.execute",
        "WikiReadService.execute",
        "KnowledgeOS.context.compile",
    ),
}
_HOST_DRIVER_ROLES = {
    "codex": "codex_app_server_native_hook",
    "opencode": "opencode_exact_project_plugin_native_hook",
}
_HOST_DRIVER_POLICIES = {
    "codex": {
        "delivery": "codex_app_server_native_hook",
    },
    "opencode": {
        "delivery": "opencode_exact_project_plugin_native_hook",
        "plugin_policy": "single_exact_candidate_plugin",
        "ambient_project_plugins": "forbidden",
    },
}
_HANDOFF_FORBIDDEN_KEY = re.compile(
    r"(?:path|command|argv|env|stdout|stderr|prompt|transcript|reasoning|secret|auth|credential|provider_body|passed|evidence)",
    re.IGNORECASE,
)
_HANDOFF_FORBIDDEN_VALUE = re.compile(
    r"(?:^|[\s=:\"'])/(?:Users|home|root|private|tmp|var|etc|opt|workspace|Volumes|System|Library|bin|sbin|usr|dev|proc|sys|run|mnt)(?:/|[\s\"']|$)|"
    r"(?:^|[\s=:\"'])(?:[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/])",
    re.IGNORECASE,
)
_HANDOFF_FORBIDDEN_LITERAL_VALUES = frozenset(
    {
        "auth",
        "command",
        "credential",
        "evidence",
        "passed",
        "path",
        "prompt",
        "reasoning",
        "secret",
        "stderr",
        "stdout",
        "transcript",
    }
)
CONTINUITY_LIFECYCLE = (
    "new",
    "resume",
    "fork",
    "compaction",
    "stale",
    "wrong_task_line",
    "forget",
    "resume_after_forget",
)
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+:-]{0,99}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")
_ACTIVE_CANDIDATE_SCHEMA = "deeplaw.v013-active-qualification/v3"
_CONSTRUCTION_KIT_SCHEMA = "deeplaw.v013-external-kit-manifest/v2"
_CONSTRUCTION_KIT_MANIFEST_SHA256_SCOPE = (
    "utf8_json_sort_keys_compact_without_manifest_sha256_no_trailing_newline"
)
_CONSTRUCTION_KIT_MANIFEST_MAX_BYTES = 1024 * 1024
_QUALIFICATION_PROTOCOL_RELATIVE_PATH = Path(
    "benchmarks/v013/qualification-protocol-v3.json"
)
_ACTIVE_QUALIFICATION_SCHEMA_RELATIVE_PATH = Path(
    "contracts/v013-active-qualification.v3.schema.json"
)
_CANDIDATE_MANIFEST_MAX_BYTES = 1024 * 1024
_BROKER_SOURCE_MAX_BYTES = 256 * 1024
_STABLE_STAT_FIELDS = (
    "st_dev",
    "st_ino",
    "st_size",
    "st_mode",
    "st_uid",
    "st_nlink",
)


def _validate_catalog_host_constraints(value: Mapping[str, Any]) -> None:
    if set(value) != set(HOSTS):
        raise HostTaskQualificationError("v0.13 Host coordinates are not closed")
    codex = value["codex"]
    if not isinstance(codex, Mapping) or set(codex) != {
        "tool_version", "binary_sha256", "model_id", "expected_response_model_id",
        "reasoning_effort", "argv_prefix",
    }:
        raise HostTaskQualificationError("v0.13 Codex coordinates are not closed")
    if (
        codex["tool_version"] is not None
        or codex["binary_sha256"] is not None
        or codex["model_id"] != "gpt-5.6-luna"
        or codex["expected_response_model_id"] != "gpt-5.6-luna"
        or codex["reasoning_effort"] != "max"
        or codex["argv_prefix"] != ["codex", "app-server", "--stdio"]
    ):
        raise HostTaskQualificationError("v0.13 Codex coordinate shape is invalid")
    opencode = value["opencode"]
    if not isinstance(opencode, Mapping) or set(opencode) != {
        "tool_version", "binary_sha256", "source_commit", "config_selector",
        "model_id", "expected_response_model_id", "reasoning_effort", "argv_prefix",
    }:
        raise HostTaskQualificationError("v0.13 OpenCode coordinates are not closed")
    if (
        opencode["tool_version"] is not None
        or opencode["binary_sha256"] is not None
        or opencode["source_commit"] is not None
        or opencode["config_selector"] != "deepseek/deepseek-v4-flash"
        or opencode["model_id"] != "deepseek-v4-flash"
        or opencode["expected_response_model_id"] != "deepseek-v4-flash"
        or opencode["reasoning_effort"] is not None
        or opencode["argv_prefix"] != ["opencode", "run", "--format", "json"]
    ):
        raise HostTaskQualificationError("v0.13 OpenCode coordinates shape is invalid")


class HostTaskQualificationError(ValueError):
    """The frozen v0.13 Host task plan or retained evidence is invalid."""


def _schema() -> dict[str, Any]:
    try:
        value = strict_json_loads(
            (REPOSITORY / SCHEMA_RELATIVE_PATH).read_bytes()
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise HostTaskQualificationError("v0.13 Host task schema is unavailable") from exc
    if not isinstance(value, dict):
        raise HostTaskQualificationError("v0.13 Host task schema is not an object")
    Draft202012Validator.check_schema(value)
    return value


def _handoff_schema() -> dict[str, Any]:
    try:
        value = strict_json_loads(
            (REPOSITORY / HANDOFF_SCHEMA_RELATIVE_PATH).read_bytes()
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise HostTaskQualificationError("v0.13 Host task handoff schema is unavailable") from exc
    if not isinstance(value, dict):
        raise HostTaskQualificationError("v0.13 Host task handoff schema is not an object")
    Draft202012Validator.check_schema(value)
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _descriptor_summary(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    raw = _canonical(descriptor).encode("utf-8")
    return {
        "role": descriptor["role"],
        "byte_size": len(raw),
        "sha256": _sha256(raw),
    }


def _validate_task_catalog(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HostTaskQualificationError("v0.13 Host task cases are not an object")
    try:
        Draft202012Validator(_schema(), format_checker=FormatChecker()).validate(value)
    except Exception as exc:  # jsonschema has several validation exception classes.
        raise HostTaskQualificationError(
            "v0.13 Host task cases failed strict schema validation"
        ) from exc
    tasks = value["task_cases"]
    if [item["task_case"] for item in tasks] != list(TASK_CASES):
        raise HostTaskQualificationError("v0.13 Host task case order is not frozen")
    for row in tasks:
        task = row["task_case"]
        if (
            row["required_duties"] != list(TASK_DUTIES[task])
            or row["required_wrong_states"] != list(TASK_WRONG_STATES[task])
            or row["required_operations"] != list(TASK_OPERATIONS[task])
            or row["seed_driver"]
            != {
                "role": "task_domain_seed",
                "driver_kind": DRIVER_KIND,
                "seams": list(_TASK_SEED_SEAMS[task]),
            }
        ):
            raise HostTaskQualificationError(
                "v0.13 Host task duties, wrong states, operations, or seed seam are not frozen"
            )
    _validate_catalog_host_constraints(value["host_constraints"])
    return json.loads(_canonical(value))


def _load_external_identity(
    path: Path | str,
    *,
    repository: Path = REPOSITORY,
) -> dict[str, Any]:
    try:
        return load_host_identity_input(path, repository=repository)
    except (HostIdentityValidationError, OSError, ValueError) as exc:
        raise HostTaskQualificationError(
            "owner-controlled Host identity input was rejected"
        ) from exc


def load_task_cases(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate the frozen three-task Host case catalog."""

    selected = REPOSITORY / CASES_RELATIVE_PATH if path is None else Path(path)
    try:
        value = strict_json_loads(selected.read_bytes())
    except (OSError, UnicodeError, ValueError) as exc:
        raise HostTaskQualificationError("v0.13 Host task cases are unavailable") from exc
    return _validate_task_catalog(value)


def task_case(host: str, task: str, *, cases: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return one host/task plan without executing a Host or changing state."""

    if host not in HOSTS or task not in TASK_CASES:
        raise HostTaskQualificationError("unsupported v0.13 Host task identity")
    catalog = load_task_cases() if cases is None else cases
    row = next((item for item in catalog["task_cases"] if item["task_case"] == task), None)
    if row is None:
        raise HostTaskQualificationError("frozen v0.13 Host task is missing")
    plan = {
        "status": "not_executed",
        "executed": False,
        "host": host,
        "task_case": task,
        "required_duties": list(row["required_duties"]),
        "required_wrong_states": list(row["required_wrong_states"]),
        "required_operations": list(row["required_operations"]),
        "public_seams": [
            "knowledge_support",
            "SourceReadService",
            "WikiReadService",
            "KnowledgeOS.retrieval.query",
            "KnowledgeOS.context.compile",
            "native_host.parse_native_host_event",
            "native_host.derive_native_host_receipt",
        ],
        "native_host_receipts_required": True,
        "claim_eligible": False,
        "release_ready": False,
        "host_identity_source": "owner_external_frozen_input",
    }
    if task == "continuity":
        plan["lifecycle"] = list(CONTINUITY_LIFECYCLE)
    if task == "living_wiki":
        plan["wiki_cases"] = list(row["required_duties"])
    if task == "professional_evidence":
        plan["source_duties"] = list(row["required_duties"])
    return plan


def _validate_handoff_projection(value: Any, *, key: str | None = None) -> None:
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise HostTaskQualificationError("Host task handoff field name is invalid")
            if _HANDOFF_FORBIDDEN_KEY.search(nested_key):
                raise HostTaskQualificationError(
                    "Host task handoff contains a forbidden field"
                )
            _validate_handoff_projection(nested_value, key=nested_key)
    elif isinstance(value, list):
        for item in value:
            _validate_handoff_projection(item, key=key)
    elif isinstance(value, str) and (
        _HANDOFF_FORBIDDEN_VALUE.search(value)
        or value.casefold() in _HANDOFF_FORBIDDEN_LITERAL_VALUES
    ):
        raise HostTaskQualificationError("Host task handoff contains a forbidden value")


def _read_handoff(value: Path | str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        loaded: Any = json.loads(_canonical(value))
    else:
        try:
            loaded = strict_json_loads(Path(value).read_bytes())
        except (OSError, UnicodeError, ValueError) as exc:
            raise HostTaskQualificationError("Host task handoff is unavailable") from exc
    if not isinstance(loaded, Mapping):
        raise HostTaskQualificationError("Host task handoff is not an object")
    return json.loads(_canonical(loaded))


def _driver_descriptor(host: str, catalog: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "role": _HOST_DRIVER_ROLES[host],
        "driver_kind": DRIVER_KIND,
        "host": host,
        "argv_prefix": list(catalog["host_constraints"][host]["argv_prefix"]),
        "policy": _HOST_DRIVER_POLICIES[host],
    }


def build_external_collector_handoff(
    host_identity_input: Path | str,
    cases: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a path-free, pre-execution six-slot external collector input.

    This function only binds frozen descriptors and owner-supplied identity
    digests.  It never seeds a vault, starts a Host, invokes a model, or emits
    typed evidence.
    """

    catalog = load_task_cases() if cases is None else _validate_task_catalog(cases)
    identity = _load_external_identity(host_identity_input)
    source_sha256 = identity.get("source_sha256")
    if not isinstance(source_sha256, str) or _SHA256.fullmatch(source_sha256) is None:
        raise HostTaskQualificationError("owner-controlled Host identity source digest is invalid")
    slots: list[dict[str, Any]] = []
    for host in HOSTS:
        host_identity = identity["hosts"][host]
        host_identity_digest = host_identity_sha256(host_identity)
        for task in TASK_CASES:
            row = next(
                item for item in catalog["task_cases"] if item["task_case"] == task
            )
            slots.append(
                {
                    "host": host,
                    "task_case": task,
                    "driver_kind": DRIVER_KIND,
                    "status": "not_executed",
                    "executed": False,
                    "claim_eligible": False,
                    "release_ready": False,
                    "seed_descriptor": _descriptor_summary(row["seed_driver"]),
                    "driver_descriptor": _descriptor_summary(
                        _driver_descriptor(host, catalog)
                    ),
                    "host_identity_sha256": host_identity_digest,
                    "host_identity_source_sha256": source_sha256,
                    "host_identity_source": "owner_external_frozen_input",
                    "typed_source_slots": list(TYPED_SOURCE_SLOTS),
                    "control_receipt_slots": list(CONTROL_RECEIPT_SLOTS),
                }
            )
    handoff: dict[str, Any] = {
        "artifact_kind": "host_task_external_collector_handoff",
        "schema_version": "deeplaw.v013-host-task-handoff/v1",
        "status": "not_executed",
        "executed": False,
        "claim_eligible": False,
        "release_ready": False,
        "task_catalog_descriptor": _descriptor_summary(
            {
                "role": "host_task_catalog",
                "catalog": catalog,
            }
        ),
        "slots": slots,
    }
    handoff["record_sha256"] = _sha256(
        _canonical(handoff).encode("utf-8")
    )
    _validate_handoff_projection(handoff)
    try:
        Draft202012Validator(
            _handoff_schema(), format_checker=FormatChecker()
        ).validate(handoff)
    except Exception as exc:  # jsonschema has several validation exception classes.
        raise HostTaskQualificationError(
            "generated Host task handoff failed strict schema validation"
        ) from exc
    return json.loads(_canonical(handoff))


def validate_external_collector_handoff(
    handoff: Path | str | Mapping[str, Any],
    *,
    host_identity_input: Path | str,
    cases: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the exact six-slot pre-execution handoff and its bindings."""

    value = _read_handoff(handoff)
    _validate_handoff_projection(value)
    try:
        Draft202012Validator(_handoff_schema(), format_checker=FormatChecker()).validate(value)
    except Exception as exc:  # jsonschema has several validation exception classes.
        raise HostTaskQualificationError(
            "Host task handoff failed strict schema validation"
        ) from exc
    record = value.get("record_sha256")
    without_record = {key: item for key, item in value.items() if key != "record_sha256"}
    if record != _sha256(_canonical(without_record).encode("utf-8")):
        raise HostTaskQualificationError("Host task handoff record digest mismatch")
    expected = build_external_collector_handoff(
        host_identity_input,
        cases=cases,
    )
    expected["record_sha256"] = value["record_sha256"]
    if value != expected:
        raise HostTaskQualificationError("Host task handoff binding or slot order mismatch")
    return value


def _stable_stat_signature(details: os.stat_result) -> tuple[Any, ...]:
    """Return identity and mutation fields for one exact file observation."""

    return (
        *(getattr(details, field, None) for field in _STABLE_STAT_FIELDS),
        getattr(details, "st_mtime_ns", getattr(details, "st_mtime", None)),
        getattr(details, "st_ctime_ns", getattr(details, "st_ctime", None)),
    )


def _parent_chain_has_symlink(path: Path) -> bool:
    selected = Path(path)
    if not selected.is_absolute():
        return True
    current = Path(selected.anchor)
    for part in selected.parent.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except OSError:
            return True
    return False


def _read_stable_regular_file(
    path: Path | str,
    *,
    label: str,
    repository: Path,
    require_external: bool,
    executable: bool = False,
    owner_only: bool = False,
    retain_bytes: bool = False,
    maximum_bytes: int | None = None,
) -> tuple[str, bytes | None]:
    """Hash one regular file through a stable FD and unchanged path identity."""

    selected = Path(path)
    if not selected.is_absolute():
        raise HostTaskQualificationError(f"{label} must be absolute")
    if _parent_chain_has_symlink(selected):
        raise HostTaskQualificationError(f"{label} parent path contains a symlink")
    try:
        before = selected.lstat()
        resolved = selected.resolve(strict=True)
        repository_root = repository.resolve(strict=True)
    except OSError as exc:
        raise HostTaskQualificationError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise HostTaskQualificationError(f"{label} must be a regular non-symlink file")
    if before.st_nlink != 1:
        raise HostTaskQualificationError(f"{label} must be a single-link file")
    if maximum_bytes is not None and not 1 <= before.st_size <= maximum_bytes:
        raise HostTaskQualificationError(f"{label} exceeds its byte bound")
    if executable and not os.access(selected, os.X_OK):
        raise HostTaskQualificationError(f"{label} is not executable")
    if owner_only and os.name != "nt" and (
        not hasattr(os, "geteuid")
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) & 0o077
    ):
        raise HostTaskQualificationError(f"{label} must be owner-only")
    inside_repository = resolved == repository_root or repository_root in resolved.parents
    if require_external and inside_repository:
        raise HostTaskQualificationError(f"{label} must be repository-external")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = -1
    chunks: list[bytes] = []
    digest = hashlib.sha256()
    total = 0
    try:
        descriptor = os.open(selected, flags)
        fd_before = os.fstat(descriptor)
        if _stable_stat_signature(fd_before) != _stable_stat_signature(before):
            raise HostTaskQualificationError(f"{label} changed before it was read")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if maximum_bytes is not None and total > maximum_bytes:
                raise HostTaskQualificationError(f"{label} exceeds its byte bound")
            digest.update(chunk)
            if retain_bytes:
                chunks.append(chunk)
        fd_after = os.fstat(descriptor)
    except HostTaskQualificationError:
        raise
    except OSError as exc:
        raise HostTaskQualificationError(f"{label} is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        after = selected.lstat()
    except OSError as exc:
        raise HostTaskQualificationError(f"{label} changed while it was read") from exc
    if (
        _parent_chain_has_symlink(selected)
        or _stable_stat_signature(fd_before) != _stable_stat_signature(fd_after)
        or _stable_stat_signature(before) != _stable_stat_signature(after)
        or total != before.st_size
    ):
        raise HostTaskQualificationError(f"{label} changed while it was read")
    return digest.hexdigest(), b"".join(chunks) if retain_bytes else None


@contextmanager
def _stage_exact_broker_executable(
    path: Path | str,
    *,
    repository: Path,
    host_binary: Path,
    expected_sha256: str,
) -> Iterator[Path]:
    """Stage verified broker bytes in one private path immune to source replacement."""

    source = Path(path)
    inspected = inspect_broker_source(
        source,
        repository=repository,
        host_binary=host_binary,
        expected_sha256=expected_sha256,
    )
    if (
        inspected.get("failure_reason_code") is not None
        or inspected.get("repository_external") is not True
        or inspected.get("owner_only_mode") is not True
        or inspected.get("sha256") != expected_sha256
    ):
        raise HostTaskQualificationError("Codex owner-external broker source was rejected")
    observed_sha256, raw = _read_stable_regular_file(
        source,
        label="Codex owner-external broker source",
        repository=repository,
        require_external=True,
        executable=True,
        owner_only=True,
        retain_bytes=True,
        maximum_bytes=_BROKER_SOURCE_MAX_BYTES,
    )
    if observed_sha256 != expected_sha256 or raw is None:
        raise HostTaskQualificationError("Codex owner-external broker source hash differs")

    with tempfile.TemporaryDirectory(prefix="deeplaw-codex-broker-") as raw_directory:
        directory = Path(raw_directory).resolve(strict=True)
        details = directory.lstat()
        if (
            not stat.S_ISDIR(details.st_mode)
            or stat.S_IMODE(details.st_mode) & 0o077
            or (os.name != "nt" and details.st_uid != os.geteuid())
        ):
            raise HostTaskQualificationError("Codex broker staging directory is unsafe")
        staged = directory / "broker-executable"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(staged, flags, 0o700)
        try:
            offset = 0
            while offset < len(raw):
                offset += os.write(descriptor, raw[offset:])
            os.fchmod(descriptor, 0o500)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(directory, 0o500)
        try:
            staged_sha256, _ = _read_stable_regular_file(
                staged,
                label="staged Codex broker executable",
                repository=repository,
                require_external=True,
                executable=True,
                owner_only=True,
                maximum_bytes=_BROKER_SOURCE_MAX_BYTES,
            )
            if staged_sha256 != expected_sha256:
                raise HostTaskQualificationError("staged Codex broker bytes differ")
            yield staged
        finally:
            os.chmod(directory, 0o700)


def load_exact_candidate_binding(
    path: Path | str,
    *,
    candidate_wheel: Path | str | None = None,
    repository: Path = REPOSITORY,
) -> dict[str, Any]:
    """Reopen the frozen candidate input and exact local artifact bytes."""

    selected = Path(path)
    try:
        _, raw = _read_stable_regular_file(
            selected,
            label="candidate binding input",
            repository=repository,
            require_external=True,
            retain_bytes=True,
            maximum_bytes=_CANDIDATE_MANIFEST_MAX_BYTES,
        )
        if raw is None:
            raise HostTaskQualificationError("candidate binding input is unavailable")
        value = strict_json_loads(raw)
    except HostTaskQualificationError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise HostTaskQualificationError("candidate binding input is unavailable") from exc
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != _ACTIVE_CANDIDATE_SCHEMA
        or value.get("status")
        != "frozen_exact_candidate_machine_evaluation_pending"
        or value.get("candidate_version") != "0.13.0"
        or value.get("construction_package_version") != "0.12.0"
        or value.get("release_target") != "0.13.0"
        or not isinstance(value.get("candidate_binding"), Mapping)
    ):
        raise HostTaskQualificationError("candidate binding input is not frozen active v3")
    source = value["candidate_binding"]
    mapping = {
        "commit": source.get("source_commit"),
        "tree": source.get("source_tree"),
        "lock_sha256": source.get("lock_sha256"),
        "wheel_sha256": source.get("wheel_sha256"),
        "sdist_sha256": source.get("sdist_sha256"),
    }
    if (
        _GIT.fullmatch(str(mapping["commit"])) is None
        or _GIT.fullmatch(str(mapping["tree"])) is None
        or any(
            _SHA256.fullmatch(str(mapping[field])) is None
            or mapping[field] == "0" * 64
            for field in ("lock_sha256", "wheel_sha256", "sdist_sha256")
        )
    ):
        raise HostTaskQualificationError("candidate binding input is incomplete")
    if source.get("package_version") != "0.13.0":
        raise HostTaskQualificationError("candidate package version differs")
    wheel_name = source.get("wheel_filename")
    sdist_name = source.get("sdist_filename")
    if any(
        not isinstance(name, str)
        or not name
        or Path(name).name != name
        or "/" in name
        or "\\" in name
        for name in (wheel_name, sdist_name)
    ):
        raise HostTaskQualificationError("candidate artifact filename is invalid")
    if (
        not wheel_name.startswith("deeplaw-0.13.0-")
        or not wheel_name.endswith(".whl")
        or sdist_name != "deeplaw-0.13.0.tar.gz"
    ):
        raise HostTaskQualificationError("candidate artifact filename version differs")

    expected_wheel = selected.parent / str(wheel_name)
    selected_wheel = expected_wheel if candidate_wheel is None else Path(candidate_wheel)
    try:
        if not selected_wheel.is_absolute():
            raise HostTaskQualificationError("candidate wheel must be absolute")
        if selected_wheel.resolve(strict=True) != expected_wheel.resolve(strict=True):
            raise HostTaskQualificationError(
                "candidate wheel path differs from frozen input"
            )
        current = repository_binding(repository)
        lock_sha256, _ = _read_stable_regular_file(
            repository.resolve(strict=True) / "uv.lock",
            label="candidate lock file",
            repository=repository,
            require_external=False,
        )
        wheel_sha256, _ = _read_stable_regular_file(
            selected_wheel,
            label="candidate wheel",
            repository=repository,
            require_external=True,
        )
        sdist_sha256, _ = _read_stable_regular_file(
            selected.parent / str(sdist_name),
            label="candidate sdist",
            repository=repository,
            require_external=True,
        )
    except HostTaskQualificationError:
        raise
    except (OSError, ValueError, QualificationOrchestrationError) as exc:
        raise HostTaskQualificationError("candidate exact-byte binding failed") from exc
    if (
        mapping["commit"] != current["commit"]
        or mapping["tree"] != current["tree"]
        or mapping["lock_sha256"] != lock_sha256
        or mapping["wheel_sha256"] != wheel_sha256
        or mapping["sdist_sha256"] != sdist_sha256
    ):
        raise HostTaskQualificationError("candidate exact-byte binding differs")
    return {field: mapping[field] for field in (
        "commit",
        "tree",
        "lock_sha256",
        "wheel_sha256",
        "sdist_sha256",
    )}


def _construction_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or _SHA256.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise HostTaskQualificationError(
            f"construction kit {label} must be a nonzero lowercase SHA-256 digest"
        )
    return value


def _construction_git(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _GIT.fullmatch(value) is None or value == "0" * 40:
        raise HostTaskQualificationError(
            f"construction kit {label} must be a nonzero Git commit digest"
        )
    return value


def load_construction_candidate_binding(
    path: Path | str,
    *,
    candidate_wheel: Path | str | None = None,
    repository: Path = REPOSITORY,
) -> dict[str, Any]:
    """Load the external construction-only v2 manifest for zero-model preflight."""

    selected = Path(path)
    try:
        _, raw = _read_stable_regular_file(
            selected,
            label="construction kit manifest",
            repository=repository,
            require_external=True,
            retain_bytes=True,
            maximum_bytes=_CONSTRUCTION_KIT_MANIFEST_MAX_BYTES,
        )
        if raw is None:
            raise HostTaskQualificationError("construction kit manifest is unavailable")
        value = strict_json_loads(raw)
    except HostTaskQualificationError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise HostTaskQualificationError("construction kit manifest is unavailable") from exc

    top_fields = {
        "schema_version",
        "evidence_class",
        "status",
        "formal_admission",
        "construction",
        "artifacts",
        "protocol_binding",
        "qualification_state",
        "manifest_sha256_scope",
        "manifest_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != top_fields:
        raise HostTaskQualificationError("construction kit manifest is not closed v2")
    try:
        canonical_raw = _canonical_bytes(value)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise HostTaskQualificationError("construction kit manifest is not canonical JSON") from exc
    if raw != canonical_raw:
        raise HostTaskQualificationError("construction kit manifest bytes are not canonical")
    if (
        value.get("schema_version") != _CONSTRUCTION_KIT_SCHEMA
        or value.get("evidence_class") != "control_manifest_only"
        or value.get("status") != "construction_zero_model_preflight_ready"
        or value.get("formal_admission") is not False
        or value.get("manifest_sha256_scope")
        != _CONSTRUCTION_KIT_MANIFEST_SHA256_SCOPE
    ):
        raise HostTaskQualificationError("construction kit manifest status is invalid")
    manifest_sha256 = _construction_sha256(
        value.get("manifest_sha256"),
        label="manifest_sha256",
    )
    without_manifest_sha256 = {
        key: item for key, item in value.items() if key != "manifest_sha256"
    }
    if _sha256(_canonical_bytes(without_manifest_sha256)) != manifest_sha256:
        raise HostTaskQualificationError("construction kit manifest self-hash differs")

    construction = value.get("construction")
    if not isinstance(construction, Mapping) or set(construction) != {
        "commit",
        "tree",
        "package_version",
        "release_target",
        "uv_lock_sha256",
    }:
        raise HostTaskQualificationError("construction kit binding is not closed")
    construction_commit = _construction_git(
        construction.get("commit"),
        label="commit",
    )
    construction_tree = _construction_git(
        construction.get("tree"),
        label="tree",
    )
    construction_lock = _construction_sha256(
        construction.get("uv_lock_sha256"),
        label="uv_lock_sha256",
    )
    if (
        construction.get("package_version") != "0.12.0"
        or construction.get("release_target") != "0.13.0"
    ):
        raise HostTaskQualificationError("construction kit package or release target differs")

    artifacts = value.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != {"wheel", "sdist"}:
        raise HostTaskQualificationError("construction kit artifacts are not closed")
    wheel = artifacts.get("wheel")
    sdist = artifacts.get("sdist")
    if (
        not isinstance(wheel, Mapping)
        or set(wheel) != {"filename", "sha256"}
        or not isinstance(sdist, Mapping)
        or set(sdist) != {"filename", "sha256"}
    ):
        raise HostTaskQualificationError("construction kit artifact binding is not closed")
    wheel_name = wheel.get("filename")
    sdist_name = sdist.get("filename")
    if any(
        not isinstance(name, str)
        or not name
        or Path(name).name != name
        or "/" in name
        or "\\" in name
        for name in (wheel_name, sdist_name)
    ):
        raise HostTaskQualificationError("construction kit artifact filename is invalid")
    if (
        not wheel_name.startswith("deeplaw-0.12.0-")
        or not wheel_name.endswith(".whl")
        or sdist_name != "deeplaw-0.12.0.tar.gz"
    ):
        raise HostTaskQualificationError("construction kit artifact filename version differs")
    wheel_sha = _construction_sha256(wheel.get("sha256"), label="wheel.sha256")
    sdist_sha = _construction_sha256(sdist.get("sha256"), label="sdist.sha256")

    protocol_binding = value.get("protocol_binding")
    if not isinstance(protocol_binding, Mapping) or set(protocol_binding) != {
        "qualification_protocol_sha256",
        "active_qualification_schema_sha256",
        "codex_control_schema_version",
    }:
        raise HostTaskQualificationError("construction kit protocol binding is not closed")
    protocol_sha = _construction_sha256(
        protocol_binding.get("qualification_protocol_sha256"),
        label="qualification_protocol_sha256",
    )
    active_schema_sha = _construction_sha256(
        protocol_binding.get("active_qualification_schema_sha256"),
        label="active_qualification_schema_sha256",
    )
    if protocol_binding.get("codex_control_schema_version") != (
        "deeplaw.codex-owner-external-broker-control/v4"
    ):
        raise HostTaskQualificationError("construction kit Codex control schema differs")

    qualification_state = value.get("qualification_state")
    if not isinstance(qualification_state, Mapping) or set(qualification_state) != {
        "formal_n6",
        "human_gold",
        "release_ready",
    }:
        raise HostTaskQualificationError("construction kit qualification state is not closed")
    if (
        qualification_state.get("formal_n6") != "not_executed"
        or qualification_state.get("human_gold") != "not_executed"
        or qualification_state.get("release_ready") is not False
    ):
        raise HostTaskQualificationError("construction kit qualification state is invalid")

    try:
        current = repository_binding(repository)
        lock_sha256, _ = _read_stable_regular_file(
            repository.resolve(strict=True) / "uv.lock",
            label="construction candidate lock file",
            repository=repository,
            require_external=False,
        )
        current_protocol_sha256, _ = _read_stable_regular_file(
            repository.resolve(strict=True) / _QUALIFICATION_PROTOCOL_RELATIVE_PATH,
            label="qualification protocol",
            repository=repository,
            require_external=False,
        )
        current_active_schema_sha256, _ = _read_stable_regular_file(
            repository.resolve(strict=True) / _ACTIVE_QUALIFICATION_SCHEMA_RELATIVE_PATH,
            label="active qualification schema",
            repository=repository,
            require_external=False,
        )
        if (
            not isinstance(current, Mapping)
            or current.get("worktree_clean") is not True
            or current.get("package_version") != "0.12.0"
            or current.get("commit") != construction_commit
            or current.get("tree") != construction_tree
            or lock_sha256 != construction_lock
            or current_protocol_sha256 != protocol_sha
            or current_active_schema_sha256 != active_schema_sha
        ):
            raise HostTaskQualificationError("construction kit current binding differs")

        expected_wheel = selected.parent / wheel_name
        selected_wheel = expected_wheel if candidate_wheel is None else Path(candidate_wheel)
        if (
            not selected_wheel.is_absolute()
            or selected_wheel.resolve(strict=True) != expected_wheel.resolve(strict=True)
        ):
            raise HostTaskQualificationError("construction kit wheel path differs")
        observed_wheel_sha, _ = _read_stable_regular_file(
            selected_wheel,
            label="construction kit wheel",
            repository=repository,
            require_external=True,
        )
        observed_sdist_sha, _ = _read_stable_regular_file(
            selected.parent / sdist_name,
            label="construction kit sdist",
            repository=repository,
            require_external=True,
        )
    except HostTaskQualificationError:
        raise
    except (OSError, ValueError, QualificationOrchestrationError) as exc:
        raise HostTaskQualificationError("construction kit exact-byte binding failed") from exc
    if observed_wheel_sha != wheel_sha or observed_sdist_sha != sdist_sha:
        raise HostTaskQualificationError("construction kit artifact bytes differ")
    return {
        "commit": construction_commit,
        "tree": construction_tree,
        "lock_sha256": construction_lock,
        "wheel_sha256": wheel_sha,
        "sdist_sha256": sdist_sha,
    }


def load_zero_model_candidate_binding(
    path: Path | str,
    *,
    candidate_wheel: Path | str | None = None,
    repository: Path = REPOSITORY,
) -> dict[str, Any]:
    """Accept either a formal frozen v3 candidate or construction-only v2 input."""

    selected = Path(path)
    try:
        _, raw = _read_stable_regular_file(
            selected,
            label="zero-model candidate binding input",
            repository=repository,
            require_external=True,
            retain_bytes=True,
            maximum_bytes=_CANDIDATE_MANIFEST_MAX_BYTES,
        )
        if raw is None:
            raise HostTaskQualificationError(
                "zero-model candidate binding input is unavailable"
            )
        value = strict_json_loads(raw)
    except HostTaskQualificationError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise HostTaskQualificationError(
            "zero-model candidate binding input is unavailable"
        ) from exc
    if isinstance(value, Mapping) and value.get("schema_version") == _CONSTRUCTION_KIT_SCHEMA:
        return load_construction_candidate_binding(
            selected,
            candidate_wheel=candidate_wheel,
            repository=repository,
        )
    return load_exact_candidate_binding(
        selected,
        candidate_wheel=candidate_wheel,
        repository=repository,
    )


def _validate_codex_binary_static(
    path: Path | str,
    *,
    identity: Mapping[str, Any],
    repository: Path,
) -> dict[str, str]:
    """Validate exact Codex bytes and topology without executing the Host."""

    selected = Path(path)
    expected = host_binary_identity(identity, "codex")
    try:
        observed_sha256, _ = _read_stable_regular_file(
            selected,
            label="Codex executable",
            repository=repository,
            require_external=True,
            executable=True,
        )
    except HostTaskQualificationError:
        raise
    except OSError as exc:
        raise HostTaskQualificationError("Codex executable hash probe failed") from exc
    if observed_sha256 != expected["sha256"]:
        raise HostTaskQualificationError("Codex executable hash differs")
    return expected


def run_codex_owner_external_zero_model_preflight(
    *,
    candidate_binding_input: Path | str,
    candidate_wheel: Path | str | None = None,
    host_identity_input: Path | str,
    codex_binary: Path | str,
    codex_broker: Path | str,
    expected_broker_sha256: str,
    task_case: str,
    run_id: str,
    evidence_run_id: int,
    qualification_run_id: int,
    repository: Path = REPOSITORY,
    seen_nonce_sha256s: set[str] | None = None,
) -> dict[str, Any]:
    """Run one transient broker-owned Codex zero-model capability preflight.

    The returned summary is not a Host task receipt and is never formal
    admission.  Only the broker subprocess can supply the transient v4 object
    that this function structurally validates in memory.
    """

    if (
        task_case not in TASK_CASES
        or not isinstance(run_id, str)
        or not run_id
        or type(evidence_run_id) is not int
        or evidence_run_id < 1
        or type(qualification_run_id) is not int
        or qualification_run_id < 1
        or not isinstance(expected_broker_sha256, str)
        or _SHA256.fullmatch(expected_broker_sha256) is None
        or expected_broker_sha256 == "0" * 64
    ):
        raise HostTaskQualificationError("Codex zero-model run binding is invalid")
    candidate = load_zero_model_candidate_binding(
        candidate_binding_input,
        candidate_wheel=candidate_wheel,
        repository=repository,
    )
    identity = _load_external_identity(host_identity_input, repository=repository)
    expected_host_binary = _validate_codex_binary_static(
        codex_binary,
        identity=identity,
        repository=repository,
    )
    issued = datetime.now(UTC).replace(microsecond=0)
    expires = issued + timedelta(seconds=60)
    nonce_sha256 = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
    request = build_codex_zero_model_preflight_request(
        task_case=task_case,
        run_id=run_id,
        candidate_binding=candidate,
        run_binding={
            "evidence_run_id": evidence_run_id,
            "qualification_run_id": qualification_run_id,
        },
        host_binary=expected_host_binary,
        broker_source_sha256=expected_broker_sha256,
        host_identity_sha256=host_identity_sha256(identity["hosts"]["codex"]),
        host_identity_source_sha256=str(identity["source_sha256"]),
        nonce_sha256=nonce_sha256,
        issued_at=issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    try:
        with _stage_exact_broker_executable(
            codex_broker,
            repository=repository,
            host_binary=Path(codex_binary),
            expected_sha256=expected_broker_sha256,
        ) as broker_executable:
            observation = consume_codex_zero_model_preflight(
                broker_executable,
                request=request,
                seen_nonce_sha256s=(
                    seen_nonce_sha256s if seen_nonce_sha256s is not None else set()
                ),
            )
    except (CodexOwnerExternalBrokerError, OSError, ValueError) as exc:
        raise HostTaskQualificationError(
            "Codex owner-external zero-model preflight failed closed"
        ) from exc
    return {
        "status": "passed",
        "evidence_class": "zero_model_preflight_only",
        "formal_admission": False,
        "host": "codex",
        "control_schema_version": observation["schema_version"],
        "observed_sequence": observation["observed_sequence"],
        "fresh_ephemeral_thread": observation["fresh_ephemeral_thread"],
        "turn_start_count": observation["turn_start_count"],
        "session_start_hook": observation["session_start_hook"],
        "provider_guard": observation["provider_guard"],
        "accepted_connection_count": observation["accepted_connection_count"],
        "request_count": observation["request_count"],
        "model_inventory_count": observation["model_inventory_count"],
        "model_invocation_count": observation["model_invocation_count"],
        "provider_request_count": observation["provider_request_count"],
        "sampling_count": observation["sampling_count"],
        "broker_source_sha256": expected_broker_sha256,
        "receipt_record_sha256": observation["host_process_receipt"]["record_sha256"],
    }


def public_source_read(
    vault: Path | str,
    *,
    source_id: str,
    fragment_id: str | None = None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    """Use the policy-admitted public Source read seam."""

    service = SourceReadService(vault)
    if fragment_id is None:
        return service.execute(action="get", source_id=source_id, max_chars=max_chars)
    return service.execute(
        action="fragment",
        source_id=source_id,
        fragment_id=fragment_id,
        max_chars=max_chars,
    )


def public_wiki_read(
    vault: Path | str,
    *,
    action: str,
    wiki_path: str | None = None,
    knowledge_id: str | None = None,
) -> dict[str, Any]:
    """Use the policy-admitted public Living Wiki read seam."""

    service = WikiReadService(vault)
    return service.execute(
        action=action,
        wiki_path=wiki_path,
        knowledge_id=knowledge_id,
        limit=20,
    )


def public_context_read(
    vault: Path | str,
    *,
    task: str,
    duties: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Use the bounded public KnowledgeOS context seam."""

    with KnowledgeOS.open(vault) as knowledge:
        return knowledge.context.compile(
            task=task,
            applicable_duties=duties,
            max_chars=8_000,
            max_tokens=6_000,
            max_sources=12,
            confirm_no_case_data=True,
        )


def validate_retained_manifest(
    manifest: Path | str,
    *,
    root: Path | str | None = None,
    expected_candidate: Mapping[str, Any] | None = None,
    expected_run_id: str | None = None,
    expected_workflow_run_id: int | None = None,
    expected_corpus_sha256: str | None = None,
    expected_runner: Mapping[str, Any] | None = None,
    expected_scorer: Mapping[str, Any] | None = None,
    host_identity_input: Path | str | None = None,
) -> dict[str, Any]:
    """Validate a retained v3 ``host_event_sequence`` evidence manifest."""

    try:
        result = parse_typed_evidence(
            manifest,
            root=root,
            expected_candidate=expected_candidate,
            expected_run_id=expected_run_id,
            expected_workflow_run_id=expected_workflow_run_id,
            expected_corpus_sha256=expected_corpus_sha256,
            expected_runner=expected_runner,
            expected_scorer=expected_scorer,
        )
    except Exception as exc:
        raise HostTaskQualificationError("retained v0.13 Host task evidence was rejected") from exc
    if result.get("kind") != "host_event_sequence":
        raise HostTaskQualificationError("retained evidence kind is not host_event_sequence")
    metrics = result.get("metrics")
    if (
        not isinstance(metrics, Mapping)
        or metrics.get("task_case") not in TASK_CASES
        or metrics.get("host") not in HOSTS
    ):
        raise HostTaskQualificationError("retained evidence lacks v0.13 Host/task metrics")
    if host_identity_input is not None:
        identity = _load_external_identity(host_identity_input)
        expected_identity = host_identity_sha256(identity["hosts"][metrics["host"]])
        if metrics.get("host_identity_sha256") != expected_identity:
            raise HostTaskQualificationError(
                "retained Host evidence does not bind the owner-controlled identity"
            )
    return result


def validate_host_task_matrix(
    results: list[Mapping[str, Any]],
    *,
    host_identity_input: Path | str | None = None,
) -> dict[str, Any]:
    """Require exactly one derived result for each Host/task pair.

    This is an aggregate admission check only.  It never turns a local result
    into a release decision and never accepts caller-authored aggregate fields.
    """

    if not isinstance(results, list) or len(results) != len(HOSTS) * len(TASK_CASES):
        raise HostTaskQualificationError("v0.13 Host task matrix requires exactly six results")
    identities: set[tuple[str, str]] = set()
    frozen_identity: Mapping[str, Any] | None = None
    if host_identity_input is not None:
        frozen_identity = _load_external_identity(host_identity_input)
    for index, result in enumerate(results):
        if not isinstance(result, Mapping) or result.get("kind") != "host_event_sequence":
            raise HostTaskQualificationError(f"Host task matrix result {index} has the wrong kind")
        metrics = result.get("metrics")
        if not isinstance(metrics, Mapping):
            raise HostTaskQualificationError(
                f"Host task matrix result {index} has no derived metrics"
            )
        host = metrics.get("host")
        task = metrics.get("task_case")
        if host not in HOSTS or task not in TASK_CASES or (host, task) in identities:
            raise HostTaskQualificationError(
                "Host task matrix contains a duplicate or unsupported pair"
            )
        identities.add((host, task))
        if frozen_identity is not None:
            identity_digest = metrics.get("host_identity_sha256")
            if not isinstance(identity_digest, str) or _SHA256.fullmatch(identity_digest) is None:
                raise HostTaskQualificationError(
                    "Host task matrix lacks a bounded Host identity digest"
                )
            if identity_digest != host_identity_sha256(frozen_identity["hosts"][host]):
                raise HostTaskQualificationError(
                    "Host task matrix does not bind the owner-controlled identity"
                )
    expected = {(host, task) for host in HOSTS for task in TASK_CASES}
    if identities != expected:
        raise HostTaskQualificationError(
            "Host task matrix does not cover both Hosts and all three tasks"
        )
    return {
        "status": "derived",
        "result_count": len(results),
        "host_count": len(HOSTS),
        "task_count_per_host": len(TASK_CASES),
        "claim_eligible": False,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan or validate v0.13 Gate v9 Host task evidence."
    )
    parser.add_argument("--host", choices=HOSTS)
    parser.add_argument("--task-case", choices=TASK_CASES)
    parser.add_argument("--cases", type=Path, default=None)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--manifest", type=Path, default=None)
    operation.add_argument("--build-handoff", action="store_true")
    operation.add_argument("--validate-handoff", type=Path, default=None)
    operation.add_argument("--codex-zero-model-preflight", action="store_true")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--host-identity-input", type=Path, default=None)
    parser.add_argument("--candidate-binding-input", type=Path, default=None)
    parser.add_argument("--codex-binary", type=Path, default=None)
    parser.add_argument("--codex-broker", type=Path, default=None)
    parser.add_argument("--expected-codex-broker-sha256", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--evidence-run-id", type=int, default=None)
    parser.add_argument("--qualification-run-id", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        if args.codex_zero_model_preflight:
            required = {
                "--candidate-binding-input": args.candidate_binding_input,
                "--host-identity-input": args.host_identity_input,
                "--codex-binary": args.codex_binary,
                "--codex-broker": args.codex_broker,
                "--expected-codex-broker-sha256": args.expected_codex_broker_sha256,
                "--task-case": args.task_case,
                "--run-id": args.run_id,
                "--evidence-run-id": args.evidence_run_id,
                "--qualification-run-id": args.qualification_run_id,
            }
            missing = [name for name, item in required.items() if item is None]
            if missing:
                raise HostTaskQualificationError(
                    "Codex zero-model preflight is missing required control input"
                )
            result = run_codex_owner_external_zero_model_preflight(
                candidate_binding_input=args.candidate_binding_input,
                host_identity_input=args.host_identity_input,
                codex_binary=args.codex_binary,
                codex_broker=args.codex_broker,
                expected_broker_sha256=args.expected_codex_broker_sha256,
                task_case=args.task_case,
                run_id=args.run_id,
                evidence_run_id=args.evidence_run_id,
                qualification_run_id=args.qualification_run_id,
            )
        elif args.build_handoff:
            if args.host_identity_input is None:
                raise HostTaskQualificationError(
                    "--host-identity-input is required with --build-handoff"
                )
            result = build_external_collector_handoff(
                args.host_identity_input,
                cases=load_task_cases(args.cases) if args.cases is not None else None,
            )
        elif args.validate_handoff is not None:
            if args.host_identity_input is None:
                raise HostTaskQualificationError(
                    "--host-identity-input is required with --validate-handoff"
                )
            result = validate_external_collector_handoff(
                args.validate_handoff,
                host_identity_input=args.host_identity_input,
                cases=load_task_cases(args.cases) if args.cases is not None else None,
            )
        elif args.manifest is not None:
            if args.root is None:
                raise HostTaskQualificationError("--root is required with --manifest")
            result = validate_retained_manifest(
                args.manifest,
                root=args.root,
                host_identity_input=args.host_identity_input,
            )
        else:
            if args.host is None or args.task_case is None:
                raise HostTaskQualificationError("--host and --task-case are required for a plan")
            catalog = load_task_cases(args.cases)
            result = task_case(args.host, args.task_case, cases=catalog)
        print(_canonical(result))
        return 0
    except HostTaskQualificationError as exc:
        parser.error(str(exc))
        return 2


def main(argv: list[str] | None = None) -> int:
    return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CASES_RELATIVE_PATH",
    "CONTINUITY_LIFECYCLE",
    "CONTROL_RECEIPT_SLOTS",
    "DRIVER_KIND",
    "HANDOFF_SCHEMA_RELATIVE_PATH",
    "HOSTS",
    "TASK_CASES",
    "TYPED_SOURCE_SLOTS",
    "HostTaskQualificationError",
    "build_external_collector_handoff",
    "load_construction_candidate_binding",
    "load_exact_candidate_binding",
    "load_task_cases",
    "load_zero_model_candidate_binding",
    "main",
    "public_context_read",
    "public_source_read",
    "public_wiki_read",
    "run_codex_owner_external_zero_model_preflight",
    "task_case",
    "validate_external_collector_handoff",
    "validate_host_task_matrix",
    "validate_retained_manifest",
]
