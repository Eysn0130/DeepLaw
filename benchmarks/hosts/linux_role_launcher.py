"""Launch two fixed, isolated roles inside an Alpine ARM64 guest.

This is a guest-side launcher for one owner supplied, already staged
configuration.  It is deliberately small and closed: importing the module
does not create users, namespaces, mounts, cgroups, or processes.  Native
mutation is reachable only through the explicit ``--run`` command line mode.

The validation mode is useful on a development host.  It verifies the same
closed input, path, digest, role, and budget rules without requiring Linux
root or touching a namespace/cgroup.  Native execution additionally requires
an ARM64 Linux guest and a root caller.

The receipt is an intermediate lifecycle record.  It omits paths, commands,
environment, and console data and never asserts formal admission.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import grp
import hashlib
import json
import os
import platform
import pwd
import re
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = "deeplaw.linux-role-launcher/v1"
CONFIG_SCHEMA: Final = f"{SCHEMA_VERSION}/config"
RECEIPT_SCHEMA: Final = f"{SCHEMA_VERSION}/receipt"

HOST_UID: Final = 1000
MCP_UID: Final = 1001
HOST_GID: Final = 1000
MCP_GID: Final = 1001
ROLE_UID_GID: Final = {"host": (HOST_UID, HOST_GID), "mcp": (MCP_UID, MCP_GID)}
ROLE_NAMES: Final = frozenset(ROLE_UID_GID)

MIN_TIMEOUT_SECONDS: Final = 1
MAX_TIMEOUT_SECONDS: Final = 1800
MIN_PIDS: Final = 2
MAX_PIDS: Final = 256
MIN_MEMORY_BYTES: Final = 64 * 1024 * 1024
MAX_MEMORY_BYTES: Final = 4 * 1024 * 1024 * 1024
MIN_CPU_PERIOD_US: Final = 1_000
MAX_CPU_PERIOD_US: Final = 1_000_000
MAX_CPU_QUOTA_US: Final = MAX_CPU_PERIOD_US
MAX_COMMAND_ITEMS: Final = 128
MAX_ARGUMENT_BYTES: Final = 16 * 1024
MAX_BINDINGS: Final = 128
MAX_PATH_BYTES: Final = 4096
MAX_TREE_ENTRIES: Final = 100_000

# Linux ARM64 uses the asm-generic syscall numbering.  These numbers are
# kept local so the module has no dependency on a system-specific header.
SYS_MOUNT: Final = 40
SYS_UMOUNT2: Final = 39
SYS_PIVOT_ROOT: Final = 41
SYS_CHROOT: Final = 51
SYS_UNSHARE: Final = 97
SYS_PTRACE: Final = 117
SYS_SOCKET: Final = 198
SYS_SETNS: Final = 268
SYS_SECCOMP: Final = 277
SYS_BPF: Final = 280
SYS_IO_URING_SETUP: Final = 425
SYS_IO_URING_ENTER: Final = 426
SYS_IO_URING_REGISTER: Final = 427
SYS_OPEN_TREE: Final = 428
SYS_MOVE_MOUNT: Final = 429
SYS_FSOPEN: Final = 430
SYS_FSCONFIG: Final = 431
SYS_FSMOUNT: Final = 432
SYS_FSPICK: Final = 433
SYS_CLONE3: Final = 435
SYS_MOUNT_SETATTR: Final = 442
SYS_CLONE: Final = 220

CLONE_NEWNS: Final = 0x0002_0000
CLONE_NEWCGROUP: Final = 0x0200_0000
CLONE_NEWUTS: Final = 0x0400_0000
CLONE_NEWIPC: Final = 0x0800_0000
CLONE_NEWUSER: Final = 0x1000_0000
CLONE_NEWPID: Final = 0x2000_0000
CLONE_NEWNET: Final = 0x4000_0000
CLONE_NEWTIME: Final = 0x0000_0080
CLONE_NAMESPACE_FLAGS: Final = (
    CLONE_NEWNS
    | CLONE_NEWCGROUP
    | CLONE_NEWUTS
    | CLONE_NEWIPC
    | CLONE_NEWUSER
    | CLONE_NEWPID
    | CLONE_NEWNET
    | CLONE_NEWTIME
)

AF_NETLINK: Final = 16
AF_PACKET: Final = 17
AF_VSOCK: Final = 40

AUDIT_ARCH_AARCH64: Final = 0xC000_00B7
PR_SET_PDEATHSIG: Final = 1
PR_SET_NO_NEW_PRIVS: Final = 38
PR_SET_SECCOMP: Final = 22
PR_CAPBSET_DROP: Final = 24
SECCOMP_MODE_FILTER: Final = 2
SECCOMP_SET_MODE_FILTER: Final = 1
SECCOMP_FILTER_FLAG_LOG: Final = 2
SECCOMP_RET_KILL_PROCESS: Final = 0x8000_0000
SECCOMP_RET_ALLOW: Final = 0x7FFF_0000
SECCOMP_RET_ERRNO: Final = 0x0005_0000
CAP_LAST_CAP_PROBE: Final = 63

MS_RDONLY: Final = 1
MS_NOSUID: Final = 2
MS_NODEV: Final = 4
MS_NOEXEC: Final = 8
MS_BIND: Final = 4096
MS_REMOUNT: Final = 32
MS_REC: Final = 16384
MS_PRIVATE: Final = 1 << 18

SIOCGIFFLAGS: Final = 0x8913
SIOCSIFFLAGS: Final = 0x8914
SIOCGIFADDR: Final = 0x8915
SIOCSIFADDR: Final = 0x8916
IFF_UP: Final = 0x1
IFF_LOOPBACK: Final = 0x8

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class LauncherError(ValueError):
    """A safe, public validation or lifecycle failure code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Budget:
    pids_max: int
    memory_max_bytes: int
    cpu_max_us: int
    cpu_period_us: int

    @classmethod
    def from_json(cls, value: Any) -> Budget:
        if not isinstance(value, Mapping) or set(value) != {
            "pids_max",
            "memory_max_bytes",
            "cpu_max_us",
            "cpu_period_us",
        }:
            raise LauncherError("budget_shape_invalid")
        values: dict[str, int] = {}
        for key in ("pids_max", "memory_max_bytes", "cpu_max_us", "cpu_period_us"):
            item = value[key]
            if isinstance(item, bool) or not isinstance(item, int):
                raise LauncherError("budget_value_invalid")
            values[key] = item
        if not MIN_PIDS <= values["pids_max"] <= MAX_PIDS:
            raise LauncherError("pids_budget_out_of_range")
        if not MIN_MEMORY_BYTES <= values["memory_max_bytes"] <= MAX_MEMORY_BYTES:
            raise LauncherError("memory_budget_out_of_range")
        if not MIN_CPU_PERIOD_US <= values["cpu_period_us"] <= MAX_CPU_PERIOD_US:
            raise LauncherError("cpu_period_out_of_range")
        if not 1 <= values["cpu_max_us"] <= min(values["cpu_period_us"], MAX_CPU_QUOTA_US):
            raise LauncherError("cpu_quota_out_of_range")
        return cls(**values)

    def to_public(self) -> dict[str, int]:
        return {
            "pids_max": self.pids_max,
            "memory_max_bytes": self.memory_max_bytes,
            "cpu_max_us": self.cpu_max_us,
            "cpu_period_us": self.cpu_period_us,
        }


@dataclass(frozen=True, slots=True)
class RuntimeBinding:
    source: Path
    target: str
    sha256: str
    is_directory: bool


