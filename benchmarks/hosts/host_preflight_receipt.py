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
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, SchemaError, ValidationError

SCHEMA_VERSION = "deeplaw.host-preflight-receipt/v1"
SCHEMA_FILENAME = "host-preflight-receipt.v1.schema.json"
RECEIPT_FILENAME = "host-preflight-receipt.json"
HOST_IDENTITY_SCHEMA_VERSION = "deeplaw.host-exact-identity/v1"
HOST_IDENTITY_MAX_BYTES = 64 * 1024
HOST_IDENTITY_FILENAME = "host-exact-identity.json"

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
_IDENTITY_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+:-]{0,99}$")
_IDENTITY_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY_GIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTITY_FORBIDDEN_TEXT = re.compile(
    r"(?:^|[\s=:\"'])/(?:Users|home|root|private|tmp|var|etc|opt|workspace|Volumes|System|Library|bin|sbin|usr|dev|proc|sys|run|mnt)(?:/|[\s\"']|$)|"
    r"(?:^|[\s=:\"'])(?:[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/])",
)
_IDENTITY_FORBIDDEN_FILENAME = re.compile(
    r"(?:^|[._-])(?:auth|credential|secret|password|passwd|api[_-]?key|"
    r"private[_-]?key|token|prompt|transcript|reasoning)(?:$|[._-])",
    re.IGNORECASE,
)
_IDENTITY_STAT_FIELDS = ("st_ino", "st_size", "st_mode", "st_uid", "st_nlink")


class HostIdentityValidationError(ValueError):
    """A repository-external frozen Host identity is unsafe or malformed."""


def _identity_fail(message: str) -> None:
    raise HostIdentityValidationError(message)


def _identity_reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _identity_fail("Host identity input contains a duplicate key")
        value[key] = item
    return value


def _identity_reject_constant(value: str) -> Any:
    _identity_fail(f"Host identity input contains a non-finite value: {value}")


def _identity_canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise HostIdentityValidationError("Host identity input is not canonical JSON") from error


def _identity_sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _IDENTITY_SHA256.fullmatch(value) is None:
        _identity_fail(f"{label} must be a lowercase SHA-256 digest")
    if value == "0" * 64:
        _identity_fail(f"{label} must identify a supplied artifact")
    return value


