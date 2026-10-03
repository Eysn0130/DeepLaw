from __future__ import annotations

import errno
import inspect
import stat
from types import SimpleNamespace

import pytest

from benchmarks.hosts import linux_role_boundary_probe as probe


def _regular_file() -> SimpleNamespace:
    return SimpleNamespace(st_mode=stat.S_IFREG | 0o444, st_size=1)


class _FakeSocket:
    def __init__(self, connect_error: BaseException | None = None) -> None:
        self.connect_error = connect_error
        self.timeout: float | None = None
        self.target: object | None = None
        self.closed = False
        self.send_called = False

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def connect(self, target: object) -> None:
        self.target = target
        if self.connect_error is not None:
            raise self.connect_error

    def send(self, *_args: object) -> None:
        self.send_called = True

    def close(self) -> None:
        self.closed = True


_UNREACHABLE_ERROR = OSError(errno.ENETUNREACH, "unreachable")


def _run_with_denials(
    *,
    uid: int = 1000,
    pid: int = 1,
    write_errno: int = errno.EROFS,
    connect_error: BaseException | None = _UNREACHABLE_ERROR,
) -> tuple[dict[str, object], list[tuple[bytes, int, int]], list[int], _FakeSocket]:
    open_calls: list[tuple[bytes, int, int]] = []
    close_calls: list[int] = []
    results = [(10, 0), (-1, write_errno)]
    fake_socket = _FakeSocket(connect_error)

    def openat(path: bytes, flags: int, mode: int) -> tuple[int, int]:
        open_calls.append((path, flags, mode))
        return results.pop(0)

    def close(fd: int) -> None:
        close_calls.append(fd)

    result = probe._run_boundary_probe(
        system_getter=lambda: "linux",
        machine_getter=lambda: "aarch64",
        uid_getter=lambda: uid,
        pid_getter=lambda: pid,
        openat_fn=openat,
        fstat_fn=lambda _fd: _regular_file(),
        close_fn=close,
        socket_factory=lambda *_args: fake_socket,
    )
    return result, open_calls, close_calls, fake_socket


def test_successful_negative_checks_are_fixed_numeric_and_hash_bound() -> None:
    result, open_calls, close_calls, fake_socket = _run_with_denials(
        write_errno=errno.EACCES
    )

    assert set(result) == {
        "schema_version",
        "formal_admission",
        "claim_eligible",
        "uid",
        "pid",
        "checks",
        "record_sha256",
    }
    assert result["schema_version"] == probe.SCHEMA_VERSION
    assert result["formal_admission"] is False
    assert result["claim_eligible"] is False
    assert result["uid"] == 1000
    assert result["pid"] == 1
    assert result["checks"] == [
        {
            "action_id": "readonly_runtime_write",
            "syscall": probe.SYS_OPENAT,
            "success": False,
            "errno": errno.EACCES,
        },
        {
            "action_id": "nonloopback_connect",
            "syscall": probe.SYS_CONNECT,
            "success": False,
            "errno": errno.ENETUNREACH,
        },
    ]
    assert result["record_sha256"] == probe._record_digest(result)
    assert open_calls[0][0] == b"/runtime/entry.py"
    assert open_calls[1][0] == b"/runtime/entry.py"
    assert open_calls[0][1] & probe.os.O_RDONLY == probe.os.O_RDONLY
    assert open_calls[1][1] & probe.os.O_WRONLY == probe.os.O_WRONLY
    assert not open_calls[1][1] & getattr(probe.os, "O_TRUNC", 0)
    assert not open_calls[1][1] & getattr(probe.os, "O_CREAT", 0)
    assert close_calls == [10]
    assert fake_socket.timeout == probe.MAX_CONNECT_TIMEOUT_SECONDS
    assert fake_socket.target == probe.NETWORK_TARGET
    assert fake_socket.closed is True
    assert fake_socket.send_called is False


def test_read_only_open_and_fstat_precede_write_attempt() -> None:
    operations: list[str] = []
    open_results = [(10, 0), (-1, errno.EPERM)]

    def openat(_path: bytes, flags: int, _mode: int) -> tuple[int, int]:
        operations.append("write_open" if flags & probe.os.O_WRONLY else "read_open")
        return open_results.pop(0)

    def fstat(_fd: int) -> SimpleNamespace:
        operations.append("fstat")
        return _regular_file()

    def close(_fd: int) -> None:
        operations.append("close")

    class NoSocket:
        def __call__(self, *_args: object) -> _FakeSocket:
            return _FakeSocket(OSError(errno.ENETUNREACH, "unreachable"))

    result = probe._run_boundary_probe(
        system_getter=lambda: "linux",
        machine_getter=lambda: "aarch64",
        uid_getter=lambda: 1001,
        pid_getter=lambda: 1,
        openat_fn=openat,
        fstat_fn=fstat,
        close_fn=close,
        socket_factory=NoSocket(),
    )

    assert operations == ["read_open", "fstat", "close", "write_open"]
    assert result["uid"] == 1001


