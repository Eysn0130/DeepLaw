"""Fixed, owner-enabled native model probe transport; no credential authority.

Provider request/response bytes exist only in memory and private transport
frames. A completed exchange is transport evidence, not model-task success,
MCP functionality, or formal qualification. Importing this module is inert.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import select
import signal
import socket
import stat
import struct
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any, Final

from benchmarks.hosts import linux_guest_slot_control as control
from benchmarks.hosts import native_slot_frames as frames
from benchmarks.hosts.linux_role_launcher import RoleHandle

MAX_PROVIDER_REPLY: Final = frames.MAX_PROVIDER_REPLY_PAYLOAD
MAX_PROVIDER_REQUEST: Final = frames.MAX_PROVIDER_REQUEST_PAYLOAD
MAX_CONTENT_TYPE: Final = 128
MAX_PROBE_SECONDS: Final = 180.0
MAX_HTTP_SECONDS: Final = 120.0
MAX_CHILD_REPLY: Final = 16 * 1024
PROXY_PORT: Final = 4100
PROBE_PROMPT: Final = "Reply with exactly DEEPLAW_NATIVE_PROBE_OK."
PROBE_REPLY: Final = "DEEPLAW_NATIVE_PROBE_OK"
PROVIDER_ID: Final = "deepseek"
MODEL_ID: Final = "deepseek-flash"
ALLOWED_HEADERS: Final = frozenset({
    "host", "content-type", "content-length", "authorization", "accept", "user-agent",
    "connection", "accept-encoding",
    "x-session-affinity", "x-session-id",
})
_REPLY_HEADER: Final = struct.Struct("!4sHHI")
_REPLY_MAGIC: Final = b"DPR1"
_SESSION_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_NONCE_RE: Final = re.compile(r"[A-Za-z0-9_-]{1,128}")
_GAPS: Final = frozenset({
    "host_http_rejected", "json_invalid", "native_assistant_missing",
    "native_assistant_binding_invalid", "session_id_invalid", "native_finish_invalid",
    "native_tokens_missing", "native_tokens_invalid", "native_parts_invalid",
    "client_child_failed", "proxy_child_failed",
})
_HOST_FAILURE_PATTERNS: Final = (
    ("host_log_enoent", rb"\bENOENT\b|No such file or directory"),
    ("host_log_permission", rb"\b(?:EACCES|EPERM)\b|Permission denied"),
    ("host_log_readonly", rb"\bEROFS\b|Read-only file system"),
    ("host_log_syscall", rb"\bENOSYS\b|Function not implemented"),
    ("host_log_module", rb"Cannot find module|ModuleNotFound"),
    ("host_log_typeerror", rb"\bTypeError\b"),
    ("host_log_referenceerror", rb"\bReferenceError\b"),
    ("host_log_model", rb"\b(?:ProviderModelNotFoundError|ModelNotFoundError)\b"),
    ("host_log_fetch", rb"fetch failed|Unable to connect|\bECONNREFUSED\b"),
    ("host_log_tool_registry", rb"ToolRegistry\.state"),
    ("host_log_ripgrep", rb"Ripgrep\."),
)


class ProviderBridgeError(RuntimeError):
    """A fixed public code, never transport payload or exception text."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or re.fullmatch(r"[a-z][a-z0-9_]{1,63}", code) is None:
            code = "provider_bridge_failed"
        self.code = code
        super().__init__(code)


def _fail(code: str) -> Any:
    raise ProviderBridgeError(code)


def _close(connection: object) -> None:
    with suppress(Exception):
        connection.close()  # type: ignore[attr-defined]


def _timeout(value: object, maximum: float = MAX_PROBE_SECONDS) -> float:
    if type(value) not in {int, float} or not 0 < value <= maximum:
        _fail("timeout_invalid")
    if not math.isfinite(value):
        _fail("timeout_invalid")
    return float(value)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _fail("probe_deadline_exceeded")
    return min(MAX_PROBE_SECONDS, remaining)


def _read(connection: object, deadline: float) -> frames.Frame:
    try:
        return frames.read_frame(connection, timeout=_remaining(deadline))
    except frames.FrameError:
        _fail("frame_read_failed")


def _write(connection: object, kind: frames.FrameKind, sequence: int,
           payload: bytes, deadline: float) -> None:
    try:
        frames.write_frame(connection, kind, sequence, payload, timeout=_remaining(deadline))
    except frames.FrameError:
        _fail("frame_write_failed")


