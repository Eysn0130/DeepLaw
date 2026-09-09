"""Freeze and reopen one exact owner-external Kernel evidence collector.

The retained descriptor proves only byte identity inside the qualification
workflow.  It does not establish third-party provenance or observation
Authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "deeplaw.kernel-evidence-collector-identity/v1"
ARTIFACT_KIND = "kernel_evidence_collector_identity"
MAX_COLLECTOR_BYTES = 1024 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_LITERAL = re.compile(
    rb"(?i)(?:api[_-]?key|access[_-]?token|authorization|bearer|password|secret)"
    rb"\s*[:=]\s*[\"']?[A-Za-z0-9+/_-]{20,}"
)
_IDENTITY_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "candidate_run_id",
        "evidence_run_id",
        "source_sha256",
        "source_byte_size",
        "frozen_sha256",
        "frozen_byte_size",
        "repository_external",
        "source_regular",
        "source_single_link",
        "source_owner_only",
        "frozen_private",
        "frozen_non_writable",
        "frozen_executable",
        "formal_authority",
        "record_sha256",
    }
)


class OwnerExternalCollectorError(ValueError):
    """Raised when collector bytes or their path-free identity are unsafe."""


def _fail(message: str) -> None:
    raise OwnerExternalCollectorError(message)


def canonical_json(value: Any) -> bytes:
    """Return canonical UTF-8 JSON bytes."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise OwnerExternalCollectorError("collector identity is not canonical JSON") from error


def record_sha256(value: Mapping[str, Any]) -> str:
    """Hash one descriptor with its self-digest excluded."""

    if not isinstance(value, Mapping):
        _fail("collector identity must be an object")
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def _strict_json(raw: bytes) -> dict[str, Any]:
    def reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                _fail("collector identity contains a duplicate field")
            value[key] = item
        return value

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_pairs,
            parse_constant=lambda _: _fail("collector identity contains a non-finite value"),
        )
    except OwnerExternalCollectorError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OwnerExternalCollectorError("collector identity is not strict JSON") from error
    if not isinstance(value, dict):
        _fail("collector identity must be an object")
    return value


def _require_posix() -> None:
    if os.name != "posix" or not hasattr(os, "geteuid"):
        _fail("collector freezing requires the formal POSIX runner")


def _parent_chain_has_symlink(path: Path) -> bool:
    current = path.parent
    while True:
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except OSError as error:
            raise OwnerExternalCollectorError(
                "collector parent path is unavailable"
            ) from error
        if current == current.parent:
            return False
        current = current.parent


