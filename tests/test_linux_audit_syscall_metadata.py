from __future__ import annotations

import hashlib

import pytest

from benchmarks.hosts import linux_audit_syscall_metadata as metadata


def _bindings() -> tuple[metadata.RoleBinding, metadata.RoleBinding]:
    return (
        metadata.RoleBinding("host", 1000, 368),
        metadata.RoleBinding("mcp", 1001, 369),
    )


def _payload(serial: int, fields: str) -> bytes:
    return f"audit(1.000:{serial}): {fields}".encode("ascii")


def _syscall(
    serial: int = 1,
    *,
    syscall: int = 198,
    success: str = "no",
    exit: int = -97,
    uid: int = 1000,
    pid: int = 368,
    extra: str = "",
) -> bytes:
    return _payload(
        serial,
        f"arch=c00000b7 syscall={syscall} success={success} exit={exit} "
        f"uid={uid} pid={pid}{extra}",
    )


def _seccomp(serial: int = 1, *, syscall: int = 198, uid: int = 1000, pid: int = 368) -> bytes:
    return _payload(
        serial,
        f"arch=c00000b7 syscall={syscall} uid={uid} pid={pid} code=0x50000 "
        'comm="private-model-text" exe="/private/secret/host"',
    )


def _expected_socket_filtered(count: int = 3) -> metadata.ExpectedCanary:
    return metadata.ExpectedCanary(
        role="host",
        action_id="socket_filtered",
        syscall=198,
        count=count,
        record_type="SECCOMP",
        expected_success=None,
        expected_errno=None,
        expected_seccomp_action="ERRNO",
    )


def _aggregate(
    records: list[tuple[int, bytes]],
    expected: tuple[metadata.ExpectedCanary, ...],
    *,
    configured: tuple[int, ...] = (198,),
    source_bound: bool = True,
    window_closed: bool = True,
) -> dict[str, object]:
    return metadata.aggregate_audit_metadata(
        records,
        role_bindings={"host": {"uid": 1000, "pid": 368}, "mcp": {"uid": 1001, "pid": 369}},
        expected_actions=expected,
        configured_syscalls=configured,
        rule_binding_sha256=hashlib.sha256(b"rule").hexdigest(),
        audit_pid_registered=True,
        audit_enabled=1,
        lost_before=0,
        lost_after=0,
        backlog_before=0,
        backlog_after=0,
        source_bound=source_bound,
        window_closed=window_closed,
    )


def test_syscall_parses_signed_exit_and_derives_errno_without_sensitive_fields() -> None:
    event = metadata.parse_syscall_payload(
        _syscall(
            extra=' a0=28 comm="private-model-text" exe="/private/secret/host" '
            'path="/private/secret/input"'
        ),
        ordinal=1,
        bindings=_bindings(),
    )

    assert event.type_code == metadata.AUDIT_SYSCALL
    assert event.arch == metadata.AUDIT_ARCH_AARCH64
    assert event.syscall == 198
    assert event.uid == 1000
    assert event.role == "host"
    assert event.success is False
    assert event.exit == -97
    assert event.errno == 97
    assert event.action_id == "unknown_family"
    public = event.to_public()
    assert public["pid_sha256"] == metadata.RoleBinding("host", 1000, 368).pid_sha256
    assert "pid" not in public
    assert "a0" not in public
    assert "/private/secret/host" not in repr(event)
    assert "private-model-text" not in repr(public)


def test_successful_syscall_has_no_errno_and_direct_conditional_call_stays_unknown() -> None:
    event = metadata.parse_syscall_payload(
        _syscall(serial=2, syscall=221, success="yes", exit=0),
        ordinal=1,
        bindings=_bindings(),
        configured_syscalls=(221,),
    )
    assert event.success is True
    assert event.exit == 0
    assert event.errno is None
    assert event.action_id == "syscall_221"

    socket_without_a0 = metadata.parse_syscall_payload(
        _syscall(serial=3), ordinal=1, bindings=_bindings()
    )
    assert socket_without_a0.action_id == "unknown_family"


