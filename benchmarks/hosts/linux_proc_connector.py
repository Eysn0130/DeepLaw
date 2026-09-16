"""Parse the bounded Linux ``cn_proc`` process-event wire format.

This module is intentionally only a binary decoder.  It does not subscribe to
the connector, enforce an event window, infer a process tree, or collect any
process content.  The kernel's process connector is a multicast netlink
source; consequently the decoder accepts only the native kernel datagram
shape observed for that source and rejects user-space or ambiguous framing.

The public projection keeps event metadata and numeric non-identity fields,
but replaces every process identifier with a SHA-256 digest.  The event's
``comm`` bytes are consumed for structural validation and are never retained,
decoded, or emitted.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Final

MAX_PACKET_BYTES: Final = 64 * 1024

NLMSG_DONE: Final = 3
CN_IDX_PROC: Final = 1
CN_VAL_PROC: Final = 1
CN_MSG_LENGTH: Final = 40
PROC_EVENT_LENGTH: Final = 40

PROC_EVENT_NONE: Final = 0x00000000
PROC_EVENT_FORK: Final = 0x00000001
PROC_EVENT_EXEC: Final = 0x00000002
PROC_EVENT_UID: Final = 0x00000004
PROC_EVENT_GID: Final = 0x00000040
PROC_EVENT_SID: Final = 0x00000080
PROC_EVENT_PTRACE: Final = 0x00000100
PROC_EVENT_COMM: Final = 0x00000200
PROC_EVENT_COREDUMP: Final = 0x40000000
PROC_EVENT_EXIT: Final = 0x80000000

_NETLINK_HEADER = struct.Struct("<IHHII")
_CN_MESSAGE = struct.Struct("<IIIIHH")
_PROC_HEADER = struct.Struct("<IIQ")
_MAX_NLMSG_LENGTH: Final = _NETLINK_HEADER.size + _CN_MESSAGE.size + PROC_EVENT_LENGTH
_PID_VALUE_KEYS: Final = frozenset(
    {
        "parent_pid",
        "parent_tgid",
        "child_pid",
        "child_tgid",
        "process_pid",
        "process_tgid",
        "tracer_pid",
        "tracer_tgid",
    }
)


class ProcessEventWhat(IntEnum):
    """The process event values accepted from Linux 6.18 ``cn_proc``."""

    NONE = PROC_EVENT_NONE
    FORK = PROC_EVENT_FORK
    EXEC = PROC_EVENT_EXEC
    UID = PROC_EVENT_UID
    GID = PROC_EVENT_GID
    SID = PROC_EVENT_SID
    PTRACE = PROC_EVENT_PTRACE
    COMM = PROC_EVENT_COMM
    COREDUMP = PROC_EVENT_COREDUMP
    EXIT = PROC_EVENT_EXIT


class ProcessConnectorError(ValueError):
    """A bounded, content-free decoder failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProcessEvent:
    """One decoded process event with private numeric identifier values.

    ``values`` contains only the numeric fields defined by the selected
    ``proc_event`` union member.  It is excluded from ``repr`` so accidental
    diagnostic output cannot expose raw process identifiers.  Callers must
    use :meth:`to_public` for a provider-facing projection.
    """

    what: ProcessEventWhat
    cpu: int
    sequence: int
    timestamp_ns: int
    values: dict[str, int] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.what, ProcessEventWhat):
            raise ProcessConnectorError("event_type_invalid")
        if not isinstance(self.values, dict):
            raise ProcessConnectorError("event_field_invalid")
        for name, value in self.values.items():
            if not isinstance(name, str) or not isinstance(value, int) or isinstance(value, bool):
                raise ProcessConnectorError("event_field_invalid")
            if name not in _expected_value_keys(self.what):
                raise ProcessConnectorError("event_field_invalid")
        for name, value in (
            ("cpu", self.cpu),
            ("sequence", self.sequence),
            ("timestamp_ns", self.timestamp_ns),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ProcessConnectorError(f"{name}_invalid")
        if set(self.values) != _expected_value_keys(self.what):
            raise ProcessConnectorError("event_field_invalid")
        # Keep the dataclass boundary independent from the decoder's scratch
        # dictionary.  The values remain owner-internal by contract.
        object.__setattr__(self, "values", dict(self.values))

    def to_public(self) -> dict[str, Any]:
        """Return the closed projection with no raw PID/TGID or ``comm``."""

        expected = _expected_value_keys(self.what)
        if set(self.values) != expected:
            raise ProcessConnectorError("event_field_invalid")
        public_values: dict[str, int | str] = {}
        for name, value in self.values.items():
            if not isinstance(value, int) or isinstance(value, bool):
                raise ProcessConnectorError("event_field_invalid")
            if name in _PID_VALUE_KEYS:
                public_values[f"{name}_sha256"] = _pid_digest(value)
            else:
                public_values[name] = value
        return {
            "what": int(self.what),
            "cpu": self.cpu,
            "sequence": self.sequence,
            "timestamp_ns": self.timestamp_ns,
            "values": public_values,
            "formal_admission": False,
        }


def decode_packet(
    raw: bytes,
    sender: tuple,
    flags: int,
    ancillary: Sequence[object],
) -> list[ProcessEvent]:
    """Decode one native ``cn_proc`` netlink datagram.

    The decoder requires the kernel multicast address ``(0, 1)`` and zero
    ``recvmsg`` flags/ancillary data.  ``nlmsg_seq`` is checked against the
    connector sequence, while no sequence-window or tree semantics are
    imposed here.
    """

    _validate_packet_arguments(raw, sender, flags, ancillary)
    if not raw:
        raise ProcessConnectorError("packet_empty")

    events: list[ProcessEvent] = []
    offset = 0
    raw_size = len(raw)
    while offset < raw_size:
        remaining = raw_size - offset
        if remaining < _NETLINK_HEADER.size:
            raise ProcessConnectorError("netlink_truncated")
        try:
            nlmsg_len, nlmsg_type, nlmsg_flags, nlmsg_seq, nlmsg_pid = _NETLINK_HEADER.unpack_from(
                raw, offset
            )
        except struct.error as error:  # pragma: no cover - guarded by remaining check
            raise ProcessConnectorError("netlink_truncated") from error
        if nlmsg_len < _NETLINK_HEADER.size or nlmsg_len > remaining:
            raise ProcessConnectorError("netlink_length_invalid")
        aligned_len = (nlmsg_len + 3) & ~3
        if aligned_len > remaining:
            raise ProcessConnectorError("netlink_truncated")
        if any(raw[offset + nlmsg_len : offset + aligned_len]):
            raise ProcessConnectorError("netlink_padding_nonzero")
        if nlmsg_type != NLMSG_DONE:
            raise ProcessConnectorError("netlink_type_invalid")
        if nlmsg_flags != 0:
            raise ProcessConnectorError("netlink_flags_invalid")
        if nlmsg_pid != 0:
            raise ProcessConnectorError("netlink_port_invalid")

        payload = raw[offset + _NETLINK_HEADER.size : offset + nlmsg_len]
        if nlmsg_len != _MAX_NLMSG_LENGTH or len(payload) != _CN_MESSAGE.size + PROC_EVENT_LENGTH:
            raise ProcessConnectorError("cn_message_size_invalid")
        events.append(_decode_event(payload, nlmsg_seq))
        offset += aligned_len

    return events


def _validate_packet_arguments(
    raw: bytes,
    sender: tuple,
    flags: int,
    ancillary: Sequence[object],
) -> None:
    if not isinstance(raw, bytes):
        raise ProcessConnectorError("packet_type_invalid")
    if len(raw) > MAX_PACKET_BYTES:
        raise ProcessConnectorError("packet_too_large")
    if (
        not isinstance(sender, tuple)
        or len(sender) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in sender)
        or sender != (0, 1)
    ):
        raise ProcessConnectorError("sender_invalid")
    if isinstance(flags, bool) or not isinstance(flags, int) or flags != 0:
        raise ProcessConnectorError("flags_invalid")
    if isinstance(ancillary, (bytes, bytearray, str)) or not isinstance(ancillary, Sequence):
        raise ProcessConnectorError("ancillary_invalid")
    try:
        ancillary_count = len(ancillary)
    except (TypeError, ValueError) as error:
        raise ProcessConnectorError("ancillary_invalid") from error
    if ancillary_count != 0:
        raise ProcessConnectorError("ancillary_invalid")