def _identity_version(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _IDENTITY_VERSION.fullmatch(value) is None:
        _identity_fail(f"{label} has an invalid version shape")
    return value


def _identity_scan(value: Any, *, key: str | None = None) -> None:
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                _identity_fail("Host identity field name is invalid")
            # ``auth_material_access`` is a closed policy fact, not auth data.
            allowed_policy_key = nested_key in {
                "auth_material_access",
                "secret_visibility",
                "auth_status_command",
            }
            forbidden_parts = (
                "path", "argv", "stdout", "stderr", "prompt", "transcript",
                "reasoning_content", "secret", "credential", "token",
            )
            if not allowed_policy_key and any(
                part in nested_key.casefold() for part in forbidden_parts
            ):
                _identity_fail("Host identity input contains a forbidden field")
            _identity_scan(nested_value, key=nested_key)
    elif isinstance(value, list):
        for item in value:
            _identity_scan(item)
    elif isinstance(value, str):
        if _IDENTITY_FORBIDDEN_TEXT.search(value):
            _identity_fail("Host identity input contains an absolute path")
    elif value is None or isinstance(value, (bool, int, float)):
        return
    else:
        _identity_fail("Host identity input contains an unsupported value")


def _validate_host_identity_document(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"schema_version", "hosts"}:
        _identity_fail("Host identity input fields are not closed")
    if value.get("schema_version") != HOST_IDENTITY_SCHEMA_VERSION:
        _identity_fail("Host identity input schema version is unsupported")
    hosts = value.get("hosts")
    if not isinstance(hosts, Mapping) or set(hosts) != {"codex", "opencode"}:
        _identity_fail("Host identity input must contain exactly Codex and OpenCode")

    codex = hosts.get("codex")
    if not isinstance(codex, Mapping) or set(codex) != {
        "binary_version",
        "binary_sha256",
        "request_model",
        "reasoning_effort",
        "auth_status_command",
        "auth_material_access",
    }:
        _identity_fail("Codex Host identity fields are not closed")
    codex_version = _identity_version(codex.get("binary_version"), label="Codex binary_version")
    codex_sha = _identity_sha(codex.get("binary_sha256"), label="Codex binary_sha256")
    if codex.get("request_model") != "gpt-5.6-luna":
        _identity_fail("Codex request model is not the fixed qualification model")
    if codex.get("reasoning_effort") != "max":
        _identity_fail("Codex reasoning effort is not fixed to max")
    if codex.get("auth_status_command") != "codex login status":
        _identity_fail("Codex authentication status seam is not fixed")
    if codex.get("auth_material_access") != "forbidden":
        _identity_fail("Codex authentication material policy is not forbidden")

    opencode = hosts.get("opencode")
    if not isinstance(opencode, Mapping) or set(opencode) != {
        "version",
        "source_commit",
        "config_selector",
        "expected_response_model_id",
        "executable_sha256",
        "package_sha256",
        "runtime",
        "dotenv_policy",
        "secret_visibility",
    }:
        _identity_fail("OpenCode Host identity fields are not closed")
    opencode_version = _identity_version(opencode.get("version"), label="OpenCode version")
    source_commit = opencode.get("source_commit")
    if not isinstance(source_commit, str) or _IDENTITY_GIT.fullmatch(source_commit) is None:
        _identity_fail("OpenCode source_commit must be an exact Git digest")
    executable_sha = _identity_sha(
        opencode.get("executable_sha256"), label="OpenCode executable_sha256"
    )
    package_sha = _identity_sha(opencode.get("package_sha256"), label="OpenCode package_sha256")
    if opencode.get("config_selector") != "deepseek/deepseek-v4-flash":
        _identity_fail("OpenCode selector is not fixed")
    if opencode.get("expected_response_model_id") != "deepseek-v4-flash":
        _identity_fail("OpenCode response model is not fixed")
    if opencode.get("runtime") != "host_bun_runtime_only":
        _identity_fail("OpenCode runtime policy is not fixed")
    if opencode.get("dotenv_policy") != "owner_only_external_strict_parser":
        _identity_fail("OpenCode dotenv policy is not fixed")
    if opencode.get("secret_visibility") != "forbidden":
        _identity_fail("OpenCode Secret visibility policy is not forbidden")

    normalized = {
        "schema_version": HOST_IDENTITY_SCHEMA_VERSION,
        "hosts": {
            "codex": {
                "binary_version": codex_version,
                "binary_sha256": codex_sha,
                "request_model": "gpt-5.6-luna",
                "reasoning_effort": "max",
                "auth_status_command": "codex login status",
                "auth_material_access": "forbidden",
            },
            "opencode": {
                "version": opencode_version,
                "source_commit": source_commit,
                "config_selector": "deepseek/deepseek-v4-flash",
                "expected_response_model_id": "deepseek-v4-flash",
                "executable_sha256": executable_sha,
                "package_sha256": package_sha,
                "runtime": "host_bun_runtime_only",
                "dotenv_policy": "owner_only_external_strict_parser",
                "secret_visibility": "forbidden",
            },
        },
    }
    _identity_scan(normalized)
    return normalized


def parse_host_identity_input(raw: bytes) -> dict[str, Any]:
    """Validate exact external identity bytes without retaining their source path."""

    if not isinstance(raw, bytes) or not 1 <= len(raw) <= HOST_IDENTITY_MAX_BYTES:
        _identity_fail("Host identity input exceeds its byte bound")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_identity_reject_pairs,
            parse_constant=_identity_reject_constant,
        )
    except HostIdentityValidationError:
        raise
    except (UnicodeError, TypeError, ValueError) as error:
        raise HostIdentityValidationError("Host identity input is not strict UTF-8 JSON") from error
    if not isinstance(value, Mapping):
        _identity_fail("Host identity input must be an object")
    return _validate_host_identity_document(value)


def host_identity_sha256(identity: Mapping[str, Any]) -> str:
    """Hash one normalized per-Host identity object, excluding source metadata."""

    return hashlib.sha256(_identity_canonical(identity)).hexdigest()


def host_identity_source_sha256(raw: bytes) -> str:
    """Hash the exact frozen input bytes."""

    return hashlib.sha256(raw).hexdigest()


def host_binary_identity(identity: Mapping[str, Any], host: str) -> dict[str, str]:
    """Project a frozen Host identity to the sanitized receipt binary shape."""

    hosts = identity.get("hosts")
    if not isinstance(hosts, Mapping) or host not in hosts:
        _identity_fail("Host identity is missing the requested Host")
    item = hosts[host]
    if not isinstance(item, Mapping):
        _identity_fail("Host identity is invalid")
    if host == "codex":
        return {
            "version": str(item["binary_version"]),
            "sha256": str(item["binary_sha256"]),
        }
    return {
        "version": str(item["version"]),
        "sha256": str(item["executable_sha256"]),
    }


