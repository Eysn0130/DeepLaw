from __future__ import annotations

import errno
import os
import select
import socket
import threading
import time
from pathlib import Path

import pytest

from benchmarks.hosts import linux_mcp_socket_transport as transport

_EXPECTED_SEND_CLOSE_ERRNOS = frozenset(
    getattr(errno, name)
    for name in (
        "EBADF",
        "ECONNABORTED",
        "ECONNRESET",
        "EPIPE",
        "ENOTCONN",
        "ESHUTDOWN",
    )
    if hasattr(errno, name)
)


def _start_relay(
    left: socket.socket,
    right: socket.socket,
    **kwargs: object,
) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            transport.relay_bidirectional(left, right, **kwargs)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors


def _send_until_done(
    connection: socket.socket, data: bytes, *, allow_expected_close: bool = False
) -> int:
    view = memoryview(data)
    deadline = time.monotonic() + 3
    while view:
        try:
            written = connection.send(view)
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            assert remaining > 0
            _readable, writable, _exceptional = select.select([], [connection], [], remaining)
            assert writable
            continue
        except OSError as error:
            if not allow_expected_close or error.errno not in _EXPECTED_SEND_CLOSE_ERRNOS:
                raise
            break
        assert written > 0
        view = view[written:]
    return len(data) - len(view)


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    received = bytearray()
    deadline = time.monotonic() + 3
    while len(received) < size:
        remaining = deadline - time.monotonic()
        assert remaining > 0
        readable, _writable, _exceptional = select.select([connection], [], [], remaining)
        assert readable
        try:
            chunk = connection.recv(size - len(received))
        except BlockingIOError:
            continue
        assert chunk
        received.extend(chunk)
    return bytes(received)


def _recv_eof(connection: socket.socket) -> None:
    readable, _writable, _exceptional = select.select([connection], [], [], 3)
    assert readable
    assert connection.recv(1) == b""


def _join_relay(thread: threading.Thread, errors: list[BaseException]) -> None:
    thread.join(3)
    assert not thread.is_alive()
    assert not errors


def _connect_when_ready(path: Path) -> socket.socket:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    deadline = time.monotonic() + 3
    while True:
        try:
            connection.connect(os.fspath(path))
            return connection
        except (FileNotFoundError, ConnectionRefusedError):
            if time.monotonic() >= deadline:
                connection.close()
                raise
            time.sleep(0.01)


def test_relay_forwards_fragmented_bytes_and_half_closes_on_eof() -> None:
    client, left = socket.socketpair()
    right, server = socket.socketpair()
    thread, errors = _start_relay(left, right)
    try:
        for part in (b'{"json":', b'"fragmented"', b'}\n'):
            assert _send_until_done(client, part) == len(part)
        client.shutdown(socket.SHUT_WR)
        for part in (b'{"json":', b'"fragmented"', b'}\n'):
            assert _recv_exact(server, len(part)) == part
        _recv_eof(server)
        for part in (b'{"reply":', b'"complete"', b'}\n'):
            assert _send_until_done(server, part) == len(part)
        server.shutdown(socket.SHUT_WR)
        for part in (b'{"reply":', b'"complete"', b'}\n'):
            assert _recv_exact(client, len(part)) == part
        _recv_eof(client)
        _join_relay(thread, errors)
        assert left.fileno() == -1
        assert right.fileno() == -1
    finally:
        client.close()
        server.close()
        left.close()
        right.close()


def test_relay_rejects_client_line_over_16k_and_closes_both_sides() -> None:
    client, left = socket.socketpair()
    right, server = socket.socketpair()
    thread, errors = _start_relay(left, right)
    try:
        sent = _send_until_done(
            client,
            b"x" * (transport.CLIENT_MAX_LINE_BYTES + 1),
            allow_expected_close=True,
        )
        assert sent > 0
        thread.join(3)
        assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], transport.MCPTransportError)
        assert errors[0].code == "relay_line_too_long"
        assert left.fileno() == -1
        assert right.fileno() == -1
    finally:
        client.close()
        server.close()
        left.close()
        right.close()