def _decode_event(payload: bytes, nlmsg_seq: int) -> ProcessEvent:
    try:
        idx, val, cn_seq, _cn_ack, cn_len, cn_flags = _CN_MESSAGE.unpack_from(payload)
    except struct.error as error:  # pragma: no cover - payload size is checked by caller
        raise ProcessConnectorError("cn_message_size_invalid") from error
    if idx != CN_IDX_PROC or val != CN_VAL_PROC:
        raise ProcessConnectorError("cn_identity_invalid")
    if cn_len != CN_MSG_LENGTH or cn_len != PROC_EVENT_LENGTH:
        raise ProcessConnectorError("cn_length_invalid")
    if cn_flags != 0:
        raise ProcessConnectorError("cn_flags_invalid")
    if nlmsg_seq != cn_seq:
        raise ProcessConnectorError("cn_sequence_mismatch")

    event_data = payload[_CN_MESSAGE.size :]
    try:
        what_value, cpu, timestamp_ns = _PROC_HEADER.unpack_from(event_data)
        what = ProcessEventWhat(what_value)
    except (struct.error, ValueError) as error:
        raise ProcessConnectorError("event_type_unknown") from error
    if len(event_data) != PROC_EVENT_LENGTH:
        raise ProcessConnectorError("event_length_invalid")
    values = _decode_event_values(what, event_data[_PROC_HEADER.size :])
    return ProcessEvent(
        what=what,
        cpu=cpu,
        sequence=cn_seq,
        timestamp_ns=timestamp_ns,
        values=values,
    )