def _identity_path_has_symlink(path: Path) -> bool:
    selected = Path(path).expanduser()
    current = Path(selected.anchor) if selected.is_absolute() else Path.cwd()
    parts = selected.parts[1:] if selected.is_absolute() else selected.parts
    for part in parts:
        current /= part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _identity_stat_signature(details: os.stat_result) -> tuple[Any, ...]:
    """Keep mutation-relevant metadata without treating atime as a mutation."""

    return (
        *(getattr(details, field, None) for field in _IDENTITY_STAT_FIELDS),
        getattr(details, "st_mtime_ns", getattr(details, "st_mtime", None)),
        getattr(details, "st_ctime_ns", getattr(details, "st_ctime", None)),
    )


def _read_identity_file(
    path: Path,
    *,
    repository: Path,
    require_external: bool,
) -> tuple[dict[str, Any], bytes]:
    selected = Path(path).expanduser()
    if not selected.is_absolute():
        _identity_fail("Host identity input must be an absolute path")
    if _identity_path_has_symlink(selected) or _IDENTITY_FORBIDDEN_FILENAME.search(selected.name):
        _identity_fail("Host identity input must be a regular non-symlink file")
    try:
        before = selected.lstat()
    except OSError as error:
        raise HostIdentityValidationError("Host identity input is unavailable") from error
    if not stat.S_ISREG(before.st_mode) or selected.is_symlink() or before.st_nlink != 1:
        _identity_fail("Host identity input must be a regular non-symlink file")
    if os.name != "nt" and (
        not hasattr(os, "geteuid")
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) & 0o077
    ):
        _identity_fail("Host identity input must be owner-only")
    try:
        resolved = selected.resolve(strict=True)
        repository_path = Path(repository).resolve(strict=True)
        inside_repository = resolved == repository_path or repository_path in resolved.parents
        if require_external and inside_repository:
            _identity_fail("Host identity input must be repository-external")
        if not 1 <= before.st_size <= HOST_IDENTITY_MAX_BYTES:
            _identity_fail("Host identity input exceeds its byte bound")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(selected, flags)
        try:
            fd_before = os.fstat(descriptor)
            if _identity_stat_signature(fd_before) != _identity_stat_signature(before):
                _identity_fail("Host identity input changed before it was read")
            chunks: list[bytes] = []
            total = 0
            while total <= HOST_IDENTITY_MAX_BYTES:
                chunk = os.read(descriptor, HOST_IDENTITY_MAX_BYTES + 1 - total)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            raw = b"".join(chunks)
            fd_after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = selected.lstat()
    except HostIdentityValidationError:
        raise
    except OSError as error:
        raise HostIdentityValidationError("Host identity input is unavailable") from error
    if (
        _identity_stat_signature(fd_before) != _identity_stat_signature(fd_after)
        or _identity_stat_signature(before) != _identity_stat_signature(after)
        or len(raw) != before.st_size
    ):
        _identity_fail("Host identity input changed while it was read")
    return parse_host_identity_input(raw), raw


def load_host_identity_input(
    path: Path | str,
    *,
    repository: Path | str,
    require_external: bool = True,
) -> dict[str, Any]:
    """Load an owner-only frozen Host identity and return path-free metadata."""

    identity, raw = _read_identity_file(
        Path(path), repository=Path(repository), require_external=require_external
    )
    return {
        "schema_version": HOST_IDENTITY_SCHEMA_VERSION,
        "hosts": identity["hosts"],
        "source_sha256": host_identity_source_sha256(raw),
        "source_bytes": len(raw),
    }


def load_host_identity_input_with_bytes(
    path: Path | str,
    *,
    repository: Path | str,
) -> tuple[dict[str, Any], bytes]:
    """Load an owner-only frozen identity and return its exact input bytes."""

    identity, raw = _read_identity_file(
        Path(path), repository=Path(repository), require_external=True
    )
    return (
        {
            "schema_version": HOST_IDENTITY_SCHEMA_VERSION,
            "hosts": identity["hosts"],
            "source_sha256": host_identity_source_sha256(raw),
            "source_bytes": len(raw),
        },
        raw,
    )


def load_host_identity_bytes(raw: bytes) -> dict[str, Any]:
    """Load retained identity bytes after they have entered a bundle root."""

    identity = parse_host_identity_input(raw)
    return {
        "schema_version": HOST_IDENTITY_SCHEMA_VERSION,
        "hosts": identity["hosts"],
        "source_sha256": host_identity_source_sha256(raw),
        "source_bytes": len(raw),
    }


