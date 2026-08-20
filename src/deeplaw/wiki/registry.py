"""Additive, governed Living Wiki page registry.

The registry is deliberately independent from the existing projection writer.  It accepts
already-governed page records and an already-verified v2 file inventory; it never derives an
identity from a file name, title, frontmatter, or path.
"""

from __future__ import annotations

import ntpath
import os
import re
import stat
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

from ..knowledge_models import canonical_timestamp
from ..util import canonical_json, sha256_bytes, strict_json_loads

PAGE_REGISTRY_SCHEMA = "deeplaw.living-wiki-page-registry/v1"
LIVING_WIKI_MANIFEST_V3_SCHEMA = "deeplaw.living-wiki-manifest/v3"
SHARD_RECORD_LIMIT = 2_000
SHARD_BYTE_LIMIT = 256 * 1024
MANIFEST_BYTE_LIMIT = 1 * 1024 * 1024
PUBLIC_RECORD_LIMIT = 200_000
PUBLIC_EDGE_LIMIT = 2_000_000
PUBLIC_SHARD_COUNT_LIMIT = 200_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,255}$")
_PATH = re.compile(r"^[^/][^\x00]*\.md$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_AUDIT_ID = re.compile(r"^[0-9a-f]{64}$")
_PAGE_KINDS = frozenset(
    {
        "aggregate",
        "concept",
        "claim",
        "comparison",
        "decision",
        "entity",
        "event",
        "experience",
        "memory",
        "preference",
        "procedure",
        "skill",
        "source",
        "synthesis",
        "system",
    }
)
_NAMESPACES = frozenset({"knowledge", "aggregate", "source", "system", "legal"})
_SCOPES = frozenset({"personal", "project", "domain"})
_SENSITIVITIES = frozenset({"public", "internal", "private", "restricted"})
_LIFECYCLES = frozenset(
    {"active", "superseded", "revoked", "expired", "forgotten", "archived", "quarantined"}
)
_FRESHNESS = frozenset({"fresh", "stale", "unknown", "invalidated"})


class RegistryError(ValueError):
    """Raised when a registry artifact is invalid or cannot be admitted."""


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise RegistryError(f"{field} must be lowercase SHA-256")
    return value


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise RegistryError(f"{field} must be a bounded stable identity")
    return value


def _path(value: Any, field: str = "canonical_page_path") -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 2_000:
        raise RegistryError(f"{field} must be a bounded relative path")
    value = value.replace("\\", "/")
    if _CONTROL.search(value):
        raise RegistryError(f"{field} contains control characters")
    parts = value.split("/")
    if (
        not _PATH.fullmatch(value)
        or any(part in {"", ".", ".."} for part in parts)
        or value.startswith("/")
        or "//" in value
    ):
        raise RegistryError(f"{field} must be a safe relative Markdown path")
    return PurePosixPath(*parts).as_posix()


def _human_text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise RegistryError(f"{field} is out of bounds")
    if _CONTROL.search(value):
        raise RegistryError(f"{field} contains control characters")
    normalized = value.strip()
    if not normalized:
        raise RegistryError(f"{field} must not be blank")
    return normalized


def _timestamp(value: Any) -> str:
    try:
        return canonical_timestamp(value, field="generated_at")
    except ValueError as error:
        raise RegistryError(str(error)) from error


def _validated_timestamp(value: Any) -> str:
    canonical = _timestamp(value)
    if canonical != value:
        raise RegistryError("generated_at must use canonical UTC timestamp")
    return canonical


def _validate_v3_configuration(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RegistryError("v3 configuration must be an object")
    expected = {
        "profile",
        "generator",
        "generator_version",
        "shard_record_limit",
        "shard_byte_limit",
    }
    if set(value) != expected:
        raise RegistryError("v3 configuration shape is invalid")
    profile = value["profile"]
    if not isinstance(profile, str) or not 1 <= len(profile) <= 128:
        raise RegistryError("v3 profile is invalid")
    if (
        not isinstance(value["generator"], str)
        or value["generator"] != "deeplaw.living-wiki-indexer/3"
        or not isinstance(value["generator_version"], str)
        or value["generator_version"] != "3"
        or not isinstance(value["shard_record_limit"], int)
        or isinstance(value["shard_record_limit"], bool)
        or value["shard_record_limit"] != SHARD_RECORD_LIMIT
        or not isinstance(value["shard_byte_limit"], int)
        or isinstance(value["shard_byte_limit"], bool)
        or value["shard_byte_limit"] != SHARD_BYTE_LIMIT
    ):
        raise RegistryError("v3 configuration is invalid")
    return dict(value)


def _canonical_digest(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _as_records(value: Any, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise RegistryError(f"{field} must be an array")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise RegistryError(f"{field} records must be objects")
        records.append(dict(item))
    return records


def _windows_path_key(value: str) -> str:
    """Return a case-insensitive Windows path key for handle/path comparisons.

    ``GetFinalPathNameByHandleW`` normally returns a ``\\?\\``-prefixed path while the
    path passed to ``CreateFileW`` may be a regular DOS or UNC path.  Strip only that
    namespace prefix and normalize separators/casing; no filesystem resolution occurs.
    """

    path = value.replace("/", "\\")
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    return ntpath.normcase(ntpath.normpath(path))


def _windows_native_api() -> tuple[Any, Any, Any, Any]:
    """Load the small Win32 API surface used by the safe reader lazily.

    Keeping this import and binding Windows-only means the POSIX descriptor path remains
    unchanged and importing DeepLaw on POSIX never requires a Windows runtime.
    """

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.GetFileSizeEx.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_longlong),
    ]
    kernel32.GetFileSizeEx.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL

    class _FileAttributeTagInfo(ctypes.Structure):
        _fields_ = (
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        )

    return ctypes, wintypes, kernel32, _FileAttributeTagInfo


def _windows_handle_value(handle: Any, ctypes: Any) -> int | None:
    if handle is None:
        return None
    value = handle if isinstance(handle, int) else getattr(handle, "value", None)
    if value is None or value == ctypes.c_void_p(-1).value:
        return None
    return int(value)


def _windows_open_handle(
    kernel32: Any,
    ctypes: Any,
    path: str,
    *,
    directory: bool,
    field: str,
) -> Any:
    generic_read = 0x80000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    flags = file_flag_open_reparse_point
    if directory:
        flags |= file_flag_backup_semantics
    handle = kernel32.CreateFileW(
        path,
        generic_read,
        share_all,
        None,
        open_existing,
        flags,
        None,
    )
    if _windows_handle_value(handle, ctypes) is None:
        raise RegistryError(f"{field} cannot be opened safely")
    return handle


def _windows_close_handle(kernel32: Any, ctypes: Any, handle: Any) -> None:
    if _windows_handle_value(handle, ctypes) is not None:
        with suppress(OSError):
            kernel32.CloseHandle(handle)


def _windows_file_attributes(
    kernel32: Any,
    ctypes: Any,
    info_type: Any,
    handle: Any,
    *,
    field: str,
) -> tuple[int, int]:
    info = info_type()
    # FileAttributeTagInfo (9) returns attributes and the reparse tag without resolving
    # the opened object.  The OPEN_REPARSE_POINT flag above therefore lets us reject the
    # reparse object itself instead of accidentally inspecting its target.
    if not kernel32.GetFileInformationByHandleEx(
        handle,
        9,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise RegistryError(f"{field} file attributes are unavailable")
    return int(info.FileAttributes), int(info.ReparseTag)


def _windows_final_path(
    kernel32: Any,
    ctypes: Any,
    handle: Any,
    *,
    field: str,
) -> str:
    size = 512
    while size <= 32_768:
        buffer = ctypes.create_unicode_buffer(size)
        length = int(kernel32.GetFinalPathNameByHandleW(handle, buffer, size, 0))
        if length == 0:
            raise RegistryError(f"{field} final path is unavailable")
        if length < size:
            return buffer.value
        size = max(size * 2, length + 1)
    raise RegistryError(f"{field} final path is too long")


def _windows_read_handle(
    kernel32: Any,
    ctypes: Any,
    wintypes: Any,
    handle: Any,
    *,
    max_bytes: int,
    field: str,
) -> bytes:
    size = ctypes.c_longlong()
    if not kernel32.GetFileSizeEx(handle, ctypes.byref(size)):
        raise RegistryError(f"{field} size is unavailable")
    if size.value < 0 or size.value > max_bytes:
        raise RegistryError(f"{field} exceeds its byte bound")

    chunks: list[bytes] = []
    total = 0
    block_size = min(1024 * 1024, max_bytes + 1)
    buffer = ctypes.create_string_buffer(max(1, block_size))
    while total <= max_bytes:
        request = min(block_size, max_bytes + 1 - total)
        if request <= 0:
            break
        count = wintypes.DWORD()
        if not kernel32.ReadFile(
            handle,
            buffer,
            request,
            ctypes.byref(count),
            None,
        ):
            raise RegistryError(f"{field} cannot be read safely")
        read = int(count.value)
        if read == 0:
            break
        chunks.append(bytes(buffer.raw[:read]))
        total += read
        if total > max_bytes:
            raise RegistryError(f"{field} exceeds its byte bound")
    return b"".join(chunks)


def _safe_read_file_windows(root: Path, relative: str, *, max_bytes: int, field: str) -> bytes:
    ctypes, wintypes, kernel32, info_type = _windows_native_api()
    root_path = ntpath.abspath(os.fspath(root))
    root_handle = _windows_open_handle(
        kernel32,
        ctypes,
        root_path,
        directory=True,
        field=field,
    )
    try:
        root_attributes, _ = _windows_file_attributes(
            kernel32,
            ctypes,
            info_type,
            root_handle,
            field=field,
        )
        if not root_attributes & 0x00000010:
            raise RegistryError(f"{field} root is not a directory")
        if root_attributes & 0x00000400:
            raise RegistryError(f"{field} root is a reparse point")
        root_key = _windows_path_key(
            _windows_final_path(kernel32, ctypes, root_handle, field=field)
        )

        # Open every declared ancestor with OPEN_REPARSE_POINT as a directory.  This rejects
        # static symlink/junction/reparse ancestors before the final read; the final handle path
        # check below also closes the race where an ancestor is replaced while these checks run.
        for index in range(1, len(PurePosixPath(relative).parts)):
            parent_path = ntpath.join(root_path, *PurePosixPath(relative).parts[:index])
            parent_handle = _windows_open_handle(
                kernel32,
                ctypes,
                parent_path,
                directory=True,
                field=field,
            )
            try:
                attributes, _ = _windows_file_attributes(
                    kernel32,
                    ctypes,
                    info_type,
                    parent_handle,
                    field=field,
                )
                if not attributes & 0x00000010 or attributes & 0x00000400:
                    raise RegistryError(f"{field} parent is not a regular directory")
            finally:
                _windows_close_handle(kernel32, ctypes, parent_handle)

        target_path = ntpath.join(root_path, *PurePosixPath(relative).parts)
        target_handle = _windows_open_handle(
            kernel32,
            ctypes,
            target_path,
            directory=False,
            field=field,
        )
        try:
            attributes, _ = _windows_file_attributes(
                kernel32,
                ctypes,
                info_type,
                target_handle,
                field=field,
            )
            if attributes & 0x00000400:
                raise RegistryError(f"{field} is a reparse point")
            if attributes & 0x00000010:
                raise RegistryError(f"{field} is not a regular file")
            expected_key = ntpath.join(root_key, *PurePosixPath(relative).parts)
            actual_key = _windows_path_key(
                _windows_final_path(kernel32, ctypes, target_handle, field=field)
            )
            if actual_key != _windows_path_key(expected_key):
                raise RegistryError(f"{field} escaped its root")
            return _windows_read_handle(
                kernel32,
                ctypes,
                wintypes,
                target_handle,
                max_bytes=max_bytes,
                field=field,
            )
        finally:
            _windows_close_handle(kernel32, ctypes, target_handle)
    finally:
        _windows_close_handle(kernel32, ctypes, root_handle)


def _safe_read_file(root: Path, relative: str, *, max_bytes: int, field: str) -> bytes:
    """Read a manifest-declared regular file without following symlinks.

    On POSIX, every path component is opened relative to the already-open parent descriptor
    with ``O_NOFOLLOW``.  This keeps the check and the use of a parent directory in one kernel
    operation and avoids a path-based lstat/open race.  Windows uses native file handles opened
    with ``FILE_FLAG_OPEN_REPARSE_POINT`` and verifies the handle's final path before reading.
    Platforms without either native guarantee use a conservative no-symlink fallback and fail
    closed when that guarantee is unavailable.
    """

    if not isinstance(relative, str) or _CONTROL.search(relative):
        raise RegistryError(f"{field} path is unsafe")
    rel = PurePosixPath(relative)
    if (
        rel.is_absolute()
        or not rel.parts
        or any(part in {"", ".", ".."} for part in rel.parts)
        or len(relative) > 2_000
    ):
        raise RegistryError(f"{field} path is unsafe")

    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0:
        raise RegistryError(f"{field} byte bound is invalid")

    if os.name == "nt":
        if any(":" in part for part in rel.parts):
            raise RegistryError(f"{field} path is unsafe")
        return _safe_read_file_windows(root, relative, max_bytes=max_bytes, field=field)

    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    supports_dir_fd = os.name == "posix" and nofollow is not None and directory is not None
    if supports_dir_fd and hasattr(os, "supports_dir_fd"):
        supports_dir_fd = os.open in os.supports_dir_fd

    if supports_dir_fd:
        root_flags = os.O_RDONLY | directory | nofollow
        try:
            current_fd = os.open(os.fspath(Path(root)), root_flags)
        except OSError as error:
            raise RegistryError(f"{field} root is unavailable or unsafe") from error
        descriptors = [current_fd]
        try:
            for part in rel.parts[:-1]:
                try:
                    current_fd = os.open(
                        part,
                        os.O_RDONLY | directory | nofollow,
                        dir_fd=current_fd,
                    )
                except OSError as error:
                    raise RegistryError(f"{field} parent is unavailable or unsafe") from error
                descriptors.append(current_fd)
            try:
                descriptor = os.open(
                    rel.parts[-1],
                    os.O_RDONLY | nofollow,
                    dir_fd=current_fd,
                )
            except OSError as error:
                raise RegistryError(f"{field} cannot be opened safely") from error
            try:
                opened_stat = os.fstat(descriptor)
                if not stat.S_ISREG(opened_stat.st_mode):
                    raise RegistryError(f"{field} is not a regular file")
                if opened_stat.st_size > max_bytes:
                    raise RegistryError(f"{field} exceeds its byte bound")
                chunks: list[bytes] = []
                remaining = max_bytes + 1
                while remaining > 0:
                    block = os.read(descriptor, min(1024 * 1024, remaining))
                    if not block:
                        break
                    chunks.append(block)
                    remaining -= len(block)
                data = b"".join(chunks)
                if len(data) > max_bytes:
                    raise RegistryError(f"{field} exceeds its byte bound")
                return data
            finally:
                os.close(descriptor)
        finally:
            for descriptor in reversed(descriptors):
                with suppress(OSError):
                    os.close(descriptor)

    # A conservative fallback for platforms without openat-style descriptor traversal.  If
    # O_NOFOLLOW is unavailable, there is no portable way to guarantee the final path cannot be
    # swapped for a symlink between lstat and open, so reject the read rather than weakening the
    # trust boundary.
    if nofollow is None:
        raise RegistryError(f"{field} safe path traversal is unavailable")
    root_path = Path(root)
    try:
        root_stat = os.lstat(root_path)
    except OSError as error:
        raise RegistryError(f"{field} root is unavailable") from error
    if not stat.S_ISDIR(root_stat.st_mode):
        raise RegistryError(f"{field} root is not a directory")
    current = root_path
    for part in rel.parts[:-1]:
        current = current / part
        try:
            item_stat = os.lstat(current)
        except OSError as error:
            raise RegistryError(f"{field} parent is unavailable") from error
        if not stat.S_ISDIR(item_stat.st_mode) or stat.S_ISLNK(item_stat.st_mode):
            raise RegistryError(f"{field} parent is not a regular directory")
    target = current / rel.parts[-1]
    try:
        target_stat = os.lstat(target)
    except OSError as error:
        raise RegistryError(f"{field} is missing") from error
    if not stat.S_ISREG(target_stat.st_mode) or stat.S_ISLNK(target_stat.st_mode):
        raise RegistryError(f"{field} is not a regular file")
    if target_stat.st_size > max_bytes:
        raise RegistryError(f"{field} exceeds its byte bound")
    try:
        descriptor = os.open(target, os.O_RDONLY | nofollow)
    except OSError as error:
        raise RegistryError(f"{field} cannot be opened safely") from error
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_size > max_bytes:
            raise RegistryError(f"{field} changed to an unsafe file")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise RegistryError(f"{field} exceeds its byte bound")
        return data
    finally:
        os.close(descriptor)


def _validate_inventory(inventory: Any) -> list[dict[str, Any]]:
    """Validate the caller-provided and already-verified v2 file inventory.

    We still validate its closed shape and hashes at this seam.  Bytes are not read here: v2
    verification belongs to the v2 projection owner and the registry only receives its result.
    """

    rows = _as_records(inventory, field="v2_file_inventory")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if set(row) - {"path", "byte_size", "sha256"}:
            raise RegistryError("v2 file inventory contains unknown fields")
        path = row.get("path")
        if not isinstance(path, str) or not path or path in seen:
            raise RegistryError("v2 file inventory paths must be unique")
        if path.startswith("/") or ".." in PurePosixPath(path).parts or "\\" in path:
            raise RegistryError("v2 file inventory path is unsafe")
        size = row.get("byte_size")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or size > 256 * 1024 * 1024
        ):
            raise RegistryError("v2 file inventory byte_size is out of bounds")
        normalized.append(
            {"path": path, "byte_size": size, "sha256": _sha(row.get("sha256"), "v2 file sha256")}
        )
        seen.add(path)
    return sorted(normalized, key=lambda row: row["path"])


def _validate_source_fragment(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise RegistryError("source_fragment must be an object")
    if set(value) - {"source_revision_id", "fragment_id", "fragment_revision_id"}:
        raise RegistryError("source_fragment has unknown fields")
    source_revision_id = _id(value.get("source_revision_id"), "source_revision_id")
    fragment_id = value.get("fragment_id")
    fragment_revision_id = value.get("fragment_revision_id")
    if (fragment_id is None) == (fragment_revision_id is None):
        raise RegistryError("source_fragment requires exactly one fragment identity")
    result = {"source_revision_id": source_revision_id}
    if fragment_id is not None:
        result["fragment_id"] = _id(fragment_id, "fragment_id")
    if fragment_revision_id is not None:
        result["fragment_revision_id"] = _id(fragment_revision_id, "fragment_revision_id")
    return result


def _validate_statement_target(value: Any) -> str | dict[str, str]:
    """Validate the future statement target carried by a page anchor.

    The registry deliberately keeps this target opaque to the current resolver.  A bounded
    stable string is sufficient for the first integration, while the small object form leaves
    room for a statement id/key without allowing arbitrary payloads into the governed index.
    """

    if isinstance(value, str):
        return _id(value, "statement_target")
    if not isinstance(value, Mapping):
        raise RegistryError("statement_target must be a stable identity or object")
    if set(value) - {"statement_id", "statement_key", "semantic_key"}:
        raise RegistryError("statement_target has unknown fields")
    if len(value) != 1:
        raise RegistryError("statement_target requires exactly one identity")
    field, target = next(iter(value.items()))
    if field in {"statement_id", "statement_key"}:
        return {field: _id(target, field)}
    return {field: _human_text(target, field, maximum=512)}


def _validate_anchor(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RegistryError("page anchor must be an object")
    allowed = {"anchor_id", "anchor", "kind", "source_fragment", "statement_target"}
    if set(value) - allowed:
        raise RegistryError("page anchor contains unknown fields")
    if set(value) & {"source_fragment", "statement_target"} == set():
        raise RegistryError("page anchor requires a source_fragment or statement_target")
    if {"source_fragment", "statement_target"} <= set(value):
        raise RegistryError("page anchor target is ambiguous")
    result = {
        "anchor_id": _id(value.get("anchor_id"), "anchor_id"),
        "anchor": _human_text(value.get("anchor"), "anchor", maximum=512),
        "kind": _human_text(value.get("kind"), "anchor kind", maximum=128),
    }
    if "source_fragment" in value:
        result["source_fragment"] = _validate_source_fragment(value["source_fragment"])
    else:
        result["statement_target"] = _validate_statement_target(value["statement_target"])
    return result


def validate_page_record(page: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one caller-owned governed page record.

    ``page_id`` and ``canonical_page_path`` are both mandatory inputs.  In particular, this
    function never generates a page identity from a title, alias, frontmatter, or path.
    """

    if not isinstance(page, Mapping):
        raise RegistryError("page record must be an object")
    allowed = {
        "page_id",
        "namespace",
        "canonical_page_path",
        "kind",
        "revision_id",
        "source_fragment",
        "audit_head",
        "byte_size",
        "sha256",
        "scope",
        "sensitivity",
        "lifecycle",
        "freshness",
        "authority",
        "input_refs",
        "knowledge_id",
        "semantic_key",
        "aliases",
        "title",
        "projection_id",
        "anchors",
    }
    if set(page) - allowed:
        raise RegistryError("page record contains unknown fields")
    page_id = _id(page.get("page_id"), "page_id")
    namespace = page.get("namespace")
    if not isinstance(namespace, str) or namespace not in _NAMESPACES:
        raise RegistryError("page namespace is invalid")
    path = _path(page.get("canonical_page_path"))
    kind = page.get("kind")
    if not isinstance(kind, str) or kind not in _PAGE_KINDS:
        raise RegistryError("page kind is invalid")
    revision_id = page.get("revision_id")
    source_fragment = page.get("source_fragment")
    if (revision_id is None) == (source_fragment is None):
        raise RegistryError(
            "page requires exactly one current revision or source fragment identity"
        )
    result: dict[str, Any] = {
        "page_id": page_id,
        "namespace": namespace,
        "canonical_page_path": path,
        "kind": kind,
    }
    if revision_id is not None:
        result["revision_id"] = _id(revision_id, "revision_id")
    else:
        result["source_fragment"] = _validate_source_fragment(source_fragment)
    result["audit_head"] = _sha(page.get("audit_head"), "audit_head")
    size = page.get("byte_size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > SHARD_BYTE_LIMIT:
        raise RegistryError("page byte_size is out of bounds")
    result["byte_size"] = size
    result["sha256"] = _sha(page.get("sha256"), "page sha256")
    for field, allowed_values in (
        ("scope", _SCOPES),
        ("sensitivity", _SENSITIVITIES),
        ("lifecycle", _LIFECYCLES),
        ("freshness", _FRESHNESS),
    ):
        value = page.get(field)
        if not isinstance(value, str) or value not in allowed_values:
            raise RegistryError(f"page {field} is invalid")
        result[field] = value
    if page.get("authority") != "none":
        raise RegistryError("Living Wiki page registry cannot grant Authority")
    result["authority"] = "none"
    refs = page.get("input_refs")
    if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes, bytearray)):
        raise RegistryError("input_refs must be an array")
    if len(refs) > 256 or any(
        not isinstance(ref, str) or not ref or len(ref) > 512 for ref in refs
    ):
        raise RegistryError("input_refs are out of bounds")
    input_refs: set[str] = set()
    for ref in refs:
        normalized_ref = _human_text(ref, "input_ref", maximum=512)
        if normalized_ref != ref:
            raise RegistryError("input_refs must not have leading or trailing whitespace")
        input_refs.add(normalized_ref)
    result["input_refs"] = sorted(input_refs)
    anchors = page.get("anchors", [])
    if not isinstance(anchors, Sequence) or isinstance(anchors, (str, bytes, bytearray)):
        raise RegistryError("anchors must be an array")
    if len(anchors) > 256:
        raise RegistryError("anchors are out of bounds")
    normalized_anchors = [_validate_anchor(anchor) for anchor in anchors]
    anchor_ids = [anchor["anchor_id"] for anchor in normalized_anchors]
    if len(anchor_ids) != len(set(anchor_ids)):
        raise RegistryError("anchors must be unique")
    normalized_anchors.sort(key=lambda anchor: (anchor["anchor_id"], anchor["anchor"]))
    result["anchors"] = normalized_anchors
    for field in ("knowledge_id", "projection_id"):
        if field in page and page[field] is not None:
            result[field] = _id(page[field], field)
    if "semantic_key" in page and page["semantic_key"] is not None:
        result["semantic_key"] = _human_text(page["semantic_key"], "semantic_key", maximum=512)
    aliases = page.get("aliases", [])
    if not isinstance(aliases, Sequence) or isinstance(aliases, (str, bytes, bytearray)):
        raise RegistryError("aliases must be an array")
    if len(aliases) > 64:
        raise RegistryError("aliases are out of bounds")
    result["aliases"] = sorted(
        {_human_text(alias, "alias", maximum=512) for alias in aliases}
    )
    if "title" in page and page["title"] is not None:
        result["title"] = _human_text(page["title"], "title", maximum=512)
    return result


def _shard_records(
    records: Sequence[dict[str, Any]],
    *,
    prefix: str,
    item_key: str = "records",
    record_limit: int = PUBLIC_RECORD_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    shards: list[dict[str, Any]] = []
    payloads: dict[str, bytes] = {}
    if not records:
        return shards, payloads
    if not isinstance(record_limit, int) or isinstance(record_limit, bool) or record_limit < 1:
        raise RegistryError("record limit is invalid")
    if len(records) > record_limit:
        raise RegistryError("record count exceeds public limit")
    batch: list[dict[str, Any]] = []
    batch_bytes = 0
    index = 0

    item_prefix = b'{"' + item_key.encode("ascii") + b'":['
    schema_suffix = b'],"schema_version":' + canonical_json(
        _SCHEMA_FOR_PREFIX[prefix]
    ).encode("utf-8") + b"}"

    def body_size(record_bytes: int, count: int) -> int:
        return len(item_prefix) + record_bytes + max(0, count - 1) + len(schema_suffix)

    def flush(items: list[dict[str, Any]], shard_index: int, encoded_bytes: int) -> None:
        path = (
            f".deeplaw/derived/wiki/v3/{prefix}/{_PREFIX_FILENAME[prefix]}-{shard_index:05d}.json"
        )
        body = {"schema_version": _SCHEMA_FOR_PREFIX[prefix], item_key: items}
        data = canonical_json(body).encode("utf-8")
        if len(items) > SHARD_RECORD_LIMIT or len(data) > SHARD_BYTE_LIMIT:
            raise RegistryError("registry shard exceeds hard bounds")
        if body_size(encoded_bytes, len(items)) != len(data):
            raise RegistryError("registry shard byte accounting mismatch")
        payloads[path] = data
        shards.append(
            {
                "path": path,
                "byte_size": len(data),
                "sha256": sha256_bytes(data),
                "record_count": len(items),
                "records_sha256": _canonical_digest(items),
            }
        )

    for record in records:
        encoded_size = len(canonical_json(record).encode("utf-8"))
        if len(batch) >= SHARD_RECORD_LIMIT:
            flush(batch, index, batch_bytes)
            index += 1
            batch = []
            batch_bytes = 0
        if batch and body_size(batch_bytes + encoded_size, len(batch) + 1) > SHARD_BYTE_LIMIT:
            flush(batch, index, batch_bytes)
            index += 1
            batch = []
            batch_bytes = 0
        batch.append(record)
        batch_bytes += encoded_size
    flush(batch, index, batch_bytes)
    return shards, payloads


_SCHEMA_FOR_PREFIX = {
    "registry": PAGE_REGISTRY_SCHEMA,
    "links": "deeplaw.living-wiki-link-index/v1",
    "coverage": "deeplaw.living-wiki-link-index/v1",
}
_PREFIX_FILENAME = {"registry": "page", "links": "link", "coverage": "coverage"}


def build_page_registry(
    pages: Sequence[Mapping[str, Any]],
    *,
    v2_file_inventory: Sequence[Mapping[str, Any]],
    registered_page_inventory: Sequence[Mapping[str, Any]] = (),
    input_audit_head: str,
    legacy_audit_head: str,
    v2_manifest_sha256: str,
    generated_at: str,
) -> dict[str, Any]:
    """Build deterministic registry component and shard payloads.

    The returned mapping is intentionally serializable.  ``payloads`` is an in-memory path to
    bytes mapping for an owner that wants to materialize the already-validated shards; no loader
    scans a directory.
    """

    if len(pages) > PUBLIC_RECORD_LIMIT:
        raise RegistryError("page count exceeds public limit")
    _sha(input_audit_head, "input_audit_head")
    _sha(legacy_audit_head, "legacy_audit_head")
    _sha(v2_manifest_sha256, "v2_manifest_sha256")
    generated_at = _timestamp(generated_at)
    inventory = _validate_inventory(v2_file_inventory)
    registered_inventory = _validate_inventory(registered_page_inventory)
    generated_markdown_paths = {
        row["path"] for row in inventory if row["path"].endswith(".md")
    }
    registered_markdown_paths = {
        row["path"] for row in registered_inventory if row["path"].endswith(".md")
    }
    if len(registered_markdown_paths) != len(registered_inventory):
        raise RegistryError("registered page inventory must contain only Markdown")
    if generated_markdown_paths & registered_markdown_paths:
        raise RegistryError("generated and registered page inventories overlap")
    markdown_paths = generated_markdown_paths | registered_markdown_paths
    normalized = [validate_page_record(page) for page in pages]
    ids = [page["page_id"] for page in normalized]
    paths = [page["canonical_page_path"] for page in normalized]
    if len(ids) != len(set(ids)):
        raise RegistryError("duplicate page_id")
    if len(paths) != len(set(paths)):
        raise RegistryError("duplicate canonical_page_path")
    if set(paths) != markdown_paths:
        missing = sorted(markdown_paths - set(paths))
        extra = sorted(set(paths) - markdown_paths)
        raise RegistryError(
            f"page registry/v2 Markdown inventory mismatch: missing={missing[:3]} extra={extra[:3]}"
        )
    normalized.sort(key=lambda page: (page["page_id"], page["canonical_page_path"]))
    ids = [page["page_id"] for page in normalized]
    paths = [page["canonical_page_path"] for page in normalized]
    shards, payloads = _shard_records(normalized, prefix="registry")
    component_body: dict[str, Any] = {
        "schema_version": PAGE_REGISTRY_SCHEMA,
        "input_audit_head": input_audit_head,
        "legacy_audit_head": legacy_audit_head,
        "v2_manifest_sha256": v2_manifest_sha256,
        "generated_at": generated_at,
        "page_count": len(normalized),
        "anchor_count": sum(len(page.get("anchors", [])) for page in normalized),
        "anchors_sha256": _canonical_digest(
            [
                {"page_id": page["page_id"], "anchors": page.get("anchors", [])}
                for page in normalized
            ]
        ),
        "page_ids_sha256": _canonical_digest(ids),
        "path_count": len(paths),
        "paths_sha256": _canonical_digest(paths),
        "shard_count": len(shards),
        "shards": shards,
    }
    component_body["registry_sha256"] = _canonical_digest(component_body)
    manifest_path = ".deeplaw/derived/wiki/v3/registry/manifest.json"
    manifest_bytes = canonical_json(component_body).encode("utf-8")
    if len(manifest_bytes) > MANIFEST_BYTE_LIMIT:
        raise RegistryError("page registry manifest exceeds 1 MiB")
    payloads[manifest_path] = manifest_bytes
    return {
        "component": component_body,
        "manifest_path": manifest_path,
        "manifest_bytes": manifest_bytes,
        "payloads": payloads,
        "records": normalized,
        "registry_sha256": component_body["registry_sha256"],
        "valid": True,
    }


def _validate_shard_metadata(shards: Any, *, prefix: str) -> list[dict[str, Any]]:
    rows = _as_records(shards, field="shards")
    if len(rows) > PUBLIC_SHARD_COUNT_LIMIT:
        raise RegistryError("shard count exceeds public limit")
    paths: set[str] = set()
    previous = ""
    for row in rows:
        expected = re.compile(
            rf"^\.deeplaw/derived/wiki/v3/{prefix}/{_PREFIX_FILENAME[prefix]}-[0-9]{{5}}\.json$"
        )
        if set(row) != {"path", "byte_size", "sha256", "record_count", "records_sha256"}:
            raise RegistryError("shard metadata has unknown or missing fields")
        path = row["path"]
        if (
            not isinstance(path, str)
            or not expected.fullmatch(path)
            or path in paths
            or path <= previous
        ):
            raise RegistryError("shard paths must be sorted and unique")
        previous = path
        paths.add(path)
        size = row["byte_size"]
        count = row["record_count"]
        if not isinstance(size, int) or not 1 <= size <= SHARD_BYTE_LIMIT:
            raise RegistryError("shard byte bound is invalid")
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or not 1 <= count <= SHARD_RECORD_LIMIT
        ):
            raise RegistryError("shard record bound is invalid")
        _sha(row["sha256"], "shard sha256")
        _sha(row["records_sha256"], "records_sha256")
    return rows


def validate_page_registry_component(
    component: Mapping[str, Any], *, payloads: Mapping[str, bytes] | None = None
) -> dict[str, Any]:
    """Validate a registry component and optionally each exact shard payload."""

    if not isinstance(component, Mapping):
        raise RegistryError("page registry component must be an object")
    expected = {
        "schema_version",
        "input_audit_head",
        "legacy_audit_head",
        "v2_manifest_sha256",
        "generated_at",
        "page_count",
        "anchor_count",
        "anchors_sha256",
        "page_ids_sha256",
        "path_count",
        "paths_sha256",
        "shard_count",
        "shards",
        "registry_sha256",
    }
    if set(component) != expected or component.get("schema_version") != PAGE_REGISTRY_SCHEMA:
        raise RegistryError("invalid page registry component shape")
    _sha(component["input_audit_head"], "input_audit_head")
    _sha(component["legacy_audit_head"], "legacy_audit_head")
    _sha(component["v2_manifest_sha256"], "v2_manifest_sha256")
    _validated_timestamp(component["generated_at"])
    for field in ("page_ids_sha256", "paths_sha256", "registry_sha256"):
        _sha(component[field], field)
    count = component["page_count"]
    if (
        not isinstance(component["anchor_count"], int)
        or isinstance(component["anchor_count"], bool)
        or not 0 <= component["anchor_count"] <= PUBLIC_RECORD_LIMIT * 256
    ):
        raise RegistryError("anchor_count is invalid")
    _sha(component["anchors_sha256"], "anchors_sha256")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or not 0 <= count <= PUBLIC_RECORD_LIMIT
    ):
        raise RegistryError("page_count is invalid")
    shards = _as_records(component["shards"], field="shards")
    if (
        not isinstance(component["path_count"], int)
        or isinstance(component["path_count"], bool)
        or component["path_count"] != count
        or component["shard_count"] != len(shards)
    ):
        raise RegistryError("registry counts do not match")
    if (
        not isinstance(component["shard_count"], int)
        or isinstance(component["shard_count"], bool)
        or not 0 <= component["shard_count"] <= PUBLIC_SHARD_COUNT_LIMIT
    ):
        raise RegistryError("registry shard_count is invalid")
    shards = _validate_shard_metadata(shards, prefix="registry")
    body = {key: component[key] for key in expected if key != "registry_sha256"}
    if _canonical_digest(body) != component["registry_sha256"]:
        raise RegistryError("registry_sha256 mismatch")
    records: list[dict[str, Any]] = []
    if payloads is not None:
        for shard in shards:
            raw = payloads.get(shard["path"])
            if not isinstance(raw, (bytes, bytearray)) or len(raw) != shard["byte_size"]:
                raise RegistryError("registry shard is missing or has wrong size")
            if sha256_bytes(bytes(raw)) != shard["sha256"]:
                raise RegistryError("registry shard hash mismatch")
            decoded = strict_json_loads(raw)
            if not isinstance(decoded, Mapping) or set(decoded) != {"schema_version", "records"}:
                raise RegistryError("registry shard shape is invalid")
            if decoded["schema_version"] != PAGE_REGISTRY_SCHEMA:
                raise RegistryError("registry shard schema is invalid")
            shard_records = _as_records(decoded["records"], field="registry shard records")
            if (
                len(shard_records) != shard["record_count"]
                or _canonical_digest(shard_records) != shard["records_sha256"]
            ):
                raise RegistryError("registry shard count or digest mismatch")
            records.extend(validate_page_record(record) for record in shard_records)
    if payloads is not None:
        if len(records) != count or [row["page_id"] for row in records] != sorted(
            row["page_id"] for row in records
        ):
            raise RegistryError("registry records are not globally sorted or complete")
        if (
            len({row["page_id"] for row in records}) != count
            or len({row["canonical_page_path"] for row in records}) != count
        ):
            raise RegistryError("registry records contain duplicate identity or path")
        if _canonical_digest([row["page_id"] for row in records]) != component["page_ids_sha256"]:
            raise RegistryError("page_ids_sha256 mismatch")
        if (
            _canonical_digest([row["canonical_page_path"] for row in records])
            != component["paths_sha256"]
        ):
            raise RegistryError("paths_sha256 mismatch")
        if component["anchor_count"] != sum(len(row.get("anchors", [])) for row in records):
            raise RegistryError("anchor_count mismatch")
        if (
            _canonical_digest(
                [
                    {"page_id": row["page_id"], "anchors": row.get("anchors", [])}
                    for row in records
                ]
            )
            != component["anchors_sha256"]
        ):
            raise RegistryError("anchors_sha256 mismatch")
    return {"component": dict(component), "records": records, "valid": True}


def build_living_wiki_manifest_v3(
    *,
    input_audit_head: str,
    legacy_audit_head: str,
    generated_at: str,
    v2_manifest_sha256: str,
    configuration: Mapping[str, Any] | None = None,
    page_registry: Mapping[str, Any],
    link_index: Mapping[str, Any],
    resolver: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the additive v3 manifest and reject component/path ownership overlap."""

    _sha(input_audit_head, "input_audit_head")
    _sha(legacy_audit_head, "legacy_audit_head")
    _sha(v2_manifest_sha256, "v2_manifest_sha256")
    generated_at = _timestamp(generated_at)
    configuration = dict(configuration or {})
    configuration.setdefault("profile", "standard")
    configuration.setdefault("generator", "deeplaw.living-wiki-indexer/3")
    configuration.setdefault("generator_version", "3")
    configuration.setdefault("shard_record_limit", SHARD_RECORD_LIMIT)
    configuration.setdefault("shard_byte_limit", SHARD_BYTE_LIMIT)
    if (
        configuration["generator"] != "deeplaw.living-wiki-indexer/3"
        or configuration["generator_version"] != "3"
        or configuration["shard_record_limit"] != SHARD_RECORD_LIMIT
        or configuration["shard_byte_limit"] != SHARD_BYTE_LIMIT
    ):
        raise RegistryError("v3 configuration is invalid")
    _validate_v3_configuration(configuration)
    components = []
    seen_components: set[str] = set()
    owned_paths: set[str] = set()
    for name, artifact, schema, directory in (
        ("page_registry", page_registry, PAGE_REGISTRY_SCHEMA, "registry"),
        ("link_index", link_index, "deeplaw.living-wiki-link-index/v1", "links"),
        ("resolver", resolver, "deeplaw.living-wiki-resolver/v1", "resolver"),
    ):
        component = dict(artifact.get("component", artifact))
        if name in seen_components or component.get("schema_version") != schema:
            raise RegistryError("v3 component identity/schema mismatch")
        seen_components.add(name)
        manifest_path = f".deeplaw/derived/wiki/v3/{directory}/manifest.json"
        paths = [manifest_path]
        if any(path in owned_paths for path in paths):
            raise RegistryError("v3 component paths overlap")
        owned_paths.update(paths)
        if (
            component.get("input_audit_head") != input_audit_head
            or component.get("legacy_audit_head") != legacy_audit_head
        ):
            raise RegistryError("v3 component audit binding mismatch")
        if component.get("v2_manifest_sha256") != v2_manifest_sha256:
            raise RegistryError("v3 component v2 binding mismatch")
        manifest_bytes = artifact.get("manifest_bytes")
        if not isinstance(manifest_bytes, (bytes, bytearray)):
            raise RegistryError("v3 component manifest bytes are required")
        descriptor = {
            "component": name,
            "manifest_path": manifest_path,
            "schema_version": schema,
            "manifest_sha256": sha256_bytes(bytes(manifest_bytes)),
            "manifest_byte_size": len(manifest_bytes),
            "shard_count": component.get("shard_count", 0),
            "record_count": (
                component.get("page_count", 0)
                if name == "page_registry"
                else component.get("edge_count", 0)
                if name == "link_index"
                else component.get("candidate_count", 0)
            ),
            "registry_or_index_sha256": (
                component.get("registry_sha256")
                if name == "page_registry"
                else component.get("index_sha256")
            ),
        }
        record_limit = PUBLIC_EDGE_LIMIT if name == "link_index" else PUBLIC_RECORD_LIMIT
        if (
            not isinstance(descriptor["record_count"], int)
            or isinstance(descriptor["record_count"], bool)
            or not 0 <= descriptor["record_count"] <= record_limit
        ):
            raise RegistryError(f"{name} record count exceeds its public limit")
        for field in (
            "page_count",
            "path_count",
            "anchor_count",
            "anchors_sha256",
            "page_ids_sha256",
            "paths_sha256",
            "edge_count",
            "edge_ids_sha256",
            "edges_sha256",
            "coverage_record_count",
            "coverage_shard_count",
            "coverage_sha256",
            "registry_page_ids_sha256",
            "candidate_count",
            "candidate_ids_sha256",
        ):
            if field in component:
                descriptor[field] = component[field]
        components.append(descriptor)
    components.sort(key=lambda row: row["component"])
    body = {
        "schema_version": LIVING_WIKI_MANIFEST_V3_SCHEMA,
        "input_audit_head": input_audit_head,
        "legacy_audit_head": legacy_audit_head,
        "generated_at": generated_at,
        "v2_manifest_sha256": v2_manifest_sha256,
        "configuration": configuration,
        "configuration_sha256": _canonical_digest(configuration),
        "components": components,
    }
    body["manifest_sha256"] = _canonical_digest(body)
    raw = canonical_json(body).encode("utf-8")
    if len(raw) > MANIFEST_BYTE_LIMIT:
        raise RegistryError("v3 manifest exceeds 1 MiB")
    return {
        "manifest": body,
        "manifest_bytes": raw,
        "manifest_sha256": body["manifest_sha256"],
        "valid": True,
    }


def validate_living_wiki_manifest_v3(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("schema_version") != LIVING_WIKI_MANIFEST_V3_SCHEMA
    ):
        raise RegistryError("invalid Living Wiki v3 manifest")
    expected = {
        "schema_version",
        "input_audit_head",
        "legacy_audit_head",
        "generated_at",
        "v2_manifest_sha256",
        "configuration",
        "configuration_sha256",
        "components",
        "manifest_sha256",
    }
    if set(manifest) != expected:
        raise RegistryError("v3 manifest has unknown or missing fields")
    _sha(manifest["input_audit_head"], "input_audit_head")
    _sha(manifest["legacy_audit_head"], "legacy_audit_head")
    _sha(manifest["v2_manifest_sha256"], "v2_manifest_sha256")
    _validated_timestamp(manifest["generated_at"])
    _validate_v3_configuration(manifest["configuration"])
    if _canonical_digest(manifest["configuration"]) != manifest["configuration_sha256"]:
        raise RegistryError("v3 configuration digest mismatch")
    _sha(manifest["manifest_sha256"], "manifest_sha256")
    body = {key: manifest[key] for key in expected if key != "manifest_sha256"}
    if _canonical_digest(body) != manifest["manifest_sha256"]:
        raise RegistryError("v3 manifest digest mismatch")
    components = _as_records(manifest["components"], field="components")
    if len(components) != 3 or {row.get("component") for row in components} != {
        "page_registry",
        "link_index",
        "resolver",
    }:
        raise RegistryError("v3 manifest must contain exactly three unique components")
    owned: set[str] = set()
    previous_component = ""
    for component in components:
        expected_fields = {
            "component",
            "manifest_path",
            "schema_version",
            "manifest_sha256",
            "manifest_byte_size",
            "shard_count",
            "record_count",
            "registry_or_index_sha256",
        }
        optional_by_component = {
            "page_registry": {
                "page_count",
                "path_count",
                "anchor_count",
                "page_ids_sha256",
                "paths_sha256",
                "anchors_sha256",
            },
            "link_index": {
                "page_count",
                "edge_count",
                "edge_ids_sha256",
                "edges_sha256",
                "coverage_record_count",
                "coverage_shard_count",
                "coverage_sha256",
                "registry_page_ids_sha256",
            },
            "resolver": {"candidate_count", "candidate_ids_sha256"},
        }
        component_name = component.get("component")
        allowed_fields = expected_fields | optional_by_component.get(component_name, set())
        if not set(component) <= allowed_fields or not expected_fields <= set(component):
            raise RegistryError("v3 component descriptor shape is invalid")
        if not isinstance(component.get("component"), str):
            raise RegistryError("v3 component name is invalid")
        if component["component"] <= previous_component:
            raise RegistryError("v3 components must be sorted and unique")
        previous_component = component["component"]
        mapping = {
            "page_registry": (
                "deeplaw.living-wiki-page-registry/v1",
                ".deeplaw/derived/wiki/v3/registry/",
            ),
            "link_index": ("deeplaw.living-wiki-link-index/v1", ".deeplaw/derived/wiki/v3/links/"),
            "resolver": ("deeplaw.living-wiki-resolver/v1", ".deeplaw/derived/wiki/v3/resolver/"),
        }
        if component["component"] not in mapping:
            raise RegistryError("unknown v3 component")
        schema, prefix = mapping[component["component"]]
        if (
            component["schema_version"] != schema
            or component["manifest_path"] != prefix + "manifest.json"
        ):
            raise RegistryError("v3 component name/path/schema mismatch")
        _sha(component["manifest_sha256"], "component manifest_sha256")
        _sha(component["registry_or_index_sha256"], "component registry_or_index_sha256")
        if (
            not isinstance(component["manifest_byte_size"], int)
            or not 1 <= component["manifest_byte_size"] <= MANIFEST_BYTE_LIMIT
        ):
            raise RegistryError("component manifest byte bound is invalid")
        if (
            not isinstance(component["shard_count"], int)
            or isinstance(component["shard_count"], bool)
            or not 0 <= component["shard_count"] <= PUBLIC_SHARD_COUNT_LIMIT
        ):
            raise RegistryError("component shard count is invalid")
        record_limit = (
            PUBLIC_EDGE_LIMIT if component_name == "link_index" else PUBLIC_RECORD_LIMIT
        )
        if (
            not isinstance(component["record_count"], int)
            or isinstance(component["record_count"], bool)
            or not 0 <= component["record_count"] <= record_limit
        ):
            raise RegistryError("component record count is invalid")
        for field in ("page_count", "path_count", "coverage_record_count", "candidate_count"):
            if field in component and (
                not isinstance(component[field], int)
                or isinstance(component[field], bool)
                or not 0 <= component[field] <= PUBLIC_RECORD_LIMIT
            ):
                raise RegistryError(f"component {field} is invalid")
        if "edge_count" in component and (
            not isinstance(component["edge_count"], int)
            or isinstance(component["edge_count"], bool)
            or not 0 <= component["edge_count"] <= PUBLIC_EDGE_LIMIT
        ):
            raise RegistryError("component edge_count is invalid")
        paths = [component.get("manifest_path")]
        for path in paths:
            if not isinstance(path, str) or path in owned:
                raise RegistryError("v3 component path overlap or invalid path")
            owned.add(path)
        expected_prefix = prefix
        if paths[0] != expected_prefix + "manifest.json" or any(
            not isinstance(path, str)
            or not path.startswith(expected_prefix)
            or ".." in PurePosixPath(path).parts
            or path.endswith(".md")
            for path in paths
        ):
            raise RegistryError("v3 component path is outside its owned index namespace")
        for field in (
            "page_ids_sha256",
            "paths_sha256",
            "edge_ids_sha256",
            "edges_sha256",
            "coverage_sha256",
            "registry_page_ids_sha256",
            "candidate_ids_sha256",
        ):
            if field in component:
                _sha(component[field], f"component {field}")
    return {"manifest": dict(manifest), "valid": True}


def load_page_registry(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Load only manifest-declared registry files; no directory scan is performed."""

    validated = validate_living_wiki_manifest_v3(manifest)
    component = next(row for row in manifest["components"] if row["component"] == "page_registry")
    manifest_bytes = _safe_read_file(
        root,
        component["manifest_path"],
        max_bytes=MANIFEST_BYTE_LIMIT,
        field="page registry manifest",
    )
    if (
        len(manifest_bytes) != component["manifest_byte_size"]
        or sha256_bytes(manifest_bytes) != component["manifest_sha256"]
    ):
        raise RegistryError("page registry manifest hash mismatch")
    component_body = strict_json_loads(manifest_bytes)
    validate_page_registry_component(component_body)
    if (
        component_body["input_audit_head"] != manifest["input_audit_head"]
        or component_body["legacy_audit_head"] != manifest["legacy_audit_head"]
        or component_body["v2_manifest_sha256"] != manifest["v2_manifest_sha256"]
    ):
        raise RegistryError("page registry audit/v2 binding mismatch")
    if component_body["page_count"] != component["record_count"]:
        raise RegistryError("page registry descriptor count mismatch")
    if component_body["shard_count"] != component["shard_count"]:
        raise RegistryError("page registry descriptor shard count mismatch")
    if component_body["registry_sha256"] != component["registry_or_index_sha256"]:
        raise RegistryError("page registry descriptor digest mismatch")
    for field in (
        "page_count",
        "path_count",
        "anchor_count",
        "anchors_sha256",
        "page_ids_sha256",
        "paths_sha256",
    ):
        if field in component and component_body.get(field) != component[field]:
            raise RegistryError(f"page registry descriptor {field} mismatch")
    payloads: dict[str, bytes] = {}
    for shard in component_body["shards"]:
        payloads[shard["path"]] = _safe_read_file(
            root, shard["path"], max_bytes=SHARD_BYTE_LIMIT, field="registry shard"
        )
    # A v3-only manifest carries all shard hashes/counts; the component manifest itself is then
    # enough to validate each shard even when the owner has not retained a separate v1 manifest.
    records: list[dict[str, Any]] = []
    for shard, raw in zip(
        component_body["shards"],
        (payloads[row["path"]] for row in component_body["shards"]),
        strict=True,
    ):
        if len(raw) != shard["byte_size"] or sha256_bytes(raw) != shard["sha256"]:
            raise RegistryError("registry shard hash/size mismatch")
        decoded = strict_json_loads(raw)
        if (
            not isinstance(decoded, Mapping)
            or decoded.get("schema_version") != PAGE_REGISTRY_SCHEMA
        ):
            raise RegistryError("registry shard schema mismatch")
        shard_records = _as_records(decoded.get("records"), field="registry shard records")
        if (
            len(shard_records) != shard["record_count"]
            or _canonical_digest(shard_records) != shard["records_sha256"]
        ):
            raise RegistryError("registry shard digest mismatch")
        records.extend(validate_page_record(row) for row in shard_records)
    if len(records) != component_body["page_count"]:
        raise RegistryError("registry record count mismatch")
    page_ids = [row["page_id"] for row in records]
    paths = [row["canonical_page_path"] for row in records]
    if page_ids != sorted(page_ids) or len(set(page_ids)) != len(page_ids):
        raise RegistryError("registry page identities are not sorted and unique")
    if len(set(paths)) != len(paths):
        raise RegistryError("registry page paths are not unique")
    if _canonical_digest(page_ids) != component_body["page_ids_sha256"]:
        raise RegistryError("registry page identity digest mismatch")
    if _canonical_digest(paths) != component_body["paths_sha256"]:
        raise RegistryError("registry page path digest mismatch")
    return {
        **validated,
        "component": component_body,
        "records": records,
        "registry_sha256": component_body["registry_sha256"],
        "valid": True,
    }


__all__ = [
    "PAGE_REGISTRY_SCHEMA",
    "PUBLIC_EDGE_LIMIT",
    "PUBLIC_RECORD_LIMIT",
    "PUBLIC_SHARD_COUNT_LIMIT",
    "SHARD_BYTE_LIMIT",
    "SHARD_RECORD_LIMIT",
    "RegistryError",
    "build_living_wiki_manifest_v3",
    "build_page_registry",
    "load_page_registry",
    "validate_living_wiki_manifest_v3",
    "validate_page_record",
    "validate_page_registry_component",
]
