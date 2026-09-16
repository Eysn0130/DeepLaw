from __future__ import annotations

import base64
import hashlib
import json
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from benchmarks.hosts import linux_guest_slot_control as control
from benchmarks.hosts import linux_role_launcher as launcher
from benchmarks.hosts import native_slot_frames as frames


def _handles(*, host_pid: int = 321, mcp_pid: int = 322) -> tuple[Any, Any]:
    return (
        SimpleNamespace(role="host", pid=host_pid, uid=1000),
        SimpleNamespace(role="mcp", pid=mcp_pid, uid=1001),
    )


def _payload(value: dict[str, object]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _send_request(connection: socket.socket, sequence: int, value: dict[str, object]) -> None:
    frames.write_frame(
        connection,
        frames.FrameKind.CONTROL_REQUEST,
        sequence,
        _payload(value),
        timeout=2,
    )


def _read_reply(connection: socket.socket) -> tuple[int, dict[str, object]]:
    reply = frames.read_frame(connection, timeout=2)
    return reply.sequence, json.loads(reply.payload)


def _start_control_connection(
    instance: control.GuestSlotControl,
    server: socket.socket,
) -> tuple[threading.Thread, list[BaseException]]:
    instance._connection = server
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            instance._serve_control_connection(time.monotonic() + 5)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    return thread, errors


def test_protocol_routes_and_request_shape_are_fixed() -> None:
    assert control._route_for_request({"op": "health"}) == (
        "GET", "/global/health", None,
    )
    assert control._route_for_request({"op": "new_session"}) == (
        "POST", "/session", b"{}",
    )
    assert control._route_for_request({"op": "fork", "session_id": "root-1"}) == (
        "POST", "/session/root-1/fork", b"{}",
    )
    assert control._route_for_request({"op": "mcp_status"}) == (
        "GET", "/mcp", None,
    )
    assert control._decode_request(b'{"op":"fork","session_id":"root-1"}') == {
        "op": "fork", "session_id": "root-1",
    }
    with pytest.raises(control.GuestSlotControlError, match="request_fields_invalid"):
        control._decode_request(b'{"op":"health","session_id":"root-1"}')
    with pytest.raises(control.GuestSlotControlError, match="request_fields_invalid"):
        control._decode_request(b'{"op":"fork","sessionID":"root-1"}')
    with pytest.raises(control.GuestSlotControlError, match="session_id_invalid"):
        control._decode_request(b'{"op":"fork","session_id":"../escape"}')
    with pytest.raises(control.GuestSlotControlError, match="request_json_invalid"):
        control._decode_request(b'{"op":"health","op":"stop"}')


@pytest.mark.parametrize("timeout", [0, -1, 60.01, float("inf")])
def test_constructor_rejects_unbounded_timeout(timeout: float) -> None:
    with pytest.raises(control.GuestSlotControlError, match="timeout_invalid"):
        control.GuestSlotControl(timeout_seconds=timeout)


def test_native_vsock_unsupported_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = control.GuestSlotControl(timeout_seconds=1)
    killed: list[tuple[int, int]] = []
    monkeypatch.delattr(control.socket, "AF_VSOCK", raising=False)
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(control.GuestSlotControlError, match="vsock_unsupported"):
        instance(_handles())
    assert killed == [(321, control.signal.SIGTERM)]
    assert instance._connection is None


def test_peer_cid_must_be_host_and_listener_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    owner, accepted = socket.socketpair()

    class FakeListener:
        def __init__(self) -> None:
            self.closed = False

        def settimeout(self, _value: float) -> None:
            pass

        def bind(self, _address: tuple[int, int]) -> None:
            pass

        def listen(self, _backlog: int) -> None:
            pass

        def accept(self) -> tuple[socket.socket, tuple[int, int]]:
            return accepted, (99, 1)

        def close(self) -> None:
            self.closed = True

    listener = FakeListener()
    monkeypatch.setattr(control.socket, "AF_VSOCK", 40, raising=False)
    monkeypatch.setattr(control.socket, "socket", lambda *_args: listener)
    monkeypatch.setattr(control.os, "kill", lambda _pid, _sig: None)
    instance = control.GuestSlotControl(timeout_seconds=1)
    try:
        with pytest.raises(control.GuestSlotControlError, match="peer_cid_rejected"):
            instance(_handles())
        assert listener.closed
        assert accepted.fileno() == -1
    finally:
        owner.close()
        accepted.close()


def test_control_reply_shape_health_then_stop_and_real_host_pid_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, owner = socket.socketpair()
    instance = control.GuestSlotControl(timeout_seconds=5)
    instance._host = _handles()[0]
    monkeypatch.setattr(
        control,
        "_run_host_http",
        lambda *_args, **_kwargs: {"healthy": True},
    )
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    thread, errors = _start_control_connection(instance, server)
    try:
        _send_request(owner, 1, {"op": "health"})
        sequence, response = _read_reply(owner)
        assert sequence == 1
        assert response == {
            "ok": True,
            "result": {"healthy": True},
            "formal_admission": False,
        }
        _send_request(owner, 2, {"op": "stop"})
        sequence, response = _read_reply(owner)
        assert sequence == 2
        assert response == {
            "ok": True,
            "result": {"stopping": True},
            "formal_admission": False,
        }
        thread.join(2)
        assert not thread.is_alive()
        assert errors == []
        assert killed == [(321, control.signal.SIGTERM)]
        assert instance._stop_requested
    finally:
        owner.close()
        instance.close()


def test_fork_control_reply_waits_for_child_observation(tmp_path, monkeypatch):
    server, owner = socket.socketpair()
    entered = threading.Event()
    release = threading.Event()
    order = []

    def snapshot(path):
        assert path == tmp_path / "tmp/native-events.jsonl"
        order.append("snapshot")
        return "frozen-prefix"

    def observe(path, boundary, child, **kwargs):
        assert boundary == "frozen-prefix" and child == "a" * 64
        order.append("await")
        entered.set()
        assert release.wait(2)
        return {"observed_at_ns": time.monotonic_ns(), "formal_admission": False}

    monkeypatch.setitem(sys.modules, "benchmarks.hosts.native_fork_observation", SimpleNamespace(
        snapshot_plugin_log=snapshot, await_child_event=observe,
    ))

    def request(*args, **kwargs):
        order.append("http")
        return {"session_id": "child", "fork_response": {"child_session_sha256": "a" * 64}}

    monkeypatch.setattr(control, "_run_host_http", request)
    monkeypatch.setattr(control.os, "kill", lambda *args: None)
    instance = control.GuestSlotControl(timeout_seconds=5, observe_fork=True)
    instance._host = SimpleNamespace(pid=321, workdir=tmp_path)
    thread, errors = _start_control_connection(instance, server)
    try:
        _send_request(owner, 1, {"op": "fork", "session_id": "parent"})
        assert entered.wait(1)
        owner.settimeout(0.05)
        with pytest.raises(TimeoutError):
            owner.recv(1)
        release.set()
        owner.settimeout(2)
        assert _read_reply(owner)[1]["result"] == {"session_id": "child"}
        _send_request(owner, 2, {"op": "stop"})
        _read_reply(owner)
        thread.join(2)
        assert not thread.is_alive() and not errors
        assert order == ["snapshot", "http", "await"]
        observed = instance._fork_observations[0]
        assert observed["child_event"]["observed_at_ns"] <= observed["control_reply_sent_at_ns"]
    finally:
        release.set()
        owner.close()
        instance.close()
        thread.join(2)


@pytest.mark.parametrize("tamper", [False, True])
def test_original_fork_sources_stay_private_and_are_hash_bound(tmp_path, monkeypatch, tamper):
    from benchmarks.hosts import native_fork_observation as observation

    route = {"method": "POST", "path": "/session/parent/fork", "status_code": 200}
    raw = b'{"id":"child","parentID":"parent","private":"private-sentinel"}'
    projection = observation.capture_fork_response(
        **route, request_body=b"{}", response=raw,
    )
    plugin = _payload({
        "schema_version": "deeplaw.opencode-native-event-observation/v1",
        "event_type": "session.created",
        "session_sha256": hashlib.sha256(b"child").hexdigest(),
        "parent_session_sha256": None, "parent_gap": "parent_absent",
        "status": "observed", "gap": None,
    })

    def request(*args, **kwargs):
        assert kwargs["retain_fork_source"] is True
        directory = tmp_path / "tmp"
        directory.mkdir(exist_ok=True)
        (directory / "native-events.jsonl").write_bytes(plugin + b"\n")
        return {
            "session_id": "child", "fork_response": projection,
            "fork_source": {
                "route_observation": route,
                "request_body": base64.b64encode(b"{}").decode(),
                "response": base64.b64encode(raw + (b" " if tamper else b"")).decode(),
            },
        }

    monkeypatch.setattr(control, "_run_host_http", request)
    instance = control.GuestSlotControl(observe_fork=True, retain_fork_source=True)
    instance._host = SimpleNamespace(pid=321, workdir=tmp_path)
    instance._stop_requested = True
    try:
        if tamper:
            with pytest.raises(control.GuestSlotControlError, match="fork_source_digest_gap"):
                instance._host_operation({"op": "fork", "session_id": "parent"}, time.monotonic()+2)
            assert not instance._private_fork_sources
        else:
            public = instance._host_operation(
                {"op": "fork", "session_id": "parent"}, time.monotonic()+2,
            )
            assert public["result"] == {"session_id": "child"}
            private = instance._private_fork_sources[0]
            assert private["response"] == raw
            assert private["child_plugin_observation"] == plugin
            serialized = json.dumps([public, instance._fork_observations])
            assert "private-sentinel" not in serialized
            assert "fork_source" not in serialized
    finally:
        instance.close()
    assert not instance._private_fork_sources


def test_callback_returns_after_stop_and_finish_reuses_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, accepted = socket.socketpair()

    class FakeListener:
        def settimeout(self, _value: float) -> None:
            pass

        def bind(self, _address: tuple[int, int]) -> None:
            pass

        def listen(self, _backlog: int) -> None:
            pass

        def accept(self) -> tuple[socket.socket, tuple[int, int]]:
            return accepted, (control.VMADDR_CID_HOST, 99)

        def close(self) -> None:
            pass

    listener = FakeListener()
    monkeypatch.setattr(control.socket, "AF_VSOCK", 40, raising=False)
    monkeypatch.setattr(control.socket, "socket", lambda *_args: listener)
    monkeypatch.setattr(
        control,
        "_run_host_http",
        lambda *_args, **_kwargs: {"healthy": True},
    )
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    instance = control.GuestSlotControl(timeout_seconds=5)
    errors: list[BaseException] = []

    def callback() -> None:
        try:
            instance(_handles())
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=callback)
    thread.start()
    try:
        _send_request(owner, 1, {"op": "health"})
        _read_reply(owner)
        _send_request(owner, 2, {"op": "stop"})
        _read_reply(owner)
        thread.join(2)
        assert not thread.is_alive()
        assert errors == []
        assert instance._callback_returned
        assert killed == [(321, control.signal.SIGTERM)]
        instance.finish({
            "status": "observed",
            "formal_admission": False,
            "claim_eligible": False,
        })
        final = frames.read_frame(owner, timeout=2)
        assert final.kind == frames.FrameKind.FINAL
        assert json.loads(final.payload) == {
            "formal_admission": False,
            "launcher_receipt": {
                "status": "observed",
                "formal_admission": False,
                "claim_eligible": False,
            },
        }
    finally:
        owner.close()
        accepted.close()
        instance.close()


