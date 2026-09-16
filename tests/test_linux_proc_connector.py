from __future__ import annotations

import hashlib
import struct

import pytest

from benchmarks.hosts import linux_proc_connector as connector

_NLMSG = struct.Struct("<IHHII")
_CN = struct.Struct("<IIIIHH")
_PROC = struct.Struct("<IIQ")


def _union(what: int, values: tuple[int, ...] = (), *, comm: bytes = b"") -> bytes:
    if what == connector.PROC_EVENT_NONE:
        return struct.pack("<I", values[0] if values else 0) + b"\0" * 20
    if what == connector.PROC_EVENT_FORK:
        return struct.pack("<iiii", *values) + b"\0" * 8
    if what in {connector.PROC_EVENT_EXEC, connector.PROC_EVENT_SID}:
        return struct.pack("<ii", *values) + b"\0" * 16
    if what == connector.PROC_EVENT_UID or what == connector.PROC_EVENT_GID:
        return struct.pack("<iiII", *values) + b"\0" * 8
    if what == connector.PROC_EVENT_PTRACE:
        return struct.pack("<iiii", *values) + b"\0" * 8
    if what == connector.PROC_EVENT_COMM:
        assert len(comm) == 16
        return struct.pack("<ii", *values) + comm
    if what == connector.PROC_EVENT_COREDUMP:
        return struct.pack("<iiii", *values) + b"\0" * 8
    if what == connector.PROC_EVENT_EXIT:
        return struct.pack("<iiIIii", *values)
    raise AssertionError(f"test fixture does not know event type {what}")


def _packet(
    what: int,
    values: tuple[int, ...] = (),
    *,
    sequence: int = 7,
    nlmsg_sequence: int | None = None,
    cn_idx: int = connector.CN_IDX_PROC,
    cn_val: int = connector.CN_VAL_PROC,
    cn_length: int = connector.CN_MSG_LENGTH,
    cn_flags: int = 0,
    nlmsg_type: int = connector.NLMSG_DONE,
    nlmsg_flags: int = 0,
    nlmsg_pid: int = 0,
    comm: bytes = b"opaque-task-name",
    padding: bytes = b"",
) -> bytes:
    if nlmsg_sequence is None:
        nlmsg_sequence = sequence
    event = _PROC.pack(what, 0, 123456789) + _union(what, values, comm=comm)
    assert len(event) == connector.PROC_EVENT_LENGTH
    cn = _CN.pack(cn_idx, cn_val, sequence, 11, cn_length, cn_flags) + event
    length = _NLMSG.size + len(cn)
    frame = _NLMSG.pack(length, nlmsg_type, nlmsg_flags, nlmsg_sequence, nlmsg_pid) + cn
    return frame + padding


def _done(*, sequence: int = 0) -> bytes:
    return _NLMSG.pack(_NLMSG.size, connector.NLMSG_DONE, 0, sequence, 0)


def _nonzero_padding_packet() -> bytes:
    raw = bytearray(_packet(connector.PROC_EVENT_EXEC, (1, 1)))
    struct.pack_into("<I", raw, 0, 75)
    raw[-1] = 1
    return bytes(raw)


def _unknown_event_packet() -> bytes:
    raw = bytearray(_packet(connector.PROC_EVENT_EXEC, (1, 1)))
    struct.pack_into("<I", raw, _NLMSG.size + _CN.size, 3)
    return bytes(raw)


def _decode(raw: bytes, *, sender: tuple[int, int] = (0, 1)) -> list[connector.ProcessEvent]:
    return connector.decode_packet(raw, sender, 0, [])