def encode_provider_reply(status: int, content_type: str, raw_body: bytes) -> bytes:
    if type(status) is not int or status not in {200, 403}:
        _fail("provider_status_invalid")
    if not isinstance(content_type, str) or not 0 < len(content_type) <= MAX_CONTENT_TYPE:
        _fail("provider_content_type_invalid")
    if any(not 32 <= ord(char) <= 126 for char in content_type):
        _fail("provider_content_type_invalid")
    if not isinstance(raw_body, bytes):
        _fail("provider_body_invalid")
    if _REPLY_HEADER.size + len(content_type) + len(raw_body) > MAX_PROVIDER_REPLY:
        _fail("provider_reply_bound")
    header = _REPLY_HEADER.pack(_REPLY_MAGIC, status, len(content_type), len(raw_body))
    return header + content_type.encode("ascii") + raw_body


def decode_provider_reply(payload: bytes) -> tuple[int, str, bytes]:
    if (not isinstance(payload, bytes)
            or not _REPLY_HEADER.size <= len(payload) <= MAX_PROVIDER_REPLY):
        _fail("provider_reply_bound")
    magic, status, type_length, body_length = _REPLY_HEADER.unpack_from(payload)
    if magic != _REPLY_MAGIC or not 0 < type_length <= MAX_CONTENT_TYPE:
        _fail("provider_reply_header_invalid")
    if _REPLY_HEADER.size + type_length + body_length != len(payload):
        _fail("provider_reply_length_invalid")
    start = _REPLY_HEADER.size
    try:
        content_type = payload[start:start + type_length].decode("ascii")
    except UnicodeError:
        _fail("provider_content_type_invalid")
    body = payload[start + type_length:]
    encode_provider_reply(status, content_type, body)
    return status, content_type, body


def owner_exchange_with_provider(
    connection: object, sequence: int, payload: bytes,
    forward: Callable[[bytes, float], tuple[int, str, bytes]], *,
    timeout_seconds: float = MAX_PROBE_SECONDS, max_requests: int = 1,
) -> bytes:
    """Send once and exclusively read this control exchange until its matching reply."""
    timeout = _timeout(timeout_seconds)
    if type(sequence) is not int or not 1 <= sequence <= frames.MAX_SEQUENCE:
        _fail("control_sequence_invalid")
    if not isinstance(payload, bytes) or len(payload) > frames.MAX_CONTROL_PAYLOAD:
        _fail("control_payload_invalid")
    if type(max_requests) is not int or max_requests != 1 or not callable(forward):
        _fail("provider_budget_invalid")
    deadline = time.monotonic() + timeout
    expected = 1
    try:
        _write(connection, frames.CONTROL_REQUEST, sequence, payload, deadline)
        while True:
            frame = _read(connection, deadline)
            if frame.kind == frames.CONTROL_REPLY:
                if frame.sequence != sequence:
                    _fail("control_reply_sequence_invalid")
                return frame.payload
            if frame.kind != frames.PROVIDER_REQUEST:
                _fail("owner_frame_kind_invalid")
            if frame.sequence != expected or expected > max_requests:
                _fail("provider_request_sequence_invalid")
            if not 0 < len(frame.payload) <= MAX_PROVIDER_REQUEST:
                _fail("provider_request_bound")
            expected += 1  # Consume before forwarding; never replay an ambiguous request.
            try:
                reply = forward(frame.payload, _remaining(deadline))
                if not isinstance(reply, tuple) or len(reply) != 3:
                    _fail("provider_reply_invalid")
                encoded = encode_provider_reply(*reply)
            except ProviderBridgeError:
                raise
            except KeyboardInterrupt:
                raise KeyboardInterrupt from None
            except SystemExit:
                raise SystemExit(1) from None
            except BaseException:
                _fail("owner_forward_failed")
            _write(connection, frames.PROVIDER_REPLY, frame.sequence, encoded, deadline)
    except BaseException:
        _close(connection)
        raise


