"""Safe Host preflight receipts.

This module records only fail-before, typed Host admission observations.  The
external owner-only broker remains responsible for any credential handling and
for its own process receipt after the Host exits; this runner never fabricates
that external process receipt and never reads authentication material.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, SchemaError, ValidationError

SCHEMA_VERSION = "deeplaw.host-preflight-receipt/v1"
SCHEMA_FILENAME = "host-preflight-receipt.v1.schema.json"
RECEIPT_FILENAME = "host-preflight-receipt.json"

REASON_CODES = frozenset(
    {
        "preflight_passed",
        "broker_missing",
        "broker_not_regular",
        "broker_hash_mismatch",
        "host_binary_mismatch",
        "auth_unavailable",
        "model_unavailable",
        "transport_start_failed",
        "mcp_not_advertised",
        "usage_receipt_missing",
        "preflight_internal_error",
    }
)
_REASON_STAGES = {
    "preflight_passed": "complete",
    "broker_missing": "broker",
    "broker_not_regular": "broker",
    "broker_hash_mismatch": "broker",
    "host_binary_mismatch": "host_binary",
    "auth_unavailable": "auth",
    "model_unavailable": "model",
    "transport_start_failed": "transport",
    "mcp_not_advertised": "mcp",
    "usage_receipt_missing": "usage",
    "preflight_internal_error": "preflight",
}
# These are code-owned messages emitted by the two Host qualification runners.
# Do not broaden this table to arbitrary Provider or process text: an unknown
# message must remain ``preflight_internal_error``.
_EXACT_REASON_MESSAGES = {
    "codex credential broker launcher must be absolute": "broker_not_regular",
    "codex credential broker launcher is unavailable": "broker_missing",
    "codex credential broker launcher is not owner-only": "broker_not_regular",
    "codex credential broker launcher must be outside the repository": "broker_not_regular",
    "codex credential broker launcher is not process-separated": "host_binary_mismatch",
    "codex credential broker launcher hash mismatch": "broker_hash_mismatch",
    "opencode credential broker launcher must be absolute": "broker_not_regular",
    "opencode credential broker launcher is unavailable": "broker_missing",
    "opencode credential broker launcher is not owner-only": "broker_not_regular",
    "opencode credential broker launcher must be outside the repository": "broker_not_regular",
    "opencode credential broker launcher is not process-separated": "host_binary_mismatch",
    "opencode credential broker launcher hash mismatch": "broker_hash_mismatch",
    "codex binary must be an absolute path": "host_binary_mismatch",
    "codex binary is unavailable": "host_binary_mismatch",
    "codex binary must be a regular executable": "host_binary_mismatch",
    "opencode binary is unavailable": "host_binary_mismatch",
    "codex version preflight failed": "host_binary_mismatch",
    "opencode version is not exactly 1.18.16": "host_binary_mismatch",
    "codex login status failed to start": "auth_unavailable",
    "codex existing login was not confirmed": "auth_unavailable",
    "selected model was absent from model/list": "model_unavailable",
    "selected model is not present in the exact model inventory": "model_unavailable",
    "deepseek model availability probe failed": "model_unavailable",
    "deepseek availability process did not complete cleanly": "model_unavailable",
    "codex mcp inventory failed to start": "transport_start_failed",
    "codex app server failed to start": "transport_start_failed",
    "opencode local server exited before readiness": "transport_start_failed",
    "opencode local server readiness timed out": "transport_start_failed",
    "codex mcp inventory failed": "mcp_not_advertised",
    "codex mcp inventory was empty": "mcp_not_advertised",
    "mcp inventory was empty": "mcp_not_advertised",
    "knowledge_support tools/list observation failed": "mcp_not_advertised",
    "knowledge_support tools/list receipt is missing": "mcp_not_advertised",
    "mcp status exposed an unexpected tool or server": "mcp_not_advertised",
    "actual codex provider token usage is missing": "usage_receipt_missing",
    "opencode token usage is missing": "usage_receipt_missing",
    "actual opencode provider token usage is missing": "usage_receipt_missing",
    "availability usage receipt is missing": "usage_receipt_missing",
}
_PREFIX_REASON_MESSAGES = (
    ("mcp status exposed an unexpected tool", "mcp_not_advertised"),
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH = re.compile(
    rb'(?:^|[\s=:\"\'])/(?!/)[A-Za-z0-9._~-]+(?:/[^\s\"\'\\]*)?|'
    rb'(?:^|[\s=\"\'(])[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._$-]+[\\/]'
)
_FORBIDDEN_KEY = re.compile(
    r"(?:path|argv|stdout|stderr|prompt|transcript|reasoning|secret|auth|credential|token)",
    re.IGNORECASE,
)


class ReceiptValidationError(ValueError):
    """A preflight receipt is not safe or does not match the strict schema."""


_STAT_FIELDS = ("st_ino", "st_size", "st_mode", "st_uid", "st_nlink")


def _stat_signature(details: os.stat_result) -> tuple[Any, ...]:
    """Return the platform-available identity and mutation fields."""

    return (
        *(getattr(details, field, None) for field in _STAT_FIELDS),
        getattr(details, "st_mtime_ns", getattr(details, "st_mtime", None)),
        getattr(details, "st_ctime_ns", getattr(details, "st_ctime", None)),
    )


def _path_stat_signature(path: Path) -> tuple[Any, ...] | None:
    try:
        return _stat_signature(path.lstat())
    except OSError:
        return None


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "contracts" / SCHEMA_FILENAME


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReceiptValidationError("receipt is not canonical JSON") from exc


def _sha256_file_with_bytes(path: Path) -> tuple[str, int]:
    """Hash exact file bytes and return the number of bytes actually read."""

    digest = hashlib.sha256()
    byte_count = 0
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    return digest.hexdigest(), byte_count


def sha256_file(path: Path) -> str:
    """Hash exact regular-file bytes without retaining the source path."""

    return _sha256_file_with_bytes(path)[0]


def _safe_source_defaults(*, expected_sha256: str | None) -> dict[str, Any]:
    return {
        "source_kind": "repository_external_launcher",
        "repository_external": False,
        "sha256": None,
        "bytes": 0,
        "owner_only_mode": False,
        "expected_sha256": expected_sha256,
    }


def inspect_broker_source(
    path: Path,
    *,
    repository: Path,
    host_binary: Path | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Return a path-free broker source observation and typed failure code.

    The hash is computed from the exact external launcher bytes.  Missing,
    symlinked, non-regular, non-owner-only, repository-contained, and
    expected-hash-mismatching sources are represented by a closed code; no
    exception text or path is returned.
    """

    if expected_sha256 is not None and _SHA256.fullmatch(expected_sha256) is None:
        expected_sha256 = None
        invalid_expected = True
    else:
        invalid_expected = False
    result = _safe_source_defaults(expected_sha256=expected_sha256)
    source = Path(path)
    if not source.is_absolute():
        result["failure_reason_code"] = "broker_not_regular"
        return result
    repository_path = Path(repository).resolve(strict=False)
    try:
        details = source.lstat()
    except FileNotFoundError:
        result["failure_reason_code"] = "broker_missing"
        return result
    except OSError:
        result["failure_reason_code"] = "broker_not_regular"
        return result

    try:
        resolved = source.resolve(strict=True)
        result["repository_external"] = not (
            resolved == repository_path or repository_path in resolved.parents
        )
    except (OSError, RuntimeError, ValueError):
        result["failure_reason_code"] = "broker_not_regular"
        return result

    mode = stat.S_IMODE(details.st_mode)
    owner_only = os.name == "nt" or not (mode & 0o077)
    owner_uid = os.name == "nt" or not hasattr(os, "geteuid") or details.st_uid == os.geteuid()
    result["owner_only_mode"] = bool(owner_only and owner_uid)
    if (
        not stat.S_ISREG(details.st_mode)
        or source.is_symlink()
        or details.st_nlink != 1
        or not os.access(source, os.X_OK)
        or not result["repository_external"]
        or not result["owner_only_mode"]
    ):
        result["failure_reason_code"] = "broker_not_regular"
        return result

    before_read = _path_stat_signature(source)
    if before_read is None or before_read != _stat_signature(details):
        result["failure_reason_code"] = "broker_not_regular"
        return result
    try:
        observed_hash, observed_bytes = _sha256_file_with_bytes(source)
    except OSError:
        result["failure_reason_code"] = "broker_not_regular"
        result["sha256"] = None
        result["bytes"] = 0
        return result
    after_read = _path_stat_signature(source)
    if after_read is None or after_read != before_read:
        result["failure_reason_code"] = "broker_not_regular"
        result["sha256"] = None
        result["bytes"] = 0
        return result
    result["sha256"] = observed_hash
    result["bytes"] = observed_bytes

    if host_binary is not None:
        try:
            if result["sha256"] == sha256_file(Path(host_binary)):
                result["failure_reason_code"] = "host_binary_mismatch"
                return result
        except OSError:
            pass
    if invalid_expected or (
        expected_sha256 is not None and result["sha256"] != expected_sha256
    ):
        result["failure_reason_code"] = "broker_hash_mismatch"
        return result
    result["failure_reason_code"] = None
    return result