def test_decodes_all_supported_event_union_members_and_keeps_numeric_fields() -> None:
    rows = [
        (connector.PROC_EVENT_NONE, (17,), {"error": 17}),
        (connector.PROC_EVENT_FORK, (10, 11, 12, 13), {
            "parent_pid": 10,
            "parent_tgid": 11,
            "child_pid": 12,
            "child_tgid": 13,
        }),
        (connector.PROC_EVENT_EXEC, (14, 15), {"process_pid": 14, "process_tgid": 15}),
        (connector.PROC_EVENT_UID, (16, 17, 1000, 1001), {
            "process_pid": 16,
            "process_tgid": 17,
            "ruid": 1000,
            "euid": 1001,
        }),
        (connector.PROC_EVENT_GID, (18, 19, 1002, 1003), {
            "process_pid": 18,
            "process_tgid": 19,
            "rgid": 1002,
            "egid": 1003,
        }),
        (connector.PROC_EVENT_SID, (20, 21), {"process_pid": 20, "process_tgid": 21}),
        (connector.PROC_EVENT_PTRACE, (22, 23, 24, 25), {
            "process_pid": 22,
            "process_tgid": 23,
            "tracer_pid": 24,
            "tracer_tgid": 25,
        }),
        (connector.PROC_EVENT_COMM, (26, 27), {"process_pid": 26, "process_tgid": 27}),
        (connector.PROC_EVENT_COREDUMP, (28, 29, 30, 31), {
            "process_pid": 28,
            "process_tgid": 29,
            "parent_pid": 30,
            "parent_tgid": 31,
        }),
        (connector.PROC_EVENT_EXIT, (32, 33, 0xCAFE, 15, 34, 35), {
            "process_pid": 32,
            "process_tgid": 33,
            "exit_code": 0xCAFE,
            "exit_signal": 15,
            "parent_pid": 34,
            "parent_tgid": 35,
        }),
    ]

    raw = b"".join(
        _packet(what, values, sequence=index)
        for index, (what, values, _) in enumerate(rows)
    )
    events = _decode(raw)

    assert len(events) == len(rows)
    for event, (what, _values, expected) in zip(events, rows, strict=True):
        assert event.what == what
        assert event.cpu == 0
        assert event.sequence == rows.index((what, _values, expected))
        assert event.timestamp_ns == 123456789
        assert event.values == expected


def test_ack_none_event_preserves_error_and_bare_done_is_rejected() -> None:
    event = _decode(_packet(connector.PROC_EVENT_NONE, (22,), sequence=22))[0]
    assert event.values == {"error": 22}
    with pytest.raises(connector.ProcessConnectorError, match="cn_message_size_invalid"):
        _decode(_done())


def test_sequence_metadata_must_bind_netlink_and_connector_headers() -> None:
    event = _decode(_packet(connector.PROC_EVENT_EXEC, (41, 42), sequence=19))[0]
    assert event.sequence == 19

    with pytest.raises(connector.ProcessConnectorError, match="cn_sequence_mismatch"):
        _decode(_packet(connector.PROC_EVENT_EXEC, (41, 42), sequence=19, nlmsg_sequence=20))


def test_public_projection_digests_all_pid_fields_and_drops_comm() -> None:
    raw_name = b"secret-command!!"
    assert len(raw_name) == 16
    event = _decode(_packet(connector.PROC_EVENT_COMM, (123, 456), comm=raw_name))[0]
    public = event.to_public()

    assert public == {
        "what": connector.PROC_EVENT_COMM,
        "cpu": 0,
        "sequence": 7,
        "timestamp_ns": 123456789,
        "values": {
            "process_pid_sha256": hashlib.sha256(b"123").hexdigest(),
            "process_tgid_sha256": hashlib.sha256(b"456").hexdigest(),
        },
        "formal_admission": False,
    }
    assert raw_name.decode("ascii") not in repr(event)
    assert "comm" not in public
    assert "process_pid" not in public["values"]


