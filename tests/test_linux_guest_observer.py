from __future__ import annotations

import copy
import hashlib
import struct
from dataclasses import replace
from typing import Any

import pytest

from benchmarks.hosts import linux_guest_observer as observer


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _status(
    *, enabled: int = 0, pid: int = 0, lost: int = 0, backlog: int = 0
) -> observer.AuditStatus:
    return observer.AuditStatus(
        enabled=enabled,
        pid=pid,
        failure=0,
        rate_limit=0,
        backlog_limit=64,
        lost=lost,
        backlog=backlog,
        feature_bitmap=0,
        backlog_wait_time=0,
        backlog_wait_time_actual=0,
    )


def _netlink_frame(payload: bytes, *, message_type: int, sequence: int = 0) -> bytes:
    length = 16 + len(payload)
    frame = struct.pack("<IHHII", length, message_type, 0, sequence, 0) + payload
    return frame + b"\x00" * ((-length) % 4)


def _legacy_audit_frame(payload: bytes, *, message_type: int = 1305) -> bytes:
    return struct.pack("<IHHII", len(payload), message_type, 0, 0, 0) + payload


def _audit_payload(serial: int) -> bytes:
    return f"audit(1.000:{serial}): hidden=must-not-retain".encode()


class _FakeTransport:
    port_id = 7001

    def __init__(self, packets: list[bytes]) -> None:
        self.status = _status()
        self.rule_digests: list[str] = []
        self.packets = list(packets)
        self.closed = False

    def get_status(self) -> observer.AuditStatus:
        return self.status

    def set_status(self, *, mask: int, enabled: int = 0, pid: int = 0) -> None:
        if mask & observer.AUDIT_STATUS_PID:
            self.status = replace(self.status, pid=pid)
        if mask & observer.AUDIT_STATUS_ENABLED:
            self.status = replace(self.status, enabled=enabled)

    def list_rule_digests(self) -> list[str]:
        return list(self.rule_digests)

    def add_rule(self, rule: observer.AuditRuleSpec) -> None:
        self.rule_digests.append(rule.digest())

    def recv(self, _size: int) -> bytes:
        return self.packets.pop(0) if self.packets else b""

    def close(self) -> None:
        self.closed = True


class _WireSocket:
    def __init__(self, *, legacy_event: bool = False) -> None:
        self.responses: list[bytes] = []
        self.legacy_event = legacy_event

    def settimeout(self, _timeout: float) -> None:
        return None

    def bind(self, _address: tuple[int, int]) -> None:
        return None

    def getsockname(self) -> tuple[int, int]:
        return (7002, 0)

    def sendall(self, packet: bytes) -> None:
        _length, message_type, flags, sequence, _port_id = struct.unpack_from(
            "<IHHII", packet
        )
        if message_type == observer.AUDIT_GET:
            if flags & observer.NLM_F_ACK:
                self.responses.append(_netlink_frame(
                    struct.pack("<i", 0), message_type=observer.NLMSG_ERROR,
                    sequence=sequence,
                ))
            event = _audit_payload(31)
            self.responses.append(
                _legacy_audit_frame(event, message_type=1300)
                if self.legacy_event
                else _netlink_frame(event, message_type=1300, sequence=0)
            )
            status = struct.pack("<11I", 0, 1, 0, 7002, 0, 64, 0, 0, 0, 0, 0)
            self.responses.append(
                _netlink_frame(status, message_type=observer.AUDIT_GET, sequence=sequence)
            )
        else:
            self.responses.append(
                _netlink_frame(
                    struct.pack("<i", 0),
                    message_type=observer.NLMSG_ERROR,
                    sequence=sequence,
                )
            )

    def recv(self, _size: int) -> bytes:
        if not self.responses:
            raise TimeoutError()
        return self.responses.pop(0)

    def recvmsg(self, size: int):
        return self.recv(size), [], 0, (0, 0)

    def close(self) -> None:
        return None


@pytest.mark.parametrize("address,flags", [((7003, 0), 0), ((0, 1), 0), ((0, 0), 32)])
def test_kernel_transport_rejects_other_sender_or_truncated_datagram(address, flags):
    sock = _WireSocket()
    sock.recvmsg = lambda size: (b"ignored", [], flags, address)
    transport = observer.NetlinkAuditTransport(socket_factory=lambda *args: sock)
    with pytest.raises(observer.AuditProtocolError, match="sender or datagram"):
        transport.get_status()


