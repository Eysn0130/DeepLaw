"""Bounded Linux guest audit observation for an external slot collector.

This module is deliberately an observation intermediate, not a sandbox or a
qualification authority.  It opens the Linux ``NETLINK_AUDIT`` socket when run
inside the guest, registers the collector with the kernel, installs and binds a
small caller-supplied rule set, and retains only fixed audit type/serial
metadata.  It does not launch a Host, install seccomp, create namespaces, or
read command, path, environment, transcript, or output fields.

The guest is expected to run this process as its trusted root observer.  The
real signing key and any external admission decision remain outside the guest.
All public receipts set ``formal_admission`` and ``claim_eligible`` to false;
an external collector must bind this intermediate to the Host/VZ observation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import stat
import struct
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = "deeplaw.linux-guest-observation/v1"
NETLINK_AUDIT: Final = 9

AUDIT_GET: Final = 1000
AUDIT_SET: Final = 1001
AUDIT_ADD_RULE: Final = 1011
AUDIT_LIST_RULES: Final = 1013

AUDIT_STATUS_ENABLED: Final = 0x0001
AUDIT_STATUS_PID: Final = 0x0004

AUDIT_FILTER_EXIT: Final = 0x04
AUDIT_ALWAYS: Final = 2
AUDIT_EQUAL: Final = 0x40000000
AUDIT_ARCH: Final = 11
AUDIT_FILTERKEY: Final = 210
AUDIT_ARCH_AARCH64: Final = 0xC00000B7
AUDIT_MAX_FIELDS: Final = 64
AUDIT_BITMASK_SIZE: Final = 64

NLMSG_NOOP: Final = 1
NLMSG_ERROR: Final = 2
NLMSG_DONE: Final = 3
NLM_F_REQUEST: Final = 0x0001
NLM_F_MULTI: Final = 0x0002
NLM_F_ACK: Final = 0x0004
NLM_F_DUMP: Final = 0x0300

_NETLINK_HEADER = struct.Struct("<IHHII")
_AUDIT_STATUS = struct.Struct("<11I")
_AUDIT_RULE_FIXED = struct.Struct(
    "<III" f"{AUDIT_BITMASK_SIZE}I" f"{AUDIT_MAX_FIELDS}I" f"{AUDIT_MAX_FIELDS}I"
    f"{AUDIT_MAX_FIELDS}I" "I"
)
_AUDIT_RULE_FIXED_BYTES: Final = _AUDIT_RULE_FIXED.size
_MAX_NETLINK_PACKET: Final = 64 * 1024
_MAX_AUDIT_PAYLOAD: Final = 8192
_MAX_AUDIT_EVENTS: Final = 4096
_MAX_AUDIT_FRAMES: Final = 8192
_MAX_RULES: Final = 32
_MAX_RULE_BYTES: Final = _AUDIT_RULE_FIXED_BYTES + 256
_MAX_CGROUP_BYTES: Final = 4096
_MAX_PROC_STATUS_BYTES: Final = 8192
_MAX_NAMESPACE_LINK_BYTES: Final = 256
_MAX_ROLE_NAME_BYTES: Final = 64
_MAX_TIMEOUT_SECONDS: Final = 30.0

_AUDIT_MESSAGE_RE = re.compile(rb"^audit\([^:()\s]{1,128}:(\d{1,20})\):")
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# The list covers the audit message kinds used by the guest's syscall/rule
# path.  A future kernel message outside this list is deliberately a gap until
# the observer is updated and reviewed.
KNOWN_AUDIT_TYPES: Final = frozenset(
    {
        "ANOM_ABEND",
        "ANOM_ADD_ACCT",
        "ANOM_DEL_ACCT",
        "ANOM_LOGIN_FAILURES",
        "ANOM_LOGIN_SESSIONS",
        "ANOM_LOGIN_TIME",
        "ANOM_MAX_DAC",
        "ANOM_MAX_MAC",
        "ANOM_MOD_ACCT",
        "ANOM_PROMAUDIT",
        "ANOM_PROM_POLICY",
        "ANOM_ROOT_TRANS",
        "AVC",
        "BPRM_FCAPS",
        "CAPSET",
        "CRED_ACQ",
        "CRED_DISP",
        "CRED_REFR",
        "CRED_REP",
        "CRED_REJECT",
        "CRED_REQ",
        "CONFIG_CHANGE",
        "CONFIG_CHANGE_USER",
        "CWD",
        "DAEMON_ABORT",
        "DAEMON_ACCEPT",
        "DAEMON_CONFIG",
        "DAEMON_END",
        "DAEMON_RESUME",
        "DAEMON_ROTATE",
        "DAEMON_START",
        "DAEMON_TEST",
        "EOE",
        "EXECVE",
        "KERNEL",
        "MMAP",
        "LANDLOCK_ACCESS_FS",
        "LANDLOCK_ACCESS",
        "MAC_CIPSOV4_ADD",
        "MAC_CIPSOV4_DEL",
        "MAC_CONFIG_CHANGE",
        "MAC_IPSEC_EVENT",
        "MAC_MAP_ADD",
        "MAC_MAP_DEL",
        "MAC_POLICY_LOAD",
        "MAC_STATUS",
        "MAC_UNLBL_ALLOW",
        "MAC_UNLBL_STCADD",
        "MAC_UNLBL_STCDEL",
        "MOUNT",
        "NETFILTER_CFG",
        "NETFILTER_PKT",
        "OBJ_PID",
        "PATH",
        "PROCTITLE",
        "RESP_ACCT_LOCK",
        "RESP_ACCT_UNLOCK",
        "RESP_ALERT",
        "RESP_ANOMALY",
        "RESP_EXEC",
        "RESP_KILL",
        "RESP_TERM_LOCK",
        "RESP_TERM_UNLOCK",
        "SECCOMP",
        "SELINUX_ERR",
        "SOCKADDR",
        "SOCKETCALL",
        "SYSCALL",
        "TTY",
        "USER_ACCT",
        "USER_AVC",
        "USER_CHAUTHTOK",
        "USER_END",
        "USER_ERR",
        "USER_LOGIN",
        "USER_MAC_POLICY_LOAD",
        "USER_MGMT",
        "USER_ROLE_CHANGE",
        "USER_SELINUX_ERR",
        "USER_START",
        "USER_TTY",
        "USER_UNLABELED_EXPORT",
        "USER_CMD",
        "USER_UNSET",
        "URINGOP",
    }
)

# Kernel audit records carry this numeric type in ``nlmsghdr.nlmsg_type``.
# The payload itself starts with ``audit(...)`` and does not repeat the type.
# Keep this map explicit so a user-space message cannot self-report a type that
# the observer then accepts as kernel evidence.
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
_SUPPORTED_AUDIT_TYPES: Final = frozenset(_AUDIT_TYPE_NAMES.values())

_FAILURE_CODES: Final = frozenset(
    {
        "audit_not_available",
        "audit_pid_not_registered",
        "audit_disabled",
        "audit_status_unknown",
        "audit_rule_binding_missing",
        "audit_rule_binding_unknown",
        "audit_lost_changed",
        "audit_backlog_not_empty",
        "audit_event_missing",
        "audit_event_unknown",
        "audit_frame_limit",
        "audit_payload_invalid",
        "audit_consumer_failed",
        "role_metadata_missing",
        "role_uid_invalid",
        "role_namespace_not_distinct",
        "role_cgroup_missing",
        "role_cgroup_populated",
        "transport_error",
    }
)


class LinuxGuestObservationError(ValueError):
    """A native observation or sanitized receipt is unsafe or incomplete."""


class NativeAuditUnavailable(LinuxGuestObservationError):
    """The current process cannot open the Linux audit netlink facility."""


class AuditProtocolError(LinuxGuestObservationError):
    """The kernel audit netlink response is malformed or not acknowledged."""


class ObservationGap(LinuxGuestObservationError):
    """The observation has an unknown, missing, dropped, or unbound portion."""


def canonical_json(value: Any) -> bytes:
    """Encode a value without nondeterministic JSON features."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise LinuxGuestObservationError("observation is not canonical JSON") from error


