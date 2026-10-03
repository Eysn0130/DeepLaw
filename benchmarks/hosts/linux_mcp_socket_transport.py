"""Bounded Unix-socket transport for one owner-controlled MCP stdio link.

The endpoint and client expose only file-descriptor transport.  They do not
parse, retain, or log MCP messages.  Importing this module has no process,
socket, or filesystem side effects.
"""

from __future__ import annotations

import errno
import math
import os
import selectors
import socket
import stat
import struct
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Final

CLIENT_MAX_LINE_BYTES: Final = 16 * 1024
SERVER_MAX_LINE_BYTES: Final = 128 * 1024
DEFAULT_MAX_TOTAL_BYTES: Final = 1024 * 1024
DEFAULT_TIMEOUT_SECONDS: Final = 30.0
_READ_CHUNK_BYTES: Final = 16 * 1024
_PEERCRED_STRUCT_SIZE: Final = struct.calcsize("=3i")


class MCPTransportError(RuntimeError):
    """A bounded transport failure represented without underlying details."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _socket_path(value: Path | str) -> Path:
    try:
        path = Path(value)
        raw = os.fspath(path)
    except (OSError, TypeError, ValueError):
        raise MCPTransportError("socket_path_invalid") from None
    if (
        not isinstance(raw, str)
        or not raw
        or "\x00" in raw
        or not path.is_absolute()
        or any(part in {".", ".."} for part in path.parts)
        or os.path.normpath(raw) != raw
    ):
        raise MCPTransportError("socket_path_invalid")
    return path


def _validate_uid(uid: int) -> None:
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 0:
        raise MCPTransportError("peer_uid_invalid")


def _validate_limits(max_total_bytes: int, timeout_seconds: float) -> None:
    if (
        isinstance(max_total_bytes, bool)
        or not isinstance(max_total_bytes, int)
        or max_total_bytes <= 0
    ):
        raise MCPTransportError("relay_budget_invalid")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise MCPTransportError("relay_timeout_invalid")


def _require_peer_credentials() -> int:
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise MCPTransportError("peer_credentials_unsupported")
    try:
        return int(option)
    except (OverflowError, TypeError, ValueError):
        raise MCPTransportError("peer_credentials_unsupported") from None


def _peer_uid(connection: socket.socket) -> int:
    option = _require_peer_credentials()
    try:
        raw = connection.getsockopt(socket.SOL_SOCKET, option, _PEERCRED_STRUCT_SIZE)
        if not isinstance(raw, bytes) or len(raw) != _PEERCRED_STRUCT_SIZE:
            raise MCPTransportError("peer_credentials_invalid")
        _pid, uid, _gid = struct.unpack("=3i", raw)
    except MCPTransportError:
        raise
    except (OSError, struct.error, ValueError):
        raise MCPTransportError("peer_credentials_failed") from None
    if uid < 0:
        raise MCPTransportError("peer_credentials_invalid")
    return uid


def _close_socket(connection: socket.socket | None) -> None:
    if connection is not None:
        with suppress(OSError):
            connection.close()


def _unlink_socket(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        raise MCPTransportError("endpoint_unlink_failed") from None


def mcp_stdio_endpoint(
    socket_path: Path | str, allowed_peer_uid: int = 0
) -> socket.socket:
    """Listen for one credential-checked owner connection and return its socket."""

    path = _socket_path(socket_path)
    _validate_uid(allowed_peer_uid)
    _require_peer_credentials()
    listener: socket.socket | None = None
    accepted: socket.socket | None = None
    bound = False
    try:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.settimeout(DEFAULT_TIMEOUT_SECONDS)
        listener.bind(os.fspath(path))
        bound = True
        listener.listen(1)
        accepted, _address = listener.accept()
        if _peer_uid(accepted) != allowed_peer_uid:
            raise MCPTransportError("peer_uid_rejected")
        accepted.settimeout(None)
        listener.close()
        listener = None
        _unlink_socket(path)
        result = accepted
        accepted = None
        return result
    except MCPTransportError:
        raise
    except TimeoutError:
        raise MCPTransportError("endpoint_timeout") from None
    except (OSError, ValueError):
        raise MCPTransportError("endpoint_failed") from None
    finally:
        _close_socket(accepted)
        _close_socket(listener)
        if bound:
            with suppress(OSError):
                path.unlink()


def _require_socket_node(path: Path) -> None:
    try:
        info = os.lstat(path)
    except OSError:
        raise MCPTransportError("socket_connect_failed") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISSOCK(info.st_mode):
        raise MCPTransportError("socket_connect_failed")


@dataclass(slots=True)
class _Flow:
    source_fd: int
    destination_fd: int
    destination_socket: socket.socket | None
    max_line_bytes: int
    line_bytes: int = 0
    pending: bytearray = field(default_factory=bytearray)
    source_open: bool = True
    destination_shutdown: bool = False


def _check_line_limit(flow: _Flow, data: bytes) -> None:
    line_bytes = flow.line_bytes
    for value in data:
        line_bytes += 1
        if line_bytes > flow.max_line_bytes:
            raise MCPTransportError("relay_line_too_long")
        if value == 0x0A:
            line_bytes = 0
    flow.line_bytes = line_bytes


def _half_close(flow: _Flow) -> None:
    if flow.destination_shutdown:
        return
    if flow.destination_socket is not None:
        try:
            flow.destination_socket.shutdown(socket.SHUT_WR)
        except OSError as error:
            if error.errno not in {errno.EBADF, errno.EPIPE, errno.ENOTCONN}:
                raise MCPTransportError("relay_half_close_failed") from None
    flow.destination_shutdown = True


def _read_flow(flow: _Flow) -> int:
    try:
        data = os.read(flow.source_fd, _READ_CHUNK_BYTES)
    except BlockingIOError:
        return 0
    except OSError:
        raise MCPTransportError("relay_read_failed") from None
    if not data:
        flow.source_open = False
        if not flow.pending:
            _half_close(flow)
        return 0
    _check_line_limit(flow, data)
    flow.pending.extend(data)
    return len(data)


def _write_flow(flow: _Flow) -> None:
    if not flow.pending:
        if not flow.source_open:
            _half_close(flow)
        return
    try:
        written = os.write(flow.destination_fd, flow.pending)
    except BlockingIOError:
        return
    except OSError:
        raise MCPTransportError("relay_write_failed") from None
    if written <= 0:
        raise MCPTransportError("relay_write_failed")
    del flow.pending[:written]
    if not flow.pending and not flow.source_open:
        _half_close(flow)


def _refresh_selector(selector: selectors.BaseSelector, flows: tuple[_Flow, ...]) -> bool:
    for key in tuple(selector.get_map().values()):
        with suppress(KeyError, OSError):
            selector.unregister(key.fileobj)
    registrations: dict[int, tuple[int, list[tuple[str, _Flow]]]] = {}

    def add(fd: int, mask: int, kind: str, flow: _Flow) -> None:
        current_mask, current_items = registrations.get(fd, (0, []))
        current_items.append((kind, flow))
        registrations[fd] = (current_mask | mask, current_items)

    for flow in flows:
        if flow.source_open and not flow.destination_shutdown:
            add(flow.source_fd, selectors.EVENT_READ, "read", flow)
        if flow.pending:
            add(flow.destination_fd, selectors.EVENT_WRITE, "write", flow)
    for fd, (mask, items) in registrations.items():
        selector.register(fd, mask, tuple(items))
    return bool(registrations)


def _relay_flows(
    flows: tuple[_Flow, ...],
    *,
    close_sockets: tuple[socket.socket, ...],
    max_total_bytes: int,
    timeout_seconds: float,
) -> None:
    selector: selectors.BaseSelector | None = None
    blocking_states: dict[int, bool] = {}
    total_bytes = 0
    try:
        _validate_limits(max_total_bytes, timeout_seconds)
        descriptors = {
            fd
            for flow in flows
            for fd in (flow.source_fd, flow.destination_fd)
        }
        if any(fd < 0 for fd in descriptors):
            raise MCPTransportError("relay_fd_invalid")
        for fd in descriptors:
            blocking_states[fd] = os.get_blocking(fd)
            os.set_blocking(fd, False)
        selector = selectors.DefaultSelector()
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                if all(not flow.source_open and not flow.pending for flow in flows):
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPTransportError("relay_deadline_exceeded")
                if not _refresh_selector(selector, flows):
                    raise MCPTransportError("relay_destination_closed")
                events = selector.select(remaining)
                if not events:
                    raise MCPTransportError("relay_deadline_exceeded")
                for key, mask in events:
                    for kind, flow in key.data:
                        if kind == "read" and mask & selectors.EVENT_READ:
                            total_bytes += _read_flow(flow)
                            if total_bytes > max_total_bytes:
                                raise MCPTransportError("relay_total_budget_exceeded")
                        elif kind == "write" and mask & selectors.EVENT_WRITE:
                            _write_flow(flow)
        except MCPTransportError:
            raise
        except BaseException:
            raise MCPTransportError("relay_failed") from None
    except MCPTransportError:
        raise
    except BaseException:
        raise MCPTransportError("relay_failed") from None
    finally:
        if selector is not None:
            with suppress(OSError):
                selector.close()
        for fd, was_blocking in blocking_states.items():
            with suppress(OSError):
                os.set_blocking(fd, was_blocking)
        closed: set[int] = set()
        for connection in close_sockets:
            identity = id(connection)
            if identity not in closed:
                closed.add(identity)
                _close_socket(connection)


def relay_bidirectional(
    left: socket.socket,
    right: socket.socket,
    *,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Forward two connected sockets with bounded lines, bytes, and lifetime."""

    if not isinstance(left, socket.socket) or not isinstance(right, socket.socket):
        _close_socket(left if isinstance(left, socket.socket) else None)
        _close_socket(right if isinstance(right, socket.socket) else None)
        raise MCPTransportError("relay_socket_invalid")
    _relay_flows(
        (
            _Flow(left.fileno(), right.fileno(), right, CLIENT_MAX_LINE_BYTES),
            _Flow(right.fileno(), left.fileno(), left, SERVER_MAX_LINE_BYTES),
        ),
        close_sockets=(left, right),
        max_total_bytes=max_total_bytes,
        timeout_seconds=timeout_seconds,
    )