@pytest.mark.parametrize("flags,sequence,port_id", [(1, 0, 0), (0, 1, 0), (0, 0, 3)])
def test_async_audit_requires_kernel_header(flags, sequence, port_id):
    payload = _audit_payload(10)
    packet = struct.pack("<IHHII", 16 + len(payload), 1300, flags, sequence, port_id) + payload
    packet += b"\0" * (-len(packet) % 4)
    with pytest.raises(observer.AuditProtocolError, match="kernel header"):
        list(observer.NetlinkAuditTransport._messages(packet))


def _rule() -> observer.AuditRuleSpec:
    return observer.AuditRuleSpec(syscall_numbers=(56, 57, 59))


def _role(role: str, *, suffix: str = "a") -> observer.RoleObservation:
    return observer.RoleObservation(
        role=role,
        uid=1000 if role == "host" else 1001,
        mount_namespace_sha256=_sha(f"mnt:{suffix}:{role}"),
        pid_namespace_sha256=_sha(f"pid:{suffix}:{role}"),
        net_namespace_sha256=_sha(f"net:{suffix}:{role}"),
        ipc_namespace_sha256=_sha(f"ipc:{suffix}:{role}"),
        cgroup_id_sha256=_sha(f"cgroup:{suffix}:{role}"),
        cgroup_events_sha256=_sha(f"events:{suffix}:{role}"),
        cgroup_populated=0,
    )


def _collector(*, packets: list[bytes]) -> tuple[observer.AuditCollector, _FakeTransport]:
    transport = _FakeTransport(packets)
    config = observer.AuditCollectorConfig(
        rules=(_rule(),),
        required_event_types=("EXECVE", "SYSCALL"),
    )
    collector = observer.AuditCollector(config, transport=transport)
    collector.start()
    return collector, transport


def test_rule_encoding_is_bounded_and_architecture_bound() -> None:
    rule = _rule()
    encoded = rule.encode()
    assert len(encoded) == observer._AUDIT_RULE_FIXED_BYTES + len(rule.key)
    assert rule.to_public()["arch"] == observer.AUDIT_ARCH_AARCH64
    assert len(rule.digest()) == 64
    words = struct.unpack_from(
        f"<{3 + 4 * observer.AUDIT_MAX_FIELDS + 1}I", encoded
    )
    fields_start = 3 + observer.AUDIT_BITMASK_SIZE
    values_start = fields_start + observer.AUDIT_MAX_FIELDS
    fieldflags_start = values_start + observer.AUDIT_MAX_FIELDS
    buflen_index = fieldflags_start + observer.AUDIT_MAX_FIELDS
    assert words[0] == observer.AUDIT_FILTER_EXIT
    assert words[1] == observer.AUDIT_ALWAYS
    assert words[2] == 2
    assert words[fields_start] == observer.AUDIT_ARCH
    assert words[values_start] == observer.AUDIT_ARCH_AARCH64
    assert words[fieldflags_start] == observer.AUDIT_EQUAL
    assert words[fields_start + 1] == observer.AUDIT_FILTERKEY
    assert words[values_start + 1] == len(rule.key)
    assert words[fieldflags_start + 1] == observer.AUDIT_EQUAL
    assert words[buflen_index] == len(rule.key)
    assert encoded[observer._AUDIT_RULE_FIXED_BYTES :] == rule.key.encode("ascii")


def test_role_failure_rule_encodes_kernel_uid_and_success_filters() -> None:
    rule = observer.AuditRuleSpec(syscall_numbers=(56, 203), uid=1000, success=False)
    words = observer._AUDIT_RULE_FIXED.unpack(rule.encode()[:observer._AUDIT_RULE_FIXED_BYTES])
    fields = 3 + observer.AUDIT_BITMASK_SIZE
    values = fields + observer.AUDIT_MAX_FIELDS
    flags = values + observer.AUDIT_MAX_FIELDS
    assert words[2] == 4
    assert words[fields + 2:fields + 4] == (1, 104)
    assert words[values + 2:values + 4] == (1000, 0)
    assert words[flags + 2:flags + 4] == (observer.AUDIT_EQUAL, observer.AUDIT_EQUAL)
    assert rule.digest() != observer.AuditRuleSpec(syscall_numbers=(56, 203)).digest()
    with pytest.raises(observer.LinuxGuestObservationError):
        observer.AuditRuleSpec(syscall_numbers=(56,), uid=True)
    with pytest.raises(observer.LinuxGuestObservationError):
        observer.AuditRuleSpec(syscall_numbers=(56,), success=0)


def test_rule_binding_digest_matches_exact_encoded_wire_rule() -> None:
    collector, transport = _collector(packets=[])
    assert transport.list_rule_digests() == [_rule().digest()]
    collector.close()


