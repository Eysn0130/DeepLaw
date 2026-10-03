"""Bounded owner-side observations for an OpenCode public session fork.

This module is an observation boundary only.  It does not start a Host, call a
provider, or interpret plugin content beyond the existing native event adapter.
The plugin log is read through a no-follow directory-descriptor chain so the
owner can bind the observed file and its parent directories between polls.
The event result is only the owner's control-reply barrier after Root observes
the plugin row; it cannot prove that an upstream HTTP response was delayed or
that no side-channel request occurred.
"""

from __future__ import annotations

import errno
import math
import os
import stat
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.hosts import v013_native_event_adapter as _adapter
from deeplaw.util import canonical_json, sha256_bytes

MAX_PLUGIN_LOG_BYTES = 64 * 1024
MAX_TIMEOUT_SECONDS = 30.0
POLL_SECONDS = 0.01

_DirectoryIdentity = tuple[int, int, int]
_FileIdentity = tuple[int, int, int]


class NativeForkObservationError(ValueError):
    """A bounded fork observation is a typed, claim-ineligible gap."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _gap(code: str) -> None:
    raise NativeForkObservationError(code) from None


def _record_digest(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return sha256_bytes(canonical_json(body).encode("utf-8"))


def capture_fork_response(
    *,
    method: str,
    path: str,
    status_code: int,
    request_body: bytes,
    response: bytes,
) -> dict[str, Any]:
    """Capture the content-minimized result of one actual public fork call.

    The route is normalized only by the existing adapter validator.  The
    response is passed to that validator as the original bytes, so its digest
    cannot be changed by an HTTP JSON reserialization step.
    """

    if type(request_body) is not bytes or request_body != b"{}":
        _gap("fork_request_body_invalid")
    if type(response) is not bytes:
        _gap("fork_response_bytes_invalid")
    try:
        route_sha256, parent_session_id, parent_session_sha256 = (
            _adapter._validate_public_fork_route(
                {
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                }
            )
        )
    except Exception as error:
        if isinstance(error, NativeForkObservationError):
            raise
        _gap("fork_route_invalid")
    try:
        response_sha256, _child_session_id, child_session_sha256 = (
            _adapter._validate_public_fork_response(
                response,
                parent_session_id=parent_session_id,
            )
        )
    except Exception as error:
        if isinstance(error, NativeForkObservationError):
            raise
        _gap("fork_response_invalid")
    result: dict[str, Any] = {
        "route_observation_sha256": route_sha256,
        "request_body_sha256": sha256_bytes(request_body),
        "response_sha256": response_sha256,
        "parent_session_sha256": parent_session_sha256,
        "child_session_sha256": child_session_sha256,
        "formal_admission": False,
        "record_sha256": "",
    }
    result["record_sha256"] = _record_digest(result)
    return result


@dataclass(frozen=True, slots=True)
class PluginLogSnapshot:
    """Owner-private bytes and filesystem identities at one log boundary."""

    data: bytes
    data_sha256: str
    existed: bool
    directory_identity: tuple[_DirectoryIdentity, ...]
    file_identity: _FileIdentity | None

    @property
    def raw_bytes(self) -> bytes:
        return self.data

    @property
    def content(self) -> bytes:
        return self.data

    @property
    def sha256(self) -> str:
        return self.data_sha256

    @property
    def byte_size(self) -> int:
        return len(self.data)


@dataclass(frozen=True, slots=True)
class _CurrentLog:
    data: bytes | None
    directory_identity: tuple[_DirectoryIdentity, ...]
    file_identity: _FileIdentity | None

    @property
    def existed(self) -> bool:
        return self.data is not None


class _PathAbsent(Exception):
    pass


def _require_safe_path_support() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
        or os.open not in getattr(os, "supports_dir_fd", ())
        or os.stat not in getattr(os, "supports_dir_fd", ())
        or os.stat not in getattr(os, "supports_follow_symlinks", ())
    ):
        _gap("plugin_log_nofollow_unavailable")


def _open_flags(*, directory: bool) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if directory:
        flags |= os.O_DIRECTORY
    else:
        flags |= getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    return flags


def _directory_identity(value: os.stat_result) -> _DirectoryIdentity:
    if not stat.S_ISDIR(value.st_mode):
        _gap("plugin_log_parent_not_directory")
    return (int(value.st_dev), int(value.st_ino), int(value.st_mode))


def _file_identity(value: os.stat_result) -> _FileIdentity:
    return (int(value.st_dev), int(value.st_ino), int(value.st_mode))


def _path_parts(path: Path | str) -> tuple[bool, list[str]]:
    try:
        selected = Path(path)
    except (TypeError, ValueError):
        _gap("plugin_log_path_invalid")
    if not selected.name or selected.name in {".", ".."}:
        _gap("plugin_log_path_invalid")
    parts = list(selected.parts)
    if selected.is_absolute():
        anchor = selected.anchor
        if parts and parts[0] == anchor:
            parts = parts[1:]
        absolute = True
    else:
        absolute = False
    if not parts or any(part in {"", ".", ".."} for part in parts):
        _gap("plugin_log_path_invalid")
    if parts[-1] != selected.name:
        _gap("plugin_log_path_invalid")
    return absolute, parts


def _open_parent_dir(
    path: Path | str,
) -> tuple[int, tuple[_DirectoryIdentity, ...], str]:
    """Open every ancestor with dirfd + O_NOFOLLOW and retain the last fd."""

    _require_safe_path_support()
    absolute, parts = _path_parts(path)
    start = "/" if absolute else "."
    descriptor = -1
    try:
        descriptor = os.open(start, _open_flags(directory=True))
        identities = [_directory_identity(os.fstat(descriptor))]
        for component in parts[:-1]:
            try:
                details = os.stat(
                    component,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError as error:
                raise _PathAbsent from error
            except OSError:
                _gap("plugin_log_parent_unavailable")
            if stat.S_ISLNK(details.st_mode):
                _gap("plugin_log_symlink_rejected")
            if not stat.S_ISDIR(details.st_mode):
                _gap("plugin_log_parent_not_directory")
            try:
                child = os.open(
                    component,
                    _open_flags(directory=True),
                    dir_fd=descriptor,
                )
            except FileNotFoundError as error:
                raise _PathAbsent from error
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                    _gap("plugin_log_symlink_rejected")
                _gap("plugin_log_parent_unavailable")
            with suppress(OSError):
                os.close(descriptor)
            descriptor = child
            identities.append(_directory_identity(os.fstat(descriptor)))
        return descriptor, tuple(identities), parts[-1]
    except FileNotFoundError as error:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        raise _PathAbsent from error
    except _PathAbsent:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        raise
    except NativeForkObservationError:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        if error.errno == errno.ELOOP:
            _gap("plugin_log_symlink_rejected")
        _gap("plugin_log_parent_unavailable")


def _read_current(
    path: Path | str,
    *,
    expected_directory_identity: tuple[_DirectoryIdentity, ...] | None = None,
    expected_file_identity: _FileIdentity | None = None,
) -> _CurrentLog:
    try:
        parent_fd, directory_identity, name = _open_parent_dir(path)
    except _PathAbsent:
        if expected_directory_identity:
            _gap("plugin_log_directory_changed")
        return _CurrentLog(None, (), None)

    descriptor = -1
    try:
        if (
            expected_directory_identity is not None
            and directory_identity != expected_directory_identity
        ):
            _gap("plugin_log_directory_changed")
        try:
            descriptor = os.open(
                name,
                _open_flags(directory=False),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            if expected_file_identity is not None:
                _gap("plugin_log_file_changed")
            return _CurrentLog(None, directory_identity, None)
        except OSError as error:
            if error.errno == errno.ELOOP:
                _gap("plugin_log_symlink_rejected")
            _gap("plugin_log_file_unavailable")

        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _gap("plugin_log_file_invalid")
        file_identity = _file_identity(before)
        if (
            expected_file_identity is not None
            and file_identity != expected_file_identity
        ):
            _gap("plugin_log_file_changed")
        if before.st_size < 0 or before.st_size > MAX_PLUGIN_LOG_BYTES:
            _gap("plugin_log_oversize")

        raw = bytearray()
        while True:
            chunk = os.read(descriptor, min(16 * 1024, MAX_PLUGIN_LOG_BYTES + 1))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_PLUGIN_LOG_BYTES:
                _gap("plugin_log_oversize")
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode):
            _gap("plugin_log_file_changed")
        if _file_identity(after) != file_identity:
            _gap("plugin_log_file_changed")
        if after.st_size < len(raw) or after.st_size > MAX_PLUGIN_LOG_BYTES:
            _gap("plugin_log_file_changed")

        try:
            path_details = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            _gap("plugin_log_file_changed")
        except OSError:
            _gap("plugin_log_file_unavailable")
        if stat.S_ISLNK(path_details.st_mode):
            _gap("plugin_log_symlink_rejected")
        if not stat.S_ISREG(path_details.st_mode):
            _gap("plugin_log_file_changed")
        if _file_identity(path_details) != _file_identity(after):
            _gap("plugin_log_file_changed")
        if path_details.st_size < len(raw):
            _gap("plugin_log_file_changed")
        if path_details.st_size > MAX_PLUGIN_LOG_BYTES:
            _gap("plugin_log_oversize")
        return _CurrentLog(bytes(raw), directory_identity, file_identity)
    except OSError:
        _gap("plugin_log_file_unavailable")
    finally:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            os.close(parent_fd)


def snapshot_plugin_log(path: Path) -> PluginLogSnapshot:
    """Freeze the bounded log prefix and its owner-side filesystem binding."""

    current = _read_current(path)
    if not current.existed:
        data = b""
        return PluginLogSnapshot(
            data=data,
            data_sha256=sha256_bytes(data),
            existed=False,
            directory_identity=current.directory_identity,
            file_identity=None,
        )
    data = current.data
    assert data is not None
    return PluginLogSnapshot(
        data=data,
        data_sha256=sha256_bytes(data),
        existed=True,
        directory_identity=current.directory_identity,
        file_identity=current.file_identity,
    )


def _validate_identity_tuple(
    value: Any,
    *,
    width: int,
    allow_empty: bool,
) -> None:
    if not isinstance(value, tuple) or (not allow_empty and not value):
        _gap("plugin_log_snapshot_invalid")
    for item in value:
        if not isinstance(item, tuple) or len(item) != width:
            _gap("plugin_log_snapshot_invalid")
        if any(type(part) is not int or part < 0 for part in item):
            _gap("plugin_log_snapshot_invalid")


def _validate_snapshot(snapshot: PluginLogSnapshot) -> None:
    if not isinstance(snapshot, PluginLogSnapshot):
        _gap("plugin_log_snapshot_invalid")
    if type(snapshot.data) is not bytes or len(snapshot.data) > MAX_PLUGIN_LOG_BYTES:
        _gap("plugin_log_snapshot_invalid")
    if type(snapshot.data_sha256) is not str or snapshot.data_sha256 != sha256_bytes(
        snapshot.data
    ):
        _gap("plugin_log_snapshot_tampered")
    if type(snapshot.existed) is not bool:
        _gap("plugin_log_snapshot_invalid")
    _validate_identity_tuple(
        snapshot.directory_identity,
        width=3,
        allow_empty=True,
    )
    if snapshot.existed:
        if not snapshot.directory_identity or snapshot.file_identity is None:
            _gap("plugin_log_snapshot_invalid")
        if not snapshot.data.endswith(b"\n") and snapshot.data:
            _gap("plugin_log_snapshot_invalid")
    else:
        if snapshot.data or snapshot.file_identity is not None:
            _gap("plugin_log_snapshot_invalid")
    if snapshot.file_identity is not None:
        _validate_identity_tuple(
            (snapshot.file_identity,),
            width=3,
            allow_empty=False,
        )


def _validate_child_digest(value: str) -> str:
    try:
        return _adapter._session(value, label="child plugin")
    except Exception:
        _gap("child_session_digest_invalid")


def _validate_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _gap("timeout_invalid")
    selected = float(value)
    if not math.isfinite(selected) or selected <= 0 or selected > MAX_TIMEOUT_SECONDS:
        _gap("timeout_invalid")
    return selected


def _deliver_source_callback(
    callback: Callable[[bytes], Any], line: bytes, deadline: float
) -> None:
    if time.monotonic() >= deadline:
        _gap("plugin_source_callback_timeout")
    try:
        callback(line)
    except Exception:
        _gap("plugin_source_callback_failed")
    if time.monotonic() >= deadline:
        _gap("plugin_source_callback_timeout")


def await_child_event(
    path: Path,
    snapshot: PluginLogSnapshot,
    child_session_sha256: str,
    *,
    timeout_seconds: float = MAX_TIMEOUT_SECONDS,
    source_callback: Callable[[bytes], Any] | None = None,
) -> dict[str, Any]:
    """Wait for one new, complete, validated child ``session.created`` row."""

    _validate_snapshot(snapshot)
    child_digest = _validate_child_digest(child_session_sha256)
    timeout = _validate_timeout(timeout_seconds)
    if source_callback is not None and not callable(source_callback):
        _gap("plugin_source_callback_invalid")
    start = time.monotonic()
    deadline = start + timeout
    snapshot_length = len(snapshot.data)
    consumed = 0
    processed_prefix = b""
    found_line: bytes | None = None
    settled_raw: bytes | None = None
    expected_directory_identity = snapshot.directory_identity or None
    expected_file_identity = snapshot.file_identity if snapshot.existed else None

    while True:
        current = _read_current(
            path,
            expected_directory_identity=expected_directory_identity,
            expected_file_identity=expected_file_identity,
        )
        if current.directory_identity:
            if (
                expected_directory_identity is not None
                and current.directory_identity != expected_directory_identity
            ):
                _gap("plugin_log_directory_changed")
            if expected_directory_identity is None:
                expected_directory_identity = current.directory_identity
        if not current.existed:
            if snapshot.existed or expected_file_identity is not None:
                _gap("plugin_log_file_changed")
        else:
            if expected_directory_identity is None:
                expected_directory_identity = current.directory_identity
            if expected_file_identity is None:
                expected_file_identity = current.file_identity
            raw = current.data
            assert raw is not None
            if len(raw) < snapshot_length or raw[:snapshot_length] != snapshot.data:
                _gap("plugin_log_prefix_changed")
            suffix = raw[snapshot_length:]
            if len(suffix) < consumed or suffix[:consumed] != processed_prefix:
                _gap("plugin_log_event_prefix_changed")
            while True:
                line_end = suffix.find(b"\n", consumed)
                if line_end < 0:
                    break
                # Match the existing native adapter's JSONL record bytes: retain
                # JSON whitespace, but exclude the framing newline.
                line = suffix[consumed:line_end]
                consumed = line_end + 1
                try:
                    source_event, _event_type, session_sha256 = (
                        _adapter.validate_opencode_native_observation(line)
                    )
                except Exception:
                    _gap("plugin_event_invalid")
                processed_prefix = suffix[:consumed]
                if source_event == "session.created":
                    if session_sha256 == child_digest:
                        if found_line is not None:
                            _gap("plugin_event_duplicate")
                        found_line = line
                elif source_event in {"session.updated", "session.compacted"}:
                    continue
                else:
                    _gap("plugin_event_invalid")
            if found_line is not None and consumed == len(suffix):
                if settled_raw == raw:
                    if source_callback is not None:
                        _deliver_source_callback(source_callback, found_line, deadline)
                    observed_at_ns = time.monotonic_ns()
                    completed = time.monotonic()
                    if completed >= deadline:
                        _gap("plugin_event_timeout")
                    elapsed_ms = int((completed - start) * 1000)
                    result: dict[str, Any] = {
                        "child_plugin_event_sha256": sha256_bytes(found_line),
                        "child_plugin_session_sha256": child_digest,
                        "elapsed_ms": elapsed_ms,
                        "observed_at_ns": observed_at_ns,
                        "formal_admission": False,
                        "claim_eligible": False,
                        "record_sha256": "",
                    }
                    result["record_sha256"] = _record_digest(result)
                    return result
                settled_raw = raw
            else:
                settled_raw = None

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _gap("plugin_event_timeout")
        time.sleep(min(POLL_SECONDS, remaining))


__all__ = [
    "MAX_PLUGIN_LOG_BYTES",
    "MAX_TIMEOUT_SECONDS",
    "NativeForkObservationError",
    "PluginLogSnapshot",
    "await_child_event",
    "capture_fork_response",
    "snapshot_plugin_log",
]