def test_seccomp_shape_uses_code_action_only_and_does_not_fabricate_errno_or_family() -> None:
    event = metadata.parse_seccomp_payload(
        _seccomp(), ordinal=1, bindings=_bindings()
    )
    assert event.type_code == metadata.AUDIT_SECCOMP
    assert event.arch == metadata.AUDIT_ARCH_AARCH64
    assert event.syscall == 198
    assert event.uid == 1000
    assert event.action_id == "socket_filtered"
    assert event.seccomp_action == "ERRNO"
    public = event.to_public()
    assert "success" not in public
    assert "exit" not in public
    assert "errno" not in public
    assert "a0" not in public
    assert "family" not in public
    assert "/private/secret/host" not in repr(public)

    # The low data word is not exposed as errno; any ERRNO action code remains
    # the same metadata class.
    changed = _seccomp().replace(b"0x50000", b"0x50001")
    event = metadata.parse_seccomp_payload(changed, ordinal=1, bindings=_bindings())
    assert event.seccomp_action == "ERRNO"


@pytest.mark.parametrize(
    ("message_type", "payload", "code"),
    [
        (1999, _syscall(), "unknown_type"),
        (
            metadata.AUDIT_SYSCALL,
            _syscall().replace(b"arch=c00000b7", b"arch=c000003e"),
            "unknown_arch",
        ),
        (
            metadata.AUDIT_SYSCALL,
            _syscall().replace(b" success=no", b" success=maybe"),
            "invalid_success",
        ),
        (
            metadata.AUDIT_SYSCALL,
            _syscall().replace(b" exit=-97", b" exit=0"),
            "result_inconsistent",
        ),
        (
            metadata.AUDIT_SECCOMP,
            _seccomp().replace(b"code=0x50000", b"code=0x30000"),
            "seccomp_action_unknown",
        ),
    ],
)
def test_malformed_or_unknown_kernel_facts_are_typed_gaps(
    message_type: int, payload: bytes, code: str
) -> None:
    with pytest.raises(metadata.AuditSyscallMetadataGap) as error:
        metadata.parse_audit_record(
            payload,
            message_type=message_type,
            ordinal=1,
            bindings=_bindings(),
        )
    assert error.value.code == code


def test_missing_and_duplicate_allowlisted_fields_are_gaps() -> None:
    missing = _syscall().replace(b" pid=368", b"")
    with pytest.raises(metadata.AuditSyscallMetadataGap) as error:
        metadata.parse_syscall_payload(missing, ordinal=1, bindings=_bindings())
    assert error.value.code == "missing_field"

    duplicate = _syscall().replace(b" uid=1000", b" uid=1000 uid=1000")
    with pytest.raises(metadata.AuditSyscallMetadataGap) as error:
        metadata.parse_syscall_payload(duplicate, ordinal=1, bindings=_bindings())
    assert error.value.code == "duplicate_field"

    duplicate_unknown = _syscall(
        extra=' comm="private-model-text" comm="second-private-text"'
    )
    with pytest.raises(metadata.AuditSyscallMetadataGap) as error:
        metadata.parse_syscall_payload(duplicate_unknown, ordinal=1, bindings=_bindings())
    assert error.value.code == "duplicate_field"


def test_unknown_pid_or_syscall_does_not_bind_by_uid_alone() -> None:
    with pytest.raises(metadata.AuditSyscallMetadataGap) as error:
        metadata.parse_syscall_payload(
            _syscall(pid=370), ordinal=1, bindings=_bindings()
        )
    assert error.value.code == "role_binding_unknown"

    with pytest.raises(metadata.AuditSyscallMetadataGap) as error:
        metadata.parse_syscall_payload(
            _syscall(syscall=999), ordinal=1, bindings=_bindings(), configured_syscalls=(198,)
        )
    assert error.value.code == "unknown_syscall"


def test_seccomp_record_without_eoe_can_form_a_filtered_action_count() -> None:
    expected = (_expected_socket_filtered(),)
    receipt = _aggregate(
        [(metadata.AUDIT_SECCOMP, _seccomp(serial)) for serial in (10, 11, 12)],
        expected,
    )

    assert receipt["status"] == "observed"
    assert receipt["formal_admission"] is False
    assert receipt["claim_eligible"] is False
    counts = receipt["action_counts"]
    assert isinstance(counts, list)
    assert counts[0]["observed_count"] == 3
    assert counts[0]["denied_count"] == 3
    records = receipt["records"]
    assert isinstance(records, list)
    assert all(record["type_code"] == metadata.AUDIT_SECCOMP for record in records)
    assert all("errno" not in record and "a0" not in record for record in records)
    assert metadata.validate_metadata_receipt(receipt) == receipt


def test_expected_canary_missing_is_gap_and_never_a_zero_count() -> None:
    receipt = _aggregate(
        [(metadata.AUDIT_SECCOMP, _seccomp(serial)) for serial in (20, 21)],
        (_expected_socket_filtered(3),),
    )
    assert receipt["status"] == "gap"
    assert receipt["failure_codes"] == ["expected_action_count_mismatch"]
    counts = receipt["action_counts"]
    assert isinstance(counts, list)
    assert counts[0]["observed_count"] is None
    assert counts[0]["denied_count"] is None