def test_audit_consumer_runs_only_after_payload_validation_and_failure_is_gap():
    consumed = []
    transport = _FakeTransport([])
    config = observer.AuditCollectorConfig(rules=(_rule(),), required_event_types=("SYSCALL",))
    with observer.AuditCollector(
        config, transport=transport,
        event_consumer=lambda kind, raw: consumed.append((kind, raw)),
    ) as collector:
        collector.start()
        transport.packets.append(_netlink_frame(_audit_payload(10), message_type=1300))
        assert collector.collect_once() == 1
        assert consumed == [(1300, _audit_payload(10))]
        transport.packets.append(_netlink_frame(b"invalid", message_type=1300))
        with pytest.raises(observer.ObservationGap):
            collector.collect_once()
        assert len(consumed) == 1

    def failed_consumer(kind, raw):
        raise ValueError("internal consumer gap")

    transport = _FakeTransport([])
    with observer.AuditCollector(
        config, transport=transport, event_consumer=failed_consumer,
    ) as collector:
        collector.start()
        transport.packets.append(_netlink_frame(_audit_payload(11), message_type=1300))
        with pytest.raises(observer.ObservationGap, match="consumer failed"):
            collector.collect_once()
        assert "audit_consumer_failed" in collector.finish(role_observation=_role("host"))[
            "failure_codes"
        ]


def test_netlink_transport_keeps_unsolicited_event_while_matching_control_reply() -> None:
    transport = observer.NetlinkAuditTransport(socket_factory=lambda *_args: _WireSocket())
    status = transport.get_status()
    assert status.pid == transport.port_id == 7002
    pending = transport.recv()
    messages = list(observer.NetlinkAuditTransport._messages(pending))
    assert messages[0][0] == 1300
    assert messages[0][2] == 0
    transport.close()


def test_netlink_transport_accepts_legacy_auditd_unicast_length() -> None:
    prefix = b"audit(1.000:17): "
    payload = prefix + b"x" * (109 - len(prefix))
    packet = _legacy_audit_frame(payload)
    assert len(packet) == 125
    assert struct.unpack_from("<IHHII", packet) == (109, 1305, 0, 0, 0)
    assert list(observer.NetlinkAuditTransport._messages(packet)) == [
        (1305, 0, 0, payload)
    ]


@pytest.mark.parametrize("tail", [b"\x00", b"\x01"])
def test_netlink_transport_rejects_legacy_auditd_unconsumed_tail(tail: bytes) -> None:
    prefix = b"audit(1.000:18): "
    payload = prefix + b"x" * (109 - len(prefix))
    with pytest.raises(observer.AuditProtocolError, match="audit frame length"):
        list(observer.NetlinkAuditTransport._messages(_legacy_audit_frame(payload) + tail))


def test_pending_legacy_audit_event_is_repacked_as_standard_frame() -> None:
    transport = observer.NetlinkAuditTransport(
        socket_factory=lambda *_args: _WireSocket(legacy_event=True)
    )
    transport.get_status()
    pending = transport.recv()
    length, message_type, flags, sequence, port_id = struct.unpack_from("<IHHII", pending)
    assert length == 16 + len(_audit_payload(31))
    assert message_type == 1300
    assert flags == sequence == port_id == 0
    assert next(observer.NetlinkAuditTransport._messages(pending))[3] == _audit_payload(31)
    transport.close()


def test_native_collector_registers_enables_binds_and_redacts_event_payloads() -> None:
    packets = [
        _netlink_frame(_audit_payload(11), message_type=1300),
        _netlink_frame(_audit_payload(11), message_type=1309),
    ]
    collector, transport = _collector(packets=packets)
    assert collector.collect_once() == 1
    assert collector.collect_once() == 1
    receipt = collector.finish(role_observation=_role("host"))

    assert receipt["status"] == "observed"
    assert receipt["formal_admission"] is False
    assert receipt["claim_eligible"] is False
    assert receipt["audit"]["pid_registered"] is True
    assert receipt["audit"]["lost_before"] == receipt["audit"]["lost_after"] == 0
    assert receipt["audit"]["backlog_after"] == 0
    assert receipt["audit"]["raw_records_retained"] is False
    assert "hidden=must-not-retain" not in repr(receipt)
    assert receipt["audit"]["observed_rule_sha256s"] == [transport.rule_digests[0]]
    assert observer.validate_observation_receipt(receipt) == receipt
    collector.close()
    assert transport.closed is True