def test_health_retries_only_transient_startup_failure_within_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = control.GuestSlotControl(timeout_seconds=5)
    instance._host = _handles()[0]
    calls: list[tuple[int, str, str, str, bytes | None]] = []

    def request(
        pid: int,
        operation: str,
        method: str,
        path: str,
        body: bytes | None,
        **_kwargs: object,
    ) -> dict[str, object]:
        calls.append((pid, operation, method, path, body))
        if len(calls) == 1:
            raise control.GuestSlotControlError("host_connect_failed")
        assert 0 < _kwargs["timeout_seconds"] <= 5
        return {"healthy": True}

    monkeypatch.setattr(control, "_run_host_http", request)
    result = instance._host_operation({"op": "health"}, time.monotonic() + 5)
    assert result == {
        "ok": True,
        "result": {"healthy": True},
        "formal_admission": False,
    }
    assert calls == [
        (321, "health", "GET", "/global/health", None),
        (321, "health", "GET", "/global/health", None),
    ]


@pytest.mark.parametrize("failure", ["host_http_failed", "host_http_timeout"])
def test_health_never_replays_a_possibly_sent_request(monkeypatch, failure):
    instance = control.GuestSlotControl(timeout_seconds=5)
    instance._host = _handles()[0]
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        raise control.GuestSlotControlError(failure)

    monkeypatch.setattr(control, "_run_host_http", request)
    with pytest.raises(control.GuestSlotControlError, match=failure):
        instance._host_operation({"op": "health"}, time.monotonic() + 5)
    assert len(calls) == 1


