"""Owner-internal Linux ARM64 role-boundary capability probe.

This module probes two fixed, negative capabilities from the already isolated
role process.  It does not accept a path or destination, emit JSON, write file
bytes, send network data, or grant an admission decision.  The returned
mapping is an owner-internal observation; callers are responsible for any
later redaction or external binding.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import platform
import socket
import stat
import sys
from collections.abc import Callable, Mapping
from typing import Any, Final

SCHEMA_VERSION: Final = "deeplaw.linux-role-boundary-probe/v1"
ROLE_UIDS: Final = frozenset({1000, 1001})
RUNTIME_ENTRY_PATH: Final = "/runtime/entry.py"
NETWORK_TARGET: Final = ("203.0.113.1", 443)
MAX_CONNECT_TIMEOUT_SECONDS: Final[float] = 1.0
SYS_OPENAT: Final[int] = 56
SYS_CONNECT: Final[int] = 203
AT_FDCWD: Final[int] = -100

_READ_ONLY_ERRNOS: Final = frozenset({errno.EROFS, errno.EACCES, errno.EPERM})
_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_WRITE_FLAGS = os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


class BoundaryProbeError(RuntimeError):
    """Base class for content-free probe failures."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BoundaryProbeGap(BoundaryProbeError):
    """A typed capability gap that cannot support the expected observation."""


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError) as error:
        raise BoundaryProbeError("receipt_not_canonical") from error


def _record_digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return hashlib.sha256(_canonical_bytes(body)).hexdigest()


def _system() -> str:
    return sys.platform


def _machine() -> str:
    return platform.machine()


def _effective_uid() -> int:
    getter = getattr(os, "geteuid", None)
    if getter is None:
        raise BoundaryProbeGap("uid_unavailable")
    try:
        return getter()
    except Exception as error:
        raise BoundaryProbeGap("uid_unavailable") from error


def _process_id() -> int:
    try:
        return os.getpid()
    except Exception as error:
        raise BoundaryProbeGap("pid_unavailable") from error


def _close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except Exception as error:
        raise BoundaryProbeGap("fd_close_failed") from error


def _syscall_openat(path: bytes, flags: int, mode: int) -> tuple[int, int]:
    """Call ARM64 ``openat`` directly so the syscall number is evidenced."""

    if not isinstance(path, bytes) or b"\x00" in path:
        raise BoundaryProbeGap("openat_path_invalid")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        syscall = libc.syscall
        syscall.restype = ctypes.c_long
        result = syscall(
            ctypes.c_long(SYS_OPENAT),
            ctypes.c_int(AT_FDCWD),
            ctypes.c_char_p(path),
            ctypes.c_int(flags),
            ctypes.c_uint(mode),
        )
        result = int(result)
        if result == -1:
            return -1, int(ctypes.get_errno())
        if result < 0:
            raise BoundaryProbeGap("openat_result_invalid")
        return result, 0
    except BoundaryProbeError:
        raise
    except Exception as error:
        raise BoundaryProbeGap("openat_syscall_unavailable") from error


def _fstat(fd: int) -> Any:
    try:
        return os.fstat(fd)
    except Exception as error:
        raise BoundaryProbeGap("runtime_entry_fstat_failed") from error


def _validate_open_result(value: object, *, code: str) -> tuple[int, int]:
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise BoundaryProbeGap(code)
    fd, error_number = value
    if fd < -1 or error_number < 0 or (fd >= 0 and error_number != 0):
        raise BoundaryProbeGap(code)
    if fd == -1 and error_number == 0:
        raise BoundaryProbeGap(code)
    return fd, error_number


def _verify_runtime_entry(
    openat_fn: Callable[[bytes, int, int], object],
    fstat_fn: Callable[[int], Any],
    close_fn: Callable[[int], Any],
) -> None:
    path = RUNTIME_ENTRY_PATH.encode("ascii")
    try:
        result = _validate_open_result(
            openat_fn(path, _OPEN_FLAGS, 0), code="runtime_entry_read_open_invalid"
        )
    except BoundaryProbeError:
        raise
    except Exception as error:
        raise BoundaryProbeGap("runtime_entry_read_open_failed") from error
    fd, _error_number = result
    if fd < 0:
        raise BoundaryProbeGap("runtime_entry_read_open_failed")
    primary: BoundaryProbeError | None = None
    try:
        try:
            metadata = fstat_fn(fd)
        except BoundaryProbeError:
            raise
        except Exception as error:
            raise BoundaryProbeGap("runtime_entry_fstat_failed") from error
        try:
            regular = stat.S_ISREG(metadata.st_mode)
            size = metadata.st_size
        except Exception as error:
            raise BoundaryProbeGap("runtime_entry_fstat_invalid") from error
        if regular is not True or isinstance(size, bool) or not isinstance(size, int):
            raise BoundaryProbeGap("runtime_entry_fstat_invalid")
        if size <= 0:
            raise BoundaryProbeGap("runtime_entry_empty")
    except BoundaryProbeError as error:
        primary = error
    finally:
        try:
            close_fn(fd)
        except BoundaryProbeError as error:
            if primary is None:
                primary = error
        except Exception as error:
            if primary is None:
                primary = BoundaryProbeGap("runtime_entry_close_failed")
                primary.__cause__ = error
    if primary is not None:
        raise primary