def test_unexpected_write_open_is_closed_and_never_written() -> None:
    open_results = [(10, 0), (11, 0)]
    close_calls: list[int] = []

    def openat(_path: bytes, _flags: int, _mode: int) -> tuple[int, int]:
        return open_results.pop(0)

    with pytest.raises(probe.BoundaryProbeGap, match="runtime_entry_write_open_succeeded"):
        probe._run_boundary_probe(
            system_getter=lambda: "linux",
            machine_getter=lambda: "aarch64",
            uid_getter=lambda: 1000,
            pid_getter=lambda: 1,
            openat_fn=openat,
            fstat_fn=lambda _fd: _regular_file(),
            close_fn=close_calls.append,
            socket_factory=lambda *_args: pytest.fail("network probe must not run"),
        )
    assert close_calls == [10, 11]


@pytest.mark.parametrize(
    ("metadata", "error_code"),
    [
        (SimpleNamespace(st_mode=stat.S_IFDIR, st_size=1), "runtime_entry_fstat_invalid"),
        (SimpleNamespace(st_mode=stat.S_IFREG, st_size=0), "runtime_entry_empty"),
    ],
)
def test_invalid_runtime_entry_precondition_is_gap_without_write_attempt(
    metadata: SimpleNamespace,
    error_code: str,
) -> None:
    open_calls: list[int] = []

    def openat(_path: bytes, _flags: int, _mode: int) -> tuple[int, int]:
        open_calls.append(1)
        return 10, 0

    with pytest.raises(probe.BoundaryProbeGap, match=error_code):
        probe._run_boundary_probe(
            system_getter=lambda: "linux",
            machine_getter=lambda: "aarch64",
            uid_getter=lambda: 1000,
            pid_getter=lambda: 1,
            openat_fn=openat,
            fstat_fn=lambda _fd: metadata,
            close_fn=lambda _fd: None,
            socket_factory=lambda *_args: pytest.fail("network probe must not run"),
        )
    assert len(open_calls) == 1


@pytest.mark.parametrize(
    "error_number",
    [errno.ENOENT, errno.EINVAL],
)
def test_unexpected_write_errno_is_gap(error_number: int) -> None:
    open_results = [(10, 0), (-1, error_number)]

    with pytest.raises(probe.BoundaryProbeGap, match="runtime_entry_write_errno_unexpected"):
        probe._run_boundary_probe(
            system_getter=lambda: "linux",
            machine_getter=lambda: "aarch64",
            uid_getter=lambda: 1000,
            pid_getter=lambda: 1,
            openat_fn=lambda _path, _flags, _mode: open_results.pop(0),
            fstat_fn=lambda _fd: _regular_file(),
            close_fn=lambda _fd: None,
            socket_factory=lambda *_args: pytest.fail("network probe must not run"),
        )


@pytest.mark.parametrize(
    ("connect_error", "error_code"),
    [
        (TimeoutError("deadline"), "network_connect_timeout"),
        (OSError(errno.EHOSTUNREACH, "host unreachable"), "network_connect_errno_unexpected"),
    ],
)
def test_network_timeout_and_other_errno_are_gaps(
    connect_error: BaseException,
    error_code: str,
) -> None:
    with pytest.raises(probe.BoundaryProbeGap, match=error_code):
        _run_with_denials(connect_error=connect_error)


def test_network_connect_success_is_gap_and_socket_is_closed() -> None:
    fake_socket = _FakeSocket(connect_error=None)
    with pytest.raises(probe.BoundaryProbeGap, match="network_connect_succeeded"):
        probe._socket_probe(lambda *_args: fake_socket)
    assert fake_socket.closed is True


def test_network_socket_close_failure_is_gap() -> None:
    class ClosingSocket(_FakeSocket):
        def close(self) -> None:
            raise OSError("close")

    fake_socket = ClosingSocket(OSError(errno.ENETUNREACH, "unreachable"))
    with pytest.raises(probe.BoundaryProbeGap, match="network_socket_close_failed"):
        probe._socket_probe(lambda *_args: fake_socket)


@pytest.mark.parametrize(
    ("system", "machine", "uid", "pid", "error_code"),
    [
        ("darwin", "aarch64", 1000, 1, "linux_required"),
        ("linux", "x86_64", 1000, 1, "aarch64_required"),
        ("linux", "aarch64", 0, 1, "role_uid_invalid"),
        ("linux", "aarch64", True, 1, "role_uid_invalid"),
        ("linux", "aarch64", 1002, 1, "role_uid_invalid"),
        ("linux", "aarch64", 1000, 0, "pid_invalid"),
    ],
)
def test_platform_role_and_pid_gates_fail_closed_before_native_calls(
    system: str,
    machine: str,
    uid: object,
    pid: int,
    error_code: str,
) -> None:
    calls: list[str] = []

    with pytest.raises(probe.BoundaryProbeGap, match=error_code):
        probe._run_boundary_probe(
            system_getter=lambda: system,
            machine_getter=lambda: machine,
            uid_getter=lambda: uid,  # type: ignore[return-value]
            pid_getter=lambda: pid,
            openat_fn=lambda *_args: calls.append("openat") or (10, 0),
            fstat_fn=lambda _fd: calls.append("fstat") or _regular_file(),
            close_fn=lambda _fd: calls.append("close"),
            socket_factory=lambda *_args: calls.append("socket"),
        )
    assert calls == []


def test_public_api_has_no_target_configuration_or_json_export() -> None:
    assert tuple(inspect.signature(probe.run_boundary_probe).parameters) == ()
    assert "RUNTIME_ENTRY_PATH" not in probe.__all__
    assert "NETWORK_TARGET" not in probe.__all__
    assert not hasattr(probe, "main")
