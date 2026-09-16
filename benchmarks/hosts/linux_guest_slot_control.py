"""Owner control for one already-started Linux guest slot.

The launcher owns role creation and lifecycle observation.  This module only
provides the small owner-side control bridge which is attached to
``linux_role_launcher.run_native(on_roles_started=...)``.  It intentionally
does not create a namespace, start a provider, read transcripts, or expose a
general RPC surface.

The callback blocks until the owner asks for ``stop``.  That lets the launcher
observe the host's real shutdown before the owner sends the final, sanitized
receipt over the same VSOCK connection.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import http.client
import json
import math
import os
import re
import select
import signal
import socket
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from threading import RLock
from typing import Any, Final

from benchmarks.hosts import native_slot_frames as frames
from benchmarks.hosts.linux_role_launcher import RoleHandle

DEFAULT_PORT: Final[int] = 4050
HOST_LOOPBACK: Final[str] = "127.0.0.1"
HOST_PORT: Final[int] = 4096
VMADDR_CID_HOST: Final[int] = 2
VMADDR_CID_ANY: Final[int] = 0xFFFF_FFFF
MAX_TIMEOUT_SECONDS: Final[float] = 60.0
MAX_HTTP_SECONDS: Final[float] = 5.0
MAX_HEALTH_READY_SECONDS: Final[float] = 15.0
MAX_OPERATIONS: Final[int] = 8
MAX_SESSION_ID_BYTES: Final[int] = 256
MAX_HOST_RESPONSE_BYTES: Final[int] = 256 * 1024
MAX_CHILD_RESULT_BYTES: Final[int] = 16 * 1024
MAX_HTTP_BODY_BYTES: Final[int] = 8 * 1024
CHILD_POLL_SECONDS: Final[float] = 0.05
CHILD_TERM_GRACE_SECONDS: Final[float] = 0.25
CHILD_KILL_GRACE_SECONDS: Final[float] = 0.25
CLONE_NEWNET: Final[int] = 0x4000_0000
PR_SET_PDEATHSIG: Final[int] = 1
_SESSION_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
)
_OPS: Final[frozenset[str]] = frozenset(
    {"health", "new_session", "fork", "mcp_status", "stop"}
)
_REQUEST_FIELDS: Final[dict[str, frozenset[str]]] = {
    "health": frozenset({"op"}),
    "new_session": frozenset({"op"}),
    "fork": frozenset({"op", "session_id"}),
    "mcp_status": frozenset({"op"}),
    "stop": frozenset({"op"}),
}


class GuestSlotControlError(RuntimeError):
    """A bounded, public failure code for the slot control bridge."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", code):
            code = "control_failed"
        super().__init__(code)
        self.code = code


def _raise(code: str) -> None:
    raise GuestSlotControlError(code)


def _finite_timeout(value: object, *, maximum: float = MAX_TIMEOUT_SECONDS) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _raise("timeout_invalid")
    result = float(value)
    if not math.isfinite(result) or result <= 0 or result > maximum:
        _raise("timeout_invalid")
    return result


def _positive_int(value: object, *, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _raise(code)
    return value


def _json_bytes(value: Mapping[str, object], *, limit: int) -> bytes:
    try:
        raw = json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError):
        _raise("response_encoding_failed")
    if len(raw) > limit:
        _raise("response_budget_exceeded")
    return raw


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _raise("request_json_invalid")
        result[key] = value
    return result


def _decode_request(payload: bytes) -> dict[str, object]:
    if not isinstance(payload, bytes) or not payload:
        _raise("request_json_invalid")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda _value: _raise("request_json_invalid"),
        )
    except GuestSlotControlError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _raise("request_json_invalid")
    if not isinstance(value, dict) or not isinstance(value.get("op"), str):
        _raise("request_shape_invalid")
    operation = value["op"]
    if operation not in _OPS:
        _raise("operation_invalid")
    if set(value) != set(_REQUEST_FIELDS[operation]):
        _raise("request_fields_invalid")
    if operation == "fork":
        session_id = value.get("session_id")
        if (
            not isinstance(session_id, str)
            or len(session_id.encode("utf-8")) > MAX_SESSION_ID_BYTES
            or _SESSION_ID_RE.fullmatch(session_id) is None
        ):
            _raise("session_id_invalid")
    return value