def _json_bytes(value: Mapping[str, object]) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(raw) > MAX_CHILD_REPLY:
        _fail("child_observation_bound")
    return raw


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            _fail("json_invalid")
        value[key] = item
    return value


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _value: _fail("json_invalid"))
    except (ValueError, UnicodeError, RecursionError):
        _fail("json_invalid")
    if not isinstance(value, dict):
        _fail("json_invalid")
    return value


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _public_host_failure_codes(workdir: Path) -> list[str]:
    """Classify one fixed bounded Host log without returning its text or paths."""
    descriptors: list[int] = []
    try:
        parent = os.open(workdir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(parent)
        for name in ("data", "opencode", "log"):
            parent = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent,
            )
            descriptors.append(parent)
        descriptor = os.open(
            "opencode.log", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent,
        )
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 512 * 1024:
            return ["host_log_unavailable"]
        raw = os.read(descriptor, info.st_size + 1)
        after = os.fstat(descriptor)
        if len(raw) != info.st_size or (after.st_size, after.st_mtime_ns) != (
            info.st_size, info.st_mtime_ns,
        ):
            return ["host_log_unavailable"]
        errors = b"\n".join(line for line in raw.splitlines() if b"level=ERROR" in line)
        codes = [code for code, pattern in _HOST_FAILURE_PATTERNS
                 if re.search(pattern, errors) is not None][:6]
        return codes or ["host_log_unclassified_error" if errors else "host_log_no_error_record"]
    except (OSError, ValueError):
        return ["host_log_unavailable"]
    finally:
        for descriptor in reversed(descriptors):
            with suppress(OSError):
                os.close(descriptor)


def validate_host_failure_codes(value: object) -> list[str]:
    allowed = {code for code, _pattern in _HOST_FAILURE_PATTERNS} | {
        "host_log_unavailable", "host_log_unclassified_error", "host_log_no_error_record",
    }
    if not isinstance(value, list) or len(value) > 6 or any(
        not isinstance(code, str) or code not in allowed for code in value
    ) or len(set(value)) != len(value):
        _fail("host_failure_codes_invalid")
    return list(value)


def _session(value: object) -> str:
    if not isinstance(value, str) or _SESSION_RE.fullmatch(value) is None:
        _fail("session_id_invalid")
    return value


def fixed_probe_body(session_id: str) -> bytes:
    _session(session_id)
    return _json_bytes({
        "model": {"providerID": PROVIDER_ID, "modelID": MODEL_ID},
        "parts": [{"type": "text", "text": PROBE_PROMPT}],
        "tools": {},
    })


def validate_provider_ingress(method: str, path: str,
                              headers: Sequence[tuple[str, str]], dummy_nonce: str, *,
                              expected_session_id: str | None = None) -> int:
    """Return a bounded content length; no header value appears in failures."""
    if method != "POST" or path != "/chat/completions":
        _fail("proxy_route_forbidden")
    values: dict[str, str] = {}
    total = 0
    for name, value in headers:
        if not isinstance(name, str) or not isinstance(value, str):
            _fail("proxy_headers_invalid")
        key = name.lower()
        total += len(name) + len(value)
        if key not in ALLOWED_HEADERS or key in values or total > 8192:
            _fail("proxy_headers_invalid")
        if any(ord(char) < 32 or ord(char) > 126 for char in value):
            _fail("proxy_headers_invalid")
        values[key] = value
    if values.get("host") != f"127.0.0.1:{PROXY_PORT}":
        _fail("proxy_headers_invalid")
    if values.get("authorization") != "Bearer " + dummy_nonce:
        _fail("proxy_association_invalid")
    session_headers = {"x-session-affinity", "x-session-id"} & values.keys()
    if session_headers and (
        expected_session_id is None
        or session_headers != {"x-session-affinity", "x-session-id"}
        or any(values[key] != _session(expected_session_id) for key in session_headers)
    ):
        _fail("proxy_association_invalid")
    if re.fullmatch(r"application/json(?:;\s*charset=UTF-8)?",
                    values.get("content-type", ""), re.IGNORECASE) is None:
        _fail("proxy_content_type_invalid")
    length = values.get("content-length", "")
    if re.fullmatch(r"[0-9]{1,6}", length) is None or not 0 < int(length) <= MAX_PROVIDER_REQUEST:
        _fail("proxy_body_bound")
    return int(length)