def _probe_runtime_write_open(
    openat_fn: Callable[[bytes, int, int], object],
    close_fn: Callable[[int], Any],
) -> dict[str, Any]:
    path = RUNTIME_ENTRY_PATH.encode("ascii")
    try:
        result = _validate_open_result(
            openat_fn(path, _WRITE_FLAGS, 0), code="runtime_entry_write_open_invalid"
        )
    except BoundaryProbeError:
        raise
    except Exception as error:
        raise BoundaryProbeGap("runtime_entry_write_open_failed") from error
    fd, error_number = result
    if fd >= 0:
        try:
            close_fn(fd)
        except BoundaryProbeError:
            raise
        except Exception as error:
            raise BoundaryProbeGap("runtime_entry_write_close_failed") from error
        raise BoundaryProbeGap("runtime_entry_write_open_succeeded")
    if error_number not in _READ_ONLY_ERRNOS:
        raise BoundaryProbeGap("runtime_entry_write_errno_unexpected")
    return {
        "action_id": "readonly_runtime_write",
        "syscall": SYS_OPENAT,
        "success": False,
        "errno": error_number,
    }


def _socket_probe(socket_factory: Callable[..., Any]) -> dict[str, Any]:
    connection: Any | None = None
    primary: BoundaryProbeError | None = None
    check: dict[str, Any] | None = None
    try:
        try:
            connection = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
        except Exception as error:
            raise BoundaryProbeGap("network_socket_create_failed") from error
        if connection is None:
            raise BoundaryProbeGap("network_socket_invalid")
        try:
            connection.settimeout(MAX_CONNECT_TIMEOUT_SECONDS)
            connection.connect(NETWORK_TARGET)
        except TimeoutError as error:
            raise BoundaryProbeGap("network_connect_timeout") from error
        except Exception as error:
            error_number = getattr(error, "errno", None)
            if error_number == errno.ENETUNREACH:
                check = {
                    "action_id": "nonloopback_connect",
                    "syscall": SYS_CONNECT,
                    "success": False,
                    "errno": errno.ENETUNREACH,
                }
            else:
                raise BoundaryProbeGap("network_connect_errno_unexpected") from error
        else:
            raise BoundaryProbeGap("network_connect_succeeded")
    except BoundaryProbeError as error:
        primary = error
    finally:
        if connection is not None:
            try:
                connection.close()
            except BoundaryProbeError as error:
                if primary is None:
                    primary = error
            except Exception as error:
                if primary is None:
                    primary = BoundaryProbeGap("network_socket_close_failed")
                    primary.__cause__ = error
    if primary is not None:
        raise primary
    if check is None:
        raise BoundaryProbeGap("network_check_missing")
    return check


def _run_boundary_probe(
    *,
    system_getter: Callable[[], str] = _system,
    machine_getter: Callable[[], str] = _machine,
    uid_getter: Callable[[], int] = _effective_uid,
    pid_getter: Callable[[], int] = _process_id,
    openat_fn: Callable[[bytes, int, int], object] = _syscall_openat,
    fstat_fn: Callable[[int], Any] = _fstat,
    close_fn: Callable[[int], Any] = _close_fd,
    socket_factory: Callable[..., Any] = socket.socket,
) -> dict[str, Any]:
    try:
        if system_getter() != "linux":
            raise BoundaryProbeGap("linux_required")
        if machine_getter() != "aarch64":
            raise BoundaryProbeGap("aarch64_required")
    except BoundaryProbeError:
        raise
    except Exception as error:
        raise BoundaryProbeGap("platform_unavailable") from error

    try:
        uid = uid_getter()
    except BoundaryProbeError:
        raise
    except Exception as error:
        raise BoundaryProbeGap("uid_unavailable") from error
    if isinstance(uid, bool) or not isinstance(uid, int) or uid not in ROLE_UIDS:
        raise BoundaryProbeGap("role_uid_invalid")

    try:
        pid = pid_getter()
    except BoundaryProbeError:
        raise
    except Exception as error:
        raise BoundaryProbeGap("pid_unavailable") from error
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise BoundaryProbeGap("pid_invalid")

    _verify_runtime_entry(openat_fn, fstat_fn, close_fn)
    write_check = _probe_runtime_write_open(openat_fn, close_fn)
    network_check = _socket_probe(socket_factory)
    observation: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "formal_admission": False,
        "claim_eligible": False,
        "uid": uid,
        "pid": pid,
        "checks": [write_check, network_check],
        "record_sha256": "",
    }
    observation["record_sha256"] = _record_digest(observation)
    return observation


def run_boundary_probe() -> dict[str, Any]:
    """Run the fixed owner-internal probe with no caller-supplied targets."""

    return _run_boundary_probe()


__all__ = [
    "SCHEMA_VERSION",
    "BoundaryProbeError",
    "BoundaryProbeGap",
    "run_boundary_probe",
]