def test_unknown_event_is_a_gap_and_cannot_be_relabelled_observed() -> None:
    collector, _transport = _collector(
        packets=[_legacy_audit_frame(_audit_payload(12), message_type=1999)]
    )
    with pytest.raises(observer.ObservationGap, match="unknown"):
        collector.collect_once()

    with pytest.raises(observer.ObservationGap, match="unknown"):
        observer.parse_audit_payload(
            b"type=EXECVE msg=audit(1.000:12): hidden=must-not-retain",
            message_type=1300,
            ordinal=1,
        )

    # A forged receipt with the same closed shape still cannot turn the gap into
    # an observed result because unknown event types are rejected by the parser.
    audit = _valid_audit()
    audit["events"][0]["message_type"] = "FUTURE_EVENT"
    audit["observed_event_types"] = ["FUTURE_EVENT", "SYSCALL"]
    audit["sequence_sha256"] = observer.sequence_sha256(audit["events"])
    # build_observation_receipt itself is strict, so the forged unknown row is
    # rejected before a caller can persist it as a valid intermediate.
    with pytest.raises(observer.LinuxGuestObservationError):
        observer.build_observation_receipt(
            status="gap",
            role_observation=None,
            audit=audit,
            failure_codes=["audit_event_unknown"],
        )


def test_lost_change_produces_gap_and_backlog_is_fail_closed() -> None:
    collector, transport = _collector(
        packets=[
            _netlink_frame(_audit_payload(13), message_type=1300),
            _netlink_frame(_audit_payload(13), message_type=1309),
        ]
    )
    collector.collect_once()
    collector.collect_once()
    transport.status = replace(transport.status, lost=1)
    receipt = collector.finish(role_observation=_role("host"))
    assert receipt["status"] == "gap"
    assert "audit_lost_changed" in receipt["failure_codes"]
    with pytest.raises(observer.LinuxGuestObservationError):
        observer.validate_observation_receipt({**receipt, "status": "observed"})


def test_validator_rejects_missing_required_event_and_secret_or_path_fields() -> None:
    collector, _transport = _collector(
        packets=[_netlink_frame(_audit_payload(14), message_type=1300)]
    )
    collector.collect_once()
    receipt = collector.finish(role_observation=_role("host"))
    assert receipt["status"] == "gap"
    assert "audit_event_missing" in receipt["failure_codes"]

    unsafe = copy.deepcopy(receipt)
    unsafe["audit"]["transcript"] = "private"
    unsafe["record_sha256"] = observer.record_sha256(unsafe)
    with pytest.raises(observer.LinuxGuestObservationError, match="forbidden"):
        observer.validate_observation_receipt(unsafe)

    unsafe = copy.deepcopy(receipt)
    unsafe["audit"]["path_fields_retained"] = True
    unsafe["record_sha256"] = observer.record_sha256(unsafe)
    with pytest.raises(observer.LinuxGuestObservationError, match="redaction"):
        observer.validate_observation_receipt(unsafe)


def test_role_set_requires_distinct_nonroot_uids_namespaces_and_cgroups() -> None:
    host = observer.build_observation_receipt(
        status="observed",
        role_observation=_role("host"),
        audit=_valid_audit(),
        failure_codes=[],
    )
    mcp = observer.build_observation_receipt(
        status="observed",
        role_observation=_role("mcp"),
        audit=_valid_audit(),
        failure_codes=[],
    )
    assert len(observer.validate_role_receipt_set((host, mcp))) == 2

    same_uid = copy.deepcopy(mcp)
    same_uid["role_observation"]["uid"] = host["role_observation"]["uid"]
    same_uid["record_sha256"] = observer.record_sha256(same_uid)
    with pytest.raises(observer.LinuxGuestObservationError, match="uid"):
        observer.validate_role_receipt_set((host, same_uid))

    populated = copy.deepcopy(mcp)
    populated["role_observation"]["cgroup_populated"] = 1
    populated["record_sha256"] = observer.record_sha256(populated)
    with pytest.raises(observer.LinuxGuestObservationError, match="populated"):
        observer.validate_observation_receipt(populated)

    unrelated_audit = copy.deepcopy(mcp)
    unrelated_audit["audit"]["events"][0]["serial"] = 999
    unrelated_audit["audit"]["sequence_sha256"] = observer.sequence_sha256(
        unrelated_audit["audit"]["events"]
    )
    unrelated_audit["record_sha256"] = observer.record_sha256(unrelated_audit)
    with pytest.raises(observer.LinuxGuestObservationError, match="sequence_sha256"):
        observer.validate_role_receipt_set((host, unrelated_audit))