def _decode_event_values(what: ProcessEventWhat, union: bytes) -> dict[str, int]:
    if len(union) != PROC_EVENT_LENGTH - _PROC_HEADER.size:
        raise ProcessConnectorError("event_union_size_invalid")
    try:
        if what is ProcessEventWhat.NONE:
            (error,) = struct.unpack_from("<I", union)
            _require_zero(union[4:])
            return {"error": error}
        if what is ProcessEventWhat.FORK:
            parent_pid, parent_tgid, child_pid, child_tgid = struct.unpack("<iiii", union[:16])
            _require_pids((parent_pid, parent_tgid, child_pid, child_tgid))
            _require_zero(union[16:])
            return {
                "parent_pid": parent_pid,
                "parent_tgid": parent_tgid,
                "child_pid": child_pid,
                "child_tgid": child_tgid,
            }
        if what is ProcessEventWhat.EXEC:
            process_pid, process_tgid = struct.unpack("<ii", union[:8])
            _require_pids((process_pid, process_tgid))
            _require_zero(union[8:])
            return {"process_pid": process_pid, "process_tgid": process_tgid}
        if what is ProcessEventWhat.UID:
            process_pid, process_tgid, ruid, euid = struct.unpack("<iiII", union[:16])
            _require_pids((process_pid, process_tgid))
            _require_zero(union[16:])
            return {
                "process_pid": process_pid,
                "process_tgid": process_tgid,
                "ruid": ruid,
                "euid": euid,
            }
        if what is ProcessEventWhat.GID:
            process_pid, process_tgid, rgid, egid = struct.unpack("<iiII", union[:16])
            _require_pids((process_pid, process_tgid))
            _require_zero(union[16:])
            return {
                "process_pid": process_pid,
                "process_tgid": process_tgid,
                "rgid": rgid,
                "egid": egid,
            }
        if what is ProcessEventWhat.SID:
            process_pid, process_tgid = struct.unpack("<ii", union[:8])
            _require_pids((process_pid, process_tgid))
            _require_zero(union[8:])
            return {"process_pid": process_pid, "process_tgid": process_tgid}
        if what is ProcessEventWhat.PTRACE:
            process_pid, process_tgid, tracer_pid, tracer_tgid = struct.unpack("<iiii", union[:16])
            _require_pids((process_pid, process_tgid, tracer_pid, tracer_tgid))
            _require_zero(union[16:])
            return {
                "process_pid": process_pid,
                "process_tgid": process_tgid,
                "tracer_pid": tracer_pid,
                "tracer_tgid": tracer_tgid,
            }
        if what is ProcessEventWhat.COMM:
            process_pid, process_tgid = struct.unpack("<ii", union[:8])
            _require_pids((process_pid, process_tgid))
            # The final 16 bytes are the opaque kernel task name.  They are
            # intentionally neither decoded nor retained.
            return {"process_pid": process_pid, "process_tgid": process_tgid}
        if what is ProcessEventWhat.COREDUMP:
            process_pid, process_tgid, parent_pid, parent_tgid = struct.unpack("<iiii", union[:16])
            _require_pids((process_pid, process_tgid))
            _require_parent_pids((parent_pid, parent_tgid))
            _require_zero(union[16:])
            return {
                "process_pid": process_pid,
                "process_tgid": process_tgid,
                "parent_pid": parent_pid,
                "parent_tgid": parent_tgid,
            }
        if what is ProcessEventWhat.EXIT:
            (
                process_pid,
                process_tgid,
                exit_code,
                exit_signal,
                parent_pid,
                parent_tgid,
            ) = struct.unpack("<iiIIii", union)
            _require_pids((process_pid, process_tgid))
            _require_parent_pids((parent_pid, parent_tgid))
            return {
                "process_pid": process_pid,
                "process_tgid": process_tgid,
                "exit_code": exit_code,
                "exit_signal": exit_signal,
                "parent_pid": parent_pid,
                "parent_tgid": parent_tgid,
            }
    except struct.error as error:  # pragma: no cover - union has fixed size
        raise ProcessConnectorError("event_field_invalid") from error
    raise ProcessConnectorError("event_type_unknown")


