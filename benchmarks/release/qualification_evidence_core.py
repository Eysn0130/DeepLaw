"""Shared fail-closed primitives for versioned qualification evidence.

Version-specific assemblers and validators own their public contracts and error
types.  This module owns only the byte, JSON, digest, and path invariants that
must not drift between those version adapters.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any


def sha256_bytes(raw: bytes) -> str:
    """Return the lowercase SHA-256 digest of exact bytes."""

    return hashlib.sha256(raw).hexdigest()


def canonical_json_bytes(
    value: Any,
    *,
    error_type: type[Exception],
    message: str = "value is not canonical JSON",
) -> bytes:
    """Encode one value with the common evidence canonicalization."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise error_type(message) from error


def digest_without(
    value: Mapping[str, Any],
    *,
    field: str,
    error_type: type[Exception],
) -> str:
    """Hash canonical object bytes after removing one self-digest field."""

    return sha256_bytes(
        canonical_json_bytes(
            {key: item for key, item in value.items() if key != field},
            error_type=error_type,
        )
    )


def strict_json_bytes(
    raw: bytes,
    *,
    label: str,
    error_type: type[Exception],
    projection: Callable[[Any], None] | None = None,
    require_object: bool = False,
) -> Any:
    """Decode bounded caller-provided bytes without duplicate/non-finite JSON."""

    if not isinstance(raw, bytes) or not raw:
        raise error_type(f"{label} is empty")

    def reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise error_type(f"{label} contains duplicate JSON keys")
            result[key] = item
        return result

    def reject_constant(_value: str) -> Any:
        raise error_type(f"{label} contains a non-finite JSON number")

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise error_type(f"{label} contains a non-finite JSON number")
        return number

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_pairs,
            parse_constant=reject_constant,
            parse_float=finite_float,
        )
    except error_type:
        raise
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise error_type(f"{label} must be strict UTF-8 JSON") from error
    if require_object and not isinstance(value, dict):
        raise error_type(f"{label} must be a JSON object")
    if projection is not None:
        projection(value)
    return value


def require_exact_protocol_gate_ids(
    protocol: Mapping[str, Any],
    *,
    expected_gate_ids: list[str] | tuple[str, ...],
    error_type: type[Exception],
) -> list[str]:
    """Require one duplicate-free protocol Gate for every classified Core Gate."""

    gates = protocol.get("gates")
    if not isinstance(gates, list):
        raise error_type("qualification protocol Gate inventory is missing")
    gate_ids: list[str] = []
    for gate in gates:
        if not isinstance(gate, Mapping) or not isinstance(gate.get("gate_id"), str):
            raise error_type("qualification protocol Gate inventory is invalid")
        gate_ids.append(gate["gate_id"])
    if (
        len(gate_ids) != len(set(gate_ids))
        or len(gate_ids) != len(expected_gate_ids)
        or set(gate_ids) != set(expected_gate_ids)
    ):
        raise error_type("qualification protocol Gate inventory differs from classification")
    return gate_ids


def has_symlink_component(path: Path) -> bool:
    """Return true when any existing lexical component is a symlink."""

    selected = path.expanduser()
    parts = selected.parts
    start = 1 if selected.is_absolute() else 0
    for index in range(start, len(parts) + 1):
        try:
            if Path(*parts[:index]).is_symlink():
                return True
        except OSError:
            return True
    return False


def regular_file_bytes(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    error_type: type[Exception],
) -> tuple[Path, bytes]:
    """Read one exact regular non-symlink file with a stable size."""

    selected = path.expanduser()
    if has_symlink_component(selected) or selected.is_symlink():
        raise error_type(f"{label} must be a regular non-symlink file")
    try:
        resolved = selected.resolve(strict=True)
        mode = os.lstat(resolved).st_mode
        if not stat.S_ISREG(mode):
            raise error_type(f"{label} must be a regular non-symlink file")
        size = os.stat(resolved).st_size
        if not 1 <= size <= max_bytes:
            raise error_type(f"{label} exceeds its byte bound")
        raw = resolved.read_bytes()
    except error_type:
        raise
    except OSError as error:
        raise error_type(f"{label} is unavailable") from error
    if len(raw) != size:
        raise error_type(f"{label} changed while it was read")
    return resolved, raw


def safe_relative_posix(
    value: Any,
    *,
    label: str,
    error_type: type[Exception],
    max_length: int = 512,
) -> str:
    """Validate a normalized relative POSIX evidence path."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > max_length
        or "\\" in value
        or "\x00" in value
        or "//" in value
    ):
        raise error_type(f"{label} is not a safe relative path")
    parsed = PurePosixPath(value)
    if (
        parsed.is_absolute()
        or not parsed.parts
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or ":" in parsed.parts[0]
        or parsed.as_posix() != value
    ):
        raise error_type(f"{label} is not a safe relative path")
    return value


def safe_root_directory(
    path: Path,
    *,
    label: str,
    error_type: type[Exception],
) -> Path:
    """Resolve one regular non-symlink directory."""

    selected = path.expanduser()
    if has_symlink_component(selected) or selected.is_symlink():
        raise error_type(f"{label} must be a regular non-symlink directory")
    try:
        resolved = selected.resolve(strict=True)
        if resolved.is_symlink() or not resolved.is_dir():
            raise error_type(f"{label} must be a regular directory")
    except error_type:
        raise
    except OSError as error:
        raise error_type(f"{label} is unavailable") from error
    return resolved


def safe_asset_file(
    root: Path,
    relative: Any,
    *,
    label: str,
    error_type: type[Exception],
) -> Path:
    """Resolve one regular file below an already trusted root."""

    name = safe_relative_posix(
        relative,
        label=f"{label} path",
        error_type=error_type,
    )
    selected = root.joinpath(*name.split("/"))
    cursor = root
    try:
        for part in name.split("/"):
            cursor /= part
            if cursor.is_symlink():
                raise error_type(f"{label} resolves through a symbolic link")
        resolved = selected.resolve(strict=True)
    except error_type:
        raise
    except OSError as error:
        raise error_type(f"{label} is unavailable") from error
    if selected.is_symlink() or not selected.is_file() or not resolved.is_relative_to(root):
        raise error_type(f"{label} is outside its root")
    return selected