def test_syscall_and_seccomp_counts_are_type_bound() -> None:
    expected = (
        metadata.ExpectedCanary(
            role="host",
            action_id="syscall_221",
            syscall=221,
            record_type="SYSCALL",
            expected_success=True,
        ),
        _expected_socket_filtered(1),
    )
    receipt = _aggregate(
        [
            (metadata.AUDIT_SYSCALL, _syscall(serial=30, syscall=221, success="yes", exit=0)),
            (metadata.AUDIT_SECCOMP, _seccomp(serial=31)),
        ],
        expected,
        configured=(198, 221),
    )
    assert receipt["status"] == "observed"
    assert [row["record_type"] for row in receipt["action_counts"]] == ["SYSCALL", "SECCOMP"]


def test_setup_or_transport_boundary_is_fail_closed() -> None:
    expected = (_expected_socket_filtered(1),)
    unbound = _aggregate(
        [(metadata.AUDIT_SECCOMP, _seccomp(40))], expected, source_bound=False
    )
    assert unbound["status"] == "gap"
    assert unbound["failure_codes"] == ["netlink_source_unbound"]

    open_window = _aggregate(
        [(metadata.AUDIT_SECCOMP, _seccomp(41))], expected, window_closed=False
    )
    assert open_window["status"] == "gap"
    assert open_window["failure_codes"] == ["window_unclosed"]


def test_exact_role_uid_mapping_is_required() -> None:
    with pytest.raises(metadata.AuditSyscallMetadataGap) as error:
        metadata.RoleBinding("host", 1001, 368)
    assert error.value.code == "role_uid_mismatch"

    mcp_expected = (
        metadata.ExpectedCanary(
            role="mcp",
            action_id="socket_filtered",
            syscall=198,
            record_type="SECCOMP",
            expected_success=None,
            expected_seccomp_action="ERRNO",
        ),
    )
    receipt = _aggregate(
        [(metadata.AUDIT_SECCOMP, _seccomp(50, uid=1001, pid=369))],
        mcp_expected,
    )
    assert receipt["status"] == "observed"

    host_expected = (
        metadata.ExpectedCanary(
            role="host",
            action_id="socket_filtered",
            syscall=198,
            record_type="SECCOMP",
            expected_success=None,
            expected_seccomp_action="ERRNO",
        ),
    )
    wrong_role = _aggregate(
        [(metadata.AUDIT_SECCOMP, _seccomp(51, uid=1001, pid=369))], host_expected
    )
    assert wrong_role["status"] == "gap"
    assert wrong_role["failure_codes"] == ["unexpected_action"]


def test_malformed_owner_inputs_fail_closed_as_typed_gaps() -> None:
    with pytest.raises(metadata.AuditSyscallMetadataGap) as error:
        metadata.RoleBinding([], 1000, 368)  # type: ignore[arg-type]
    assert error.value.code == "invalid_role_bindings"

    with pytest.raises(metadata.AuditSyscallMetadataGap) as error:
        metadata.ExpectedCanary("host", 7, 198)  # type: ignore[arg-type]
    assert error.value.code == "invalid_expected_actions"

    receipt = metadata.aggregate_audit_metadata(
        None,  # type: ignore[arg-type]
        role_bindings={"host": {"uid": 1000, "pid": 368}, "mcp": {"uid": 1001, "pid": 369}},
        expected_actions=(_expected_socket_filtered(1),),
        configured_syscalls=(198,),
        rule_binding_sha256=hashlib.sha256(b"rule").hexdigest(),
        audit_pid_registered=True,
        audit_enabled=1,
        lost_before=0,
        lost_after=0,
        backlog_before=0,
        backlog_after=0,
        source_bound=True,
        window_closed=True,
    )
    assert receipt["status"] == "gap"
    assert receipt["failure_codes"] == ["payload_invalid"]


def test_aggregate_stops_reading_at_record_budget(monkeypatch):
    monkeypatch.setattr(metadata, "MAX_RECORDS", 2)
    consumed = 0

    def records():
        nonlocal consumed
        for _ in range(3):
            consumed += 1
            yield (metadata.AUDIT_SECCOMP, b"unused")
        raise AssertionError("read beyond hard record budget")

    result = _aggregate(records(), (_expected_socket_filtered(),))
    assert consumed == 3
    assert result["status"] == "gap"