def sanitize_native_response(status: int, raw: bytes, session_id: str) -> dict[str, object]:
    """Project native assistant metadata and hashes; never raw Host response text."""
    _session(session_id)
    if type(status) is not int or not 100 <= status <= 599:
        _fail("host_status_invalid")
    if not isinstance(raw, bytes) or len(raw) > MAX_PROVIDER_REPLY:
        _fail("host_response_bound")
    result: dict[str, object] = {
        "http_status": status, "http_response_bytes": len(raw),
        "http_response_sha256": _hash(raw), "assistant": None,
        "model_task_executed": False, "reply_matches": False,
        "output_sha256": None, "output_bytes": None, "gap": None,
    }
    if status != 200:
        result["gap"] = "host_http_rejected"
        return result
    try:
        value = _json_object(raw)
        info = value.get("info")
        if not isinstance(info, dict) or info.get("role") != "assistant":
            _fail("native_assistant_missing")
        if (info.get("providerID") != PROVIDER_ID or info.get("modelID") != MODEL_ID
                or info.get("sessionID") != session_id):
            _fail("native_assistant_binding_invalid")
        message_id = _session(info.get("id"))
        finish = info.get("finish")
        if finish is not None and (not isinstance(finish, str) or not 0 < len(finish) <= 64):
            _fail("native_finish_invalid")
        tokens = info.get("tokens")
        if not isinstance(tokens, dict):
            _fail("native_tokens_missing")
        cache = tokens.get("cache", {})
        if not isinstance(cache, dict):
            _fail("native_tokens_invalid")
        normalized = {
            "input": tokens.get("input"), "output": tokens.get("output"),
            "reasoning": tokens.get("reasoning"), "cache_read": cache.get("read"),
            "cache_write": cache.get("write"),
        }
        for token in normalized.values():
            if token is not None and (type(token) is not int or not 0 <= token <= 10**9):
                _fail("native_tokens_invalid")
        completion = info.get("time", {}).get("completed") if isinstance(
            info.get("time"), dict,
        ) else None
        if (type(completion) not in {int, float} or not 0 <= completion <= 10**16
                or not math.isfinite(completion)):
            completion = None
        result["assistant"] = {
            "role": "assistant", "providerID": PROVIDER_ID, "modelID": MODEL_ID,
            "sessionID_sha256": _hash(session_id.encode()),
            "messageID_sha256": _hash(message_id.encode()), "tokens": normalized,
            "finish": finish if finish in {"stop", "length", "tool-calls", "error"} else None,
            "finish_sha256": _hash(finish.encode()) if finish is not None else None,
            "time_completed": completion,
        }
        result["model_task_executed"] = completion is not None and (
            (normalized["output"] or 0) > 0 or (normalized["reasoning"] or 0) > 0
        )
        parts = value.get("parts")
        if not isinstance(parts, list):
            _fail("native_parts_invalid")
        texts = [part.get("text", "") for part in parts
                 if isinstance(part, dict) and part.get("type") == "text"]
        if any(not isinstance(text, str) for text in texts):
            _fail("native_parts_invalid")
        output = "".join(texts).encode()
        result["output_sha256"] = _hash(output)
        result["output_bytes"] = len(output)
        result["reply_matches"] = output.decode().strip() == PROBE_REPLY
    except ProviderBridgeError as error:
        result["gap"] = error.code
    return result