def host_binary_sha256(path: Path) -> str | None:
    """Hash a safe regular Host binary, or return null for an unavailable one."""

    try:
        details = Path(path).lstat()
        if not stat.S_ISREG(details.st_mode) or Path(path).is_symlink():
            return None
        return sha256_file(Path(path))
    except OSError:
        return None


def stage_for_reason(reason_code: str) -> str:
    return _REASON_STAGES.get(reason_code, "preflight")


def reason_code_for_exception(error: BaseException) -> str:
    """Map bounded known failures to closed codes; never retain free text."""

    explicit = getattr(error, "preflight_reason_code", None)
    if explicit is not None:
        return (
            explicit
            if isinstance(explicit, str) and explicit in REASON_CODES
            else "preflight_internal_error"
        )
    try:
        message = str(error).strip().casefold()
    except Exception:
        return "preflight_internal_error"
    if message in _EXACT_REASON_MESSAGES:
        return _EXACT_REASON_MESSAGES[message]
    for prefix, reason_code in _PREFIX_REASON_MESSAGES:
        if message.startswith(prefix):
            return reason_code
    return "preflight_internal_error"


def _scan_safe(value: Any, *, key: str | None = None) -> None:
    if key is not None and _FORBIDDEN_KEY.search(key):
        raise ReceiptValidationError("receipt contains a forbidden field")
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise ReceiptValidationError("receipt field name is invalid")
            _scan_safe(nested_value, key=nested_key)
    elif isinstance(value, list):
        for item in value:
            _scan_safe(item)
    elif isinstance(value, str) and _ABSOLUTE_PATH.search(value.encode("utf-8")):
        raise ReceiptValidationError("receipt contains an absolute path")