def inspect_host_binary(
    path: Path | str,
    *,
    host: str,
    identity: Mapping[str, Any],
    repository: Path | str,
) -> dict[str, Any]:
    """Check a Host executable against frozen identity without retaining its path.

    Codex requires the selected path itself to be a regular, non-symlink,
    single-link executable.  OpenCode may be entered through a selector
    symlink, but its resolved execution target must satisfy the same regular
    single-link boundary.
    """

    if host not in {"codex", "opencode"}:
        _identity_fail("Host executable identity is unsupported")
    selected = Path(path).expanduser()
    if not selected.is_absolute() or _identity_path_has_symlink(selected.parent):
        _identity_fail("Host executable path is outside the closed scope")
    try:
        source_stat = selected.lstat()
    except OSError as error:
        raise HostIdentityValidationError("Host executable is unavailable") from error
    source_symlink = stat.S_ISLNK(source_stat.st_mode)
    if host == "codex" and source_symlink:
        _identity_fail("Codex executable must not be a symlink")

    # OpenCode may use one selector symlink, but the selector's direct target
    # must itself be a regular file.  Resolving the selector first would
    # silently accept a symlink chain, so inspect the direct link target before
    # resolving it.
    direct_target = selected
    if source_symlink:
        try:
            link_target = selected.readlink()
        except OSError as error:
            raise HostIdentityValidationError("Host executable selector is unavailable") from error
        direct_target = link_target if link_target.is_absolute() else selected.parent / link_target
    if _identity_path_has_symlink(direct_target.parent):
        _identity_fail("Host executable parent path contains a symlink")
    try:
        direct_stat = direct_target.lstat()
    except OSError as error:
        raise HostIdentityValidationError("Host executable is unavailable") from error
    if source_symlink and stat.S_ISLNK(direct_stat.st_mode):
        _identity_fail("Host executable selector must not be a symlink chain")
    try:
        resolved = direct_target.resolve(strict=True)
        target_stat = resolved.lstat()
    except OSError as error:
        raise HostIdentityValidationError("Host executable is unavailable") from error
    if (
        stat.S_ISLNK(target_stat.st_mode)
        or not stat.S_ISREG(target_stat.st_mode)
        or target_stat.st_nlink != 1
    ):
        _identity_fail("Host executable must be a regular single-link file")
    if not os.access(resolved, os.X_OK):
        _identity_fail("Host executable is not executable")
    repository_path = Path(repository).resolve(strict=True)
    try:
        resolved.relative_to(repository_path)
    except ValueError:
        pass
    else:
        _identity_fail("Host executable must be repository-external")
    expected = host_binary_identity(identity, host)

    def mutation_snapshot() -> tuple[Any, ...]:
        if _identity_path_has_symlink(
            selected.parent
        ) or _identity_path_has_symlink(direct_target.parent):
            _identity_fail("Host executable parent path contains a symlink")
        try:
            selected_details = selected.lstat()
            direct_details = direct_target.lstat()
            resolved_details = resolved.lstat()
            selector_target = os.readlink(selected) if source_symlink else None
        except OSError as error:
            raise HostIdentityValidationError(
                "Host executable changed during inspection"
            ) from error
        return (
            _identity_stat_signature(selected_details),
            _identity_stat_signature(direct_details),
            _identity_stat_signature(resolved_details),
            selector_target,
        )

    before_probe = mutation_snapshot()
    try:
        observed_sha = sha256_file(resolved)
    except OSError as error:
        raise HostIdentityValidationError("Host executable hash probe failed") from error
    after_hash = mutation_snapshot()
    if after_hash != before_probe:
        _identity_fail("Host executable changed during hash probe")
    if observed_sha != expected["sha256"]:
        _identity_fail("Host executable hash differs from frozen identity")
    try:
        completed = subprocess.run(
            [str(resolved), "--version"],
            capture_output=True,
            check=False,
            timeout=30,
            env={"PATH": os.defpath, "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise HostIdentityValidationError("Host executable version probe failed") from error
    after_version = mutation_snapshot()
    if after_version != before_probe:
        _identity_fail("Host executable changed during version probe")
    stdout = completed.stdout if isinstance(completed.stdout, bytes) else b""
    stderr = completed.stderr if isinstance(completed.stderr, bytes) else b""
    observed_version = (stdout + stderr).decode("utf-8", errors="strict").strip()
    if completed.returncode != 0 or observed_version != expected["version"]:
        _identity_fail("Host executable version differs from frozen identity")
    return {
        "host": host,
        "version": observed_version,
        "sha256": observed_sha,
        "source_symlink": source_symlink,
        "selector_source_symlink": source_symlink,
        "execution_target_regular": True,
        "execution_target_single_link": True,
        "repository_external": True,
        "host_identity_sha256": host_identity_sha256(identity["hosts"][host]),
        "host_identity_source_sha256": str(identity["source_sha256"]),
    }


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