def sha256_hex(value: bytes) -> str:
    """Return one opaque SHA-256 digest."""

    return hashlib.sha256(value).hexdigest()


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise LinuxGuestObservationError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _bounded_int(value: Any, *, label: str, maximum: int = 2**32 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise LinuxGuestObservationError(f"{label} is invalid")
    return value


def _safe_role(value: Any) -> str:
    if not isinstance(value, str) or _ROLE_RE.fullmatch(value) is None:
        raise LinuxGuestObservationError("role is invalid")
    if len(value.encode("utf-8")) > _MAX_ROLE_NAME_BYTES:
        raise LinuxGuestObservationError("role is invalid")
    return value


def _sha_projection(values: Sequence[str]) -> str:
    return sha256_hex(canonical_json(sorted(values)))


@dataclass(frozen=True, slots=True)
class AuditStatus:
    """The fixed, non-content portion of ``struct audit_status``."""

    enabled: int
    pid: int
    failure: int
    rate_limit: int
    backlog_limit: int
    lost: int
    backlog: int
    feature_bitmap: int
    backlog_wait_time: int
    backlog_wait_time_actual: int

    @classmethod
    def from_payload(cls, payload: bytes) -> AuditStatus:
        if not isinstance(payload, bytes) or len(payload) != _AUDIT_STATUS.size:
            raise AuditProtocolError("audit status payload is missing or has an unknown size")
        try:
            (
                _mask,
                enabled,
                failure,
                pid,
                rate_limit,
                backlog_limit,
                lost,
                backlog,
                feature_bitmap,
                backlog_wait_time,
                backlog_wait_time_actual,
            ) = _AUDIT_STATUS.unpack(payload)
        except struct.error as error:
            raise AuditProtocolError("audit status payload is malformed") from error
        return cls(
            enabled=enabled,
            pid=pid,
            failure=failure,
            rate_limit=rate_limit,
            backlog_limit=backlog_limit,
            lost=lost,
            backlog=backlog,
            feature_bitmap=feature_bitmap,
            backlog_wait_time=backlog_wait_time,
            backlog_wait_time_actual=backlog_wait_time_actual,
        )

    def public(self, *, port_id: int) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "pid_registered": self.pid == port_id,
            "lost": self.lost,
            "backlog": self.backlog,
            "failure": self.failure,
            "rate_limit": self.rate_limit,
            "backlog_limit": self.backlog_limit,
            "feature_bitmap": self.feature_bitmap,
            "backlog_wait_time": self.backlog_wait_time,
            "backlog_wait_time_actual": self.backlog_wait_time_actual,
        }