def build_receipt(
    *,
    host: Mapping[str, Any],
    broker_source: Mapping[str, Any],
    status: str,
    stage: str,
    reason_code: str,
    check_count: int = 0,
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    """Build the only retained Host preflight shape."""

    selected_reason = reason_code if reason_code in REASON_CODES else "preflight_internal_error"
    selected_stage = stage if stage_for_reason(selected_reason) == stage else stage_for_reason(
        selected_reason
    )
    value = {
        "schema_version": SCHEMA_VERSION,
        "host": {
            "name": host.get("name"),
            "version": host.get("version") or "unknown",
            "sha256": host.get("sha256"),
        },
        "broker_source": {
            "source_kind": broker_source.get("source_kind", "repository_external_launcher"),
            "repository_external": bool(broker_source.get("repository_external", False)),
            "sha256": broker_source.get("sha256"),
            "bytes": broker_source.get("bytes", 0),
            "owner_only_mode": bool(broker_source.get("owner_only_mode", False)),
            "expected_sha256": broker_source.get("expected_sha256"),
        },
        "status": status,
        "stage": selected_stage,
        "reason_code": selected_reason,
        "observed": {
            "check_count": check_count,
            "elapsed_ms": elapsed_ms,
        },
    }
    validate_receipt(value)
    return value


def validate_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate strict schema and reject unsafe fields before persistence."""

    if not isinstance(value, Mapping):
        raise ReceiptValidationError("receipt must be an object")
    _scan_safe(value)
    try:
        schema = json.loads(_schema_path().read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(dict(value))
    except (OSError, UnicodeError, TypeError, ValueError, SchemaError, ValidationError) as exc:
        raise ReceiptValidationError("receipt schema validation failed") from exc
    return dict(value)


def write_receipt(
    output_dir: Path,
    value: Mapping[str, Any],
    *,
    filename: str = RECEIPT_FILENAME,
) -> Path:
    """Atomically retain one path-free receipt outside the temporary Host root."""

    validate_receipt(value)
    root = Path(output_dir)
    if root.is_symlink() or not root.is_dir():
        raise ReceiptValidationError("receipt output directory is unavailable")
    if filename != RECEIPT_FILENAME:
        raise ReceiptValidationError("receipt filename is invalid")
    target = root / filename
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise ReceiptValidationError("receipt target is not regular")
        validate_receipt(json.loads(target.read_text(encoding="utf-8")))
        return target
    temporary = root / f".{filename}.tmp"
    temporary.write_bytes(_canonical(value) + b"\n")
    os.replace(temporary, target)
    return target


def failed_receipt(
    *,
    host_name: str,
    host_version: str,
    host_binary: Path,
    broker_path: Path,
    repository: Path,
    expected_broker_sha256: str | None = None,
    error: BaseException | None = None,
    check_count: int = 1,
) -> dict[str, Any]:
    """Construct a safe failure receipt without serializing the exception."""

    broker = inspect_broker_source(
        broker_path,
        repository=repository,
        host_binary=host_binary,
        expected_sha256=expected_broker_sha256,
    )
    reason = (
        str(broker.get("failure_reason_code"))
        if broker.get("failure_reason_code") in REASON_CODES
        else reason_code_for_exception(error or RuntimeError())
    )
    return build_receipt(
        host={
            "name": host_name,
            "version": host_version,
            "sha256": host_binary_sha256(host_binary),
        },
        broker_source=broker,
        status="failed",
        stage=stage_for_reason(reason),
        reason_code=reason,
        check_count=check_count,
    )
