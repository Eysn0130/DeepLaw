"""Path-free Linux ARM64 audit syscall metadata for an external observer.

This module is a small intermediate only.  It parses the fields needed to bind
one kernel audit SYSCALL record to an owner-supplied role PID/UID, derives the
numeric errno from the signed audit ``exit`` value, and discards all other
payload text.  It also accepts the separate kernel SECCOMP record emitted by a
logged seccomp filter.  A SECCOMP record carries no syscall success/exit or
socket-family fact, so this module records only its verified ``ERRNO`` action
mask and never invents an errno or family.  It does not open a socket, inspect
``/proc``, install seccomp, or make a qualification decision.

The caller must provide the native transport/source and the frozen action
canaries.  A receipt with a missing or mismatched canary is a gap; a zero count
is emitted only after the complete expected window has been matched.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import islice
from types import MappingProxyType
from typing import Any, Final, Literal

SCHEMA_VERSION: Final = "deeplaw.linux-audit-syscall-metadata/v2"

AUDIT_SYSCALL: Final = 1300
AUDIT_PATH: Final = 1302
AUDIT_SOCKETCALL: Final = 1304
AUDIT_CONFIG_CHANGE: Final = 1305
AUDIT_SOCKADDR: Final = 1306
AUDIT_CWD: Final = 1307
AUDIT_EXECVE: Final = 1309
AUDIT_SECCOMP: Final = 1326
AUDIT_EOE: Final = 1320
AUDIT_URINGOP: Final = 1336

AUDIT_ARCH_AARCH64: Final = 0xC000_00B7
SECCOMP_RET_ACTION_FULL: Final = 0xFFFF_0000
SECCOMP_RET_ERRNO: Final = 0x0005_0000

HOST_UID: Final = 1000
MCP_UID: Final = 1001
ROLE_UIDS: Final = MappingProxyType({"host": HOST_UID, "mcp": MCP_UID})

# Linux ARM64 uses the asm-generic syscall numbering.  These are the fixed
# deny-by-number calls in linux_role_launcher.py.  clone(220) and socket(198)
# are conditional and are represented as ``unknown_family``/``unknown_flags``
# in a direct SYSCALL record.  A separate SECCOMP record is the evidence for a
# filtered action; its code is checked below without exposing syscall args.
SYS_UMOUNT2: Final = 39
SYS_MOUNT: Final = 40
SYS_PIVOT_ROOT: Final = 41
SYS_CHROOT: Final = 51
SYS_UNSHARE: Final = 97
SYS_PTRACE: Final = 117
SYS_SETNS: Final = 268
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
SYS_SOCKET: Final = 198
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

AF_UNIX: Final = 1
AF_INET: Final = 2
AF_INET6: Final = 10
AF_NETLINK: Final = 16
AF_PACKET: Final = 17
AF_VSOCK: Final = 40

FIXED_DENY_SYSCALLS: Final = (
    SYS_UMOUNT2,
    SYS_MOUNT,
    SYS_PIVOT_ROOT,
    SYS_CHROOT,
    SYS_UNSHARE,
    SYS_PTRACE,
    SYS_SETNS,
    SYS_BPF,
    SYS_IO_URING_SETUP,
    SYS_IO_URING_ENTER,
    SYS_IO_URING_REGISTER,
    SYS_OPEN_TREE,
    SYS_MOVE_MOUNT,
    SYS_FSOPEN,
    SYS_FSCONFIG,
    SYS_FSMOUNT,
    SYS_FSPICK,
    SYS_CLONE3,
    SYS_MOUNT_SETATTR,
)
DEFAULT_SYSCALLS: Final = tuple(sorted((*FIXED_DENY_SYSCALLS, SYS_SOCKET, SYS_CLONE)))

_AUDIT_TYPE_NAMES: Final = {
    1300: "SYSCALL",
    1302: "PATH",
    1304: "SOCKETCALL",
    1305: "CONFIG_CHANGE",
    1306: "SOCKADDR",
    1307: "CWD",
    1309: "EXECVE",
    1318: "OBJ_PID",
    1319: "TTY",
    1320: "EOE",
    1321: "BPRM_FCAPS",
    1322: "CAPSET",
    1323: "MMAP",
    1324: "NETFILTER_PKT",
    1325: "NETFILTER_CFG",
    1326: "SECCOMP",
    1327: "PROCTITLE",
    1336: "URINGOP",
    1400: "AVC",
    1401: "SELINUX_ERR",
    1403: "MAC_POLICY_LOAD",
    1404: "MAC_STATUS",
    1405: "MAC_CONFIG_CHANGE",
    1406: "MAC_UNLBL_ALLOW",
    1407: "MAC_CIPSOV4_ADD",
    1408: "MAC_CIPSOV4_DEL",
    1409: "MAC_MAP_ADD",
    1410: "MAC_MAP_DEL",
    1415: "MAC_IPSEC_EVENT",
    1416: "MAC_UNLBL_STCADD",
    1417: "MAC_UNLBL_STCDEL",
    1423: "LANDLOCK_ACCESS",
    1701: "ANOM_ABEND",
    2000: "KERNEL",
}

MAX_PAYLOAD_BYTES: Final = 8192
MAX_RECORDS: Final = 4096
MAX_SYSCALLS: Final = 2048
MAX_ACTIONS: Final = 256
MAX_COUNTER: Final = 1_000_000
MAX_SERIAL: Final = 2**63 - 1
MAX_PID: Final = 2**31 - 1
MAX_UID: Final = 2**32 - 1

_AUDIT_HEADER_RE = re.compile(rb"^audit\([^:()\s]{1,128}:(\d{1,20})\):(.*)$", re.DOTALL)
_FIELD_NAME_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*\Z")
_DECIMAL_RE = re.compile(rb"(?:0|[1-9][0-9]*)\Z")
_SIGNED_DECIMAL_RE = re.compile(rb"-?(?:0|[1-9][0-9]*)\Z")
_HEX_RE = re.compile(rb"(?:0[xX])?[0-9a-fA-F]{1,16}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_ACTION_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}\Z")

GapCode = Literal[
    "payload_invalid",
    "unknown_type",
    "header_invalid",
    "duplicate_field",
    "malformed_field",
    "missing_field",
    "invalid_arch",
    "unknown_arch",
    "invalid_syscall",
    "unknown_syscall",
    "invalid_uid",
    "role_uid_mismatch",
    "invalid_pid",
    "role_binding_unknown",
    "invalid_success",
    "invalid_exit",
    "result_inconsistent",
    "conditional_argument_missing",
    "seccomp_code_missing",
    "seccomp_code_invalid",
    "seccomp_action_unknown",
    "invalid_ordinal",
    "invalid_role_bindings",
    "invalid_expected_actions",
    "expected_actions_missing",
    "expected_action_count_mismatch",
    "expected_action_outcome_mismatch",
    "unexpected_action",
    "duplicate_syscall_record",
    "duplicate_seccomp_record",
    "serial_group_conflict",
    "rule_binding_missing",
    "audit_state_invalid",
    "audit_lost_changed",
    "audit_backlog_not_empty",
    "netlink_source_unbound",
    "window_unclosed",
]


class AuditSyscallMetadataError(ValueError):
    """Base error for this path-free intermediate."""


class AuditSyscallMetadataGap(AuditSyscallMetadataError):
    """A typed, non-authoritative observation gap with no payload echo."""

    def __init__(self, code: GapCode):
        self.code = code
        super().__init__(code)


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise AuditSyscallMetadataError("metadata is not canonical JSON") from error


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _pid_digest(pid: int) -> str:
    """Return a public, domain-separated PID binding without retaining PID."""

    return sha256_hex(f"deeplaw.linux-audit-syscall-metadata/v2:pid:{pid}".encode("ascii"))


def _bounded_int(value: Any, *, minimum: int, maximum: int, code: GapCode) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise AuditSyscallMetadataGap(code)
    return value


def _parse_decimal(raw: bytes, *, maximum: int, code: GapCode) -> int:
    if _DECIMAL_RE.fullmatch(raw) is None:
        raise AuditSyscallMetadataGap(code)
    try:
        value = int(raw, 10)
    except ValueError as error:
        raise AuditSyscallMetadataGap(code) from error
    if value > maximum:
        raise AuditSyscallMetadataGap(code)
    return value


def _parse_signed_decimal(raw: bytes) -> int:
    if _SIGNED_DECIMAL_RE.fullmatch(raw) is None:
        raise AuditSyscallMetadataGap("invalid_exit")
    try:
        value = int(raw, 10)
    except ValueError as error:
        raise AuditSyscallMetadataGap("invalid_exit") from error
    if not -(2**63) <= value <= 2**63 - 1:
        raise AuditSyscallMetadataGap("invalid_exit")
    return value


def _parse_hex(raw: bytes, *, code: GapCode) -> int:
    if _HEX_RE.fullmatch(raw) is None:
        raise AuditSyscallMetadataGap(code)
    try:
        value = int(raw, 16)
    except ValueError as error:
        raise AuditSyscallMetadataGap(code) from error
    if value > 2**64 - 1:
        raise AuditSyscallMetadataGap(code)
    return value


def _normalize_syscalls(value: Sequence[int]) -> tuple[int, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise AuditSyscallMetadataGap("invalid_syscall")
    normalized = tuple(value)
    if (
        not normalized
        or len(normalized) > MAX_SYSCALLS
        or any(
            isinstance(item, bool)
            or not isinstance(item, int)
            or not 0 <= item < MAX_SYSCALLS
            for item in normalized
        )
        or tuple(sorted(normalized)) != normalized
        or len(set(normalized)) != len(normalized)
    ):
        raise AuditSyscallMetadataGap("invalid_syscall")
    return normalized


def _header(payload: bytes, *, ordinal: int) -> tuple[int, bytes]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_PAYLOAD_BYTES:
        raise AuditSyscallMetadataGap("payload_invalid")
    if not 1 <= ordinal <= MAX_RECORDS:
        raise AuditSyscallMetadataGap("invalid_ordinal")
    match = _AUDIT_HEADER_RE.match(payload)
    if match is None:
        raise AuditSyscallMetadataGap("header_invalid")
    serial = _parse_decimal(match.group(1), maximum=MAX_SERIAL, code="header_invalid")
    if serial == 0:
        raise AuditSyscallMetadataGap("header_invalid")
    return serial, match.group(2)


def _fields(body: bytes, *, allowlist: frozenset[str]) -> dict[str, bytes]:
    """Tokenize fields without retaining any unallowlisted value."""

    result: dict[str, bytes] = {}
    seen: set[str] = set()
    index = 0
    length = len(body)
    while index < length:
        while index < length and body[index] in b" \t\r\n":
            index += 1
        if index == length:
            break
        key_start = index
        while index < length and body[index] not in b"= \t\r\n":
            index += 1
        key_raw = body[key_start:index]
        if _FIELD_NAME_RE.fullmatch(key_raw) is None or index >= length or body[index] != 61:
            raise AuditSyscallMetadataGap("malformed_field")
        key = key_raw.decode("ascii")
        if key in seen:
            raise AuditSyscallMetadataGap("duplicate_field")
        seen.add(key)
        index += 1
        if index < length and body[index] == 34:  # quoted value
            index += 1
            value_start = index
            escaped = False
            while index < length:
                char = body[index]
                if escaped:
                    escaped = False
                elif char == 92:
                    escaped = True
                elif char == 34:
                    break
                index += 1
            if index >= length or body[index] != 34 or escaped:
                raise AuditSyscallMetadataGap("malformed_field")
            value = body[value_start:index]
            index += 1
        else:
            value_start = index
            while index < length and body[index] not in b" \t\r\n":
                index += 1
            value = body[value_start:index]
        # Unknown values are consumed only long enough to skip their token.
        # They are never copied into the parsed result, event, or exception.
        if key in allowlist:
            result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class RoleBinding:
    """Owner-supplied live role binding; ``pid`` is intentionally private."""

    role: str
    uid: int
    pid: int = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or self.role not in ROLE_UIDS:
            raise AuditSyscallMetadataGap("invalid_role_bindings")
        _bounded_int(self.uid, minimum=1, maximum=MAX_UID, code="invalid_uid")
        if self.uid != ROLE_UIDS[self.role]:
            raise AuditSyscallMetadataGap("role_uid_mismatch")
        _bounded_int(self.pid, minimum=1, maximum=MAX_PID, code="invalid_pid")

    @property
    def pid_sha256(self) -> str:
        return _pid_digest(self.pid)

    def to_public(self) -> dict[str, Any]:
        return {"role": self.role, "uid": self.uid, "pid_sha256": self.pid_sha256}


def normalize_role_bindings(
    bindings: Mapping[str, RoleBinding | Mapping[str, Any]],
) -> tuple[RoleBinding, ...]:
    if not isinstance(bindings, Mapping) or set(bindings) != set(ROLE_UIDS):
        raise AuditSyscallMetadataGap("invalid_role_bindings")
    normalized: list[RoleBinding] = []
    seen_pids: set[int] = set()
    for role in ("host", "mcp"):
        value = bindings[role]
        if isinstance(value, RoleBinding):
            item = value
            if item.role != role:
                raise AuditSyscallMetadataGap("invalid_role_bindings")
        elif isinstance(value, Mapping) and set(value) == {"uid", "pid"}:
            item = RoleBinding(role=role, uid=value["uid"], pid=value["pid"])
        else:
            raise AuditSyscallMetadataGap("invalid_role_bindings")
        if item.pid in seen_pids:
            raise AuditSyscallMetadataGap("invalid_role_bindings")
        seen_pids.add(item.pid)
        normalized.append(item)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class ExpectedCanary:
    """One frozen role/action/outcome/count expectation."""

    role: str
    action_id: str
    syscall: int
    count: int = 1
    record_type: Literal["SYSCALL", "SECCOMP"] = "SYSCALL"
    expected_success: bool | None = False
    expected_errno: int | None = None
    expected_seccomp_action: Literal["ERRNO"] | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.role, str)
            or self.role not in ROLE_UIDS
            or not isinstance(self.action_id, str)
            or _ACTION_ID_RE.fullmatch(self.action_id) is None
        ):
            raise AuditSyscallMetadataGap("invalid_expected_actions")
        _bounded_int(
            self.syscall,
            minimum=0,
            maximum=MAX_SYSCALLS - 1,
            code="invalid_expected_actions",
        )
        _bounded_int(
            self.count,
            minimum=1,
            maximum=MAX_COUNTER,
            code="invalid_expected_actions",
        )
        if not isinstance(self.record_type, str) or self.record_type not in {
            "SYSCALL",
            "SECCOMP",
        }:
            raise AuditSyscallMetadataGap("invalid_expected_actions")
        if self.record_type == "SYSCALL":
            if type(self.expected_success) is not bool or self.expected_seccomp_action is not None:
                raise AuditSyscallMetadataGap("invalid_expected_actions")
            if self.expected_errno is not None:
                _bounded_int(
                    self.expected_errno,
                    minimum=1,
                    maximum=2**31 - 1,
                    code="invalid_expected_actions",
                )
                if self.expected_success:
                    raise AuditSyscallMetadataGap("invalid_expected_actions")
        else:
            if self.expected_success is not None or self.expected_errno is not None:
                raise AuditSyscallMetadataGap("invalid_expected_actions")
            if self.expected_seccomp_action != "ERRNO":
                raise AuditSyscallMetadataGap("invalid_expected_actions")

    def to_public(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "action_id": self.action_id,
            "syscall": self.syscall,
            "count": self.count,
            "record_type": self.record_type,
            "expected_success": self.expected_success,
            "expected_errno": self.expected_errno,
            "expected_seccomp_action": self.expected_seccomp_action,
        }


def normalize_expected_actions(
    actions: Sequence[ExpectedCanary | Mapping[str, Any]],
    *,
    configured_syscalls: Sequence[int],
) -> tuple[ExpectedCanary, ...]:
    if isinstance(actions, (str, bytes, bytearray)) or not isinstance(actions, Sequence):
        raise AuditSyscallMetadataGap("invalid_expected_actions")
    if not actions or len(actions) > MAX_ACTIONS:
        raise AuditSyscallMetadataGap("expected_actions_missing")
    normalized: list[ExpectedCanary] = []
    seen: set[tuple[str, str, int, str]] = set()
    allowed = set(_normalize_syscalls(configured_syscalls))
    for value in actions:
        if isinstance(value, ExpectedCanary):
            item = value
        elif isinstance(value, Mapping) and set(value) == {
            "role",
            "action_id",
            "syscall",
            "count",
            "record_type",
            "expected_success",
            "expected_errno",
            "expected_seccomp_action",
        }:
            item = ExpectedCanary(
                role=value["role"],
                action_id=value["action_id"],
                syscall=value["syscall"],
                count=value["count"],
                record_type=value["record_type"],
                expected_success=value["expected_success"],
                expected_errno=value["expected_errno"],
                expected_seccomp_action=value["expected_seccomp_action"],
            )
        else:
            raise AuditSyscallMetadataGap("invalid_expected_actions")
        if item.syscall not in allowed:
            raise AuditSyscallMetadataGap("unknown_syscall")
        identity = (item.role, item.action_id, item.syscall, item.record_type)
        if identity in seen:
            raise AuditSyscallMetadataGap("invalid_expected_actions")
        seen.add(identity)
        normalized.append(item)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class SyscallEvent:
    ordinal: int
    type_code: int
    serial: int
    role: str
    uid: int
    pid_sha256: str
    syscall: int
    arch: int
    success: bool
    exit: int
    errno: int | None
    action_id: str

    def to_public(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "type_code": self.type_code,
            "type_name": "SYSCALL",
            "serial": self.serial,
            "role": self.role,
            "uid": self.uid,
            "pid_sha256": self.pid_sha256,
            "syscall": self.syscall,
            "arch": self.arch,
            "success": self.success,
            "exit": self.exit,
            "errno": self.errno,
            "action_id": self.action_id,
        }


@dataclass(frozen=True, slots=True)
class SeccompEvent:
    """Kernel ``SECCOMP`` metadata; the record has no syscall result fields."""

    ordinal: int
    type_code: int
    serial: int
    role: str
    uid: int
    pid_sha256: str
    syscall: int
    arch: int
    seccomp_action: Literal["ERRNO"]
    action_id: str

    def to_public(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "type_code": self.type_code,
            "type_name": "SECCOMP",
            "serial": self.serial,
            "role": self.role,
            "uid": self.uid,
            "pid_sha256": self.pid_sha256,
            "syscall": self.syscall,
            "arch": self.arch,
            "seccomp_action": self.seccomp_action,
            "action_id": self.action_id,
        }


@dataclass(frozen=True, slots=True)
class AuditRecord:
    ordinal: int
    type_code: int
    type_name: str
    serial: int
    syscall: SyscallEvent | None = None
    seccomp: SeccompEvent | None = None

    def to_public(self) -> dict[str, Any]:
        if self.syscall is not None:
            return self.syscall.to_public()
        if self.seccomp is not None:
            return self.seccomp.to_public()
        return {
            "ordinal": self.ordinal,
            "type_code": self.type_code,
            "type_name": self.type_name,
            "serial": self.serial,
        }


def _role_for(*, uid: int, pid: int, bindings: Sequence[RoleBinding]) -> RoleBinding:
    if isinstance(bindings, (str, bytes, bytearray)) or not isinstance(bindings, Sequence):
        raise AuditSyscallMetadataGap("invalid_role_bindings")
    if any(not isinstance(item, RoleBinding) for item in bindings):
        raise AuditSyscallMetadataGap("invalid_role_bindings")
    matches = [item for item in bindings if item.uid == uid and item.pid == pid]
    if len(matches) != 1:
        raise AuditSyscallMetadataGap("role_binding_unknown")
    return matches[0]


def _action_id(syscall: int, *, seccomp: bool) -> str:
    """Use only facts present in this record; never infer a socket family."""

    if seccomp:
        if syscall == SYS_SOCKET:
            return "socket_filtered"
        if syscall == SYS_CLONE:
            return "clone_filtered"
        return f"seccomp_syscall_{syscall}"
    if syscall == SYS_SOCKET:
        return "unknown_family"
    if syscall == SYS_CLONE:
        return "unknown_flags"
    return f"syscall_{syscall}"


def parse_syscall_payload(
    payload: bytes,
    *,
    ordinal: int,
    bindings: Sequence[RoleBinding],
    configured_syscalls: Sequence[int] = DEFAULT_SYSCALLS,
) -> SyscallEvent:
    """Parse one numeric-type-1300 kernel payload without retaining its text."""

    record = parse_audit_record(
        payload,
        message_type=AUDIT_SYSCALL,
        ordinal=ordinal,
        bindings=bindings,
        configured_syscalls=configured_syscalls,
    )
    if record.syscall is None:
        raise AuditSyscallMetadataGap("unknown_type")
    return record.syscall


def parse_seccomp_payload(
    payload: bytes,
    *,
    ordinal: int,
    bindings: Sequence[RoleBinding],
    configured_syscalls: Sequence[int] = DEFAULT_SYSCALLS,
) -> SeccompEvent:
    """Parse one type-1326 record and retain only its action mask."""

    record = parse_audit_record(
        payload,
        message_type=AUDIT_SECCOMP,
        ordinal=ordinal,
        bindings=bindings,
        configured_syscalls=configured_syscalls,
    )
    if record.seccomp is None:
        raise AuditSyscallMetadataGap("unknown_type")
    return record.seccomp


def parse_audit_record(
    payload: bytes,
    *,
    message_type: int,
    ordinal: int,
    bindings: Sequence[RoleBinding],
    configured_syscalls: Sequence[int] = DEFAULT_SYSCALLS,
) -> AuditRecord:
    if isinstance(message_type, bool) or not isinstance(message_type, int):
        raise AuditSyscallMetadataGap("unknown_type")
    type_name = _AUDIT_TYPE_NAMES.get(message_type)
    if type_name is None:
        raise AuditSyscallMetadataGap("unknown_type")
    serial, body = _header(payload, ordinal=ordinal)
    if message_type not in {AUDIT_SYSCALL, AUDIT_SECCOMP}:
        return AuditRecord(ordinal, message_type, type_name, serial)

    allowed = _normalize_syscalls(configured_syscalls)
    if message_type == AUDIT_SECCOMP:
        fields = _fields(
            body,
            allowlist=frozenset({"arch", "syscall", "uid", "pid", "code"}),
        )
        required = ("arch", "syscall", "uid", "pid", "code")
        if any(name not in fields for name in required):
            raise AuditSyscallMetadataGap("missing_field")
        arch = _parse_hex(fields["arch"], code="invalid_arch")
        if arch != AUDIT_ARCH_AARCH64:
            raise AuditSyscallMetadataGap("unknown_arch")
        syscall = _parse_decimal(
            fields["syscall"], maximum=MAX_SYSCALLS - 1, code="invalid_syscall"
        )
        if syscall not in set(allowed):
            raise AuditSyscallMetadataGap("unknown_syscall")
        uid = _parse_decimal(fields["uid"], maximum=MAX_UID, code="invalid_uid")
        pid = _parse_decimal(fields["pid"], maximum=MAX_PID, code="invalid_pid")
        code = _parse_hex(fields["code"], code="seccomp_code_invalid")
        if code > 0xFFFF_FFFF:
            raise AuditSyscallMetadataGap("seccomp_code_invalid")
        if code & SECCOMP_RET_ACTION_FULL != SECCOMP_RET_ERRNO:
            raise AuditSyscallMetadataGap("seccomp_action_unknown")
        binding = _role_for(uid=uid, pid=pid, bindings=bindings)
        event = SeccompEvent(
            ordinal=ordinal,
            type_code=message_type,
            serial=serial,
            role=binding.role,
            uid=uid,
            pid_sha256=binding.pid_sha256,
            syscall=syscall,
            arch=arch,
            seccomp_action="ERRNO",
            action_id=_action_id(syscall, seccomp=True),
        )
        return AuditRecord(
            ordinal=ordinal,
            type_code=message_type,
            type_name=type_name,
            serial=serial,
            seccomp=event,
        )

    fields = _fields(
        body,
        allowlist=frozenset({"arch", "syscall", "success", "exit", "uid", "pid"}),
    )
    required = ("arch", "syscall", "success", "exit", "uid", "pid")
    if any(name not in fields for name in required):
        raise AuditSyscallMetadataGap("missing_field")
    arch_raw = fields["arch"]
    arch = _parse_hex(arch_raw, code="invalid_arch")
    if arch != AUDIT_ARCH_AARCH64:
        raise AuditSyscallMetadataGap("unknown_arch")
    syscall = _parse_decimal(
        fields["syscall"], maximum=MAX_SYSCALLS - 1, code="invalid_syscall"
    )
    if syscall not in set(allowed):
        raise AuditSyscallMetadataGap("unknown_syscall")
    uid = _parse_decimal(fields["uid"], maximum=MAX_UID, code="invalid_uid")
    pid = _parse_decimal(fields["pid"], maximum=MAX_PID, code="invalid_pid")
    if fields["success"] == b"yes":
        success = True
    elif fields["success"] == b"no":
        success = False
    else:
        raise AuditSyscallMetadataGap("invalid_success")
    exit_value = _parse_signed_decimal(fields["exit"])
    if (success and exit_value < 0) or (not success and exit_value >= 0):
        raise AuditSyscallMetadataGap("result_inconsistent")
    error_number = -exit_value if exit_value < 0 else None
    binding = _role_for(uid=uid, pid=pid, bindings=bindings)
    action_id = _action_id(syscall, seccomp=False)
    return AuditRecord(
        ordinal=ordinal,
        type_code=message_type,
        type_name=type_name,
        serial=serial,
        syscall=SyscallEvent(
            ordinal=ordinal,
            type_code=message_type,
            serial=serial,
            role=binding.role,
            uid=uid,
            pid_sha256=binding.pid_sha256,
            syscall=syscall,
            arch=arch,
            success=success,
            exit=exit_value,
            errno=error_number,
            action_id=action_id,
        ),
    )


def _digest(value: Any) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise AuditSyscallMetadataGap("rule_binding_missing")
    return value


def _validate_state(
    *,
    audit_pid_registered: bool,
    audit_enabled: int,
    lost_before: int,
    lost_after: int,
    backlog_before: int,
    backlog_after: int,
    source_bound: bool,
    window_closed: bool,
) -> list[str]:
    if (
        type(audit_pid_registered) is not bool
        or type(audit_enabled) is not int
        or audit_enabled not in {1, 2}
    ):
        raise AuditSyscallMetadataGap("audit_state_invalid")
    for value in (lost_before, lost_after, backlog_before, backlog_after):
        _bounded_int(value, minimum=0, maximum=MAX_COUNTER, code="audit_state_invalid")
    if type(source_bound) is not bool or not source_bound:
        raise AuditSyscallMetadataGap("netlink_source_unbound")
    if type(window_closed) is not bool or not window_closed:
        raise AuditSyscallMetadataGap("window_unclosed")
    failures: list[str] = []
    if not audit_pid_registered:
        failures.append("audit_state_invalid")
    if lost_before != 0 or lost_after != 0 or lost_before != lost_after:
        failures.append("audit_lost_changed")
    if backlog_before != 0 or backlog_after != 0:
        failures.append("audit_backlog_not_empty")
    return failures


def _expected_digest(actions: Sequence[ExpectedCanary]) -> str:
    return sha256_hex(canonical_json([item.to_public() for item in actions]))


def _serial_groups(records: Sequence[AuditRecord]) -> list[dict[str, Any]]:
    groups: dict[int, list[AuditRecord]] = defaultdict(list)
    for record in records:
        groups[record.serial].append(record)
    result: list[dict[str, Any]] = []
    for serial, rows in sorted(groups.items(), key=lambda pair: pair[1][0].ordinal):
        syscall_rows = [row for row in rows if row.syscall is not None]
        seccomp_rows = [row for row in rows if row.seccomp is not None]
        if len(syscall_rows) > 1:
            raise AuditSyscallMetadataGap("duplicate_syscall_record")
        if len(seccomp_rows) > 1:
            raise AuditSyscallMetadataGap("duplicate_seccomp_record")
        syscall_ordinal = syscall_rows[0].ordinal if syscall_rows else None
        seccomp_ordinal = seccomp_rows[0].ordinal if seccomp_rows else None
        result.append(
            {
                "serial": serial,
                "first_ordinal": rows[0].ordinal,
                "record_count": len(rows),
                "type_codes": [row.type_code for row in rows],
                "syscall_ordinal": syscall_ordinal,
                "seccomp_ordinal": seccomp_ordinal,
            }
        )
    return result


AuditEvent = SyscallEvent | SeccompEvent


def _event_type(event: AuditEvent) -> Literal["SYSCALL", "SECCOMP"]:
    return "SYSCALL" if isinstance(event, SyscallEvent) else "SECCOMP"


def _event_key(event: AuditEvent) -> tuple[str, str, int, str]:
    return (event.role, event.action_id, event.syscall, _event_type(event))


def _expected_key(item: ExpectedCanary) -> tuple[str, str, int, str]:
    return (item.role, item.action_id, item.syscall, item.record_type)


def _action_counts(
    events: Sequence[AuditEvent],
    expected: Sequence[ExpectedCanary],
    *,
    complete: bool,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int, str], list[AuditEvent]] = defaultdict(list)
    for event in events:
        grouped[_event_key(event)].append(event)
    output: list[dict[str, Any]] = []
    expected_keys = {
        (item.role, item.action_id, item.syscall, item.record_type) for item in expected
    }
    for item in expected:
        rows = grouped.get((item.role, item.action_id, item.syscall, item.record_type), [])
        denied_count: int | None
        if not complete:
            denied_count = None
        elif item.record_type == "SYSCALL":
            denied_count = sum(
                isinstance(row, SyscallEvent) and not row.success for row in rows
            )
        else:
            denied_count = sum(
                isinstance(row, SeccompEvent) and row.seccomp_action == "ERRNO"
                for row in rows
            )
        output.append(
            {
                "role": item.role,
                "action_id": item.action_id,
                "syscall": item.syscall,
                "record_type": item.record_type,
                "expected_count": item.count,
                "observed_count": len(rows) if complete else None,
                "denied_count": denied_count,
            }
        )
    if any(key not in expected_keys for key in grouped):
        raise AuditSyscallMetadataGap("unexpected_action")
    return output


def _record_sha256(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return sha256_hex(canonical_json(body))


def _gap_receipt(
    *,
    code: str,
    roles: Sequence[RoleBinding] = (),
    configured_syscalls: Sequence[int] = (),
    rule_binding_sha256: str | None = None,
    expected_actions: Sequence[ExpectedCanary] = (),
    audit_pid_registered: bool = False,
    audit_enabled: int | None = None,
    lost_before: int | None = None,
    lost_after: int | None = None,
    backlog_before: int | None = None,
    backlog_after: int | None = None,
    source_bound: bool = False,
    window_closed: bool = False,
) -> dict[str, Any]:
    policy = {
        "arch": AUDIT_ARCH_AARCH64,
        "configured_syscalls": list(configured_syscalls),
        "rule_binding_sha256": rule_binding_sha256,
        "expected_actions_sha256": _expected_digest(expected_actions) if expected_actions else None,
    }
    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "gap",
        "formal_admission": False,
        "claim_eligible": False,
        "role_bindings": [item.to_public() for item in roles],
        "policy": policy,
        "expected_actions": [item.to_public() for item in expected_actions],
        "records": [],
        "serial_groups": [],
        "action_counts": [
            {
                "role": item.role,
                "action_id": item.action_id,
                "syscall": item.syscall,
                "record_type": item.record_type,
                "expected_count": item.count,
                "observed_count": None,
                "denied_count": None,
            }
            for item in expected_actions
        ],
        "wire_sequence_sha256": sha256_hex(canonical_json([])),
        "audit_pid_registered": audit_pid_registered,
        "audit_enabled": audit_enabled,
        "lost_before": lost_before,
        "lost_after": lost_after,
        "backlog_before": backlog_before,
        "backlog_after": backlog_after,
        "source_bound": source_bound,
        "window_closed": window_closed,
        "failure_codes": [code],
        "record_sha256": "",
    }
    value["record_sha256"] = _record_sha256(value)
    return value


def aggregate_audit_metadata(
    records: Iterable[tuple[int, bytes]],
    *,
    role_bindings: Mapping[str, RoleBinding | Mapping[str, Any]],
    expected_actions: Sequence[ExpectedCanary | Mapping[str, Any]],
    configured_syscalls: Sequence[int],
    rule_binding_sha256: str,
    audit_pid_registered: bool,
    audit_enabled: int,
    lost_before: int,
    lost_after: int,
    backlog_before: int,
    backlog_after: int,
    source_bound: bool,
    window_closed: bool,
) -> dict[str, Any]:
    """Parse and aggregate one bounded, already-received audit window.

    ``records`` contains ``(nlmsg_type, payload)`` pairs.  The caller is
    responsible for validating the native netlink sender tuple before setting
    ``source_bound=True``.  The function never emits raw payload or raw PID.
    """

    normalized_roles: tuple[RoleBinding, ...] = ()
    normalized_actions: tuple[ExpectedCanary, ...] = ()
    normalized_syscalls: tuple[int, ...] = ()
    digest: str | None = None
    try:
        normalized_roles = normalize_role_bindings(role_bindings)
        normalized_syscalls = _normalize_syscalls(configured_syscalls)
        digest = _digest(rule_binding_sha256)
        normalized_actions = normalize_expected_actions(
            expected_actions,
            configured_syscalls=normalized_syscalls,
        )
        failures = _validate_state(
            audit_pid_registered=audit_pid_registered,
            audit_enabled=audit_enabled,
            lost_before=lost_before,
            lost_after=lost_after,
            backlog_before=backlog_before,
            backlog_after=backlog_after,
            source_bound=source_bound,
            window_closed=window_closed,
        )
        try:
            raw_records = list(islice(records, MAX_RECORDS + 1))
        except TypeError as error:
            raise AuditSyscallMetadataGap("payload_invalid") from error
        if len(raw_records) > MAX_RECORDS:
            raise AuditSyscallMetadataGap("payload_invalid")
        parsed: list[AuditRecord] = []
        for ordinal, item in enumerate(raw_records, start=1):
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or isinstance(item[0], bool)
                or not isinstance(item[0], int)
                or not isinstance(item[1], bytes)
            ):
                raise AuditSyscallMetadataGap("payload_invalid")
            parsed.append(
                parse_audit_record(
                    item[1],
                    message_type=item[0],
                    ordinal=ordinal,
                    bindings=normalized_roles,
                    configured_syscalls=normalized_syscalls,
                )
            )
        groups = _serial_groups(parsed)
        events: list[AuditEvent] = [
            event
            for record in parsed
            if (event := record.syscall or record.seccomp) is not None
        ]
        expected_by_key = {_expected_key(item): item for item in normalized_actions}
        for event in events:
            item = expected_by_key.get(_event_key(event))
            if item is None:
                raise AuditSyscallMetadataGap("unexpected_action")
            if isinstance(event, SyscallEvent):
                if event.success != item.expected_success:
                    raise AuditSyscallMetadataGap("expected_action_outcome_mismatch")
                if item.expected_errno is not None and event.errno != item.expected_errno:
                    raise AuditSyscallMetadataGap("expected_action_outcome_mismatch")
            elif event.seccomp_action != item.expected_seccomp_action:
                raise AuditSyscallMetadataGap("expected_action_outcome_mismatch")
        observed_counts = {
            key: sum(_event_key(event) == key for event in events)
            for key in expected_by_key
        }
        if any(observed_counts[key] != item.count for key, item in expected_by_key.items()):
            raise AuditSyscallMetadataGap("expected_action_count_mismatch")
        counts = _action_counts(events, normalized_actions, complete=True)
        failure_codes = sorted(set(failures))
        status = "observed" if not failure_codes else "gap"
        if status == "gap":
            counts = _action_counts(events, normalized_actions, complete=False)
        public_records = [record.to_public() for record in parsed]
        value: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "formal_admission": False,
            "claim_eligible": False,
            "role_bindings": [item.to_public() for item in normalized_roles],
            "policy": {
                "arch": AUDIT_ARCH_AARCH64,
                "configured_syscalls": list(normalized_syscalls),
                "rule_binding_sha256": digest,
                "expected_actions_sha256": _expected_digest(normalized_actions),
            },
            "expected_actions": [item.to_public() for item in normalized_actions],
            "records": public_records,
            "serial_groups": groups,
            "action_counts": counts,
            "wire_sequence_sha256": sha256_hex(canonical_json(public_records)),
            "audit_pid_registered": audit_pid_registered,
            "audit_enabled": audit_enabled,
            "lost_before": lost_before,
            "lost_after": lost_after,
            "backlog_before": backlog_before,
            "backlog_after": backlog_after,
            "source_bound": source_bound,
            "window_closed": window_closed,
            "failure_codes": failure_codes,
            "record_sha256": "",
        }
        value["record_sha256"] = _record_sha256(value)
        return value
    except AuditSyscallMetadataGap as error:
        return _gap_receipt(
            code=error.code,
            roles=normalized_roles,
            configured_syscalls=normalized_syscalls,
            rule_binding_sha256=digest,
            expected_actions=normalized_actions,
            audit_pid_registered=audit_pid_registered,
            audit_enabled=audit_enabled,
            lost_before=lost_before,
            lost_after=lost_after,
            backlog_before=backlog_before,
            backlog_after=backlog_after,
            source_bound=source_bound,
            window_closed=window_closed,
        )


def validate_metadata_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Check the closed public shape and self-digest of a generated receipt."""

    if not isinstance(value, Mapping):
        raise AuditSyscallMetadataError("metadata receipt is not an object")
    expected = {
        "schema_version",
        "status",
        "formal_admission",
        "claim_eligible",
        "role_bindings",
        "policy",
        "expected_actions",
        "records",
        "serial_groups",
        "action_counts",
        "wire_sequence_sha256",
        "audit_pid_registered",
        "audit_enabled",
        "lost_before",
        "lost_after",
        "backlog_before",
        "backlog_after",
        "source_bound",
        "window_closed",
        "failure_codes",
        "record_sha256",
    }
    if set(value) != expected:
        raise AuditSyscallMetadataError("metadata receipt fields are not closed")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise AuditSyscallMetadataError("metadata receipt version differs")
    if value.get("status") not in {"observed", "gap"}:
        raise AuditSyscallMetadataError("metadata receipt status is unknown")
    if value.get("formal_admission") is not False or value.get("claim_eligible") is not False:
        raise AuditSyscallMetadataError("metadata receipt cannot grant admission")
    if value.get("record_sha256") != _record_sha256(value):
        raise AuditSyscallMetadataError("metadata receipt digest differs")
    if not isinstance(value.get("records"), list) or len(value["records"]) > MAX_RECORDS:
        raise AuditSyscallMetadataError("metadata records are unbounded")
    return dict(value)


__all__ = [
    "AF_NETLINK",
    "AF_PACKET",
    "AF_VSOCK",
    "AUDIT_ARCH_AARCH64",
    "AUDIT_SYSCALL",
    "SCHEMA_VERSION",
    "AuditRecord",
    "AuditSyscallMetadataError",
    "AuditSyscallMetadataGap",
    "ExpectedCanary",
    "RoleBinding",
    "SeccompEvent",
    "SyscallEvent",
    "aggregate_audit_metadata",
    "canonical_json",
    "normalize_expected_actions",
    "normalize_role_bindings",
    "parse_audit_record",
    "parse_seccomp_payload",
    "parse_syscall_payload",
    "sha256_hex",
    "validate_metadata_receipt",
]