def _expected_value_keys(what: ProcessEventWhat) -> frozenset[str]:
    return {
        ProcessEventWhat.NONE: frozenset({"error"}),
        ProcessEventWhat.FORK: frozenset(
            {"parent_pid", "parent_tgid", "child_pid", "child_tgid"}
        ),
        ProcessEventWhat.EXEC: frozenset({"process_pid", "process_tgid"}),
        ProcessEventWhat.UID: frozenset({"process_pid", "process_tgid", "ruid", "euid"}),
        ProcessEventWhat.GID: frozenset({"process_pid", "process_tgid", "rgid", "egid"}),
        ProcessEventWhat.SID: frozenset({"process_pid", "process_tgid"}),
        ProcessEventWhat.PTRACE: frozenset(
            {"process_pid", "process_tgid", "tracer_pid", "tracer_tgid"}
        ),
        ProcessEventWhat.COMM: frozenset({"process_pid", "process_tgid"}),
        ProcessEventWhat.COREDUMP: frozenset(
            {"process_pid", "process_tgid", "parent_pid", "parent_tgid"}
        ),
        ProcessEventWhat.EXIT: frozenset(
            {
                "process_pid",
                "process_tgid",
                "exit_code",
                "exit_signal",
                "parent_pid",
                "parent_tgid",
            }
        ),
    }[what]


def _require_zero(value: bytes) -> None:
    if any(value):
        raise ProcessConnectorError("event_reserved_nonzero")


def _require_pids(values: Sequence[int]) -> None:
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values):
        raise ProcessConnectorError("event_pid_invalid")


def _require_parent_pids(values: Sequence[int]) -> None:
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise ProcessConnectorError("event_pid_invalid")


def _pid_digest(value: int) -> str:
    return hashlib.sha256(str(value).encode("ascii")).hexdigest()


__all__ = [
    "CN_IDX_PROC",
    "CN_MSG_LENGTH",
    "CN_VAL_PROC",
    "MAX_PACKET_BYTES",
    "NLMSG_DONE",
    "PROC_EVENT_COMM",
    "PROC_EVENT_COREDUMP",
    "PROC_EVENT_EXEC",
    "PROC_EVENT_EXIT",
    "PROC_EVENT_FORK",
    "PROC_EVENT_GID",
    "PROC_EVENT_LENGTH",
    "PROC_EVENT_NONE",
    "PROC_EVENT_PTRACE",
    "PROC_EVENT_SID",
    "PROC_EVENT_UID",
    "ProcessConnectorError",
    "ProcessEvent",
    "ProcessEventWhat",
    "decode_packet",
]