def _child_observation(payload: bytes, *, proxy: bool) -> dict[str, Any]:
    if len(payload) > MAX_CHILD_REPLY:
        _fail("child_observation_bound")
    value = _json_object(payload)
    gap = value.get("gap")
    if gap is not None and (not isinstance(gap, str) or gap not in _GAPS):
        _fail("child_observation_invalid")
    if proxy:
        if set(value) not in ({"admitted", "rejected"}, {"admitted", "rejected", "gap"}):
            _fail("child_observation_invalid")
        if type(value["admitted"]) is not int or not 0 <= value["admitted"] <= 1:
            _fail("child_observation_invalid")
        if type(value["rejected"]) is not int or not 0 <= value["rejected"] <= 10**6:
            _fail("child_observation_invalid")
        return value
    if set(value) == {"gap"} and gap == "client_child_failed":
        return value
    if set(value) != {
        "http_status", "http_response_bytes", "http_response_sha256", "assistant",
        "model_task_executed", "reply_matches", "output_sha256", "output_bytes", "gap",
    }:
        _fail("child_observation_invalid")
    for key in ("http_response_sha256", "output_sha256"):
        if value[key] is not None and (
            not isinstance(value[key], str) or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
        ):
            _fail("child_observation_invalid")
    if (type(value["http_status"]) is not int or not 100 <= value["http_status"] <= 599
            or type(value["http_response_bytes"]) is not int
            or not 0 <= value["http_response_bytes"] <= MAX_PROVIDER_REPLY):
        _fail("child_observation_invalid")
    if (type(value["model_task_executed"]) is not bool or type(value["reply_matches"]) is not bool
            or (value["output_bytes"] is not None and (
                type(value["output_bytes"]) is not int
                or not 0 <= value["output_bytes"] <= MAX_PROVIDER_REPLY
            ))):
        _fail("child_observation_invalid")
    assistant = value["assistant"]
    if assistant is not None:
        if not isinstance(assistant, dict) or set(assistant) != {
            "role", "providerID", "modelID", "sessionID_sha256", "messageID_sha256",
            "tokens", "finish", "finish_sha256", "time_completed",
        }:
            _fail("child_observation_invalid")
        if (assistant["role"] != "assistant" or assistant["providerID"] != PROVIDER_ID
                or assistant["modelID"] != MODEL_ID
                or assistant["finish"] not in {None, "stop", "length", "tool-calls", "error"}):
            _fail("child_observation_invalid")
        for key in ("sessionID_sha256", "messageID_sha256", "finish_sha256"):
            if assistant[key] is not None and (not isinstance(assistant[key], str)
                    or re.fullmatch(r"[0-9a-f]{64}", assistant[key]) is None):
                _fail("child_observation_invalid")
        tokens = assistant["tokens"]
        if not isinstance(tokens, dict) or set(tokens) != {
            "input", "output", "reasoning", "cache_read", "cache_write",
        }:
            _fail("child_observation_invalid")
        if any(token is not None and (type(token) is not int or not 0 <= token <= 10**9)
               for token in tokens.values()):
            _fail("child_observation_invalid")
        completion = assistant["time_completed"]
        if completion is not None and (type(completion) not in {int, float}
                or not 0 <= completion <= 10**16 or not math.isfinite(completion)):
            _fail("child_observation_invalid")
        executed = completion is not None and (
            (tokens["output"] or 0) > 0 or (tokens["reasoning"] or 0) > 0
        )
        if value["model_task_executed"] is not executed:
            _fail("child_observation_invalid")
    elif value["model_task_executed"]:
        _fail("child_observation_invalid")
    return value


def _child_setup(host_pid: int, connection: object, unwanted: Sequence[socket.socket]) -> None:
    # Fail before namespace entry if the inherited control descriptor cannot close.
    for endpoint in (connection, *unwanted):
        try:
            endpoint.close()
            fileno = getattr(endpoint, "fileno", None)
            if callable(fileno) and type(fileno()) is int and fileno() >= 0:
                _fail("child_descriptor_close_failed")
        except Exception:
            _fail("child_descriptor_close_failed")
    control._set_child_parent_death_signal()
    control._enter_host_netns(host_pid)


class _LoopbackHTTPServer(HTTPServer):
    """Bind the fixed loopback proxy without a reverse DNS lookup."""

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name = control.HOST_LOOPBACK
        self.server_port = self.server_address[1]


def _proxy_child(host_pid: int, connection: object, endpoint: socket.socket,
                 unwanted: Sequence[socket.socket], nonce: str, deadline: float,
                 session_id: str) -> None:
    server: HTTPServer | None = None
    counters = {"admitted": 0, "rejected": 0}
    success = False
    try:
        _child_setup(host_pid, connection, unwanted)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def handle(self) -> None:
                self.connection.settimeout(min(MAX_HTTP_SECONDS, _remaining(deadline)))
                super().handle()

            def handle_expect_100(self) -> bool:
                self.send_error(403)
                return False

            def send_error(self, *_args: object, **_kwargs: object) -> None:
                counters["rejected"] += 1
                body = b'{"error":"probe_rejected"}'
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                try:
                    length = validate_provider_ingress(
                        self.command, self.path, list(self.headers.items()), nonce,
                        expected_session_id=session_id,
                    )
                    if counters["admitted"] >= 1:
                        _fail("proxy_request_budget")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        _fail("proxy_body_bound")
                    counters["admitted"] += 1
                    _write(endpoint, frames.PROVIDER_REQUEST, 1, raw, deadline)
                    reply = _read(endpoint, deadline)
                    if reply.kind != frames.PROVIDER_REPLY or reply.sequence != 1:
                        _fail("proxy_reply_invalid")
                    status, content_type, body = decode_provider_reply(reply.payload)
                except Exception:
                    counters["rejected"] += 1
                    status, content_type, body = (
                        403, "application/json", b'{"error":"probe_rejected"}',
                    )
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                self.send_error(403)

        server = _LoopbackHTTPServer((control.HOST_LOOPBACK, PROXY_PORT), Handler)
        _write(endpoint, frames.CONTROL_REPLY, 1, _json_bytes({"ready": True}), deadline)
        while counters["admitted"] == 0:
            server.timeout = min(0.1, _remaining(deadline))
            server.handle_request()
        _write(endpoint, frames.CONTROL_REPLY, 2, _json_bytes(counters), deadline)
        success = True
    except BaseException:
        with suppress(Exception):
            _write(endpoint, frames.CONTROL_REPLY, 2,
                   _json_bytes({**counters, "gap": "proxy_child_failed"}), deadline)
    finally:
        if server is not None:
            with suppress(Exception):
                server.server_close()
        _close(endpoint)
    os._exit(0 if success else 1)