def test_relay_rejects_server_line_over_128k_and_closes_both_sides() -> None:
    client, left = socket.socketpair()
    right, server = socket.socketpair()
    thread, errors = _start_relay(left, right)
    try:
        sent = _send_until_done(
            server,
            b"y" * (transport.SERVER_MAX_LINE_BYTES + 1),
            allow_expected_close=True,
        )
        assert sent > 0
        thread.join(3)
        assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], transport.MCPTransportError)
        assert errors[0].code == "relay_line_too_long"
        assert left.fileno() == -1
        assert right.fileno() == -1
    finally:
        client.close()
        server.close()
        left.close()
        right.close()


def test_relay_rejects_total_budget_and_closes_both_sides() -> None:
    client, left = socket.socketpair()
    right, server = socket.socketpair()
    thread, errors = _start_relay(left, right, max_total_bytes=6)
    try:
        assert _send_until_done(client, b"abc\n") == 4
        assert _send_until_done(server, b"def\n", allow_expected_close=True) > 0
        thread.join(3)
        assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], transport.MCPTransportError)
        assert errors[0].code == "relay_total_budget_exceeded"
        assert left.fileno() == -1
        assert right.fileno() == -1
    finally:
        client.close()
        server.close()
        left.close()
        right.close()


def test_relay_deadline_closes_idle_socketpair() -> None:
    client, left = socket.socketpair()
    right, server = socket.socketpair()
    thread, errors = _start_relay(left, right, timeout_seconds=0.05)
    try:
        thread.join(3)
        assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], transport.MCPTransportError)
        assert errors[0].code == "relay_deadline_exceeded"
        assert left.fileno() == -1
        assert right.fileno() == -1
    finally:
        client.close()
        server.close()
        left.close()
        right.close()


def test_relay_invalid_budget_closes_both_sockets() -> None:
    client, left = socket.socketpair()
    right, server = socket.socketpair()
    with pytest.raises(transport.MCPTransportError, match="relay_budget_invalid"):
        transport.relay_bidirectional(left, right, max_total_bytes=0)
    assert left.fileno() == -1
    assert right.fileno() == -1
    client.close()
    server.close()


def test_relay_closed_descriptor_closes_remaining_socket() -> None:
    client, left = socket.socketpair()
    right, server = socket.socketpair()
    left.close()
    with pytest.raises(transport.MCPTransportError, match="relay_fd_invalid"):
        transport.relay_bidirectional(left, right)
    assert right.fileno() == -1
    client.close()
    server.close()


@pytest.mark.skipif(
    not hasattr(transport.socket, "SO_PEERCRED"),
    reason="platform does not expose Linux SO_PEERCRED",
)
def test_mcp_endpoint_uses_actual_peer_uid_and_removes_socket_path(tmp_path: Path) -> None:
    path = tmp_path / "mcp.sock"
    accepted: list[socket.socket] = []
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            accepted.append(
                transport.mcp_stdio_endpoint(path, allowed_peer_uid=os.geteuid())
            )
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    client = _connect_when_ready(path)
    try:
        thread.join(3)
        assert not thread.is_alive()
        assert not errors
        assert len(accepted) == 1
        assert not path.exists()
        with accepted[0]:
            client.sendall(b"peer-checked\n")
            assert accepted[0].recv(32) == b"peer-checked\n"
    finally:
        client.close()
        for connection in accepted:
            connection.close()


@pytest.mark.skipif(
    not hasattr(transport.socket, "SO_PEERCRED"),
    reason="platform does not expose Linux SO_PEERCRED",
)
def test_mcp_endpoint_rejects_actual_peer_uid_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "mcp.sock"
    errors: list[BaseException] = []
    wrong_uid = os.geteuid() + 1

    def serve() -> None:
        try:
            transport.mcp_stdio_endpoint(path, allowed_peer_uid=wrong_uid)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    client = _connect_when_ready(path)
    try:
        thread.join(3)
        assert not thread.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], transport.MCPTransportError)
        assert errors[0].code == "peer_uid_rejected"
        assert not path.exists()
    finally:
        client.close()


def test_peer_credentials_unsupported_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delattr(transport.socket, "SO_PEERCRED", raising=False)
    path = tmp_path / "mcp.sock"
    with pytest.raises(transport.MCPTransportError, match="peer_credentials_unsupported"):
        transport.mcp_stdio_endpoint(path)
    with pytest.raises(transport.MCPTransportError, match="peer_credentials_unsupported"):
        transport.host_stdio_client(path)
    assert not path.exists()