@dataclass(frozen=True, slots=True)
class RoleSpec:
    role: str
    command: tuple[str, ...]
    bindings: tuple[RuntimeBinding, ...]
    budget: Budget

    @property
    def uid(self) -> int:
        return ROLE_UID_GID[self.role][0]

    @property
    def gid(self) -> int:
        return ROLE_UID_GID[self.role][1]


@dataclass(frozen=True, slots=True)
class RoleHandle:
    """Owner-only handle for a role that has passed native startup checks."""

    role: str
    pid: int
    uid: int
    cgroup_dir: Path
    workdir: Path


RolesStartedCallback = Callable[[tuple[RoleHandle, ...]], None]


@dataclass(frozen=True, slots=True)
class LauncherConfig:
    freshroot: Path
    cgroup_root: Path
    timeout_seconds: int
    roles: tuple[RoleSpec, ...]


def canonical_json(value: Any) -> bytes:
    """Return deterministic JSON bytes for config and receipt hashing."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise LauncherError("json_not_canonical") from error


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_absolute_canonical(value: Any, *, code: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise LauncherError(code)
    if len(value.encode("utf-8")) > MAX_PATH_BYTES:
        raise LauncherError(code)
    path = Path(value)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise LauncherError(code)
    if os.path.normpath(value) != value:
        raise LauncherError(code)
    return path


def _lstat_no_symlink(path: Path, *, code: str, require_exists: bool = True) -> os.stat_result:
    """Lstat each path component, rejecting symlink traversal."""

    parts = path.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        try:
            item = os.lstat(current)
        except FileNotFoundError:
            if require_exists:
                raise LauncherError(code) from None
            return os.stat_result((0,) * 10)
        except OSError:
            raise LauncherError(code) from None
        if stat.S_ISLNK(item.st_mode):
            raise LauncherError(code)
    try:
        return os.lstat(path)
    except FileNotFoundError:
        if require_exists:
            raise LauncherError(code) from None
        return os.stat_result((0,) * 10)
    except OSError:
        raise LauncherError(code) from None


def _require_directory(path: Path, *, code: str) -> os.stat_result:
    item = _lstat_no_symlink(path, code=code)
    if not stat.S_ISDIR(item.st_mode):
        raise LauncherError(code)
    if item.st_mode & 0o022:
        raise LauncherError(f"{code}_writable")
    return item


def _path_is_or_below(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _reject_reserved_root(path: Path, *, code: str) -> None:
    # A dedicated child under /tmp is allowed for validation fixtures and for
    # an owner-created guest staging directory.  The well-known roots
    # themselves are never accepted as mutation targets.
    reserved = {Path("/"), Path("/proc"), Path("/sys"), Path("/dev"), Path("/run")}
    if path in reserved:
        raise LauncherError(code)


def _tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    count = 0

    def visit(directory: Path, relative: str) -> None:
        nonlocal count
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError:
            raise LauncherError("runtime_binding_unreadable") from None
        for entry in entries:
            count += 1
            if count > MAX_TREE_ENTRIES:
                raise LauncherError("runtime_binding_too_large")
            name = entry.name
            if name in {".", ".."} or "/" in name or "\x00" in name:
                raise LauncherError("runtime_binding_name_invalid")
            rel = f"{relative}/{name}" if relative else name
            try:
                item = entry.stat(follow_symlinks=False)
            except OSError:
                raise LauncherError("runtime_binding_unreadable") from None
            if stat.S_ISLNK(item.st_mode):
                raise LauncherError("runtime_binding_symlink")
            if stat.S_ISDIR(item.st_mode):
                digest.update(f"d\0{rel}\0".encode())
                visit(Path(entry.path), rel)
            elif stat.S_ISREG(item.st_mode):
                digest.update(f"f\0{rel}\0{item.st_size}\0".encode())
                try:
                    with open(entry.path, "rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                except OSError:
                    raise LauncherError("runtime_binding_unreadable") from None
                digest.update(b"\0")
            else:
                raise LauncherError("runtime_binding_type_invalid")

    visit(path, "")
    return digest.hexdigest()


def _file_or_tree_sha256(path: Path) -> tuple[str, bool]:
    item = _lstat_no_symlink(path, code="runtime_binding_missing")
    if stat.S_ISREG(item.st_mode):
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            raise LauncherError("runtime_binding_unreadable") from None
        return digest.hexdigest(), False
    if stat.S_ISDIR(item.st_mode):
        return _tree_sha256(path), True
    raise LauncherError("runtime_binding_type_invalid")


def _validate_target(value: Any) -> str:
    path = _is_absolute_canonical(value, code="runtime_target_invalid")
    target = str(path)
    if target == "/" or target in {"/proc", "/dev", "/work"}:
        raise LauncherError("runtime_target_reserved")
    if _path_is_or_below(path, Path("/proc")) or _path_is_or_below(path, Path("/dev")):
        raise LauncherError("runtime_target_reserved")
    return target


def _validate_source(path: Path) -> None:
    for reserved in ("/", "/proc", "/sys", "/dev", "/run", "/etc", "/home", "/root"):
        reserved_path = Path(reserved)
        if path == reserved_path or (reserved != "/" and _path_is_or_below(path, reserved_path)):
            raise LauncherError("runtime_binding_path_reserved")


def _validate_command(value: Any, *, bindings: Sequence[RuntimeBinding]) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > MAX_COMMAND_ITEMS
        or any(not isinstance(item, str) or not item or "\x00" in item for item in value)
    ):
        raise LauncherError("command_shape_invalid")
    encoded_size = sum(len(item.encode("utf-8")) for item in value)
    if encoded_size > MAX_ARGUMENT_BYTES:
        raise LauncherError("command_too_large")
    executable = _is_absolute_canonical(value[0], code="command_path_invalid")
    if executable in {Path("/bin/sh"), Path("/bin/ash"), Path("/bin/bash"), Path("/usr/bin/env")}:
        raise LauncherError("command_shell_wrapper_forbidden")
    if not any(
        executable == Path(binding.target)
        or _path_is_or_below(executable, Path(binding.target))
        for binding in bindings
    ):
        raise LauncherError("command_not_bound")
    return tuple(value)


def _parse_binding(value: Any) -> RuntimeBinding:
    if not isinstance(value, Mapping) or set(value) != {"source", "target", "sha256"}:
        raise LauncherError("runtime_binding_shape_invalid")
    source = _is_absolute_canonical(value["source"], code="runtime_binding_path_invalid")
    _validate_source(source)
    _lstat_no_symlink(source, code="runtime_binding_missing")
    target = _validate_target(value["target"])
    sha256 = value["sha256"]
    if not isinstance(sha256, str) or _SHA256_RE.fullmatch(sha256) is None:
        raise LauncherError("runtime_binding_digest_invalid")
    actual, is_directory = _file_or_tree_sha256(source)
    if actual != sha256:
        raise LauncherError("runtime_binding_digest_mismatch")
    return RuntimeBinding(source=source, target=target, sha256=sha256, is_directory=is_directory)


def _verify_binding(binding: RuntimeBinding) -> None:
    _lstat_no_symlink(binding.source, code="runtime_binding_missing")
    actual, is_directory = _file_or_tree_sha256(binding.source)
    if is_directory != binding.is_directory or actual != binding.sha256:
        raise LauncherError("runtime_binding_digest_mismatch")


def _require_native_binding(binding: RuntimeBinding) -> None:
    """Require a root-owned, non-writable staged tree before native bind."""

    def check(path: Path) -> None:
        item = _lstat_no_symlink(path, code="runtime_binding_untrusted")
        if item.st_uid != 0 or item.st_mode & 0o022:
            raise LauncherError("runtime_binding_untrusted")

    current = Path(binding.source.parts[0])
    for part in binding.source.parts[1:]:
        current /= part
        check(current)
    if binding.is_directory:
        try:
            for root, directories, files in os.walk(binding.source, followlinks=False):
                for name in (*directories, *files):
                    check(Path(root) / name)
        except OSError:
            raise LauncherError("runtime_binding_untrusted") from None


def _parse_role(value: Any) -> RoleSpec:
    if not isinstance(value, Mapping) or set(value) != {
        "role",
        "command",
        "runtime_bindings",
        "budgets",
    }:
        raise LauncherError("role_shape_invalid")
    role = value["role"]
    if not isinstance(role, str) or _ROLE_RE.fullmatch(role) is None or role not in ROLE_NAMES:
        raise LauncherError("role_invalid")
    raw_bindings = value["runtime_bindings"]
    if not isinstance(raw_bindings, list) or not raw_bindings or len(raw_bindings) > MAX_BINDINGS:
        raise LauncherError("runtime_bindings_shape_invalid")
    bindings = tuple(_parse_binding(item) for item in raw_bindings)
    command = _validate_command(value["command"], bindings=bindings)
    budget = Budget.from_json(value["budgets"])
    return RoleSpec(role=role, command=command, bindings=bindings, budget=budget)


def parse_config(value: Any) -> LauncherConfig:
    """Validate an owner-staged closed configuration and all input digests."""

    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "freshroot",
        "cgroup_root",
        "timeout_seconds",
        "roles",
    }:
        raise LauncherError("config_shape_invalid")
    if value["schema"] != CONFIG_SCHEMA:
        raise LauncherError("config_schema_invalid")
    freshroot = _is_absolute_canonical(value["freshroot"], code="freshroot_invalid")
    cgroup_root = _is_absolute_canonical(value["cgroup_root"], code="cgroup_root_invalid")
    _reject_reserved_root(freshroot, code="freshroot_reserved")
    _reject_reserved_root(cgroup_root, code="cgroup_root_reserved")
    _require_directory(freshroot, code="freshroot_invalid")
    _require_directory(cgroup_root, code="cgroup_root_invalid")
    if _path_is_or_below(freshroot, cgroup_root) or _path_is_or_below(cgroup_root, freshroot):
        raise LauncherError("root_overlap")
    timeout = value["timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise LauncherError("timeout_invalid")
    if not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
        raise LauncherError("timeout_out_of_range")
    raw_roles = value["roles"]
    if not isinstance(raw_roles, list) or len(raw_roles) != 2:
        raise LauncherError("role_set_invalid")
    roles = tuple(_parse_role(item) for item in raw_roles)
    if {role.role for role in roles} != ROLE_NAMES:
        raise LauncherError("role_set_invalid")
    reserved_children = {"host", "host-work", "mcp", "mcp-work"}
    if any(
        (freshroot / child).exists() or (freshroot / child).is_symlink()
        for child in reserved_children
    ):
        raise LauncherError("freshroot_role_exists")
    if any(
        (cgroup_root / role).exists() or (cgroup_root / role).is_symlink()
        for role in ROLE_NAMES
    ):
        raise LauncherError("cgroup_role_exists")
    return LauncherConfig(
        freshroot=freshroot,
        cgroup_root=cgroup_root,
        timeout_seconds=timeout,
        roles=roles,
    )


def _safe_command_digest(command: Sequence[str]) -> str:
    return sha256_bytes(canonical_json(list(command)))


def _role_public(spec: RoleSpec) -> dict[str, Any]:
    uid, gid = ROLE_UID_GID[spec.role]
    return {
        "role": spec.role,
        "uid": uid,
        "gid": gid,
        "namespace_mode": "new_mount_pid_net_ipc",
        "mount_propagation": "private",
        "root_mode": "fresh_chroot",
        "readonly_runtime_bindings": len(spec.bindings),
        "writable_workdir": True,
        "proc_mount": "new_proc",
        "dev_profile": "minimal_tmpfs",
        "network": "loopback_only",
        "command_argc": len(spec.command),
        "command_sha256": _safe_command_digest(spec.command),
        "budget": spec.budget.to_public(),
        "cgroup_populated_checked": False,
        "cgroup_populated": None,
        "seccomp": {
            "arch": "aarch64",
            "installed": False,
            "blocked_syscalls": [
                "unshare",
                "setns",
                "clone_namespace_flags",
                "clone3",
                "mount",
                "umount2",
                "pivot_root",
                "chroot",
                "open_tree",
                "move_mount",
                "fsopen",
                "fsconfig",
                "fsmount",
                "fspick",
                "mount_setattr",
                "ptrace",
                "bpf",
                "io_uring_setup",
                "io_uring_enter",
                "io_uring_register",
            ],
            "blocked_socket_families": ["AF_VSOCK", "AF_NETLINK", "AF_PACKET"],
            "destination_filtering": "not_implemented",
        },
    }


def _receipt(
    *,
    status: str,
    config_sha256: str | None,
    roles: Sequence[dict[str, Any]],
    failure_codes: Sequence[str],
    events: Sequence[str] = (),
    native_mutation: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "status": status,
        "formal_admission": False,
        "claim_eligible": False,
        "native_mutation": native_mutation,
        "config_sha256": config_sha256,
        "roles": list(roles),
        "failure_codes": sorted(set(failure_codes)),
        "events": list(events),
    }
    body["record_sha256"] = sha256_bytes(canonical_json(body))
    return body


def build_validation_receipt(config: LauncherConfig, *, config_sha256: str) -> dict[str, Any]:
    return _receipt(
        status="validated",
        config_sha256=config_sha256,
        roles=[_role_public(role) | {"lifecycle": "validated"} for role in config.roles],
        failure_codes=(),
        events=("validated",),
        native_mutation=False,
    )


class _LibC:
    """Small lazy libc wrapper; loading it never mutates the host."""

    def __init__(self) -> None:
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.unshare.argtypes = [ctypes.c_int]
        self.libc.unshare.restype = ctypes.c_int
        self.libc.mount.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_ulong,
            ctypes.c_char_p,
        ]
        self.libc.mount.restype = ctypes.c_int
        self.libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
        self.libc.umount2.restype = ctypes.c_int
        self.libc.prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        self.libc.prctl.restype = ctypes.c_int
        self.libc.capset.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.libc.capset.restype = ctypes.c_int
        self.libc.setns.argtypes = [ctypes.c_int, ctypes.c_int]
        self.libc.setns.restype = ctypes.c_int

    def syscall_failed(self, result: int, code: str) -> None:
        if result != 0:
            raise LauncherError(code)

    def unshare(self, flags: int) -> None:
        self.syscall_failed(self.libc.unshare(flags), "namespace_create_failed")

    def mount(
        self,
        source: str | None,
        target: Path,
        filesystem: str | None,
        flags: int,
        data: str | None,
    ) -> None:
        encoded_source = source.encode() if source is not None else None
        encoded_target = os.fsencode(target)
        encoded_fs = filesystem.encode() if filesystem is not None else None
        encoded_data = data.encode() if data is not None else None
        self.syscall_failed(
            self.libc.mount(encoded_source, encoded_target, encoded_fs, flags, encoded_data),
            "mount_setup_failed",
        )

    def umount(self, target: Path) -> None:
        self.libc.umount2(os.fsencode(target), 2)

    def prctl(
        self,
        option: int,
        arg2: int = 0,
        arg3: int = 0,
        arg4: int = 0,
        arg5: int = 0,
        *,
        code: str,
    ) -> None:
        self.syscall_failed(self.libc.prctl(option, arg2, arg3, arg4, arg5), code)


def _native_requirements() -> None:
    if os.geteuid() != 0:
        raise LauncherError("native_requires_root")
    if platform.system() != "Linux":
        raise LauncherError("native_requires_linux")
    if platform.machine().lower() not in {"aarch64", "arm64"}:
        raise LauncherError("native_requires_arm64")


def _require_native_directory(path: Path, *, code: str, require_empty: bool = True) -> None:
    item = _require_directory(path, code=code)
    if item.st_uid != 0:
        raise LauncherError(f"{code}_owner")
    if not require_empty:
        return
    try:
        with os.scandir(path) as entries:
            if next(entries, None) is not None:
                raise LauncherError(f"{code}_not_empty")
    except OSError:
        raise LauncherError(code) from None


def _write_text(path: Path, value: str, *, code: str) -> None:
    try:
        with open(path, "w", encoding="ascii") as stream:
            stream.write(value)
    except OSError:
        raise LauncherError(code) from None


def _prepare_cgroup(root: Path, spec: RoleSpec) -> Path:
    directory = root / spec.role
    try:
        directory.mkdir(mode=0o700)
    except OSError:
        raise LauncherError("cgroup_create_failed") from None
    _write_text(
        directory / "pids.max",
        str(spec.budget.pids_max),
        code="cgroup_budget_write_failed",
    )
    _write_text(
        directory / "memory.max",
        str(spec.budget.memory_max_bytes),
        code="cgroup_budget_write_failed",
    )
    _write_text(
        directory / "cpu.max",
        f"{spec.budget.cpu_max_us} {spec.budget.cpu_period_us}",
        code="cgroup_budget_write_failed",
    )
    return directory


def _require_cgroup_budget_controllers(root: Path) -> None:
    try:
        controllers = (root / "cgroup.controllers").read_text(encoding="ascii").split()
        subtree_control = (root / "cgroup.subtree_control").read_text(encoding="ascii").split()
    except OSError:
        raise LauncherError("cgroup_v2_required") from None
    required = {"pids", "memory", "cpu"}
    if not required.issubset(controllers) or not required.issubset(subtree_control):
        raise LauncherError("cgroup_controller_missing")


def _enter_cgroup(directory: Path, pid: int) -> None:
    _write_text(directory / "cgroup.procs", str(pid), code="cgroup_enter_failed")


def _cgroup_kill(directory: Path) -> None:
    try:
        _write_text(directory / "cgroup.kill", "1", code="cgroup_kill_failed")
    except LauncherError:
        try:
            with open(directory / "cgroup.procs", encoding="ascii") as stream:
                pids = [int(item) for item in stream.read().split() if item]
        except (OSError, ValueError):
            pids = []
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                raise LauncherError("cgroup_kill_failed") from None


def _bring_loopback_up() -> None:
    request = bytearray(40)
    struct.pack_into("16sH", request, 0, b"lo", 0)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0) as sock:
            fcntl.ioctl(sock.fileno(), SIOCGIFFLAGS, request, True)
            flags = struct.unpack_from("H", request, 16)[0]
            flags |= IFF_UP | IFF_LOOPBACK
            struct.pack_into("H", request, 16, flags)
            fcntl.ioctl(sock.fileno(), SIOCSIFFLAGS, request)
            try:
                fcntl.ioctl(sock.fileno(), SIOCGIFADDR, request, True)
                address = bytes(request[20:24])
                if address != socket.inet_aton("127.0.0.1"):
                    raise OSError(errno.EADDRNOTAVAIL, "loopback address is not fixed")
            except OSError:
                struct.pack_into(
                    "H2x4s8x",
                    request,
                    16,
                    socket.AF_INET,
                    socket.inet_aton("127.0.0.1"),
                )
                fcntl.ioctl(sock.fileno(), SIOCSIFADDR, request)
    except OSError:
        raise LauncherError("loopback_setup_failed") from None


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter))]


def _bpf_stmt(code: int, k: int = 0) -> _SockFilter:
    return _SockFilter(code=code, jt=0, jf=0, k=k)


def _bpf_jump(code: int, k: int, jt: int, jf: int) -> _SockFilter:
    return _SockFilter(code=code, jt=jt, jf=jf, k=k)


def _seccomp_program() -> list[_SockFilter]:
    # Classic BPF instruction values from linux/filter.h.  The program first
    # checks AArch64, then blocks fixed syscall numbers, namespace-bearing
    # clone flags, and three socket families.  Destination filtering is
    # intentionally absent and is named as such in the receipt.
    bpf_ld_abs = 0x20
    bpf_jmp_jeq = 0x15
    bpf_alu_and = 0x54
    bpf_ret_k = 0x06
    deny = SECCOMP_RET_ERRNO | errno.EPERM
    program = [
        _bpf_stmt(bpf_ld_abs, 4),
        _bpf_jump(bpf_jmp_jeq, AUDIT_ARCH_AARCH64, 1, 0),
        _bpf_stmt(bpf_ret_k, SECCOMP_RET_KILL_PROCESS),
        _bpf_stmt(bpf_ld_abs, 0),
    ]
    blocked = (
        SYS_UNSHARE,
        SYS_SETNS,
        SYS_CLONE3,
        SYS_MOUNT,
        SYS_UMOUNT2,
        SYS_PIVOT_ROOT,
        SYS_CHROOT,
        SYS_OPEN_TREE,
        SYS_MOVE_MOUNT,
        SYS_FSOPEN,
        SYS_FSCONFIG,
        SYS_FSMOUNT,
        SYS_FSPICK,
        SYS_MOUNT_SETATTR,
        SYS_PTRACE,
        SYS_BPF,
        SYS_IO_URING_SETUP,
        SYS_IO_URING_ENTER,
        SYS_IO_URING_REGISTER,
    )
    for number in blocked:
        program.extend(
            [
                _bpf_jump(bpf_jmp_jeq, number, 0, 1),
                _bpf_stmt(bpf_ret_k, deny),
            ]
        )
    # For non-clone syscalls, skip the four clone inspection instructions to
    # the socket check.  Namespace flags on clone are denied while ordinary
    # thread/process clone remains available to the Host.
    program.extend(
        [
            _bpf_jump(bpf_jmp_jeq, SYS_CLONE, 0, 4),
            _bpf_stmt(bpf_ld_abs, 16),
            _bpf_stmt(bpf_alu_and, CLONE_NAMESPACE_FLAGS),
            _bpf_jump(bpf_jmp_jeq, 0, 1, 0),
            _bpf_stmt(bpf_ret_k, deny),
            # Non-socket syscalls skip the family checks to allow.
            _bpf_jump(bpf_jmp_jeq, SYS_SOCKET, 0, 7),
            _bpf_stmt(bpf_ld_abs, 16),
            _bpf_jump(bpf_jmp_jeq, AF_VSOCK, 0, 1),
            _bpf_stmt(bpf_ret_k, deny),
            _bpf_jump(bpf_jmp_jeq, AF_NETLINK, 0, 1),
            _bpf_stmt(bpf_ret_k, deny),
            _bpf_jump(bpf_jmp_jeq, AF_PACKET, 0, 1),
            _bpf_stmt(bpf_ret_k, deny),
            _bpf_stmt(bpf_ret_k, SECCOMP_RET_ALLOW),
        ]
    )
    return program


def _install_seccomp(libc: _LibC) -> None:
    program = (_SockFilter * len(_seccomp_program()))(*_seccomp_program())
    fprog = _SockFprog(len=len(program), filter=program)
    libc.prctl(PR_SET_NO_NEW_PRIVS, 1, code="no_new_privs_failed")
    pointer = ctypes.cast(ctypes.pointer(fprog), ctypes.c_void_p).value
    if pointer is None:
        raise LauncherError("seccomp_install_failed")
    # ERRNO actions can bypass ordinary syscall audit records. Request kernel
    # SECCOMP events explicitly; the collector must still observe their delivery.
    install = libc.libc.syscall
    install.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    install.restype = ctypes.c_long
    libc.syscall_failed(
        install(SYS_SECCOMP, SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_LOG, pointer),
        "seccomp_install_failed",
    )


class _CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint), ("pid", ctypes.c_int)]


class _CapData(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint),
        ("permitted", ctypes.c_uint),
        ("inheritable", ctypes.c_uint),
    ]


def _drop_privileges(libc: _LibC, uid: int, gid: int) -> None:
    try:
        os.setgroups([])
        if os.getgroups():
            raise LauncherError("supplementary_groups_not_cleared")
    except OSError:
        raise LauncherError("supplementary_groups_clear_failed") from None
    for capability in range(CAP_LAST_CAP_PROBE + 1):
        result = libc.libc.prctl(PR_CAPBSET_DROP, capability, 0, 0, 0)
        if result != 0 and ctypes.get_errno() != errno.EINVAL:
            raise LauncherError("capability_bounding_drop_failed")
    try:
        os.setresgid(gid, gid, gid)
        os.setresuid(uid, uid, uid)
    except OSError:
        raise LauncherError("uid_gid_drop_failed") from None
    if os.geteuid() != uid or os.getegid() != gid or os.getgroups():
        raise LauncherError("uid_gid_drop_unverified")
    # Changing UID/GID requires CAP_SETUID/CAP_SETGID until the transition.
    # Clear all remaining sets afterwards, before enabling no_new_privs.
    header = _CapHeader(version=0x2008_0522, pid=0)
    data = (_CapData * 2)()
    if libc.libc.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
        raise LauncherError("capabilities_clear_failed")
    libc.prctl(PR_SET_NO_NEW_PRIVS, 1, code="no_new_privs_failed")


def _make_device(path: Path, mode: int, device: int) -> None:
    try:
        os.mknod(path, stat.S_IFCHR | mode, device)
        os.chmod(path, mode)
    except OSError:
        raise LauncherError("minimal_dev_setup_failed") from None


def _silence_stdio() -> None:
    try:
        descriptor = os.open("/dev/null", os.O_RDWR)
        for target in (0, 1, 2):
            os.dup2(descriptor, target)
        if descriptor > 2:
            os.close(descriptor)
    except OSError:
        raise LauncherError("stdio_isolation_failed") from None


def _close_inherited_fds() -> None:
    try:
        maximum = int(os.sysconf("SC_OPEN_MAX"))
    except (OSError, ValueError):
        maximum = 65_536
    os.closerange(3, min(maximum, 65_536))


def _mkdir_no_symlink(path: Path, *, mode: int = 0o755) -> None:
    try:
        path.mkdir(mode=mode)
        os.chmod(path, mode)
    except OSError:
        raise LauncherError("chroot_layout_failed") from None


def _prepare_target(root: Path, target: str, *, is_directory: bool) -> Path:
    relative = target.lstrip("/")
    path = root / relative
    current = root
    for part in Path(relative).parts[:-1]:
        current /= part
        if current.exists() or current.is_symlink():
            _lstat_no_symlink(current, code="chroot_layout_failed")
            if not current.is_dir():
                raise LauncherError("chroot_layout_failed")
        else:
            _mkdir_no_symlink(current)
    if path.exists() or path.is_symlink():
        _lstat_no_symlink(path, code="chroot_layout_failed")
        if is_directory and not path.is_dir():
            raise LauncherError("chroot_layout_failed")
        if not is_directory and not path.is_file():
            raise LauncherError("chroot_layout_failed")
    elif is_directory:
        _mkdir_no_symlink(path)
    else:
        try:
            path.touch(mode=0o555, exist_ok=False)
        except OSError:
            raise LauncherError("chroot_layout_failed") from None
    return path


def _setup_chroot(libc: _LibC, spec: RoleSpec, root: Path, work: Path) -> None:
    try:
        root.mkdir(mode=0o755)
        os.chmod(root, 0o755)
    except OSError:
        raise LauncherError("chroot_layout_failed") from None
    _mkdir_no_symlink(root / "proc")
    _mkdir_no_symlink(root / "dev")
    _mkdir_no_symlink(root / "work")
    for binding in spec.bindings:
        _verify_binding(binding)
        _require_native_binding(binding)
        target = _prepare_target(root, binding.target, is_directory=binding.is_directory)
        flags = MS_BIND | (MS_REC if binding.is_directory else 0)
        libc.mount(str(binding.source), target, None, flags, None)
        libc.mount(
            None,
            target,
            None,
            MS_BIND | MS_REMOUNT | MS_RDONLY | (MS_REC if binding.is_directory else 0),
            None,
        )
    libc.mount("proc", root / "proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, "hidepid=2")
    libc.mount("tmpfs", root / "dev", "tmpfs", MS_NOSUID | MS_NOEXEC, "size=16m,mode=755")
    _make_device(root / "dev/null", 0o666, os.makedev(1, 3))
    _make_device(root / "dev/zero", 0o666, os.makedev(1, 5))
    _make_device(root / "dev/random", 0o444, os.makedev(1, 8))
    _make_device(root / "dev/urandom", 0o444, os.makedev(1, 9))
    _make_device(root / "dev/tty", 0o620, os.makedev(5, 0))
    try:
        libc.mount(str(work), root / "work", None, MS_BIND, None)
    except LauncherError:
        raise
    try:
        os.chroot(root)
        os.chdir("/work")
    except OSError:
        raise LauncherError("chroot_enter_failed") from None


def _lookup_fixed_user(role: str) -> tuple[int, int] | None:
    uid, gid = ROLE_UID_GID[role]
    try:
        entry = pwd.getpwuid(uid)
    except KeyError:
        return None
    if entry.pw_gid != gid:
        raise LauncherError("fixed_role_identity_conflict")
    try:
        group = grp.getgrgid(gid)
    except KeyError:
        raise LauncherError("fixed_role_identity_conflict") from None
    if group.gr_name != f"deeplaw-{role}":
        raise LauncherError("fixed_role_identity_conflict")
    if entry.pw_name != f"deeplaw-{role}":
        raise LauncherError("fixed_role_identity_conflict")
    return entry.pw_uid, entry.pw_gid


def _fixed_tool(names: Sequence[str]) -> str | None:
    for name in names:
        item = Path(name)
        try:
            info = os.stat(item)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and info.st_mode & 0o111:
            return name
    return None


def _run_fixed_user_tool(command: Sequence[str]) -> None:
    try:
        subprocess.run(
            list(command),
            check=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        raise LauncherError("role_user_setup_failed") from None


def _ensure_fixed_users() -> None:
    group_tool = _fixed_tool(("/usr/sbin/addgroup", "/sbin/addgroup"))
    user_tool = _fixed_tool(("/usr/sbin/adduser", "/sbin/adduser"))
    for role, uid, gid in (("host", HOST_UID, HOST_GID), ("mcp", MCP_UID, MCP_GID)):
        identity = _lookup_fixed_user(role)
        if identity is not None:
            continue
        if group_tool is None or user_tool is None:
            raise LauncherError("role_user_tools_missing")
        group_name = f"deeplaw-{role}"
        user_name = group_name
        try:
            group = grp.getgrgid(gid)
        except KeyError:
            _run_fixed_user_tool(
                [group_tool, "-S", "-g", str(gid), group_name],
            )
        else:
            if group.gr_name != group_name:
                raise LauncherError("fixed_role_identity_conflict")
        try:
            by_name = pwd.getpwnam(user_name)
        except KeyError:
            pass
        else:
            if by_name.pw_uid != uid or by_name.pw_gid != gid:
                raise LauncherError("fixed_role_identity_conflict")
        try:
            by_uid = pwd.getpwuid(uid)
        except KeyError:
            _run_fixed_user_tool(
                [
                    user_tool,
                    "-S",
                    "-D",
                    "-H",
                    "-u",
                    str(uid),
                    "-G",
                    group_name,
                    "-s",
                    "/sbin/nologin",
                    user_name,
                ],
            )
        else:
            if by_uid.pw_gid != gid:
                raise LauncherError("fixed_role_identity_conflict")
        if _lookup_fixed_user(role) != (uid, gid):
            raise LauncherError("fixed_role_identity_unverified")


def _supervisor_setup(
    spec: RoleSpec,
    config: LauncherConfig,
    cgroup_dir: Path,
    release_r: int,
    ready_r: int,
    ready_w: int,
    status_w: int,
) -> None:
    """Run in the supervisor fork; all failures become fixed protocol codes."""

    try:
        libc = _LibC()
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, code="parent_death_signal_failed")
        with suppress(OSError):
            os.setpgid(0, 0)
        if os.read(release_r, 1) != b"G":
            raise LauncherError("release_protocol_failed")
        os.close(release_r)
        libc.unshare(CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWNET | CLONE_NEWIPC)
        libc.mount(None, Path("/"), None, MS_REC | MS_PRIVATE, None)
        grandchild = os.fork()
        if grandchild == 0:
            os.close(ready_r)
            try:
                role_root = config.freshroot / spec.role
                work = config.freshroot / f"{spec.role}-work"
                _bring_loopback_up()
                _setup_chroot(libc, spec, role_root, work)
                _drop_privileges(libc, spec.uid, spec.gid)
                _install_seccomp(libc)
                _silence_stdio()
                os.environ.clear()
                os.environ.update(
                    {
                        "HOME": "/work",
                        "PATH": "/runtime/bin:/bin:/usr/bin",
                        "LANG": "C",
                    }
                )
                os.write(ready_w, b"R")
                os.close(ready_w)
                os.close(status_w)
                _close_inherited_fds()
                os.execv(spec.command[0], list(spec.command))
            except LauncherError as error:
                with suppress(OSError):
                    os.write(ready_w, f"E:{error.code}".encode("ascii"))
                os._exit(127)
            except BaseException:
                with suppress(OSError):
                    os.write(ready_w, b"E:role_start_failed")
                os._exit(127)
        os.close(ready_w)
        message = os.read(ready_r, 128)
        os.close(ready_r)
        if message != b"R":
            _, status = os.waitpid(grandchild, 0)
            del status
            if re.fullmatch(rb"E:[a-z][a-z0-9_]{0,95}", message):
                raise LauncherError(message[2:].decode("ascii"))
            raise LauncherError("role_start_failed")
        os.write(status_w, f"READY:{grandchild}\n".encode("ascii"))
        _, status = os.waitpid(grandchild, 0)
        if os.WIFEXITED(status):
            code = os.WEXITSTATUS(status)
            os.write(status_w, f"EXIT:{code}\n".encode("ascii"))
        elif os.WIFSIGNALED(status):
            os.write(status_w, f"SIGNAL:{os.WTERMSIG(status)}\n".encode("ascii"))
        else:
            os.write(status_w, b"EXIT:255\n")
        os.close(status_w)
        os._exit(0)
    except LauncherError as error:
        with suppress(OSError):
            os.write(status_w, f"ERROR:{error.code}\n".encode("ascii"))
        os._exit(127)
    except BaseException:
        with suppress(OSError):
            os.write(status_w, b"ERROR:role_supervisor_failed\n")
        os._exit(127)


def _read_status_line(fd: int, buffer: bytearray, deadline: float) -> str | None:
    while time.monotonic() < deadline:
        if b"\n" in buffer:
            raw, _, rest = buffer.partition(b"\n")
            buffer[:] = rest
            try:
                return raw.decode("ascii")
            except UnicodeDecodeError:
                raise LauncherError("role_protocol_invalid") from None
        remaining = max(0.0, deadline - time.monotonic())
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            return None
        chunk = os.read(fd, 128)
        if not chunk:
            if buffer:
                raise LauncherError("role_protocol_incomplete")
            return None
        buffer.extend(chunk)
    if b"\n" in buffer:
        raw, _, rest = buffer.partition(b"\n")
        buffer[:] = rest
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError:
            raise LauncherError("role_protocol_invalid") from None
    return None


def _kill_role(pid: int, cgroup_dir: Path | None, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            pass
    if sig == signal.SIGKILL and cgroup_dir is not None:
        with suppress(LauncherError):
            _cgroup_kill(cgroup_dir)


def _capture_role_identity(
    role: str, pid: int, cgroup_dir: Path
) -> tuple[Any | None, str | None]:
    """Capture identity while the role is alive through the sibling observer."""

    try:
        from benchmarks.hosts.linux_guest_observer import observe_role_identity

        return observe_role_identity(role=role, pid=pid, cgroup_dir=cgroup_dir), None
    except Exception:
        return None, "role_observation_missing"


def _finish_role_observation(
    identity: Any | None,
    cgroup_dir: Path,
    row: dict[str, Any],
) -> str | None:
    """Require cgroup empty and retain only the observer's public row."""

    if identity is None:
        row["lifecycle"] = "gap"
        row["cgroup_populated_checked"] = False
        return "role_observation_missing"
    try:
        from benchmarks.hosts.linux_guest_observer import ObservationGap, observe_role_cgroup

        observation = None
        populated_error = False
        for attempt in range(25):
            try:
                observation = observe_role_cgroup(identity, cgroup_dir=cgroup_dir)
                break
            except ObservationGap as error:
                if str(error) != "role cgroup remains populated":
                    raise
                populated_error = True
                if attempt < 24:
                    time.sleep(0.02)
        if observation is None:
            row["lifecycle"] = "gap"
            row["cgroup_populated_checked"] = True
            row["cgroup_populated"] = 1 if populated_error else None
            return "role_cgroup_populated"
    except Exception:
        row["lifecycle"] = "gap"
        row["cgroup_populated_checked"] = False
        return "role_cgroup_observation_missing"
    row["observation"] = observation.to_public()
    row["cgroup_populated_checked"] = True
    row["cgroup_populated"] = observation.cgroup_populated
    return None