@dataclass(frozen=True, slots=True)
class AuditRuleSpec:
    """A small architecture-bound exit rule encoded through audit netlink."""

    syscall_numbers: tuple[int, ...]
    arch: int = AUDIT_ARCH_AARCH64
    key: str = "deeplaw_guest_observer"
    flags: int = AUDIT_FILTER_EXIT
    action: int = AUDIT_ALWAYS
    uid: int | None = None
    success: bool | None = None

    def __post_init__(self) -> None:
        if not self.syscall_numbers or len(self.syscall_numbers) > AUDIT_BITMASK_SIZE * 32:
            raise LinuxGuestObservationError("audit rule syscall set is invalid")
        if tuple(sorted(set(self.syscall_numbers))) != self.syscall_numbers:
            raise LinuxGuestObservationError("audit rule syscall set is not canonical")
        if any(
            isinstance(number, bool) or not isinstance(number, int) or not 0 <= number < 2048
            for number in self.syscall_numbers
        ):
            raise LinuxGuestObservationError("audit rule syscall number is invalid")
        if (
            isinstance(self.arch, bool)
            or not isinstance(self.arch, int)
            or not 0 <= self.arch < 2**32
        ):
            raise LinuxGuestObservationError("audit rule architecture is invalid")
        if not isinstance(self.key, str) or _KEY_RE.fullmatch(self.key) is None:
            raise LinuxGuestObservationError("audit rule key is invalid")
        if self.flags != AUDIT_FILTER_EXIT or self.action != AUDIT_ALWAYS:
            raise LinuxGuestObservationError("audit rule action or filter is unsupported")
        if self.uid is not None and (type(self.uid) is not int or self.uid not in {1000, 1001}):
            raise LinuxGuestObservationError("audit role uid is invalid")
        if self.success is not None and type(self.success) is not bool:
            raise LinuxGuestObservationError("audit success filter is invalid")

    def to_public(self) -> dict[str, Any]:
        result = {
            "syscall_numbers": list(self.syscall_numbers),
            "arch": self.arch,
            "key_sha256": sha256_hex(self.key.encode("ascii")),
            "flags": self.flags,
            "action": self.action,
        }
        if self.uid is not None:
            result["uid"] = self.uid
        if self.success is not None:
            result["success"] = self.success
        return result

    def encode(self) -> bytes:
        mask = [0] * AUDIT_BITMASK_SIZE
        for number in self.syscall_numbers:
            mask[number // 32] |= 1 << (number % 32)
        fields = [0] * AUDIT_MAX_FIELDS
        values = [0] * AUDIT_MAX_FIELDS
        fieldflags = [0] * AUDIT_MAX_FIELDS
        # AUDIT_ARCH is 11 in the Linux audit UAPI.
        fields[0] = AUDIT_ARCH
        values[0] = self.arch
        fieldflags[0] = AUDIT_EQUAL
        # The key is a second string-valued field.  ``buflen`` is the byte
        # count excluding the terminating NUL; the kernel adds that terminator
        # while unpacking the rule.
        key_bytes = self.key.encode("ascii")
        fields[1] = AUDIT_FILTERKEY
        values[1] = len(key_bytes)
        fieldflags[1] = AUDIT_EQUAL
        field_count = 2
        # Linux v6.18 include/uapi/linux/audit.h: AUDIT_UID=1, AUDIT_SUCCESS=104.
        for field, value in ((1, self.uid), (104, self.success)):
            if value is not None:
                fields[field_count] = field
                values[field_count] = int(value)
                fieldflags[field_count] = AUDIT_EQUAL
                field_count += 1
        if len(key_bytes) > 256:
            raise LinuxGuestObservationError("audit rule key exceeds its bound")
        fixed = _AUDIT_RULE_FIXED.pack(
            self.flags,
            self.action,
            field_count,
            *mask,
            *fields,
            *values,
            *fieldflags,
            len(key_bytes),
        )
        encoded = fixed + key_bytes
        if len(encoded) > _MAX_RULE_BYTES:
            raise LinuxGuestObservationError("audit rule exceeds its byte bound")
        return encoded

    def digest(self) -> str:
        return sha256_hex(self.encode())


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Content-minimized audit event metadata."""

    ordinal: int
    message_type: str
    serial: int

    def to_public(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "message_type": self.message_type,
            "serial": self.serial,
        }


def parse_audit_payload(payload: bytes, *, message_type: int, ordinal: int) -> AuditEvent:
    """Parse a kernel netlink type and audit serial; never retain the payload."""

    if not isinstance(payload, bytes) or not 1 <= len(payload) <= _MAX_AUDIT_PAYLOAD:
        raise ObservationGap("audit payload exceeds its bound")
    if (
        isinstance(message_type, bool)
        or not isinstance(message_type, int)
        or not 1300 <= message_type <= 2099
    ):
        raise ObservationGap("audit message type is unknown")
    public_message_type = _AUDIT_TYPE_NAMES.get(message_type)
    if public_message_type is None or public_message_type not in _SUPPORTED_AUDIT_TYPES:
        raise ObservationGap("audit message type is unknown")
    match = _AUDIT_MESSAGE_RE.match(payload)
    if match is None:
        raise ObservationGap("audit payload header is unknown")
    try:
        serial = int(match.group(1))
    except ValueError as error:
        raise ObservationGap("audit payload header is malformed") from error
    if not 1 <= serial <= 2**63 - 1:
        raise ObservationGap("audit serial is invalid")
    if not 1 <= ordinal <= _MAX_AUDIT_EVENTS:
        raise ObservationGap("audit event count exceeds its bound")
    return AuditEvent(ordinal=ordinal, message_type=public_message_type, serial=serial)


def sequence_sha256(events: Sequence[AuditEvent | Mapping[str, Any]]) -> str:
    """Bind the ordered, sanitized audit event sequence."""

    projection: list[dict[str, Any]] = []
    for event in events:
        if isinstance(event, AuditEvent):
            projection.append(event.to_public())
        elif isinstance(event, Mapping):
            projection.append(
                {
                    "ordinal": event.get("ordinal"),
                    "message_type": event.get("message_type"),
                    "serial": event.get("serial"),
                }
            )
        else:
            raise LinuxGuestObservationError("audit event sequence contains an invalid row")
    return sha256_hex(canonical_json(projection))


class NetlinkAuditTransport:
    """Small raw ``NETLINK_AUDIT`` transport with bounded replies."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 2.0,
        socket_factory: Callable[..., Any] | None = None,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise NativeAuditUnavailable("audit timeout is invalid")
        timeout = float(timeout_seconds)
        if not 0 < timeout <= _MAX_TIMEOUT_SECONDS:
            raise NativeAuditUnavailable("audit timeout is invalid")
        factory = socket_factory or socket.socket
        if socket_factory is None and (
            sys.platform != "linux" or not hasattr(socket, "AF_NETLINK")
        ):
            raise NativeAuditUnavailable("Linux NETLINK_AUDIT is unavailable")
        if socket_factory is None and os.geteuid() != 0:
            raise NativeAuditUnavailable("guest audit observer must run as root")
        try:
            self._socket = factory(
                getattr(socket, "AF_NETLINK", 16),
                socket.SOCK_RAW,
                getattr(socket, "NETLINK_AUDIT", NETLINK_AUDIT),
            )
            self._socket.settimeout(timeout)
            self._socket.bind((0, 0))
            address = self._socket.getsockname()
            port_id = address[0] if isinstance(address, tuple) and address else None
        except (OSError, TypeError, ValueError, IndexError) as error:
            raise NativeAuditUnavailable("Linux NETLINK_AUDIT could not be opened") from error
        if isinstance(port_id, bool) or not isinstance(port_id, int) or port_id <= 0:
            self.close()
            raise NativeAuditUnavailable("audit netlink port identity is unavailable")
        self.port_id = port_id
        self._timeout_seconds = timeout
        self._next_sequence = 0
        self._pending_events: list[bytes] = []

    def close(self) -> None:
        sock = getattr(self, "_socket", None)
        if sock is not None:
            with suppress(OSError):
                sock.close()
            self._socket = None

    def __enter__(self) -> NetlinkAuditTransport:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def _align(length: int) -> int:
        return (length + 3) & ~3

    @classmethod
    def _pack_message(
        cls, message_type: int, flags: int, sequence: int, payload: bytes
    ) -> bytes:
        length = _NETLINK_HEADER.size + len(payload)
        packet = _NETLINK_HEADER.pack(length, message_type, flags, sequence, 0) + payload
        return packet + b"\x00" * ((-length) % 4)

    @classmethod
    def _messages(cls, packet: bytes) -> Iterable[tuple[int, int, int, bytes]]:
        if not isinstance(packet, bytes) or not 1 <= len(packet) <= _MAX_NETLINK_PACKET:
            raise AuditProtocolError("audit netlink packet is missing or too large")
        offset = 0
        while offset < len(packet):
            remaining = len(packet) - offset
            if remaining < _NETLINK_HEADER.size:
                if packet[offset:] == b"\x00" * remaining:
                    return
                raise AuditProtocolError("audit netlink header is truncated")
            try:
                length, message_type, flags, sequence, _port_id = _NETLINK_HEADER.unpack_from(
                    packet, offset
                )
            except struct.error as error:
                raise AuditProtocolError("audit netlink header is malformed") from error
            if 1300 <= message_type <= 2099 and (flags != 0 or sequence != 0 or _port_id != 0):
                raise AuditProtocolError("audit event kernel header is invalid")
            # Older Linux auditd unicast records intentionally carry the
            # payload length in nlmsg_len.  The skb still includes the 16-byte
            # netlink header, so an async kernel audit datagram has
            # ``remaining == NLMSG_HDRLEN + nlmsg_len``.  Keep this narrow to
            # kernel audit types and the header shape emitted by audit.c; all
            # other messages use the standard netlink contract below.
            if (
                _NETLINK_HEADER.size <= length
                and 1300 <= message_type <= 2099
                and flags == 0
                and sequence == 0
                and _port_id == 0
            ):
                length_delta = remaining - length
                if length_delta == _NETLINK_HEADER.size:
                    payload_end = offset + _NETLINK_HEADER.size + length
                    payload = packet[offset + _NETLINK_HEADER.size : payload_end]
                    if len(payload) > _MAX_AUDIT_PAYLOAD:
                        raise AuditProtocolError("audit netlink payload exceeds its bound")
                    # auditd sends one audit skb as one netlink datagram.  Do
                    # not interpret any unconsumed bytes as padding or a
                    # second message.
                    yield message_type, flags, sequence, payload
                    return
                # A standard message may have at most three bytes of alignment
                # beyond nlmsg_len.  Any larger delta is a malformed or mixed
                # legacy frame and must not be silently consumed.
                if length_delta < 0 or length_delta > 3:
                    raise AuditProtocolError("audit netlink audit frame length is invalid")
            if length < _NETLINK_HEADER.size or length > remaining:
                raise AuditProtocolError("audit netlink message length is invalid")
            aligned = cls._align(length)
            if aligned > remaining:
                raise AuditProtocolError("audit netlink message alignment is invalid")
            payload = packet[offset + _NETLINK_HEADER.size : offset + length]
            if len(payload) > _MAX_AUDIT_PAYLOAD:
                raise AuditProtocolError("audit netlink payload exceeds its bound")
            yield message_type, flags, sequence, payload
            offset += aligned

    def _request(
        self,
        message_type: int,
        payload: bytes,
        *,
        dump: bool = False,
    ) -> list[bytes]:
        if self._socket is None:
            raise NativeAuditUnavailable("audit netlink transport is closed")
        if not isinstance(payload, bytes) or len(payload) > _MAX_AUDIT_PAYLOAD:
            raise AuditProtocolError("audit netlink request payload exceeds its bound")
        self._next_sequence += 1
        sequence = self._next_sequence
        # AUDIT_GET already has a data reply. Requesting a separate ACK can
        # deliver that ACK before the asynchronous status reply, or leave it
        # pending for the next request. Mutations need the ACK instead.
        flags = NLM_F_REQUEST | (
            NLM_F_DUMP if dump else (0 if message_type == AUDIT_GET else NLM_F_ACK)
        )
        length = _NETLINK_HEADER.size + len(payload)
        packet = _NETLINK_HEADER.pack(length, message_type, flags, sequence, 0) + payload
        try:
            sendto = getattr(self._socket, "sendto", None)
            if callable(sendto):
                sendto(packet, (0, 0))
            else:
                self._socket.sendall(packet)
        except OSError as error:
            raise NativeAuditUnavailable("audit netlink request could not be sent") from error
        replies: list[bytes] = []
        for _ in range(_MAX_AUDIT_FRAMES):
            try:
                received = self._receive_kernel_packet(_MAX_NETLINK_PACKET)
            except TimeoutError as error:
                raise AuditProtocolError("audit netlink response timed out") from error
            except OSError as error:
                raise NativeAuditUnavailable("audit netlink response could not be read") from error
            ended = False
            messages = self._messages(received)
            for response_type, _response_flags, response_sequence, response_payload in messages:
                if response_sequence != sequence:
                    if response_sequence == 0 and response_type >= 1100:
                        if len(self._pending_events) >= _MAX_AUDIT_FRAMES:
                            raise AuditProtocolError("pending audit event frame limit exceeded")
                        self._pending_events.append(
                            self._pack_message(
                                response_type,
                                _response_flags,
                                response_sequence,
                                response_payload,
                            )
                        )
                        continue
                    raise AuditProtocolError("audit netlink response sequence differs")
                if response_type == NLMSG_NOOP:
                    continue
                if response_type == NLMSG_ERROR:
                    if len(response_payload) < 4:
                        raise AuditProtocolError("audit netlink error response is truncated")
                    (error_code,) = struct.unpack_from("<i", response_payload)
                    if error_code != 0:
                        raise AuditProtocolError("audit netlink request was rejected")
                    ended = True
                    continue
                if response_type == NLMSG_DONE:
                    ended = True
                    continue
                if response_type != message_type:
                    raise AuditProtocolError("audit netlink response type differs")
                replies.append(response_payload)
            if ended:
                return replies
            if not dump and replies:
                return replies
        raise AuditProtocolError("audit netlink response frame limit exceeded")

    def get_status(self) -> AuditStatus:
        replies = self._request(AUDIT_GET, b"")
        if len(replies) != 1:
            raise AuditProtocolError("audit status response count is unknown")
        return AuditStatus.from_payload(replies[0])

    def set_status(self, *, mask: int, enabled: int = 0, pid: int = 0) -> None:
        if mask not in {
            AUDIT_STATUS_PID,
            AUDIT_STATUS_ENABLED,
            AUDIT_STATUS_PID | AUDIT_STATUS_ENABLED,
        }:
            raise AuditProtocolError("audit status update mask is unsupported")
        values = [0] * 11
        values[0] = mask
        values[1] = enabled
        values[3] = pid
        self._request(AUDIT_SET, _AUDIT_STATUS.pack(*values))

    def list_rule_digests(self) -> list[str]:
        replies = self._request(AUDIT_LIST_RULES, b"", dump=True)
        if len(replies) > _MAX_RULES:
            raise AuditProtocolError("audit rule response count exceeds its bound")
        digests: list[str] = []
        for reply in replies:
            if not 1 <= len(reply) <= _MAX_RULE_BYTES:
                raise AuditProtocolError("audit rule response size is unknown")
            digest = sha256_hex(reply)
            if digest in digests:
                raise AuditProtocolError("audit rule response is duplicated")
            digests.append(digest)
        return sorted(digests)

    def add_rule(self, rule: AuditRuleSpec) -> None:
        self._request(AUDIT_ADD_RULE, rule.encode())

    def _receive_kernel_packet(self, size: int) -> bytes:
        packet, ancillary, flags, address = self._socket.recvmsg(size)
        if address != (0, 0) or ancillary or flags != 0:
            raise AuditProtocolError("audit netlink sender or datagram boundary is invalid")
        return packet

    def recv(self, size: int = _MAX_NETLINK_PACKET) -> bytes:
        if self._socket is None:
            raise NativeAuditUnavailable("audit netlink transport is closed")
        if not 1 <= size <= _MAX_NETLINK_PACKET:
            raise AuditProtocolError("audit receive size is invalid")
        if self._pending_events:
            return self._pending_events.pop(0)
        try:
            return self._receive_kernel_packet(size)
        except (TimeoutError, BlockingIOError):
            return b""
        except OSError as error:
            raise NativeAuditUnavailable("audit netlink event could not be read") from error


def _require_linux_transport(transport: Any | None) -> Any:
    return transport if transport is not None else NetlinkAuditTransport()


@dataclass(frozen=True, slots=True)
class RoleIdentity:
    """Identity metadata captured while a target process is still alive."""

    role: str
    uid: int
    mount_namespace_sha256: str
    pid_namespace_sha256: str
    net_namespace_sha256: str
    ipc_namespace_sha256: str
    cgroup_id_sha256: str | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "uid": self.uid,
            "mount_namespace_sha256": self.mount_namespace_sha256,
            "pid_namespace_sha256": self.pid_namespace_sha256,
            "net_namespace_sha256": self.net_namespace_sha256,
            "ipc_namespace_sha256": self.ipc_namespace_sha256,
        }