def test_health_waits_for_ready_marker_before_one_http_request(tmp_path, monkeypatch):
    instance = control.GuestSlotControl(require_host_ready=True)
    instance._host = _handles()[0]
    instance._host.workdir = tmp_path
    (tmp_path / "tmp").mkdir()
    marker = tmp_path / "tmp/host-startup.log"
    calls = []

    def write_ready():
        time.sleep(0.02)
        marker.write_bytes(b"opencode server listening on http://127.0.0.1:4096\n")

    def request(*args, **kwargs):
        assert marker.is_file()
        calls.append(args)
        return {"healthy": True}

    monkeypatch.setattr(control, "_run_host_http", request)
    thread = threading.Thread(target=write_ready)
    thread.start()
    assert instance._host_operation({"op": "health"}, time.monotonic() + 2)["ok"] is True
    thread.join(timeout=1)
    assert len(calls) == 1


def test_missing_ready_marker_never_sends_health(tmp_path, monkeypatch):
    instance = control.GuestSlotControl(require_host_ready=True)
    instance._host = _handles()[0]
    instance._host.workdir = tmp_path
    calls = []
    monkeypatch.setattr(control, "_run_host_http", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(control.GuestSlotControlError, match="host_ready_timeout"):
        instance._host_operation({"op": "health"}, time.monotonic() + 0.02)
    assert calls == []


def test_success_reply_shapes_for_all_non_stop_operations() -> None:
    assert control._response_for_operation("health", {"healthy": True}) == {
        "ok": True, "result": {"healthy": True}, "formal_admission": False,
    }
    assert control._response_for_operation("new_session", {"session_id": "s1"}) == {
        "ok": True, "result": {"session_id": "s1"}, "formal_admission": False,
    }
    assert control._response_for_operation("fork", {"session_id": "s2"}) == {
        "ok": True, "result": {"session_id": "s2"}, "formal_admission": False,
    }
    assert control._response_for_operation("mcp_status", {"connected": True}) == {
        "ok": True, "result": {"connected": True}, "formal_admission": False,
    }


def test_final_uses_next_sequence_and_preserves_public_receipt_fields() -> None:
    server, owner = socket.socketpair()
    instance = control.GuestSlotControl(timeout_seconds=5)
    instance._connection = server
    instance._callback_returned = True
    instance._expected_sequence = 3
    try:
        instance.finish({
            "status": "observed",
            "formal_admission": False,
            "claim_eligible": False,
            "roles": [{
                "role": "host",
                "cgroup_populated_checked": True,
                "cgroup_populated": 0,
            }],
        })
        final = frames.read_frame(owner, timeout=2)
        assert final.kind == frames.FrameKind.FINAL
        assert final.sequence == 3
        assert json.loads(final.payload) == {
            "formal_admission": False,
            "launcher_receipt": {
                "status": "observed",
                "formal_admission": False,
                "claim_eligible": False,
                "roles": [{
                    "role": "host",
                    "cgroup_populated_checked": True,
                    "cgroup_populated": 0,
                }],
            },
        }
        assert server.fileno() == -1
    finally:
        owner.close()
        server.close()


def test_invalid_semantic_request_returns_typed_error_and_stops_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, owner = socket.socketpair()
    instance = control.GuestSlotControl(timeout_seconds=5)
    instance._host = _handles()[0]
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    thread, errors = _start_control_connection(instance, server)
    try:
        _send_request(owner, 1, {"op": "health", "extra": False})
        sequence, response = _read_reply(owner)
        assert sequence == 1
        assert response == {
            "ok": False,
            "error": "request_fields_invalid",
            "formal_admission": False,
        }
        thread.join(2)
        assert not thread.is_alive()
        assert [str(error) for error in errors] == ["request_fields_invalid"]
        assert killed == [(321, control.signal.SIGTERM)]
    finally:
        owner.close()
        instance.close()


def test_sequence_and_operation_limit_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    server, owner = socket.socketpair()
    instance = control.GuestSlotControl(timeout_seconds=5)
    instance._host = _handles()[0]
    instance._operation_count = control.MAX_OPERATIONS
    monkeypatch.setattr(control.os, "kill", lambda _pid, _sig: None)
    thread, errors = _start_control_connection(instance, server)
    try:
        _send_request(owner, 2, {"op": "stop"})
        thread.join(2)
        assert not thread.is_alive()
        assert [str(error) for error in errors] == ["control_sequence_invalid"]
    finally:
        owner.close()
        instance.close()

    server, owner = socket.socketpair()
    instance = control.GuestSlotControl(timeout_seconds=5)
    instance._host = _handles()[0]
    instance._operation_count = control.MAX_OPERATIONS
    monkeypatch.setattr(control.os, "kill", lambda _pid, _sig: None)
    thread, errors = _start_control_connection(instance, server)
    try:
        _send_request(owner, 1, {"op": "stop"})
        thread.join(2)
        assert not thread.is_alive()
        assert [str(error) for error in errors] == ["operation_limit_exceeded"]
    finally:
        owner.close()
        instance.close()


def test_host_response_is_sanitized_to_fixed_fields() -> None:
    assert control._sanitize_host_response(
        "health", 200, b'{"healthy":false,"version":"secret"}'
    ) == {
        "healthy": False,
    }
    assert control._sanitize_host_response(
        "new_session", 200, b'{"id":"s1","title":"private"}'
    ) == {
        "session_id": "s1",
    }
    assert control._sanitize_host_response("fork", 200, b'{"id":"s2","messages":["private"]}') == {
        "session_id": "s2",
    }
    assert control._sanitize_host_response(
        "mcp_status", 200, b'{"maintenance_environment":{"status":"connected","path":"private"}}',
    ) == {"connected": True}
    with pytest.raises(control.GuestSlotControlError, match="host_response_budget_exceeded"):
        control._sanitize_host_response("health", 200, b"x" * (control.MAX_HOST_RESPONSE_BYTES + 1))
    with pytest.raises(control.GuestSlotControlError, match="host_response_invalid"):
        control._sanitize_host_response("health", 200, b'{}')
    assert control._sanitize_host_response(
        "mcp_status", 200,
        b'{"maintenance_environment":{"status":"connected"},"broken":{"status":"error"}}',
    ) == {"connected": False}
    with pytest.raises(control.GuestSlotControlError, match="host_response_invalid"):
        control._sanitize_host_response("mcp_status", 200, b'{"connected":true}')
    with pytest.raises(control.GuestSlotControlError, match="host_response_invalid"):
        control._sanitize_host_response("mcp_status", 200, b'{}')


def test_final_rejects_private_or_formal_receipt_and_preserves_public_cgroup_fields() -> None:
    receipt = {
        "status": "observed",
        "formal_admission": False,
        "claim_eligible": False,
        "roles": [{"cgroup_populated_checked": True, "cgroup_populated": 0}],
    }
    assert control._sanitized_final(receipt) == {
        "formal_admission": False,
        "launcher_receipt": receipt,
    }
    with pytest.raises(control.GuestSlotControlError, match="launcher_receipt_invalid"):
        control._sanitized_final({**receipt, "path": "/private"})
    with pytest.raises(control.GuestSlotControlError, match="launcher_receipt_invalid"):
        control._sanitized_final({**receipt, "formal_admission": True})


def test_final_accepts_the_launcher_public_receipt_without_mutating_its_hash_shape() -> None:
    budget = launcher.Budget(
        pids_max=4,
        memory_max_bytes=launcher.MIN_MEMORY_BYTES,
        cpu_max_us=1_000,
        cpu_period_us=1_000,
    )
    roles = tuple(
        launcher.RoleSpec(role=role, command=("python",), bindings=(), budget=budget)
        for role in ("host", "mcp")
    )
    receipt = launcher.build_validation_receipt(
        launcher.LauncherConfig(
            freshroot=Path("/fresh"),
            cgroup_root=Path("/cgroup"),
            timeout_seconds=1,
            roles=roles,
        ),
        config_sha256="a" * 64,
    )
    final = control._sanitized_final(receipt)
    assert final["launcher_receipt"] == receipt
    assert receipt["roles"][0]["writable_workdir"] is True
    assert receipt["roles"][0]["cgroup_populated_checked"] is False


@pytest.mark.skipif(not hasattr(control.os, "fork"), reason="fork unavailable")
def test_http_child_timeout_is_reaped_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def sleeping_child(*_args: object, **_kwargs: object) -> None:
        time.sleep(2)

    monkeypatch.setattr(control, "_child_http_entry", sleeping_child)
    monkeypatch.setattr(control.os, "geteuid", lambda: 0)
    started = time.monotonic()
    with pytest.raises(control.GuestSlotControlError, match="host_http_timeout"):
        control._run_host_http(321, "health", "GET", "/global/health", None, timeout_seconds=0.05)
    assert time.monotonic() - started < 2


def test_http_request_has_fixed_loopback_route_and_no_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int, str, bytes | None]] = []

    class Response:
        status = 200

        def read(self, _limit: int) -> bytes:
            return b'{"healthy":true}'

    class Connection:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            assert timeout == 1
            calls.append((host, port, "", None))

        def connect(self) -> None:
            pass

        def request(
            self,
            method: str,
            path: str,
            *,
            body: bytes | None,
            headers: dict[str, str],
        ) -> None:
            calls[-1] = (calls[-1][0], calls[-1][1], f"{method} {path}", body)
            assert "Proxy" not in headers

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(control.http.client, "HTTPConnection", Connection)
    assert control._http_request_in_host_netns(
        "health", "GET", "/global/health", None, 1,
    ) == {"healthy": True}
    assert calls == [("127.0.0.1", 4096, "GET /global/health", None)]