@dataclass(slots=True)
class _RoleRuntime:
    supervisor_pid: int
    pid: int
    uid: int
    status_fd: int
    status_buffer: bytearray
    cgroup_dir: Path
    workdir: Path
    identity: Any | None


def _notify_roles_started(
    config: LauncherConfig,
    processes: Mapping[str, _RoleRuntime],
    failure_codes: list[str],
    callback: RolesStartedCallback | None,
) -> bool:
    """Notify the owner only after both roles are ready and identified."""

    if (
        callback is None
        or len(processes) != len(config.roles)
        or any(role.role not in processes for role in config.roles)
        or any(runtime.identity is None for runtime in processes.values())
        or failure_codes
    ):
        return False
    handles = tuple(
        RoleHandle(
            role=spec.role,
            pid=processes[spec.role].pid,
            uid=processes[spec.role].uid,
            cgroup_dir=processes[spec.role].cgroup_dir,
            workdir=processes[spec.role].workdir,
        )
        for spec in config.roles
    )
    try:
        callback(handles)
    except BaseException:
        failure_codes.append("roles_started_callback_failed")
        return True
    return False


def run_native(
    config: LauncherConfig,
    *,
    config_sha256: str,
    on_roles_started: RolesStartedCallback | None = None,
) -> tuple[dict[str, Any], int]:
    """Perform the explicitly requested native guest mutation."""

    _native_requirements()
    _require_native_directory(config.freshroot, code="freshroot_invalid")
    _require_native_directory(config.cgroup_root, code="cgroup_root_invalid", require_empty=False)
    try:
        if not (config.cgroup_root / "cgroup.controllers").is_file() or not (
            config.cgroup_root / "cgroup.procs"
        ).is_file():
            raise LauncherError("cgroup_v2_required")
    except OSError:
        raise LauncherError("cgroup_v2_required") from None
    _require_cgroup_budget_controllers(config.cgroup_root)
    role_rows = [_role_public(spec) | {"lifecycle": "starting"} for spec in config.roles]
    processes: dict[str, _RoleRuntime] = {}
    pending_children: dict[int, tuple[Path, int]] = {}
    failure_codes: list[str] = []
    events: list[str] = ["native_preflight_ok"]
    signal_count = 0
    stopping = False
    lifecycle_deadline = time.monotonic() + config.timeout_seconds

    def on_signal(_signum: int, _frame: Any) -> None:
        nonlocal signal_count, stopping
        signal_count += 1
        stopping = True

    old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        for spec in config.roles:
            for binding in spec.bindings:
                _verify_binding(binding)
                _require_native_binding(binding)
        _ensure_fixed_users()
        if stopping:
            failure_codes.append("shutdown_before_start")
        for spec in config.roles:
            if stopping or time.monotonic() >= lifecycle_deadline:
                if not stopping:
                    failure_codes.append("timeout")
                failure_codes.append("role_not_started")
                break
            cgroup_dir = _prepare_cgroup(config.cgroup_root, spec)
            try:
                work_directory = config.freshroot / f"{spec.role}-work"
                work_directory.mkdir(mode=0o700)
                os.chown(work_directory, spec.uid, spec.gid)
            except OSError:
                raise LauncherError("freshroot_role_create_failed") from None
            release_r, release_w = os.pipe()
            ready_r, ready_w = os.pipe()
            status_r, status_w = os.pipe()
            child = os.fork()
            if child == 0:
                os.close(release_w)
                os.close(status_r)
                _supervisor_setup(spec, config, cgroup_dir, release_r, ready_r, ready_w, status_w)
                os._exit(127)
            os.close(release_r)
            os.close(ready_r)
            os.close(ready_w)
            os.close(status_w)
            pending_children[child] = (cgroup_dir, status_r)
            with suppress(OSError):
                os.setpgid(child, child)
            try:
                _enter_cgroup(cgroup_dir, child)
                os.write(release_w, b"G")
            except (LauncherError, OSError):
                failure_codes.append("cgroup_enter_failed")
                row = next(item for item in role_rows if item["role"] == spec.role)
                row["lifecycle"] = "gap"
                _kill_role(child, cgroup_dir, signal.SIGKILL)
                with suppress(OSError):
                    os.close(release_w)
                os.close(status_r)
                with suppress(ChildProcessError):
                    os.waitpid(child, 0)
                pending_children.pop(child, None)
                stopping = True
                break
            os.close(release_w)
            status_buffer = bytearray()
            try:
                line = _read_status_line(
                    status_r,
                    status_buffer,
                    min(lifecycle_deadline, time.monotonic() + 30),
                )
            except LauncherError as error:
                line = f"ERROR:{error.code}"
            if line is None or not line.startswith("READY:"):
                row = next(item for item in role_rows if item["role"] == spec.role)
                row["lifecycle"] = "gap"
                if line and line.startswith("ERROR:"):
                    failure_codes.append(line[6:])
                else:
                    failure_codes.append("role_start_timeout")
                _kill_role(child, cgroup_dir, signal.SIGKILL)
                os.close(status_r)
                os.waitpid(child, 0)
                pending_children.pop(child, None)
                stopping = True
                break
            try:
                pid = int(line.split(":", 1)[1])
            except (ValueError, IndexError):
                failure_codes.append("role_pid_protocol_invalid")
                row = next(item for item in role_rows if item["role"] == spec.role)
                row["lifecycle"] = "gap"
                _kill_role(child, cgroup_dir, signal.SIGKILL)
                os.close(status_r)
                os.waitpid(child, 0)
                pending_children.pop(child, None)
                stopping = True
                break
            identity, identity_error = _capture_role_identity(spec.role, pid, cgroup_dir)
            if identity_error is not None:
                failure_codes.append(identity_error)
            processes[spec.role] = _RoleRuntime(
                supervisor_pid=child,
                pid=pid,
                uid=spec.uid,
                status_fd=status_r,
                status_buffer=status_buffer,
                cgroup_dir=cgroup_dir,
                workdir=work_directory,
                identity=identity,
            )
            pending_children.pop(child, None)
            row = next(item for item in role_rows if item["role"] == spec.role)
            row["lifecycle"] = "started"
            row["seccomp"]["installed"] = True
            events.append(f"{spec.role}_started")

        if _notify_roles_started(config, processes, failure_codes, on_roles_started):
            stopping = True
        deadline = lifecycle_deadline
        if processes and not failure_codes:
            while processes and time.monotonic() < deadline and not stopping:
                for role, runtime in list(processes.items()):
                    try:
                        line = _read_status_line(
                            runtime.status_fd,
                            runtime.status_buffer,
                            min(deadline, time.monotonic() + 0.1),
                        )
                    except LauncherError as error:
                        line = f"ERROR:{error.code}"
                    if line is None:
                        continue
                    row = next(item for item in role_rows if item["role"] == role)
                    if line.startswith("EXIT:"):
                        try:
                            exit_code = int(line[5:])
                        except ValueError:
                            exit_code = 255
                        row["exit_code"] = exit_code
                        row["lifecycle"] = "exited"
                        if exit_code != 0:
                            failure_codes.append("role_exit_nonzero")
                        try:
                            os.waitpid(runtime.supervisor_pid, 0)
                        except ChildProcessError:
                            failure_codes.append("role_supervisor_reap_failed")
                        error = _finish_role_observation(runtime.identity, runtime.cgroup_dir, row)
                        if error is not None:
                            failure_codes.append(error)
                        os.close(runtime.status_fd)
                        del processes[role]
                    elif line.startswith("SIGNAL:"):
                        row["lifecycle"] = "exited"
                        if not (stopping and signal_count == 1):
                            failure_codes.append("role_signaled")
                        try:
                            os.waitpid(runtime.supervisor_pid, 0)
                        except ChildProcessError:
                            failure_codes.append("role_supervisor_reap_failed")
                        error = _finish_role_observation(runtime.identity, runtime.cgroup_dir, row)
                        if error is not None:
                            failure_codes.append(error)
                        os.close(runtime.status_fd)
                        del processes[role]
                    elif line.startswith("ERROR:"):
                        failure_codes.append(line[6:])
                        row["lifecycle"] = "gap"
                        try:
                            os.waitpid(runtime.supervisor_pid, 0)
                        except ChildProcessError:
                            failure_codes.append("role_supervisor_reap_failed")
                        error = _finish_role_observation(runtime.identity, runtime.cgroup_dir, row)
                        if error is not None:
                            failure_codes.append(error)
                        os.close(runtime.status_fd)
                        del processes[role]
                time.sleep(0.01)
        if stopping:
            events.append("shutdown_requested")
            if signal_count > 1:
                failure_codes.append("repeated_signal")
        elif processes:
            failure_codes.append("timeout")
        if processes:
            for runtime in processes.values():
                _kill_role(runtime.supervisor_pid, runtime.cgroup_dir, signal.SIGTERM)
            grace_deadline = time.monotonic() + 2
            while processes and time.monotonic() < grace_deadline:
                for role, runtime in list(processes.items()):
                    try:
                        line = _read_status_line(
                            runtime.status_fd,
                            runtime.status_buffer,
                            min(grace_deadline, time.monotonic() + 0.1),
                        )
                    except LauncherError as error:
                        line = f"ERROR:{error.code}"
                    if line and (line.startswith("EXIT:") or line.startswith("SIGNAL:")):
                        row = next(item for item in role_rows if item["role"] == role)
                        if line.startswith("EXIT:"):
                            try:
                                code = int(line[5:])
                            except ValueError:
                                code = 255
                            row["exit_code"] = code
                            if code != 0:
                                failure_codes.append("role_exit_nonzero")
                        else:
                            if not (stopping and signal_count == 1):
                                failure_codes.append("role_signaled")
                        row["lifecycle"] = "exited"
                        try:
                            os.waitpid(runtime.supervisor_pid, 0)
                        except ChildProcessError:
                            failure_codes.append("role_supervisor_reap_failed")
                        error = _finish_role_observation(runtime.identity, runtime.cgroup_dir, row)
                        if error is not None:
                            failure_codes.append(error)
                        os.close(runtime.status_fd)
                        del processes[role]
                time.sleep(0.01)
            for role, runtime in list(processes.items()):
                runtime = processes[role]
                _kill_role(runtime.supervisor_pid, runtime.cgroup_dir, signal.SIGKILL)
                failure_codes.append("forced_role_kill")
                with suppress(ChildProcessError):
                    os.waitpid(runtime.supervisor_pid, 0)
                row = next(item for item in role_rows if item["role"] == role)
                row["lifecycle"] = "gap"
                error = _finish_role_observation(runtime.identity, runtime.cgroup_dir, row)
                if error is not None:
                    failure_codes.append(error)
                os.close(runtime.status_fd)
                del processes[role]
        for spec in config.roles:
            row = next(item for item in role_rows if item["role"] == spec.role)
            if row["lifecycle"] == "started":
                row["lifecycle"] = "gap"
                failure_codes.append("role_lifecycle_incomplete")
    finally:
        for child, (cgroup_dir, status_fd) in pending_children.items():
            _kill_role(child, cgroup_dir, signal.SIGKILL)
            with suppress(ChildProcessError, OSError):
                os.waitpid(child, 0)
            with suppress(OSError):
                os.close(status_fd)
        pending_children.clear()
        for runtime in processes.values():
            _kill_role(runtime.supervisor_pid, runtime.cgroup_dir, signal.SIGKILL)
            with suppress(ChildProcessError, OSError):
                os.waitpid(runtime.supervisor_pid, 0)
            with suppress(OSError):
                os.close(runtime.status_fd)
        processes.clear()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    status = "observed" if not failure_codes else "gap"
    receipt = _receipt(
        status=status,
        config_sha256=config_sha256,
        roles=role_rows,
        failure_codes=failure_codes,
        events=events,
        native_mutation=True,
    )
    return receipt, 0 if status == "observed" else 1