@dataclass(frozen=True, slots=True)
class RoleObservation:
    """Sanitized identity plus final cgroup metadata for one target role."""

    role: str
    uid: int
    mount_namespace_sha256: str
    pid_namespace_sha256: str
    net_namespace_sha256: str
    ipc_namespace_sha256: str
    cgroup_id_sha256: str
    cgroup_events_sha256: str
    cgroup_populated: int

    def to_public(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "uid": self.uid,
            "mount_namespace_sha256": self.mount_namespace_sha256,
            "pid_namespace_sha256": self.pid_namespace_sha256,
            "net_namespace_sha256": self.net_namespace_sha256,
            "ipc_namespace_sha256": self.ipc_namespace_sha256,
            "cgroup_id_sha256": self.cgroup_id_sha256,
            "cgroup_events_sha256": self.cgroup_events_sha256,
            "cgroup_populated": self.cgroup_populated,
        }


def _read_bounded_file(path: Path, *, limit: int) -> bytes:
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            data = os.read(descriptor, limit + 1)
        finally:
            os.close(descriptor)
    except (OSError, TypeError, ValueError) as error:
        raise ObservationGap("required native metadata is unavailable") from error
    if len(data) > limit:
        raise ObservationGap("required native metadata exceeds its bound")
    return data


def _read_uid(proc_dir: Path) -> int:
    data = _read_bounded_file(proc_dir / "status", limit=_MAX_PROC_STATUS_BYTES)
    for line in data.splitlines():
        if line.startswith(b"Uid:"):
            fields = line.split()
            if len(fields) < 2:
                break
            try:
                uid = int(fields[1])
            except ValueError:
                break
            if uid < 1:
                break
            return uid
    raise ObservationGap("role uid is missing")


def _namespace_digest(proc_dir: Path, name: str) -> str:
    if name not in {"mnt", "pid", "net", "ipc"}:
        raise ObservationGap("namespace kind is unsupported")
    try:
        target = os.readlink(proc_dir / "ns" / name)
    except (OSError, TypeError, ValueError) as error:
        raise ObservationGap("role namespace metadata is unavailable") from error
    if not 1 <= len(target.encode("utf-8")) <= _MAX_NAMESPACE_LINK_BYTES:
        raise ObservationGap("role namespace metadata exceeds its bound")
    if not re.fullmatch(r"[a-z]+:\[[0-9]+\]", target):
        raise ObservationGap("role namespace metadata is malformed")
    return sha256_hex(target.encode("ascii"))


def _read_cgroup(cgroup_dir: Path) -> tuple[str, str, int]:
    cgroup_id_digest = _cgroup_id_digest(cgroup_dir)
    data = _read_bounded_file(cgroup_dir / "cgroup.events", limit=_MAX_CGROUP_BYTES)
    values: dict[str, int] = {}
    for line in data.splitlines():
        fields = line.split()
        if len(fields) != 2 or not re.fullmatch(rb"[a-z_]+", fields[0]):
            raise ObservationGap("role cgroup events are malformed")
        try:
            parsed = int(fields[1])
        except ValueError as error:
            raise ObservationGap("role cgroup event value is malformed") from error
        if parsed not in {0, 1}:
            raise ObservationGap("role cgroup event value is outside its bound")
        key = fields[0].decode("ascii")
        if key in values:
            raise ObservationGap("role cgroup event is duplicated")
        values[key] = parsed
    if values.get("populated") != 0:
        raise ObservationGap("role cgroup remains populated")
    return cgroup_id_digest, sha256_hex(canonical_json(values)), 0