@pytest.mark.parametrize(
    ("raw", "sender", "flags", "ancillary", "code"),
    [
        (_packet(connector.PROC_EVENT_EXEC, (1, 1))[:-1], (0, 1), 0, [], "netlink_length_invalid"),
        (_packet(connector.PROC_EVENT_EXEC, (1, 1)), (100, 1), 0, [], "sender_invalid"),
        (_packet(connector.PROC_EVENT_EXEC, (1, 1)), (0, 1), 1, [], "flags_invalid"),
        (
            _packet(connector.PROC_EVENT_EXEC, (1, 1)),
            (0, 1),
            0,
            [(1, 2, b"x")],
            "ancillary_invalid",
        ),
        (_packet(connector.PROC_EVENT_EXEC, (1, 1)), (0, 0), 0, [], "sender_invalid"),
        (b"\0" * (connector.MAX_PACKET_BYTES + 1), (0, 1), 0, [], "packet_too_large"),
        (
            _packet(connector.PROC_EVENT_EXEC, (1, 1), nlmsg_type=1),
            (0, 1),
            0,
            [],
            "netlink_type_invalid",
        ),
        (
            _packet(connector.PROC_EVENT_EXEC, (1, 1), nlmsg_flags=1),
            (0, 1),
            0,
            [],
            "netlink_flags_invalid",
        ),
        (
            _packet(connector.PROC_EVENT_EXEC, (1, 1), nlmsg_pid=9),
            (0, 1),
            0,
            [],
            "netlink_port_invalid",
        ),
        (
            _packet(connector.PROC_EVENT_EXEC, (1, 1), cn_idx=2),
            (0, 1),
            0,
            [],
            "cn_identity_invalid",
        ),
        (
            _packet(connector.PROC_EVENT_EXEC, (1, 1), cn_val=2),
            (0, 1),
            0,
            [],
            "cn_identity_invalid",
        ),
        (
            _packet(connector.PROC_EVENT_EXEC, (1, 1), cn_length=39),
            (0, 1),
            0,
            [],
            "cn_length_invalid",
        ),
        (_packet(connector.PROC_EVENT_EXEC, (1, 1), cn_flags=1), (0, 1), 0, [], "cn_flags_invalid"),
        (_unknown_event_packet(), (0, 1), 0, [], "event_type_unknown"),
        (_packet(connector.PROC_EVENT_EXEC, (0, 1)), (0, 1), 0, [], "event_pid_invalid"),
        (_nonzero_padding_packet(), (0, 1), 0, [], "netlink_padding_nonzero"),
        (_packet(connector.PROC_EVENT_EXEC, (1, 1)) + b"\0", (0, 1), 0, [], "netlink_truncated"),
    ],
)
def test_rejects_invalid_transport_or_wire_structure(
    raw: bytes,
    sender: tuple[int, int],
    flags: int,
    ancillary: list[object],
    code: str,
) -> None:
    with pytest.raises(connector.ProcessConnectorError, match=code):
        connector.decode_packet(raw, sender, flags, ancillary)


@pytest.mark.parametrize(
    ("what", "values", "offset", "code"),
    [
        (connector.PROC_EVENT_NONE, (0,), 16 + 20 + 16 + 4, "event_reserved_nonzero"),
        (connector.PROC_EVENT_EXEC, (1, 1), 16 + 20 + 16 + 8, "event_reserved_nonzero"),
        (connector.PROC_EVENT_FORK, (1, 1, 2, 2), 16 + 20 + 16 + 16, "event_reserved_nonzero"),
        (connector.PROC_EVENT_COREDUMP, (1, 1, 2, 2), 16 + 20 + 16 + 16, "event_reserved_nonzero"),
    ],
)
def test_rejects_nonzero_reserved_union_bytes(
    what: int, values: tuple[int, ...], offset: int, code: str
) -> None:
    raw = bytearray(_packet(what, values))
    raw[offset] = 1
    with pytest.raises(connector.ProcessConnectorError, match=code):
        _decode(bytes(raw))


def test_comm_bytes_are_opaque_and_invalid_pid_does_not_leak_input() -> None:
    raw_name = bytes(range(16))
    event = _decode(_packet(connector.PROC_EVENT_COMM, (1, 1), comm=raw_name))[0]
    assert event.values == {"process_pid": 1, "process_tgid": 1}
    assert raw_name.hex() not in repr(event.to_public())

    malformed = bytearray(_packet(connector.PROC_EVENT_EXIT, (1, 1, 0, 0, -1, 1)))
    with pytest.raises(connector.ProcessConnectorError, match="event_pid_invalid") as caught:
        _decode(bytes(malformed))
    assert "-1" not in str(caught.value)