def _parse_cli(argv: Sequence[str]) -> tuple[dict[str, str | bool], str | None]:
    values: dict[str, str | bool] = {"validation_only": False, "run": False}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--help":
            values["help"] = True
            index += 1
            continue
        if token in {"--validation-only", "--run"}:
            key = token[2:].replace("-", "_")
            if values[key] is True:
                return values, "cli_duplicate_option"
            values[key] = True
            index += 1
            continue
        if token in {"--config", "--config-sha256"}:
            key = token[2:].replace("-", "_")
            if key in values:
                return values, "cli_duplicate_option"
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                return values, "cli_option_value_missing"
            values[key] = argv[index + 1]
            index += 2
            continue
        return values, "cli_unknown_option"
    if values.get("help"):
        return values, None
    if values["validation_only"] == values["run"]:
        return values, "cli_mode_required"
    if not isinstance(values.get("config"), str) or not isinstance(
        values.get("config_sha256"), str
    ):
        return values, "cli_config_required"
    digest = values["config_sha256"]
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        return values, "config_digest_invalid"
    return values, None


def _read_config(path_value: str, expected_sha256: str) -> tuple[Any, str]:
    path = _is_absolute_canonical(path_value, code="config_path_invalid")
    info = _lstat_no_symlink(path, code="config_missing")
    if not stat.S_ISREG(info.st_mode):
        raise LauncherError("config_not_regular")
    try:
        raw = path.read_bytes()
    except OSError:
        raise LauncherError("config_unreadable") from None
    actual = sha256_bytes(raw)
    if actual != expected_sha256:
        raise LauncherError("config_digest_mismatch")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LauncherError("config_json_invalid") from None
    return value, actual


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    values, cli_error = _parse_cli(args)
    if values.get("help") and cli_error is None:
        print("linux_role_launcher --config PATH --config-sha256 HEX (--validation-only|--run)")
        return 0
    if cli_error is not None:
        print(
            canonical_json(
                _receipt(
                    status="failed",
                    config_sha256=None,
                    roles=(),
                    failure_codes=(cli_error,),
                    events=("cli_rejected",),
                )
            ).decode()
        )
        return 64
    try:
        raw_config, digest = _read_config(str(values["config"]), str(values["config_sha256"]))
        config = parse_config(raw_config)
        if values["validation_only"]:
            print(canonical_json(build_validation_receipt(config, config_sha256=digest)).decode())
            return 0
        receipt, exit_code = run_native(config, config_sha256=digest)
        print(canonical_json(receipt).decode())
        return exit_code
    except LauncherError as error:
        print(
            canonical_json(
                _receipt(
                    status="failed",
                    config_sha256=None,
                    roles=(),
                    failure_codes=(error.code,),
                    events=("rejected",),
                )
            ).decode()
        )
        return 1
    except (OSError, ValueError, TypeError):
        print(
            canonical_json(
                _receipt(
                    status="failed",
                    config_sha256=None,
                    roles=(),
                    failure_codes=("launcher_failed",),
                    events=("rejected",),
                )
            ).decode()
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