def _cgroup_id_digest(cgroup_dir: Path) -> str:
    try:
        metadata = cgroup_dir.stat(follow_symlinks=False)
    except (OSError, TypeError, ValueError) as error:
        raise ObservationGap("role cgroup is unavailable") from error
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0:
        raise ObservationGap("role cgroup is not a root-owned directory")
    return sha256_hex(f"{metadata.st_dev}:{metadata.st_ino}".encode("ascii"))


def observe_role_identity(
    *, role: str, pid: int, cgroup_dir: str | os.PathLike[str] | None = None
) -> RoleIdentity:
    """Capture one live role's UID and namespace IDs before it exits."""

    safe_role = _safe_role(role)
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ObservationGap("role process identity is invalid")
    proc_dir = Path("/proc") / str(pid)
    cgroup_id = _cgroup_id_digest(Path(cgroup_dir)) if cgroup_dir is not None else None
    return RoleIdentity(
        role=safe_role,
        uid=_read_uid(proc_dir),
        mount_namespace_sha256=_namespace_digest(proc_dir, "mnt"),
        pid_namespace_sha256=_namespace_digest(proc_dir, "pid"),
        net_namespace_sha256=_namespace_digest(proc_dir, "net"),
        ipc_namespace_sha256=_namespace_digest(proc_dir, "ipc"),
        cgroup_id_sha256=cgroup_id,
    )


def observe_role_cgroup(
    identity: RoleIdentity, *, cgroup_dir: str | os.PathLike[str]
) -> RoleObservation:
    """Bind a live identity snapshot to the cgroup state after exit."""

    if not isinstance(identity, RoleIdentity):
        raise ObservationGap("role identity snapshot is missing")
    cgroup_path = Path(cgroup_dir)
    cgroup_id_digest, cgroup_digest, populated = _read_cgroup(cgroup_path)
    if identity.cgroup_id_sha256 is None or identity.cgroup_id_sha256 != cgroup_id_digest:
        raise ObservationGap("role cgroup binding differs")
    return RoleObservation(
        role=identity.role,
        uid=identity.uid,
        mount_namespace_sha256=identity.mount_namespace_sha256,
        pid_namespace_sha256=identity.pid_namespace_sha256,
        net_namespace_sha256=identity.net_namespace_sha256,
        ipc_namespace_sha256=identity.ipc_namespace_sha256,
        cgroup_id_sha256=cgroup_id_digest,
        cgroup_events_sha256=cgroup_digest,
        cgroup_populated=populated,
    )


def observe_role(*, role: str, pid: int, cgroup_dir: str | os.PathLike[str]) -> RoleObservation:
    """Read one role's identity and final cgroup in a single live snapshot."""

    identity = observe_role_identity(role=role, pid=pid, cgroup_dir=cgroup_dir)
    return observe_role_cgroup(identity, cgroup_dir=cgroup_dir)


@dataclass(frozen=True, slots=True)
class AuditCollectorConfig:
    """Closed bounds and exact audit rule/event expectations."""

    rules: tuple[AuditRuleSpec, ...]
    required_event_types: tuple[str, ...]
    max_events: int = _MAX_AUDIT_EVENTS
    drain_timeout_seconds: float = 0.25

    def __post_init__(self) -> None:
        if not self.rules or len(self.rules) > _MAX_RULES:
            raise LinuxGuestObservationError("audit rule configuration is invalid")
        rule_digests = [rule.digest() for rule in self.rules]
        if len(set(rule_digests)) != len(rule_digests):
            raise LinuxGuestObservationError("audit rule configuration is duplicated")
        if (
            not self.required_event_types
            or len(self.required_event_types) > len(_SUPPORTED_AUDIT_TYPES)
            or tuple(sorted(set(self.required_event_types))) != self.required_event_types
            or any(event not in _SUPPORTED_AUDIT_TYPES for event in self.required_event_types)
        ):
            raise LinuxGuestObservationError("required audit event types are invalid")
        if isinstance(self.max_events, bool) or not 1 <= self.max_events <= _MAX_AUDIT_EVENTS:
            raise LinuxGuestObservationError("audit event bound is invalid")
        if isinstance(self.drain_timeout_seconds, bool) or not isinstance(
            self.drain_timeout_seconds, (int, float)
        ):
            raise LinuxGuestObservationError("audit drain timeout is invalid")
        if not 0 < float(self.drain_timeout_seconds) <= _MAX_TIMEOUT_SECONDS:
            raise LinuxGuestObservationError("audit drain timeout is invalid")