def test_role_identity_can_be_captured_before_exit_and_cgroup_after_exit(monkeypatch) -> None:
    monkeypatch.setattr(observer, "_read_uid", lambda _proc_dir: 1000)
    monkeypatch.setattr(observer, "_namespace_digest", lambda _proc_dir, name: _sha(name))
    monkeypatch.setattr(observer, "_cgroup_id_digest", lambda _cgroup_dir: _sha("cgroup"))
    identity = observer.observe_role_identity(
        role="host", pid=42, cgroup_dir="/synthetic-cgroup"
    )
    monkeypatch.setattr(
        observer,
        "_read_cgroup",
        lambda _cgroup_dir: (_sha("cgroup"), _sha("events"), 0),
    )
    final = observer.observe_role_cgroup(identity, cgroup_dir="/synthetic-cgroup")
    assert final.uid == 1000
    assert final.cgroup_populated == 0
    assert final.pid_namespace_sha256 == _sha("pid")


def _valid_audit() -> dict[str, Any]:
    events = [
        {"ordinal": 1, "message_type": "EXECVE", "serial": 21},
        {"ordinal": 2, "message_type": "SYSCALL", "serial": 21},
    ]
    configured = [_rule().digest()]
    return {
        "pid_registered": True,
        "enabled": 1,
        "lost_before": 0,
        "lost_after": 0,
        "backlog_before": 0,
        "backlog_after": 0,
        "rule_binding_sha256": observer._sha_projection(configured),
        "configured_rule_sha256s": configured,
        "observed_rule_sha256s": configured,
        "frame_count": 2,
        "event_count": 2,
        "events": events,
        "unknown_event_count": 0,
        "required_event_types": ["EXECVE", "SYSCALL"],
        "observed_event_types": ["EXECVE", "SYSCALL"],
        "sequence_sha256": observer.sequence_sha256(events),
        "sequence_bound": True,
        "raw_records_retained": False,
        "path_fields_retained": False,
        "command_fields_retained": False,
        "start_status": {
            "enabled": 1,
            "pid_registered": True,
            "lost": 0,
            "backlog": 0,
            "failure": 0,
            "rate_limit": 0,
            "backlog_limit": 64,
            "feature_bitmap": 0,
            "backlog_wait_time": 0,
            "backlog_wait_time_actual": 0,
        },
    }


def test_native_probe_requires_linux_transport_but_always_stays_nonformal() -> None:
    class ProbeTransport:
        port_id = 91
        closed = False

        def get_status(self) -> observer.AuditStatus:
            return _status(enabled=1, pid=self.port_id)

        def close(self) -> None:
            self.closed = True

    transport = ProbeTransport()
    result = observer.probe_native_audit(transport=transport)
    assert result["audit_get_succeeded"] is True
    assert result["pid_registered"] is True
    assert result["formal_admission"] is False
    assert transport.closed is True


def test_setup_failure_has_a_valid_gap_without_fabricating_native_state() -> None:
    receipt = observer.build_gap_receipt(failure_codes=["audit_not_available"])
    assert receipt["status"] == "gap"
    assert receipt["formal_admission"] is False
    assert receipt["claim_eligible"] is False
    assert receipt["role_observation"] is None
    assert receipt["audit"]["sequence_bound"] is False
    assert observer.validate_observation_receipt(receipt) == receipt


def test_non_linux_native_transport_is_unavailable_without_injected_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observer.sys, "platform", "darwin")
    with pytest.raises(observer.NativeAuditUnavailable):
        observer.NetlinkAuditTransport()


@pytest.mark.parametrize("clears", [True, False])
def test_start_drains_rule_event_but_requires_observed_empty_backlog(clears):
    class StartupTransport(_FakeTransport):
        def add_rule(self, rule):
            super().add_rule(rule)
            self.status = replace(self.status, backlog=1)
            self.packets.append(_netlink_frame(_audit_payload(91), message_type=1305))

        def recv(self, size):
            packet = super().recv(size)
            if packet and clears:
                self.status = replace(self.status, backlog=0)
            return packet

    transport = StartupTransport([])
    collector = observer.AuditCollector(
        observer.AuditCollectorConfig(
            rules=(observer.AuditRuleSpec(syscall_numbers=(198,)),),
            required_event_types=("CONFIG_CHANGE",),
        ),
        transport=transport,
    )
    if not clears:
        with pytest.raises(observer.ObservationGap, match="backlog"):
            collector.start()
        return
    collector.start()
    result = collector.finish(role_observation=None)
    assert result["audit"]["backlog_before"] == 0
    assert result["audit"]["events"][0]["message_type"] == "CONFIG_CHANGE"
    assert "audit_backlog_not_empty" not in result["failure_codes"]