def _signature(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _external_path(path: Path | str, *, repository: Path, label: str) -> Path:
    selected = Path(path)
    if not selected.is_absolute():
        _fail(f"{label} must be absolute")
    if _parent_chain_has_symlink(selected):
        _fail(f"{label} parent path contains a symlink")
    try:
        resolved = selected.resolve(strict=True)
        repository_root = repository.resolve(strict=True)
    except OSError as error:
        raise OwnerExternalCollectorError(f"{label} is unavailable") from error
    if resolved == repository_root or repository_root in resolved.parents:
        _fail(f"{label} must be repository-external")
    return selected


def _read_stable_source(
    path: Path | str,
    *,
    repository: Path,
    expected_sha256: str,
) -> tuple[bytes, str]:
    _require_posix()
    if not isinstance(expected_sha256, str) or _SHA256.fullmatch(expected_sha256) is None:
        _fail("collector expected SHA-256 is invalid")
    selected = _external_path(path, repository=repository, label="collector source")
    try:
        before = selected.lstat()
    except OSError as error:
        raise OwnerExternalCollectorError("collector source is unavailable") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        _fail("collector source must be a regular non-symlink file")
    if before.st_nlink != 1:
        _fail("collector source must be a single-link file")
    if not 1 <= before.st_size <= MAX_COLLECTOR_BYTES:
        _fail("collector source exceeds its byte bound")
    if before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) & 0o077:
        _fail("collector source must be owner-only")
    if not before.st_mode & stat.S_IXUSR:
        _fail("collector source must be owner-executable")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = -1
    raw = bytearray()
    try:
        descriptor = os.open(selected, flags)
        fd_before = os.fstat(descriptor)
        if _signature(fd_before) != _signature(before):
            _fail("collector source changed before stable read")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_COLLECTOR_BYTES:
                _fail("collector source exceeds its byte bound")
        fd_after = os.fstat(descriptor)
    except OwnerExternalCollectorError:
        raise
    except OSError as error:
        raise OwnerExternalCollectorError("collector source is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        after = selected.lstat()
    except OSError as error:
        raise OwnerExternalCollectorError("collector source changed during stable read") from error
    if (
        _parent_chain_has_symlink(selected)
        or _signature(before) != _signature(after)
        or _signature(fd_before) != _signature(fd_after)
        or _signature(fd_after) != _signature(after)
        or len(raw) != before.st_size
    ):
        _fail("collector source changed during stable read")
    observed = hashlib.sha256(raw).hexdigest()
    if observed != expected_sha256:
        _fail("collector source SHA-256 differs from the owner input")
    if b"\x00" in raw:
        _fail("collector source must be UTF-8 source text")
    try:
        raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise OwnerExternalCollectorError(
            "collector source must be UTF-8 source text"
        ) from error
    if _SECRET_LITERAL.search(raw):
        _fail("collector source contains a credential literal")
    return bytes(raw), observed


def _private_parent(path: Path, *, repository: Path) -> Path:
    parent = _external_path(path.parent, repository=repository, label="collector output parent")
    try:
        details = parent.lstat()
    except OSError as error:
        raise OwnerExternalCollectorError("collector output parent is unavailable") from error
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        _fail("collector output parent must be an owner-only directory")
    return parent


def _write_exclusive(path: Path, raw: bytes, *, final_mode: int) -> None:
    if path.exists() or path.is_symlink():
        _fail("collector frozen output already exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fchmod(descriptor, final_mode)
        os.fsync(descriptor)
    except OSError as error:
        raise OwnerExternalCollectorError("collector frozen output could not be written") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_frozen(path: Path | str, *, repository: Path) -> tuple[bytes, str]:
    selected = _external_path(path, repository=repository, label="frozen collector")
    try:
        details = selected.lstat()
    except OSError as error:
        raise OwnerExternalCollectorError("frozen collector is unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o500
        or not 1 <= details.st_size <= MAX_COLLECTOR_BYTES
    ):
        _fail("frozen collector is not a private non-writable executable")
    return _read_stable_frozen(selected, before=details)


def _read_stable_frozen(selected: Path, *, before: os.stat_result) -> tuple[bytes, str]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = -1
    raw = bytearray()
    try:
        descriptor = os.open(selected, flags)
        fd_before = os.fstat(descriptor)
        if _signature(fd_before) != _signature(before):
            _fail("frozen collector changed before stable read")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_COLLECTOR_BYTES:
                _fail("frozen collector exceeds its byte bound")
        fd_after = os.fstat(descriptor)
    except OwnerExternalCollectorError:
        raise
    except OSError as error:
        raise OwnerExternalCollectorError("frozen collector is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        after = selected.lstat()
    except OSError as error:
        raise OwnerExternalCollectorError("frozen collector changed during stable read") from error
    if (
        _parent_chain_has_symlink(selected)
        or _signature(before) != _signature(after)
        or _signature(fd_before) != _signature(fd_after)
        or _signature(fd_after) != _signature(after)
        or len(raw) != before.st_size
    ):
        _fail("frozen collector changed during stable read")
    return bytes(raw), hashlib.sha256(raw).hexdigest()


def _identity_bytes(path: Path | str, *, repository: Path) -> bytes:
    selected = _external_path(path, repository=repository, label="collector identity")
    try:
        before = selected.lstat()
    except OSError as error:
        raise OwnerExternalCollectorError("collector identity is unavailable") from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) != 0o400
        or not 1 <= before.st_size <= 16 * 1024
    ):
        _fail("collector identity is not a private stable file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = -1
    raw = bytearray()
    try:
        descriptor = os.open(selected, flags)
        fd_before = os.fstat(descriptor)
        if _signature(fd_before) != _signature(before):
            _fail("collector identity changed before stable read")
        while True:
            chunk = os.read(descriptor, 16 * 1024)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > 16 * 1024:
                _fail("collector identity exceeds its byte bound")
        fd_after = os.fstat(descriptor)
    except OwnerExternalCollectorError:
        raise
    except OSError as error:
        raise OwnerExternalCollectorError("collector identity is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        after = selected.lstat()
    except OSError as error:
        raise OwnerExternalCollectorError(
            "collector identity changed during stable read"
        ) from error
    if (
        _parent_chain_has_symlink(selected)
        or _signature(before) != _signature(after)
        or _signature(fd_before) != _signature(fd_after)
        or _signature(fd_after) != _signature(after)
        or len(raw) != before.st_size
    ):
        _fail("collector identity changed during stable read")
    return bytes(raw)


def validate_identity(
    value: Mapping[str, Any],
    *,
    candidate_run_id: int,
    evidence_run_id: int,
    frozen_raw: bytes,
) -> dict[str, Any]:
    """Validate one path-free descriptor against exact frozen bytes."""

    if not isinstance(value, Mapping) or set(value) != _IDENTITY_FIELDS:
        _fail("collector identity fields are not closed")
    if any(
        type(value.get(field)) is not int
        for field in (
            "candidate_run_id",
            "evidence_run_id",
            "source_byte_size",
            "frozen_byte_size",
        )
    ):
        _fail("collector identity integer fields are invalid")
    if not isinstance(frozen_raw, bytes) or not 1 <= len(frozen_raw) <= MAX_COLLECTOR_BYTES:
        _fail("collector frozen bytes are invalid")
    if b"\x00" in frozen_raw:
        _fail("collector frozen bytes must be UTF-8 source text")
    try:
        frozen_raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise OwnerExternalCollectorError(
            "collector frozen bytes must be UTF-8 source text"
        ) from error
    if _SECRET_LITERAL.search(frozen_raw):
        _fail("collector frozen bytes contain a credential literal")
    digest = hashlib.sha256(frozen_raw).hexdigest()
    size = len(frozen_raw)
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_kind") != ARTIFACT_KIND
        or value.get("candidate_run_id") != candidate_run_id
        or value.get("evidence_run_id") != evidence_run_id
        or value.get("source_sha256") != digest
        or value.get("frozen_sha256") != digest
        or value.get("source_byte_size") != size
        or value.get("frozen_byte_size") != size
        or value.get("repository_external") is not True
        or value.get("source_regular") is not True
        or value.get("source_single_link") is not True
        or value.get("source_owner_only") is not True
        or value.get("frozen_private") is not True
        or value.get("frozen_non_writable") is not True
        or value.get("frozen_executable") is not True
        or value.get("formal_authority") is not False
        or value.get("record_sha256") != record_sha256(value)
    ):
        _fail("collector identity differs from exact frozen bytes or run binding")
    return dict(value)


def freeze_collector(
    source: Path | str,
    frozen_output: Path | str,
    identity_output: Path | str,
    *,
    expected_sha256: str,
    candidate_run_id: int,
    evidence_run_id: int,
    repository: Path,
) -> dict[str, Any]:
    """Freeze exact source bytes and create one path-free identity descriptor."""

    _require_posix()
    if any(type(value) is not int or value < 1 for value in (candidate_run_id, evidence_run_id)):
        _fail("collector run binding is invalid")
    frozen = Path(frozen_output)
    identity = Path(identity_output)
    if frozen.parent != identity.parent:
        _fail("collector frozen output and identity must share one private parent")
    _private_parent(frozen, repository=repository)
    raw, observed = _read_stable_source(
        source,
        repository=repository,
        expected_sha256=expected_sha256,
    )
    _write_exclusive(frozen, raw, final_mode=0o500)
    frozen_raw, frozen_sha256 = _read_frozen(frozen, repository=repository)
    if frozen_raw != raw or frozen_sha256 != observed:
        _fail("frozen collector bytes differ from the verified source")
    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "candidate_run_id": candidate_run_id,
        "evidence_run_id": evidence_run_id,
        "source_sha256": observed,
        "source_byte_size": len(raw),
        "frozen_sha256": frozen_sha256,
        "frozen_byte_size": len(frozen_raw),
        "repository_external": True,
        "source_regular": True,
        "source_single_link": True,
        "source_owner_only": True,
        "frozen_private": True,
        "frozen_non_writable": True,
        "frozen_executable": True,
        "formal_authority": False,
    }
    value["record_sha256"] = record_sha256(value)
    _write_exclusive(identity, canonical_json(value) + b"\n", final_mode=0o400)
    return validate_frozen_collector(
        frozen,
        identity,
        candidate_run_id=candidate_run_id,
        evidence_run_id=evidence_run_id,
        repository=repository,
    )


def validate_frozen_collector(
    frozen_input: Path | str,
    identity_input: Path | str,
    *,
    candidate_run_id: int,
    evidence_run_id: int,
    repository: Path,
) -> dict[str, Any]:
    """Reopen exact frozen bytes and their path-free descriptor."""

    if any(type(value) is not int or value < 1 for value in (candidate_run_id, evidence_run_id)):
        _fail("collector run binding is invalid")
    frozen_raw, _ = _read_frozen(frozen_input, repository=repository)
    value = _strict_json(_identity_bytes(identity_input, repository=repository))
    return validate_identity(
        value,
        candidate_run_id=candidate_run_id,
        evidence_run_id=evidence_run_id,
        frozen_raw=frozen_raw,
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--source", type=Path, required=True)
    freeze.add_argument("--expected-sha256", required=True)
    freeze.add_argument("--frozen-output", type=Path, required=True)
    freeze.add_argument("--identity-output", type=Path, required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--frozen-input", type=Path, required=True)
    validate.add_argument("--identity-input", type=Path, required=True)
    for command in (freeze, validate):
        command.add_argument("--candidate-run-id", type=int, required=True)
        command.add_argument("--evidence-run-id", type=int, required=True)
        command.add_argument("--repository", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.operation == "freeze":
        freeze_collector(
            args.source,
            args.frozen_output,
            args.identity_output,
            expected_sha256=args.expected_sha256,
            candidate_run_id=args.candidate_run_id,
            evidence_run_id=args.evidence_run_id,
            repository=args.repository,
        )
    else:
        validate_frozen_collector(
            args.frozen_input,
            args.identity_input,
            candidate_run_id=args.candidate_run_id,
            evidence_run_id=args.evidence_run_id,
            repository=args.repository,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except OwnerExternalCollectorError as error:
        raise SystemExit(f"owner-external collector rejected: {error}") from error


__all__ = [
    "ARTIFACT_KIND",
    "MAX_COLLECTOR_BYTES",
    "SCHEMA_VERSION",
    "OwnerExternalCollectorError",
    "canonical_json",
    "freeze_collector",
    "record_sha256",
    "validate_frozen_collector",
    "validate_identity",
]