class AuditCollector:
    """Configure one bounded audit window.

    An optional trusted, in-process consumer receives validated kernel payloads
    for numeric correlation. It must bound transient memory and never export
    raw payloads; consumer failure makes the observation a gap.
    """

    def __init__(
        self,
        config: AuditCollectorConfig,
        *,
        transport: Any | None = None,
        event_consumer: Callable[[int, bytes], None] | None = None,
    ) -> None:
        self.config = config
        self.transport = _require_linux_transport(transport)
        self._event_consumer = event_consumer
        self._started = False
        self._closed = False
        self._start_status: AuditStatus | None = None
        self._end_status: AuditStatus | None = None
        self._configured_rule_digests = [rule.digest() for rule in config.rules]
        self._observed_rule_digests: list[str] = []
        self._events: list[AuditEvent] = []
        self._frame_count = 0
        self._unknown_event_count = 0
        self._failure_codes: set[str] = set()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            close = getattr(self.transport, "close", None)
            if callable(close):
                close()

    def __enter__(self) -> AuditCollector:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _status(self) -> AuditStatus:
        try:
            status = self.transport.get_status()
        except LinuxGuestObservationError:
            self._failure_codes.add("audit_status_unknown")
            raise
        if not isinstance(status, AuditStatus):
            self._failure_codes.add("audit_status_unknown")
            raise AuditProtocolError("audit status object is unknown")
        return status

    def _rules(self) -> list[str]:
        try:
            values = self.transport.list_rule_digests()
        except LinuxGuestObservationError:
            self._failure_codes.add("audit_rule_binding_unknown")
            raise
        if (
            not isinstance(values, list)
            or len(values) > _MAX_RULES
            or any(_SHA256_RE.fullmatch(value or "") is None for value in values)
            or len(set(values)) != len(values)
        ):
            self._failure_codes.add("audit_rule_binding_unknown")
            raise AuditProtocolError("audit rule digest list is unknown")
        return sorted(values)

    def start(self) -> None:
        """Register the collector, enable audit, and bind every configured rule."""

        if self._closed or self._started:
            raise AuditProtocolError("audit collector lifecycle is invalid")
        before = self._status()
        if before.failure not in {0, 1, 2}:
            self._failure_codes.add("audit_status_unknown")
            raise ObservationGap("audit failure mode is unknown")
        if before.lost != 0 or before.backlog != 0:
            if before.lost:
                self._failure_codes.add("audit_lost_changed")
            if before.backlog:
                self._failure_codes.add("audit_backlog_not_empty")
            raise ObservationGap("audit status is not clean at start")
        port_id = getattr(self.transport, "port_id", None)
        if isinstance(port_id, bool) or not isinstance(port_id, int) or port_id <= 0:
            self._failure_codes.add("audit_pid_not_registered")
            raise ObservationGap("audit collector port identity is unavailable")
        if before.pid not in {0, port_id}:
            self._failure_codes.add("audit_pid_not_registered")
            raise ObservationGap("another audit collector is registered")
        if before.pid != port_id:
            try:
                self.transport.set_status(mask=AUDIT_STATUS_PID, pid=port_id)
            except (LinuxGuestObservationError, TypeError):
                self._failure_codes.add("audit_pid_not_registered")
                raise
        registered = self._status()
        if registered.pid != port_id:
            self._failure_codes.add("audit_pid_not_registered")
            raise ObservationGap("audit collector PID registration was not observed")
        if registered.enabled == 0:
            try:
                self.transport.set_status(mask=AUDIT_STATUS_ENABLED, enabled=1)
            except LinuxGuestObservationError:
                self._failure_codes.add("audit_disabled")
                raise
        elif registered.enabled not in {1, 2}:
            self._failure_codes.add("audit_disabled")
            raise ObservationGap("audit enabled state is unknown")
        current_rules = self._rules()
        expected = set(self._configured_rule_digests)
        for rule, digest in zip(self.config.rules, self._configured_rule_digests, strict=True):
            if digest not in current_rules:
                if registered.enabled == 2:
                    self._failure_codes.add("audit_rule_binding_missing")
                    raise ObservationGap("locked audit policy lacks the required rule")
                try:
                    self.transport.add_rule(rule)
                except LinuxGuestObservationError:
                    self._failure_codes.add("audit_rule_binding_missing")
                    raise
        self._observed_rule_digests = self._rules()
        if not expected.issubset(self._observed_rule_digests):
            self._failure_codes.add("audit_rule_binding_missing")
            raise ObservationGap("audit rule binding was not observed")
        clean = self._status()
        if clean.backlog != 0:
            # Installing the rule itself produces CONFIG_CHANGE. Drain and
            # retain startup events before choosing an empty baseline; never
            # relabel a nonempty backlog as zero.
            self._started = True
            try:
                deadline = time.monotonic() + self.config.drain_timeout_seconds
                while clean.backlog and time.monotonic() < deadline:
                    if self.collect_once() == 0:
                        break
                    clean = self._status()
            finally:
                self._started = False
        if clean.pid != port_id or clean.enabled not in {1, 2}:
            self._failure_codes.add("audit_pid_not_registered")
            raise ObservationGap("audit start status is not bound")
        if clean.lost != 0:
            self._failure_codes.add("audit_lost_changed")
            raise ObservationGap("audit lost count is nonzero at start")
        if clean.backlog != 0:
            self._failure_codes.add("audit_backlog_not_empty")
            raise ObservationGap("audit backlog is nonzero at start")
        self._start_status = clean
        self._started = True

    def collect_once(self) -> int:
        """Read one bounded netlink packet and retain only sanitized events."""

        if not self._started or self._closed:
            raise AuditProtocolError("audit collector is not running")
        packet = self.transport.recv(_MAX_NETLINK_PACKET)
        if not packet:
            return 0
        count = 0
        try:
            messages = NetlinkAuditTransport._messages(packet)
            for message_type, _flags, _sequence, payload in messages:
                if message_type in {NLMSG_NOOP, NLMSG_DONE}:
                    continue
                if message_type == NLMSG_ERROR:
                    if len(payload) < 4 or struct.unpack_from("<i", payload)[0] != 0:
                        self._failure_codes.add("transport_error")
                        raise AuditProtocolError("audit event error response is unknown")
                    continue
                self._frame_count += 1
                if self._frame_count > _MAX_AUDIT_FRAMES:
                    self._failure_codes.add("audit_frame_limit")
                    raise ObservationGap("audit frame count exceeds its bound")
                try:
                    event = parse_audit_payload(
                        payload,
                        message_type=message_type,
                        ordinal=len(self._events) + 1,
                    )
                except ObservationGap as error:
                    message = str(error)
                    self._failure_codes.add(
                        "audit_event_unknown"
                        if "unknown" in message
                        else "audit_payload_invalid"
                    )
                    self._unknown_event_count += 1
                    raise
                self._events.append(event)
                if self._event_consumer is not None:
                    try:
                        self._event_consumer(message_type, payload)
                    except Exception as error:
                        self._failure_codes.add("audit_consumer_failed")
                        raise ObservationGap("audit consumer failed") from error
                count += 1
                if len(self._events) > self.config.max_events:
                    self._failure_codes.add("audit_frame_limit")
                    raise ObservationGap("audit event count exceeds its bound")
        except AuditProtocolError:
            self._failure_codes.add("transport_error")
            raise
        return count

    def drain(self) -> None:
        """Drain a bounded tail; timeout means the socket is currently empty."""

        deadline = time.monotonic() + float(self.config.drain_timeout_seconds)
        while time.monotonic() < deadline:
            if self.collect_once() == 0:
                return

    def finish(
        self, *, role_observation: RoleObservation | Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """Drain and return a non-authoritative sanitized intermediate receipt."""

        if not self._started or self._closed or self._start_status is None:
            raise AuditProtocolError("audit collector has no start observation")
        try:
            self.drain()
        except LinuxGuestObservationError:
            self._failure_codes.add("transport_error")
        try:
            end_status = self._status()
        except LinuxGuestObservationError:
            end_status = None
            self._failure_codes.add("audit_status_unknown")
        self._end_status = end_status
        if end_status is None:
            self._failure_codes.add("audit_status_unknown")
        else:
            if end_status.failure not in {0, 1, 2}:
                self._failure_codes.add("audit_status_unknown")
            if end_status.pid != self._start_status.pid:
                self._failure_codes.add("audit_pid_not_registered")
            if end_status.lost != self._start_status.lost:
                self._failure_codes.add("audit_lost_changed")
            if end_status.lost != 0:
                self._failure_codes.add("audit_lost_changed")
            if end_status.backlog != 0:
                self._failure_codes.add("audit_backlog_not_empty")
        safe_role_observation: RoleObservation | Mapping[str, Any] | None = role_observation
        if role_observation is None:
            self._failure_codes.add("role_metadata_missing")
        else:
            try:
                _validate_role_observation(
                    role_observation.to_public()
                    if isinstance(role_observation, RoleObservation)
                    else role_observation
                )
            except LinuxGuestObservationError:
                self._failure_codes.add("role_metadata_missing")
                safe_role_observation = None
        observed_types = sorted({event.message_type for event in self._events})
        if not set(self.config.required_event_types).issubset(observed_types):
            self._failure_codes.add("audit_event_missing")
        if self._unknown_event_count:
            self._failure_codes.add("audit_event_unknown")
        if (
            isinstance(role_observation, RoleObservation)
            and role_observation.cgroup_populated != 0
        ):
            self._failure_codes.add("role_cgroup_populated")
        failure_codes = sorted(self._failure_codes)
        status = "observed" if not failure_codes else "gap"
        start_public = self._start_status.public(port_id=self.transport.port_id)
        end_public = (
            self._end_status.public(port_id=self.transport.port_id)
            if self._end_status is not None
            else None
        )
        audit = {
            "pid_registered": bool(end_public and end_public["pid_registered"]),
            "enabled": end_public["enabled"] if end_public else None,
            "lost_before": self._start_status.lost,
            "lost_after": self._end_status.lost if self._end_status else None,
            "backlog_before": self._start_status.backlog,
            "backlog_after": self._end_status.backlog if self._end_status else None,
            "rule_binding_sha256": _sha_projection(self._configured_rule_digests),
            "configured_rule_sha256s": sorted(self._configured_rule_digests),
            "observed_rule_sha256s": sorted(self._observed_rule_digests),
            "frame_count": self._frame_count,
            "event_count": len(self._events),
            "events": [event.to_public() for event in self._events],
            "unknown_event_count": self._unknown_event_count,
            "required_event_types": list(self.config.required_event_types),
            "observed_event_types": observed_types,
            "sequence_sha256": sequence_sha256(self._events),
            "sequence_bound": not failure_codes,
            "raw_records_retained": False,
            "path_fields_retained": False,
            "command_fields_retained": False,
            "start_status": start_public,
        }
        return build_observation_receipt(
            status=status,
            role_observation=safe_role_observation,
            audit=audit,
            failure_codes=failure_codes,
        )


def build_observation_receipt(
    *,
    status: str,
    role_observation: RoleObservation | Mapping[str, Any] | None,
    audit: Mapping[str, Any],
    failure_codes: Sequence[str],
) -> dict[str, Any]:
    """Build a closed intermediate receipt; never set formal admission true."""

    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "formal_admission": False,
        "claim_eligible": False,
        "role_observation": (
            role_observation.to_public()
            if isinstance(role_observation, RoleObservation)
            else dict(role_observation)
            if isinstance(role_observation, Mapping)
            else None
        ),
        "audit": dict(audit),
        "failure_codes": list(failure_codes),
        "record_sha256": "",
    }
    value["record_sha256"] = record_sha256(value)
    return validate_observation_receipt(value)