def _relay_stdio_socket(
    connection: socket.socket,
    stdin: BinaryIO,
    stdout: BinaryIO,
    *,
    max_total_bytes: int,
    timeout_seconds: float,
) -> None:
    try:
        stdin_fd = stdin.fileno()
        stdout_fd = stdout.fileno()
    except BaseException:
        raise MCPTransportError("stdio_fd_invalid") from None
    _relay_flows(
        (
            _Flow(stdin_fd, connection.fileno(), connection, CLIENT_MAX_LINE_BYTES),
            _Flow(connection.fileno(), stdout_fd, None, SERVER_MAX_LINE_BYTES),
        ),
        close_sockets=(connection,),
        max_total_bytes=max_total_bytes,
        timeout_seconds=timeout_seconds,
    )


def host_stdio_client(
    socket_path: Path | str,
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Connect to a root-owned relay and forward the caller's stdio."""

    path = _socket_path(socket_path)
    _validate_limits(max_total_bytes, timeout_seconds)
    _require_peer_credentials()
    _require_socket_node(path)
    try:
        input_stream = sys.stdin.buffer if stdin is None else stdin
        output_stream = sys.stdout.buffer if stdout is None else stdout
    except AttributeError:
        raise MCPTransportError("stdio_fd_invalid") from None
    connection: socket.socket | None = None
    try:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(timeout_seconds)
        connection.connect(os.fspath(path))
        if _peer_uid(connection) != 0:
            raise MCPTransportError("server_uid_rejected")
        connection.settimeout(None)
        _relay_stdio_socket(
            connection,
            input_stream,
            output_stream,
            max_total_bytes=max_total_bytes,
            timeout_seconds=timeout_seconds,
        )
        try:
            output_stream.flush()
        except BaseException:
            raise MCPTransportError("stdio_flush_failed") from None
    except MCPTransportError:
        raise
    except TimeoutError:
        raise MCPTransportError("socket_connect_timeout") from None
    except (OSError, ValueError):
        raise MCPTransportError("socket_connect_failed") from None
    finally:
        _close_socket(connection)