def _route_for_request(request: Mapping[str, object]) -> tuple[str, str, bytes | None]:
    """Return the only HTTP method/path/body allowed for a request."""

    operation = request.get("op")
    if operation == "health":
        return "GET", "/global/health", None
    if operation == "new_session":
        return "POST", "/session", b"{}"
    if operation == "fork":
        session_id = request.get("session_id")
        if not isinstance(session_id, str) or _SESSION_ID_RE.fullmatch(session_id) is None:
            _raise("session_id_invalid")
        return "POST", f"/session/{session_id}/fork", b"{}"
    if operation == "mcp_status":
        return "GET", "/mcp", None
    if operation == "stop":
        _raise("stop_has_no_http_route")
    _raise("operation_invalid")


def _close_fd(fd: int) -> None:
    with suppress(OSError):
        os.close(fd)


def _terminate_child(pid: int) -> None:
    """Terminate and reap a child without leaving a timed-out worker behind."""

    with suppress(ProcessLookupError, PermissionError, OSError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + CHILD_TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            waited, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        except OSError:
            break
        if waited == pid:
            return
        time.sleep(min(CHILD_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
    with suppress(ProcessLookupError, PermissionError, OSError):
        os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + CHILD_KILL_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            waited, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        except OSError:
            break
        if waited == pid:
            return
        time.sleep(min(CHILD_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
    with suppress(ChildProcessError, OSError):
        os.waitpid(pid, 0)


def _libc_function(name: str, *, restype: object, argtypes: list[object]) -> Any:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        function = getattr(libc, name)
        function.restype = restype
        function.argtypes = argtypes
        return function
    except (AttributeError, OSError):
        _raise("host_netns_unavailable")


def _set_child_parent_death_signal() -> None:
    try:
        prctl = _libc_function(
            "prctl",
            restype=ctypes.c_int,
            argtypes=[ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong],
        )
        if prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) != 0:
            _raise("host_http_process_failed")
    except GuestSlotControlError:
        raise
    except Exception:
        _raise("host_http_process_failed")


def _enter_host_netns(host_pid: int) -> None:
    _positive_int(host_pid, code="host_pid_invalid")
    path = f"/proc/{host_pid}/ns/net"
    try:
        namespace_fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        _raise("host_netns_unavailable")
    try:
        setns = _libc_function(
            "setns",
            restype=ctypes.c_int,
            argtypes=[ctypes.c_int, ctypes.c_int],
        )
        if setns(namespace_fd, CLONE_NEWNET) != 0:
            _raise("host_netns_unavailable")
    except GuestSlotControlError:
        raise
    except Exception:
        _raise("host_netns_unavailable")
    finally:
        _close_fd(namespace_fd)


def _session_identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > MAX_SESSION_ID_BYTES
        or _SESSION_ID_RE.fullmatch(value) is None
    ):
        _raise("host_response_invalid")
    return value


def _mcp_connected(value: object) -> bool:
    if not isinstance(value, Mapping) or not value:
        _raise("host_response_invalid")
    statuses: list[str] = []
    for key, entry in value.items():
        if not isinstance(key, str) or not isinstance(entry, Mapping):
            _raise("host_response_invalid")
        status = entry.get("status")
        if not isinstance(status, str) or not status:
            _raise("host_response_invalid")
        statuses.append(status)
    return bool(statuses) and all(status == "connected" for status in statuses)


def _sanitize_host_response(operation: str, status: int, raw: bytes) -> dict[str, object]:
    if isinstance(status, bool) or not isinstance(status, int) or not 200 <= status < 300:
        _raise("host_http_status")
    if len(raw) > MAX_HOST_RESPONSE_BYTES:
        _raise("host_response_budget_exceeded")
    if raw:
        try:
            value: object = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            _raise("host_response_invalid")
    else:
        value = {}
    if operation == "health":
        if not isinstance(value, Mapping) or not isinstance(value.get("healthy"), bool):
            _raise("host_response_invalid")
        return {"healthy": value["healthy"]}
    elif operation == "new_session":
        if not isinstance(value, Mapping):
            _raise("host_response_invalid")
        return {"session_id": _session_identifier(value.get("id"))}
    if operation == "fork":
        if not isinstance(value, Mapping):
            _raise("host_response_invalid")
        return {"session_id": _session_identifier(value.get("id"))}
    if operation == "mcp_status":
        return {"connected": _mcp_connected(value)}
    _raise("operation_invalid")


def _http_request_in_host_netns(
    operation: str,
    method: str,
    path: str,
    body: bytes | None,
    timeout_seconds: float,
    *,
    retain_fork_source: bool = False,
) -> dict[str, object]:
    """Perform one direct request after entering the host's network namespace."""

    timeout = _finite_timeout(
        timeout_seconds,
        maximum=MAX_HEALTH_READY_SECONDS if operation == "health" else MAX_HTTP_SECONDS,
    )
    if operation not in {"health", "new_session", "fork", "mcp_status"}:
        _raise("operation_invalid")
    if method not in {"GET", "POST"} or not path.startswith("/") or "?" in path:
        _raise("host_route_invalid")
    if body is not None and len(body) > MAX_HTTP_BODY_BYTES:
        _raise("host_request_budget_exceeded")
    connected = False
    phase = "connect"
    try:
        connection = http.client.HTTPConnection(HOST_LOOPBACK, HOST_PORT, timeout=timeout)
        connection.connect()
        connected = True
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        phase = "request"
        connection.request(method, path, body=body, headers=headers)
        phase = "response_headers"
        response = connection.getresponse()
        phase = "response_body"
        raw = response.read(MAX_HOST_RESPONSE_BYTES + 1)
        status = response.status
    except TimeoutError:
        _raise("host_http_" + phase + "_timeout" if connected else "host_connect_failed")
    except (OSError, http.client.HTTPException):
        _raise("host_http_failed" if connected else "host_connect_failed")
    finally:
        with suppress(Exception):
            connection.close()  # type: ignore[possibly-undefined]
    result = _sanitize_host_response(operation, status, raw)
    if operation == "fork":
        from benchmarks.hosts.native_fork_observation import capture_fork_response

        try:
            result["fork_response"] = capture_fork_response(
                method=method, path=path, status_code=status, request_body=body, response=raw,
            )
            if retain_fork_source:
                # Private root-to-root pipe only; public responses discard this.
                result["fork_source"] = {
                    "route_observation": {"method": method, "path": path, "status_code": status},
                    "request_body": base64.b64encode(body).decode("ascii"),
                    "response": base64.b64encode(raw).decode("ascii"),
                }
        except Exception:
            _raise("fork_response_observation_invalid")
    return result


def _child_http_entry(
    write_fd: int,
    host_pid: int,
    operation: str,
    method: str,
    path: str,
    body: bytes | None,
    timeout_seconds: float,
    *,
    absolute_deadline: float | None = None,
    retain_fork_source: bool = False,
) -> None:
    result: dict[str, object]
    success = False
    try:
        _set_child_parent_death_signal()
        _enter_host_netns(host_pid)
        deadline = (
            time.monotonic() + timeout_seconds if absolute_deadline is None else absolute_deadline
        )
        request_budget = deadline - time.monotonic() - min(0.1, timeout_seconds / 4)
        if request_budget <= 0:
            _raise("host_http_setup_timeout")
        source_options = {"retain_fork_source": True} if retain_fork_source else {}
        result = {"ok": True, **_http_request_in_host_netns(
            operation, method, path, body, request_budget, **source_options,
        )}
        success = True
    except GuestSlotControlError as error:
        result = {"ok": False, "error": error.code}
    except BaseException:
        result = {"ok": False, "error": "host_http_failed"}
    try:
        payload = _json_bytes(result, limit=MAX_CHILD_RESULT_BYTES)
        view = memoryview(payload)
        while view:
            written = os.write(write_fd, view)
            if written <= 0:
                break
            view = view[written:]
    except BaseException:
        success = False
    finally:
        _close_fd(write_fd)
    os._exit(0 if success else 1)


def _read_child_result(pid: int, read_fd: int, timeout_seconds: float) -> dict[str, object]:
    timeout = _finite_timeout(timeout_seconds, maximum=MAX_HEALTH_READY_SECONDS)
    deadline = time.monotonic() + timeout
    chunks = bytearray()
    eof = False
    waited_status: int | None = None
    try:
        while not eof or waited_status is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_child(pid)
                _raise("host_http_timeout")
            if not eof:
                try:
                    readable, _writable, _exceptional = select.select(
                        [read_fd], [], [], min(CHILD_POLL_SECONDS, remaining),
                    )
                except (OSError, ValueError):
                    _terminate_child(pid)
                    _raise("host_http_process_failed")
                if readable:
                    try:
                        chunk = os.read(read_fd, MAX_CHILD_RESULT_BYTES + 1 - len(chunks))
                    except OSError:
                        _terminate_child(pid)
                        _raise("host_http_process_failed")
                    if not chunk:
                        eof = True
                    else:
                        chunks.extend(chunk)
                        if len(chunks) > MAX_CHILD_RESULT_BYTES:
                            _terminate_child(pid)
                            _raise("host_result_budget_exceeded")
            if waited_status is None:
                try:
                    waited, status = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    _raise("host_http_process_failed")
                except OSError:
                    _terminate_child(pid)
                    _raise("host_http_process_failed")
                if waited == pid:
                    waited_status = status
        if not chunks or waited_status is None or not os.WIFEXITED(waited_status):
            _raise("host_http_process_failed")
        try:
            result = json.loads(bytes(chunks).decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            _raise("host_http_process_failed")
        if not isinstance(result, dict):
            _raise("host_http_process_failed")
        if result.get("ok") is False and set(result) == {"ok", "error"}:
            code = result["error"]
            _raise(code if isinstance(code, str) else "host_http_failed")
        if (
            result.get("ok") is not True or os.WEXITSTATUS(waited_status) != 0
            or set(result) not in (
                {"ok", "healthy"}, {"ok", "session_id"}, {"ok", "connected"},
                {"ok", "session_id", "fork_response"},
                {"ok", "session_id", "fork_response", "fork_source"},
            )
        ):
            _raise("host_http_process_failed")
        return {key: value for key, value in result.items() if key != "ok"}
    finally:
        _close_fd(read_fd)


def _run_host_http(
    host_pid: int,
    operation: str,
    method: str,
    path: str,
    body: bytes | None,
    *,
    timeout_seconds: float = MAX_HTTP_SECONDS,
    retain_fork_source: bool = False,
) -> dict[str, object]:
    """Run one bounded HTTP request in a short-lived host-netns child."""

    timeout = _finite_timeout(
        timeout_seconds,
        maximum=MAX_HEALTH_READY_SECONDS if operation == "health" else MAX_HTTP_SECONDS,
    )
    if os.geteuid() != 0:
        _raise("controller_not_root")
    deadline = time.monotonic() + timeout
    try:
        read_fd, write_fd = os.pipe()
        pid = os.fork()
    except (AttributeError, OSError):
        with suppress(UnboundLocalError):
            _close_fd(read_fd)  # type: ignore[possibly-undefined]
            _close_fd(write_fd)  # type: ignore[possibly-undefined]
        _raise("host_http_process_unavailable")
    if pid == 0:
        _close_fd(read_fd)
        _child_http_entry(
            write_fd, host_pid, operation, method, path, body, timeout,
            absolute_deadline=deadline,
            retain_fork_source=retain_fork_source,
        )
        os._exit(1)
    _close_fd(write_fd)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _close_fd(read_fd)
        _terminate_child(pid)
        _raise("host_http_timeout")
    return _read_child_result(pid, read_fd, remaining)


def _response_for_operation(operation: str, result: Mapping[str, object]) -> dict[str, object]:
    checked: dict[str, object]
    if operation == "health":
        healthy = result.get("healthy")
        if not isinstance(healthy, bool):
            _raise("host_response_invalid")
        checked = {"healthy": healthy}
    elif operation == "new_session" or operation == "fork":
        checked = {"session_id": _session_identifier(result.get("session_id"))}
    elif operation == "mcp_status":
        connected = result.get("connected")
        if not isinstance(connected, bool):
            _raise("host_response_invalid")
        checked = {"connected": connected}
    elif operation == "stop":
        checked = {"stopping": True}
    else:
        _raise("operation_invalid")
    return {"ok": True, "result": checked, "formal_admission": False}


_PRIVATE_RECEIPT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "path", "paths", "workdir", "working_dir", "cgroup_dir", "command",
        "stdout", "stderr", "console", "rawconsole", "host_text", "environment",
    }
)


def _validate_receipt_value(value: object, *, depth: int = 0) -> object:
    if depth > 8:
        _raise("launcher_receipt_invalid")
    if isinstance(value, Mapping):
        validated: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                _raise("launcher_receipt_invalid")
            lowered = key.lower()
            if (
                lowered in _PRIVATE_RECEIPT_KEYS
                or lowered.endswith(("_path", "_paths", "_console", "_text", "_json"))
            ):
                _raise("launcher_receipt_invalid")
            validated[key] = _validate_receipt_value(item, depth=depth + 1)
        return validated
    if isinstance(value, list):
        if len(value) > 128:
            _raise("launcher_receipt_invalid")
        return [_validate_receipt_value(item, depth=depth + 1) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, float) and not math.isfinite(value):
            _raise("launcher_receipt_invalid")
        return value
    _raise("launcher_receipt_invalid")


def _sanitized_final(receipt: Mapping[str, object]) -> dict[str, object]:
    """Keep the exact launcher receipt while rejecting private fields."""

    validated = _validate_receipt_value(receipt)
    if not isinstance(validated, dict):  # pragma: no cover - Mapping always yields dict
        _raise("launcher_receipt_invalid")
    if (
        validated.get("formal_admission") is not False
        or validated.get("claim_eligible") is not False
    ):
        _raise("launcher_receipt_invalid")
    return {
        "formal_admission": False,
        "launcher_receipt": validated,
    }


class GuestSlotControl:
    """Synchronous owner control callback for one Linux guest slot."""

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        timeout_seconds: float = MAX_TIMEOUT_SECONDS,
        observe_fork: bool = False,
        require_host_ready: bool = False,
        retain_fork_source: bool = False,
    ) -> None:
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
            _raise("port_invalid")
        self.port = port
        self.timeout_seconds = _finite_timeout(timeout_seconds)
        self._lock = RLock()
        self._listener: socket.socket | None = None
        self._connection: socket.socket | None = None
        self._host: Any | None = None
        self._started = False
        self._callback_returned = False
        self._stop_requested = False
        self._finished = False
        self._closed = False
        self._expected_sequence = 1
        self._operation_count = 0
        if type(observe_fork) is not bool:
            _raise("fork_observation_option_invalid")
        self._observe_fork = observe_fork
        if type(require_host_ready) is not bool:
            _raise("host_ready_option_invalid")
        self._require_host_ready = require_host_ready
        if type(retain_fork_source) is not bool or (retain_fork_source and not observe_fork):
            _raise("fork_source_option_invalid")
        self._retain_fork_source = retain_fork_source
        self._private_fork_sources: list[dict[str, object]] = []
        self._fork_observations: list[dict[str, object]] = []

    def _validate_handles(self, handles: Sequence[RoleHandle]) -> Any:
        if isinstance(handles, (str, bytes)) or not isinstance(handles, Sequence):
            _raise("roles_invalid")
        if len(handles) != 2:
            _raise("roles_invalid")
        roles: dict[str, Any] = {}
        for handle in handles:
            role = getattr(handle, "role", None)
            pid = getattr(handle, "pid", None)
            uid = getattr(handle, "uid", None)
            if role not in {"host", "mcp"} or role in roles:
                _raise("roles_invalid")
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
                _raise("role_pid_invalid")
            if isinstance(uid, bool) or not isinstance(uid, int) or uid not in {1000, 1001}:
                _raise("role_uid_invalid")
            roles[role] = handle
        if (
            set(roles) != {"host", "mcp"}
            or roles["host"].uid != 1000
            or roles["mcp"].uid != 1001
        ):
            _raise("roles_invalid")
        return roles["host"]

    def _open_connection(self, deadline: float) -> socket.socket:
        family = getattr(socket, "AF_VSOCK", None)
        if family is None:
            _raise("vsock_unsupported")
        listener: socket.socket | None = None
        try:
            listener = socket.socket(family, socket.SOCK_STREAM)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _raise("control_timeout")
            listener.settimeout(remaining)
            listener.bind((VMADDR_CID_ANY, self.port))
            listener.listen(1)
            self._listener = listener
            connection, address = listener.accept()
            if (
                not isinstance(address, tuple)
                or len(address) < 1
                or isinstance(address[0], bool)
                or not isinstance(address[0], int)
                or address[0] != VMADDR_CID_HOST
            ):
                with suppress(Exception):
                    connection.close()
                _raise("peer_cid_rejected")
            try:
                connection.settimeout(
                    min(frames.MAX_TIMEOUT_SECONDS, max(0.001, deadline - time.monotonic()))
                )
            except OSError:
                with suppress(Exception):
                    connection.close()
                _raise("vsock_connection_failed")
            self._connection = connection
            return connection
        except GuestSlotControlError:
            raise
        except TimeoutError:
            _raise("control_accept_timeout")
        except OSError:
            _raise("vsock_listen_failed")
        finally:
            if listener is not None:
                with suppress(Exception):
                    listener.close()
                self._listener = None

    def _stop_host(self) -> None:
        with self._lock:
            if self._stop_requested:
                return
            host = self._host
            if host is None:
                _raise("host_handle_missing")
            pid = getattr(host, "pid", None)
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
                _raise("host_pid_invalid")
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                _raise("host_stop_failed")
            self._stop_requested = True

    def _send_reply(self, sequence: int, value: Mapping[str, object], deadline: float) -> None:
        connection = self._connection
        if connection is None:
            _raise("control_connection_missing")
        payload = _json_bytes(value, limit=frames.MAX_CONTROL_PAYLOAD)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _raise("control_timeout")
        try:
            frames.write_frame(
                connection,
                frames.FrameKind.CONTROL_REPLY,
                sequence,
                payload,
                timeout=min(frames.MAX_TIMEOUT_SECONDS, remaining),
            )
        except frames.FrameError:
            _raise("control_write_failed")

    def _await_host_ready(self, deadline: float) -> None:
        """Gate startup only; the marker is never an admission or health proof."""
        from benchmarks.hosts.native_fork_observation import snapshot_plugin_log

        marker = b"opencode server listening on http://127.0.0.1:4096"
        while time.monotonic() < deadline:
            try:
                snapshot = snapshot_plugin_log(self._host.workdir / "tmp/host-startup.log")
            except Exception:
                _raise("host_ready_log_gap")
            if len(snapshot.data) > 4096:
                _raise("host_ready_log_budget")
            if snapshot.data.splitlines().count(marker) == 1:
                if time.monotonic() >= deadline:
                    _raise("host_ready_timeout")
                return
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        _raise("host_ready_timeout")

    def _host_operation(self, request: Mapping[str, object], deadline: float) -> dict[str, object]:
        operation = request["op"]
        method, path, body = _route_for_request(request)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _raise("control_timeout")
        timeout = min(MAX_HTTP_SECONDS, remaining)
        if operation == "health":
            retry_deadline = time.monotonic() + min(MAX_HEALTH_READY_SECONDS, remaining)
            if self._require_host_ready:
                self._await_host_ready(retry_deadline)
            while True:
                if retry_deadline - time.monotonic() < 0.05:
                    _raise("host_health_timeout")
                try:
                    result = _run_host_http(
                        self._host.pid, operation, method, path, body,
                        timeout_seconds=min(
                            MAX_HEALTH_READY_SECONDS,
                            max(0.001, retry_deadline - time.monotonic()),
                        ),
                    )
                    return _response_for_operation(operation, result)
                except GuestSlotControlError as error:
                    if (
                        error.code != "host_connect_failed"
                        or time.monotonic() >= retry_deadline
                    ):
                        raise
                    time.sleep(min(0.05, max(0.0, retry_deadline - time.monotonic())))
        snapshot = None
        if operation == "fork" and self._observe_fork:
            from benchmarks.hosts.native_fork_observation import snapshot_plugin_log

            snapshot = snapshot_plugin_log(self._host.workdir / "tmp/native-events.jsonl")
        source_options = (
            {"retain_fork_source": True} if operation == "fork" and self._retain_fork_source else {}
        )
        result = _run_host_http(
            self._host.pid, operation, method, path, body,
            timeout_seconds=timeout, **source_options,
        )
        if operation == "fork" and self._observe_fork:
            from benchmarks.hosts.native_fork_observation import await_child_event

            projection = result.get("fork_response")
            if not isinstance(projection, dict):
                _raise("fork_response_observation_missing")
            source = None
            if self._retain_fork_source:
                source = result.get("fork_source")
                if not isinstance(source, dict) or set(source) != {
                    "route_observation", "request_body", "response",
                }:
                    _raise("fork_source_missing")
                try:
                    source = {
                        "route_observation": source["route_observation"],
                        "request_body": base64.b64decode(source["request_body"], validate=True),
                        "response": base64.b64decode(source["response"], validate=True),
                    }
                    from benchmarks.hosts.native_fork_observation import capture_fork_response

                    if capture_fork_response(
                        **source["route_observation"], request_body=source["request_body"],
                        response=source["response"],
                    ) != projection:
                        _raise("fork_source_digest_gap")
                except GuestSlotControlError:
                    raise
                except Exception:
                    _raise("fork_source_invalid")
            try:
                callback_options = {}
                if source is not None:
                    callback_options["source_callback"] = lambda raw: source.update(
                        child_plugin_observation=raw
                    )
                event = await_child_event(
                    self._host.workdir / "tmp/native-events.jsonl", snapshot,
                    projection["child_session_sha256"],
                    timeout_seconds=min(30, max(0.001, deadline - time.monotonic())),
                    **callback_options,
                )
            except Exception:
                _raise("fork_child_event_gap")
            self._fork_observations.append({"response": projection, "child_event": event})
            if source is not None:
                self._private_fork_sources.append(source)
        return _response_for_operation(operation, result)

    def _serve_control_connection(self, deadline: float) -> None:
        connection = self._connection
        if connection is None:
            _raise("control_connection_missing")
        while not self._stop_requested:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _raise("control_timeout")
            try:
                frame = frames.read_frame(
                    connection,
                    timeout=min(frames.MAX_TIMEOUT_SECONDS, remaining),
                )
            except frames.FrameTimeoutError:
                _raise("control_timeout")
            except frames.FrameTruncatedError:
                _raise("control_connection_closed")
            except frames.FrameError:
                _raise("control_read_failed")
            if frame.kind != frames.FrameKind.CONTROL_REQUEST:
                _raise("control_frame_kind_invalid")
            if frame.sequence != self._expected_sequence:
                _raise("control_sequence_invalid")
            if self._operation_count >= MAX_OPERATIONS:
                _raise("operation_limit_exceeded")
            try:
                request = _decode_request(frame.payload)
                operation = request["op"]
                self._operation_count += 1
                if operation == "stop":
                    self._stop_host()
                    response = _response_for_operation(operation, {})
                else:
                    response = self._host_operation(request, deadline)
                self._send_reply(frame.sequence, response, deadline)
                if operation == "fork" and self._observe_fork:
                    self._fork_observations[-1]["control_reply_sent_at_ns"] = time.monotonic_ns()
                self._expected_sequence += 1
            except GuestSlotControlError as error:
                # A bounded error is useful to the owner, but every semantic
                # or host failure still closes the control path and stops the
                # real host so an invalid request cannot leave work running.
                with suppress(GuestSlotControlError):
                    self._send_reply(
                        frame.sequence,
                        {"ok": False, "error": error.code, "formal_admission": False},
                        deadline,
                    )
                self._stop_host_quietly()
                raise

    def _stop_host_quietly(self) -> None:
        with suppress(Exception):
            self._stop_host()

    def __call__(self, handles: tuple[RoleHandle, ...]) -> None:
        with self._lock:
            if self._started:
                _raise("control_already_started")
            if self._closed:
                _raise("control_closed")
            self._started = True
        deadline = time.monotonic() + self.timeout_seconds
        try:
            self._host = self._validate_handles(handles)
            self._open_connection(deadline)
            self._serve_control_connection(deadline)
            self._callback_returned = True
        except GuestSlotControlError:
            self._stop_host_quietly()
            self.close()
            raise
        except Exception:
            self._stop_host_quietly()
            self.close()
            _raise("control_failed")

    def adapt_fork_source(
        self, *, process_binding: Mapping[str, Any],
        host_identity: Mapping[str, Any], execution_identity: Mapping[str, Any],
        route: Mapping[str, Any], event_sequence: Mapping[str, Any] | int,
    ) -> dict[str, Any]:
        """Consume root-private bytes using the existing native adapter.

        The external producer supplies independently observed process and run
        bindings. This method does not infer those bindings, mint observation
        authority, or claim to delay the upstream HTTP response. Its release
        boundary is the broker's owner control reply.
        """
        if (
            self._closed or not self._callback_returned or not self._stop_requested
            or len(self._private_fork_sources) != 1 or len(self._fork_observations) != 1
        ):
            _raise("fork_source_not_ready")
        observed = self._fork_observations[0]
        event = observed["child_event"]
        released = observed.get("control_reply_sent_at_ns")
        if (
            type(released) is not int or type(event.get("observed_at_ns")) is not int
            or released < event["observed_at_ns"]
        ):
            _raise("fork_source_barrier_gap")
        source = self._private_fork_sources.pop()
        try:
            from benchmarks.hosts.v013_native_event_adapter import (
                adapt_opencode_public_fork_observation,
            )

            return adapt_opencode_public_fork_observation(
                {
                    "schema_version": "deeplaw.opencode-public-fork-proof/v1",
                    **source,
                    "event_barrier": {
                        "status": "satisfied",
                        "response_release": "after_child_plugin_event",
                        "timed_out": False,
                        "child_plugin_event_count": 1,
                        "event_type": "session.created",
                        "timeout_seconds": 30,
                        "elapsed_ms": event["elapsed_ms"],
                        "parent_source": "actual_ingress_route",
                    },
                    "process_binding": dict(process_binding),
                },
                host_identity=host_identity, execution_identity=execution_identity,
                route=route, event_sequence=event_sequence,
                expected_process_binding=process_binding,
            )
        finally:
            source.clear()

    def finish(
        self, launcher_receipt: Mapping[str, object], *,
        process_observation: Mapping[str, object] | None = None,
        boundary_observation: Mapping[str, object] | None = None,
        route_observation: Mapping[str, object] | None = None,
    ) -> None:
        """Send one sanitized FINAL frame and close the retained connection."""

        if not isinstance(launcher_receipt, Mapping):
            _raise("launcher_receipt_invalid")
        with self._lock:
            if self._finished:
                return
            if self._connection is None or not self._callback_returned:
                _raise("control_not_stopped")
            connection = self._connection
            self._finished = True
        try:
            final = _sanitized_final(launcher_receipt)
            if process_observation is not None:
                if process_observation.get("formal_admission") is not False:
                    _raise("process_observation_invalid")
                final["process_observation"] = _validate_receipt_value(process_observation)
            if boundary_observation is not None:
                if boundary_observation.get("formal_admission") is not False:
                    _raise("boundary_observation_invalid")
                final["boundary_observation"] = _validate_receipt_value(boundary_observation)
            if self._observe_fork:
                observation = {
                    "formal_admission": False, "claim_eligible": False,
                    "release_boundary": "owner_control_reply",
                    "forks": self._fork_observations,
                }
                observation["record_sha256"] = hashlib.sha256(
                    json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                final["fork_observation"] = _validate_receipt_value(observation)
            if route_observation is not None:
                if route_observation.get("formal_admission") is not False:
                    _raise("route_observation_invalid")
                final["route_observation"] = _validate_receipt_value(route_observation)
            payload = _json_bytes(final, limit=frames.MAX_CONTROL_PAYLOAD)
            frames.write_frame(
                connection,
                frames.FrameKind.FINAL,
                self._expected_sequence,
                payload,
                timeout=MAX_HTTP_SECONDS,
            )
        except frames.FrameError:
            _raise("final_write_failed")
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            should_stop = (
                self._host is not None
                and not self._stop_requested
                and not self._finished
            )
        if should_stop:
            self._stop_host_quietly()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            # Drop private original payload references on every terminal path.
            # This is lifecycle cleanup, not a claim of secure memory erasure.
            self._private_fork_sources.clear()
            listener, connection = self._listener, self._connection
            self._listener = None
            self._connection = None
        for item in (connection, listener):
            if item is not None:
                with suppress(Exception):
                    item.close()


__all__ = [
    "DEFAULT_PORT",
    "HOST_LOOPBACK",
    "HOST_PORT",
    "MAX_HTTP_SECONDS",
    "MAX_OPERATIONS",
    "MAX_TIMEOUT_SECONDS",
    "VMADDR_CID_ANY",
    "VMADDR_CID_HOST",
    "GuestSlotControl",
    "GuestSlotControlError",
]