def build_gap_receipt(
    *,
    failure_codes: Sequence[str],
    role_observation: RoleObservation | Mapping[str, Any] | None = None,
    configured_rule_sha256s: Sequence[str] = (),
    observed_rule_sha256s: Sequence[str] = (),
    required_event_types: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a diagnostic gap when native setup failed before a full window."""

    configured = sorted(configured_rule_sha256s)
    observed = sorted(observed_rule_sha256s)
    required = sorted(required_event_types)
    empty_events: list[dict[str, Any]] = []
    audit = {
        "pid_registered": False,
        "enabled": None,
        "lost_before": None,
        "lost_after": None,
        "backlog_before": None,
        "backlog_after": None,
        "rule_binding_sha256": _sha_projection(configured),
        "configured_rule_sha256s": configured,
        "observed_rule_sha256s": observed,
        "frame_count": 0,
        "event_count": 0,
        "events": empty_events,
        "unknown_event_count": 0,
        "required_event_types": required,
        "observed_event_types": [],
        "sequence_sha256": sequence_sha256(empty_events),
        "sequence_bound": False,
        "raw_records_retained": False,
        "path_fields_retained": False,
        "command_fields_retained": False,
        "start_status": None,
    }
    return build_observation_receipt(
        status="gap",
        role_observation=role_observation,
        audit=audit,
        failure_codes=failure_codes,
    )


def record_sha256(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise LinuxGuestObservationError("observation receipt must be an object")
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return sha256_hex(canonical_json(body))


def _scan_safe(value: Any, *, key: str | None = None) -> None:
    forbidden = {
        "path",
        "command",
        "argv",
        "env",
        "stdout",
        "stderr",
        "transcript",
        "reasoning",
        "secret",
        "credential",
        "token",
        "password",
        "raw_payload",
        "raw_record",
    }
    allowed_control = {
        "raw_records_retained",
        "path_fields_retained",
        "command_fields_retained",
        "pid_registered",
        "pid_namespace_sha256",
    }
    if key is not None and key.lower() in forbidden and key not in allowed_control:
        raise LinuxGuestObservationError("observation receipt contains a forbidden field")
    if isinstance(value, Mapping):
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise LinuxGuestObservationError("observation receipt field name is invalid")
            _scan_safe(nested_value, key=nested_key)
    elif isinstance(value, list):
        for item in value:
            _scan_safe(item)
    elif isinstance(value, str):
        if "\x00" in value or re.search(r"(?:^|[\s(=:])/(?!/)", value):
            raise LinuxGuestObservationError("observation receipt contains a private path")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise LinuxGuestObservationError("observation receipt contains an unsupported value")


def _validate_events(
    audit: Mapping[str, Any], *, status: str
) -> list[dict[str, Any]]:
    events = audit.get("events")
    minimum = 1 if status == "observed" else 0
    if not isinstance(events, list) or not minimum <= len(events) <= _MAX_AUDIT_EVENTS:
        raise LinuxGuestObservationError("audit events are missing or unbounded")
    normalized: list[dict[str, Any]] = []
    for ordinal, event in enumerate(events, start=1):
        if not isinstance(event, Mapping) or set(event) != {"ordinal", "message_type", "serial"}:
            raise LinuxGuestObservationError("audit event projection is not closed")
        if event.get("ordinal") != ordinal:
            raise LinuxGuestObservationError("audit event order is not bound")
        message_type = event.get("message_type")
        if not isinstance(message_type, str) or message_type not in _SUPPORTED_AUDIT_TYPES:
            raise LinuxGuestObservationError("audit event type is unknown")
        serial = _bounded_int(event.get("serial"), label="audit serial", maximum=2**63 - 1)
        if serial == 0:
            raise LinuxGuestObservationError("audit serial is invalid")
        normalized.append({"ordinal": ordinal, "message_type": message_type, "serial": serial})
    if audit.get("event_count") != len(normalized):
        raise LinuxGuestObservationError("audit event count differs")
    if audit.get("sequence_sha256") != sequence_sha256(normalized):
        raise LinuxGuestObservationError("audit sequence digest differs")
    return normalized


def _validate_status_public(value: Mapping[str, Any]) -> None:
    expected = {
        "enabled",
        "pid_registered",
        "lost",
        "backlog",
        "failure",
        "rate_limit",
        "backlog_limit",
        "feature_bitmap",
        "backlog_wait_time",
        "backlog_wait_time_actual",
    }
    if set(value) != expected or type(value.get("pid_registered")) is not bool:
        raise LinuxGuestObservationError("audit status projection is not closed")
    if value.get("enabled") not in {1, 2}:
        raise LinuxGuestObservationError("audit status enabled state is unknown")
    if value.get("failure") not in {0, 1, 2}:
        raise LinuxGuestObservationError("audit status failure mode is unknown")
    for field in expected - {"enabled", "pid_registered"}:
        _bounded_int(value.get(field), label=f"audit status {field}")


def _validate_role_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LinuxGuestObservationError("role observation is missing")
    expected = {
        "role",
        "uid",
        "mount_namespace_sha256",
        "pid_namespace_sha256",
        "net_namespace_sha256",
        "ipc_namespace_sha256",
        "cgroup_id_sha256",
        "cgroup_events_sha256",
        "cgroup_populated",
    }
    if set(value) != expected:
        raise LinuxGuestObservationError("role observation fields are not closed")
    role = _safe_role(value.get("role"))
    uid = _bounded_int(value.get("uid"), label="role uid", maximum=2**31 - 1)
    if uid == 0:
        raise LinuxGuestObservationError("role uid is root")
    for field in expected - {"role", "uid", "cgroup_populated"}:
        _digest(value.get(field), label=field)
    if value.get("cgroup_populated") != 0:
        raise LinuxGuestObservationError("role cgroup remains populated")
    return {
        "role": role,
        "uid": uid,
        **{field: str(value[field]) for field in expected - {"role", "uid", "cgroup_populated"}},
        "cgroup_populated": 0,
    }


def _validate_audit(value: Mapping[str, Any], *, status: str) -> dict[str, Any]:
    expected = {
        "pid_registered",
        "enabled",
        "lost_before",
        "lost_after",
        "backlog_before",
        "backlog_after",
        "rule_binding_sha256",
        "configured_rule_sha256s",
        "observed_rule_sha256s",
        "frame_count",
        "event_count",
        "events",
        "unknown_event_count",
        "required_event_types",
        "observed_event_types",
        "sequence_sha256",
        "sequence_bound",
        "raw_records_retained",
        "path_fields_retained",
        "command_fields_retained",
        "start_status",
    }
    if set(value) != expected:
        raise LinuxGuestObservationError("audit observation fields are not closed")
    if type(value.get("pid_registered")) is not bool:
        raise LinuxGuestObservationError("audit PID registration is unknown")
    enabled = value.get("enabled")
    if status == "observed" and enabled not in {1, 2}:
        raise LinuxGuestObservationError("audit enabled state is unknown")
    for field in ("lost_before", "lost_after", "backlog_before", "backlog_after"):
        field_value = value.get(field)
        if field_value is None and status == "gap":
            continue
        _bounded_int(field_value, label=field)
    for field in ("frame_count", "event_count", "unknown_event_count"):
        _bounded_int(value.get(field), label=field, maximum=_MAX_AUDIT_FRAMES)
    if value["event_count"] > value["frame_count"]:
        raise LinuxGuestObservationError("audit event count exceeds frame count")
    if value["unknown_event_count"] > value["frame_count"]:
        raise LinuxGuestObservationError("audit unknown count exceeds frame count")
    configured = value.get("configured_rule_sha256s")
    observed = value.get("observed_rule_sha256s")
    for field, rows in (
        ("configured_rule_sha256s", configured),
        ("observed_rule_sha256s", observed),
    ):
        minimum = 1 if status == "observed" else 0
        if not isinstance(rows, list) or not minimum <= len(rows) <= _MAX_RULES:
            raise LinuxGuestObservationError(f"{field} is missing or unbounded")
        if any(_SHA256_RE.fullmatch(item or "") is None for item in rows):
            raise LinuxGuestObservationError(f"{field} contains an invalid digest")
        if rows != sorted(rows) or len(set(rows)) != len(rows):
            raise LinuxGuestObservationError(f"{field} is not canonical")
    if value.get("rule_binding_sha256") != _sha_projection(configured):
        raise LinuxGuestObservationError("audit rule binding digest differs")
    if not set(configured).issubset(observed):
        raise LinuxGuestObservationError("audit rule binding is missing")
    required = value.get("required_event_types")
    observed_types = value.get("observed_event_types")
    if (
        not isinstance(required, list)
        or (not required and status == "observed")
        or required != sorted(set(required))
        or any(item not in _SUPPORTED_AUDIT_TYPES for item in required)
        or not isinstance(observed_types, list)
        or observed_types != sorted(set(observed_types))
        or any(item not in _SUPPORTED_AUDIT_TYPES for item in observed_types)
    ):
        raise LinuxGuestObservationError("audit event type binding is unknown")
    normalized_events = _validate_events(value, status=status)
    if sorted({event["message_type"] for event in normalized_events}) != observed_types:
        raise LinuxGuestObservationError("observed audit event types differ")
    if status == "observed" and not set(required).issubset(observed_types):
        raise LinuxGuestObservationError("required audit event is missing")
    if status == "observed" and value.get("sequence_bound") is not True:
        raise LinuxGuestObservationError("audit sequence is not bound")
    if status == "gap" and type(value.get("sequence_bound")) is not bool:
        raise LinuxGuestObservationError("audit sequence is not bound")
    if any(value.get(field) is not False for field in (
        "raw_records_retained",
        "path_fields_retained",
        "command_fields_retained",
    )):
        raise LinuxGuestObservationError("audit redaction is incomplete")
    start_status = value.get("start_status")
    if start_status is None and status == "gap":
        return dict(value)
    if not isinstance(start_status, Mapping):
        raise LinuxGuestObservationError("audit start status is missing")
    _validate_status_public(start_status)
    if start_status.get("pid_registered") is not True:
        raise LinuxGuestObservationError("audit start status is not registered")
    if (
        start_status["lost"] != value["lost_before"]
        or start_status["backlog"] != value["backlog_before"]
    ):
        raise LinuxGuestObservationError("audit start status is not bound")
    if status == "observed":
        if value.get("pid_registered") is not True:
            raise LinuxGuestObservationError("audit PID is not registered at finish")
        if value.get("lost_before") != 0 or value.get("lost_after") != 0:
            raise LinuxGuestObservationError("audit lost count is not zero")
        if value.get("lost_before") != value.get("lost_after"):
            raise LinuxGuestObservationError("audit lost count changed")
        if value.get("backlog_before") != 0 or value.get("backlog_after") != 0:
            raise LinuxGuestObservationError("audit backlog is not empty")
        if value.get("unknown_event_count") != 0:
            raise LinuxGuestObservationError("audit unknown event was observed")
    return dict(value)


def validate_observation_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a strict path-free intermediate; never grant admission."""

    if not isinstance(value, Mapping):
        raise LinuxGuestObservationError("observation receipt must be an object")
    _scan_safe(value)
    expected = {
        "schema_version",
        "status",
        "formal_admission",
        "claim_eligible",
        "role_observation",
        "audit",
        "failure_codes",
        "record_sha256",
    }
    if set(value) != expected:
        raise LinuxGuestObservationError("observation receipt fields are not closed")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise LinuxGuestObservationError("observation receipt version differs")
    status = value.get("status")
    if status not in {"observed", "gap"}:
        raise LinuxGuestObservationError("observation status is unknown")
    if value.get("formal_admission") is not False or value.get("claim_eligible") is not False:
        raise LinuxGuestObservationError("guest observation cannot grant admission")
    failures = value.get("failure_codes")
    if (
        not isinstance(failures, list)
        or failures != sorted(set(failures))
        or any(item not in _FAILURE_CODES for item in failures)
    ):
        raise LinuxGuestObservationError("observation failure codes are unknown")
    role_value = value.get("role_observation")
    if status == "observed":
        if not isinstance(role_value, Mapping):
            raise LinuxGuestObservationError("role observation is missing")
        _validate_role_observation(role_value)
        if failures:
            raise LinuxGuestObservationError("observed receipt contains failure codes")
    elif not failures:
        raise LinuxGuestObservationError("gap receipt lacks a failure code")
    audit = value.get("audit")
    if not isinstance(audit, Mapping):
        raise LinuxGuestObservationError("audit observation is missing")
    _validate_audit(audit, status=status)
    if value.get("record_sha256") != record_sha256(value):
        raise LinuxGuestObservationError("observation receipt self-digest differs")
    return dict(value)


def validate_role_receipt_set(values: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Require Host and MCP receipts to use distinct non-root identities."""

    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise LinuxGuestObservationError("role receipt set is invalid")
    if len(values) != 2:
        raise LinuxGuestObservationError("role receipt set must contain Host and MCP")
    validated = [validate_observation_receipt(value) for value in values]
    if any(value["status"] != "observed" for value in validated):
        raise LinuxGuestObservationError("role receipt set contains a gap")
    roles = [value["role_observation"]["role"] for value in validated]
    if set(roles) != {"host", "mcp"}:
        raise LinuxGuestObservationError("role receipt set roles are not Host and MCP")
    observations = [value["role_observation"] for value in validated]
    if any(item["uid"] == 0 for item in observations):
        raise LinuxGuestObservationError("role receipt set contains root")
    audit_rows = [value["audit"] for value in validated]
    for field in ("rule_binding_sha256", "sequence_sha256"):
        if len({row[field] for row in audit_rows}) != 1:
            raise LinuxGuestObservationError(f"role audit {field} is not shared")
    for field in (
        "uid",
        "mount_namespace_sha256",
        "pid_namespace_sha256",
        "net_namespace_sha256",
        "cgroup_id_sha256",
    ):
        if len({item[field] for item in observations}) != 2:
            raise LinuxGuestObservationError(f"role {field} is not distinct")
    return tuple(validated)


def probe_native_audit(*, transport: Any | None = None) -> dict[str, Any]:
    """Probe the real audit status without claiming event observation."""

    selected = _require_linux_transport(transport)
    try:
        status = selected.get_status()
        port_id = getattr(selected, "port_id", None)
        if isinstance(port_id, bool) or not isinstance(port_id, int) or port_id <= 0:
            raise NativeAuditUnavailable("audit collector port identity is unavailable")
        return {
            "schema_version": f"{SCHEMA_VERSION}/capability",
            "audit_get_succeeded": True,
            "enabled": status.enabled,
            "pid_registered": status.pid == port_id,
            "lost": status.lost,
            "backlog": status.backlog,
            "formal_admission": False,
        }
    finally:
        close = getattr(selected, "close", None)
        if callable(close):
            close()


def main(argv: Sequence[str] | None = None) -> int:
    """Run a bounded guest capability probe; no Host is launched."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="query native audit status only")
    args = parser.parse_args(argv)
    if not args.probe:
        parser.error("--probe is required; this tool never launches a Host")
    result = probe_native_audit()
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