def _fixed_host_http(session_id: str, deadline: float) -> dict[str, object]:
    connection: http.client.HTTPConnection | None = None
    try:
        connection = http.client.HTTPConnection(
            control.HOST_LOOPBACK, control.HOST_PORT,
            timeout=min(MAX_HTTP_SECONDS, _remaining(deadline)),
        )
        body = fixed_probe_body(session_id)
        connection.request(
            "POST", f"/session/{session_id}/message", body=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        response = connection.getresponse()
        raw = response.read(MAX_PROVIDER_REPLY + 1)
        return sanitize_native_response(response.status, raw, session_id)
    except ProviderBridgeError:
        raise
    except Exception:
        _fail("host_http_failed")
    finally:
        if connection is not None:
            _close(connection)


def _client_child(host_pid: int, connection: object, endpoint: socket.socket,
                  unwanted: Sequence[socket.socket], session_id: str, deadline: float) -> None:
    success = False
    try:
        _child_setup(host_pid, connection, unwanted)
        result = _fixed_host_http(session_id, min(deadline, time.monotonic() + MAX_HTTP_SECONDS))
        success = True
    except BaseException:
        result = {"gap": "client_child_failed"}
    with suppress(Exception):
        _write(endpoint, frames.CONTROL_REPLY, 1, _json_bytes(result), deadline)
    _close(endpoint)
    os._exit(0 if success else 1)


def _reap_child(pid: int) -> dict[str, object]:
    for sig, grace in ((None, 0.1), (signal.SIGTERM, 0.25), (signal.SIGKILL, 0.5)):
        if sig is not None:
            with suppress(OSError):
                os.kill(pid, sig)
        deadline = time.monotonic() + grace
        while True:
            try:
                waited, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return {"reaped": False, "exit_code": None}
            except OSError:
                return {"reaped": False, "exit_code": None}
            if waited == pid:
                return {"reaped": True, "exit_code": os.waitstatus_to_exitcode(status)}
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
    return {"reaped": False, "exit_code": None}


def _pump_guest(connection: object, proxy: socket.socket, client: socket.socket,
                deadline: float, observation: dict[str, object]) -> dict[str, object]:
    admitted = forwarded = 0
    client_result: dict[str, Any] | None = None
    proxy_result: dict[str, Any] | None = None
    while client_result is None or proxy_result is None:
        readable, _, _ = select.select(
            [endpoint for endpoint, done in ((proxy, proxy_result), (client, client_result))
             if done is None], [], [], _remaining(deadline),
        )
        if not readable:
            _fail("probe_deadline_exceeded")
        for endpoint in readable:
            frame = _read(endpoint, deadline)
            if endpoint is proxy and frame.kind == frames.PROVIDER_REQUEST:
                if frame.sequence != 1 or admitted != 0 or not frame.payload:
                    _fail("proxy_request_sequence_invalid")
                admitted += 1
                observation["provider_requests_admitted"] = admitted
                _write(connection, frames.PROVIDER_REQUEST, 1, frame.payload, deadline)
                forwarded += 1
                observation["provider_requests_forwarded"] = forwarded
                reply = _read(connection, deadline)
                if reply.kind != frames.PROVIDER_REPLY or reply.sequence != 1:
                    _fail("provider_reply_sequence_invalid")
                decode_provider_reply(reply.payload)
                _write(proxy, frames.PROVIDER_REPLY, 1, reply.payload, deadline)
            elif endpoint is proxy and frame.kind == frames.CONTROL_REPLY and frame.sequence == 2:
                proxy_result = _child_observation(frame.payload, proxy=True)
            elif endpoint is client and frame.kind == frames.CONTROL_REPLY and frame.sequence == 1:
                client_result = _child_observation(frame.payload, proxy=False)
            else:
                _fail("child_frame_invalid")
        if client_result is not None and admitted == 0:
            break
    return {"provider_requests_admitted": admitted, "provider_requests_forwarded": forwarded,
            "client": client_result, "proxy": proxy_result}


def run_fixed_model_probe(host: RoleHandle, connection: object, *, session_id: str,
                          dummy_nonce: str, deadline: float) -> dict[str, object]:
    """Temporarily own the VSOCK reader during the guest control callback only."""
    _session(session_id)
    if not isinstance(dummy_nonce, str) or _NONCE_RE.fullmatch(dummy_nonce) is None:
        _fail("nonce_invalid")
    if type(deadline) not in {int, float}:
        _fail("timeout_invalid")
    try:
        deadline = float(deadline)
    except OverflowError:
        _fail("timeout_invalid")
    if not math.isfinite(deadline):
        _fail("timeout_invalid")
    _timeout(deadline - time.monotonic())
    host_pid = getattr(host, "pid", None)
    if getattr(host, "role", None) != "host" or type(host_pid) is not int or host_pid <= 1:
        _fail("host_handle_invalid")
    if not callable(getattr(os, "fork", None)):
        _fail("fork_unavailable")
    deadline = min(deadline, time.monotonic() + MAX_HTTP_SECONDS)
    endpoints: list[socket.socket] = []
    children: dict[str, int] = {}
    result: dict[str, object] = {
        "schema_version": "deeplaw.fixed-native-model-probe/v1",
        "formal_admission": False, "mcp_functional_claim": False,
        "model_invocation_count": None, "model_task_executed": False,
        "provider_requests_admitted": 0, "provider_requests_forwarded": 0,
        "client": None, "proxy": None, "children": {}, "cleanup_confirmed": False,
        "gaps": [],
    }
    try:
        proxy_parent, proxy_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        endpoints.extend((proxy_parent, proxy_child))
        client_parent, client_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        endpoints.extend((client_parent, client_child))
        pid = os.fork()
        if pid == 0:
            _proxy_child(host.pid, connection, proxy_child,
                         (proxy_parent, client_parent, client_child), dummy_nonce, deadline,
                         session_id)
            os._exit(1)
        children["proxy"] = pid
        _close(proxy_child)
        ready = _read(proxy_parent, deadline)
        if (ready.kind != frames.CONTROL_REPLY or ready.sequence != 1
                or _json_object(ready.payload) != {"ready": True}):
            _fail("proxy_start_failed")
        pid = os.fork()
        if pid == 0:
            _client_child(host.pid, connection, client_child,
                          (proxy_parent, client_parent), session_id, deadline)
            os._exit(1)
        children["client"] = pid
        _close(client_child)
        result.update(_pump_guest(connection, proxy_parent, client_parent, deadline, result))
        result["model_task_executed"] = bool(
            result["client"] and result["client"].get("model_task_executed", False),
        )
        result["gaps"] = [item["gap"] for item in (result["client"], result["proxy"])
                          if isinstance(item, dict) and item.get("gap") is not None]
        if isinstance(result["client"], dict) and (
            type(result["client"].get("http_status")) is int
            and result["client"]["http_status"] != 200
        ):
            result["gaps"] = [
                *result["gaps"], *_public_host_failure_codes(host.workdir),
            ]
        if result["provider_requests_admitted"] == 0:
            result["gaps"] = [*result["gaps"], "provider_request_not_observed"]
    except ProviderBridgeError as error:
        result["gaps"] = [error.code]
        _close(connection)
    except Exception:
        result["gaps"] = ["native_probe_failed"]
        _close(connection)
    finally:
        for endpoint in endpoints:
            _close(endpoint)
        exits = {name: _reap_child(pid) for name, pid in children.items()}
        result["children"] = exits
        result["cleanup_confirmed"] = all(item["reaped"] for item in exits.values())
        if not result["cleanup_confirmed"]:
            result["gaps"] = [*result["gaps"], "child_cleanup_gap"]  # type: ignore[misc]
    return result