@pytest.mark.parametrize("stage", ["connect", "request", "response_headers", "response_body"])
def test_http_timeout_reports_stage_without_replaying(monkeypatch, stage):
    calls = []

    def step(name):
        calls.append(name)
        if name == stage:
            raise TimeoutError

    class Connection:
        status = 200

        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            step("connect")

        def request(self, *args, **kwargs):
            step("request")

        def getresponse(self):
            step("response_headers")
            return self

        def read(self, limit):
            step("response_body")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(control.http.client, "HTTPConnection", Connection)
    expected = "host_connect_failed" if stage == "connect" else f"host_http_{stage}_timeout"
    with pytest.raises(control.GuestSlotControlError, match=expected):
        control._http_request_in_host_netns("health", "GET", "/global/health", None, 1)
    assert calls.count(stage) == 1
    assert calls[-1] == "close"


def test_role_handles_require_fixed_uids() -> None:
    instance = control.GuestSlotControl(timeout_seconds=1)
    with pytest.raises(control.GuestSlotControlError, match="roles_invalid"):
        instance._validate_handles((
            SimpleNamespace(role="host", pid=321, uid=1000),
            SimpleNamespace(role="mcp", pid=322, uid=1000),
        ))

@pytest.mark.skipif(not hasattr(control.os, "fork"), reason="fork unavailable")
def test_real_child_result_preserves_success_fields(monkeypatch):
    monkeypatch.setattr(control.os, "geteuid", lambda: 0)
    monkeypatch.setattr(control, "_set_child_parent_death_signal", lambda: None)
    monkeypatch.setattr(control, "_enter_host_netns", lambda pid: None)
    monkeypatch.setattr(control, "_http_request_in_host_netns", lambda *args: {"healthy": True})
    assert control._run_host_http(321, "health", "GET", "/global/health", None) == {
        "healthy": True,
    }

@pytest.mark.parametrize("formal", [False, True])
def test_process_observation_is_separate_and_never_promotes_authority(formal):
    server, owner = socket.socketpair()
    instance = control.GuestSlotControl(timeout_seconds=5)
    instance._connection = server
    instance._callback_returned = True
    receipt = {"formal_admission": False, "claim_eligible": False, "record_sha256": "a" * 64}
    observation = {"formal_admission": formal, "record_sha256": "b" * 64}
    with owner, server:
        if formal:
            with pytest.raises(control.GuestSlotControlError, match="process_observation_invalid"):
                instance.finish(receipt, process_observation=observation)
        else:
            instance.finish(receipt, process_observation=observation)
            value = json.loads(frames.read_frame(owner, timeout=2).payload)
            assert value == {"formal_admission": False, "launcher_receipt": receipt,
                             "process_observation": observation}
        assert receipt["record_sha256"] == "a" * 64
        assert observation["record_sha256"] == "b" * 64
        assert server.fileno() == -1
