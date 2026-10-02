"""Pure, mock and local socket checks; no Provider, VM or namespace execution."""

from __future__ import annotations

import hashlib
import json
import socket
import struct
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from benchmarks.hosts import native_provider_bridge as bridge
from benchmarks.hosts import native_slot_frames as frames


def _assistant(*, output=4, reasoning=0, completed=1234):
    return {
        "info": {
            "role": "assistant", "id": "message-1", "sessionID": "session-1",
            "providerID": "deepseek", "modelID": "deepseek-v4-flash", "finish": "stop",
            "time": {"completed": completed},
            "tokens": {"input": 8, "output": output, "reasoning": reasoning,
                       "cache": {"read": 0, "write": 0}},
            "directory": "/private/canary", "reasoning": "private reasoning canary",
        },
        "parts": [{"type": "text", "text": bridge.PROBE_REPLY}],
    }


def _native_result(value=None):
    return bridge.sanitize_native_response(
        200, json.dumps(_assistant() if value is None else value).encode(), "session-1",
    )


@pytest.mark.skipif(bridge.os.name != "posix", reason="POSIX guest log descriptors")
def test_host_failure_log_projection_exports_only_finite_error_categories(tmp_path):
    directory = tmp_path / "data/opencode/log"
    directory.mkdir(parents=True)
    (directory / "opencode.log").write_bytes(
        b"level=INFO message=TypeError\n"
        b"level=ERROR cause=TypeError private=/canary/private secret=sk-canary "
        b"stack=ToolRegistry.state errno=ENOENT\n"
    )
    assert bridge._public_host_failure_codes(tmp_path) == [
        "host_log_enoent", "host_log_typeerror", "host_log_tool_registry",
    ]


@pytest.mark.skipif(bridge.os.name != "posix", reason="POSIX guest log descriptors")
@pytest.mark.parametrize("kind", ["missing", "link", "parent_link", "oversized"])
def test_host_failure_log_projection_rejects_missing_or_unsafe_log(tmp_path, kind):
    root = tmp_path / "role"
    directory = root / "data/opencode/log"
    directory.mkdir(parents=True)
    path = directory / "opencode.log"
    if kind == "link":
        path.symlink_to(tmp_path / "outside")
    elif kind == "parent_link":
        directory.rmdir()
        directory.symlink_to(tmp_path)
    elif kind == "oversized":
        path.write_bytes(b"x" * (512 * 1024 + 1))
    assert bridge._public_host_failure_codes(root) == ["host_log_unavailable"]


@pytest.mark.parametrize("value", [["/private/canary"], ["sk-canary"], ["host_log_enoent"] * 2,
                                   [True], {}, ["host_log_enoent"] * 7])
def test_host_failure_diagnostic_rejects_nonfinite_or_duplicate_categories(value):
    with pytest.raises(bridge.ProviderBridgeError, match="host_failure_codes_invalid"):
        bridge.validate_host_failure_codes(value)


def test_host_failure_diagnostic_returns_a_new_closed_projection():
    value = ["host_log_no_error_record"]
    actual = bridge.validate_host_failure_codes(value)
    assert actual == value and actual is not value


@pytest.mark.parametrize("status", [200, 403])
def test_reply_codec_preserves_opaque_bytes_and_bounded_header(status):
    raw = b"\x00\xff\r\n{not-json}"
    encoded = bridge.encode_provider_reply(status, "text/event-stream", raw)
    assert bridge.decode_provider_reply(encoded) == (status, "text/event-stream", raw)
    maximum = bridge.MAX_PROVIDER_REPLY - bridge._REPLY_HEADER.size - 1
    encoded = bridge.encode_provider_reply(status, "x", b"x" * maximum)
    assert len(encoded) == bridge.MAX_PROVIDER_REPLY
    with pytest.raises(bridge.ProviderBridgeError, match="provider_reply_bound"):
        bridge.encode_provider_reply(status, "x", b"x" * (maximum + 1))


@pytest.mark.parametrize("status,ctype,body", [
    (201, "application/json", b"{}"), (True, "application/json", b"{}"),
    (200, "x" * 129, b"{}"), (200, "x\r\nCanary: secret", b"{}"),
    (200, "\u00e9", b"{}"), (200, "", b"{}"), (200, "x", "not-bytes"),
])
def test_reply_codec_rejects_invalid_types_bounds_and_injection(status, ctype, body):
    with pytest.raises(bridge.ProviderBridgeError) as caught:
        bridge.encode_provider_reply(status, ctype, body)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("payload", [
    b"", b"DPR1", b"x" * (bridge.MAX_PROVIDER_REPLY + 1),
    struct.pack("!4sHHI", b"BAD1", 200, 1, 0) + b"x",
    struct.pack("!4sHHI", b"DPR1", 200, 129, 0) + b"x" * 129,
    struct.pack("!4sHHI", b"DPR1", 200, 1, 1) + b"x",
    struct.pack("!4sHHI", b"DPR1", 500, 1, 0) + b"x",
], ids=[
    "empty-payload", "truncated-header", "frame-overflow", "wrong-magic",
    "content-type-overflow", "body-length-mismatch", "unsupported-status",
])
def test_reply_decoder_rejects_header_length_magic_and_status(payload):
    with pytest.raises(bridge.ProviderBridgeError):
        bridge.decode_provider_reply(payload)


def _thread(action):
    errors = []

    def run():
        try:
            action()
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    return worker, errors


def test_owner_pump_sends_control_once_and_forwards_one_opaque_request():
    owner, guest = socket.socketpair()
    calls = []

    def peer():
        request = frames.read_frame(guest, timeout=2)
        assert (request.kind, request.sequence, request.payload) == (
            frames.CONTROL_REQUEST, 7, b'{"op":"model_probe"}',
        )
        frames.write_frame(guest, frames.PROVIDER_REQUEST, 1, b"opaque canary", timeout=2)
        reply = frames.read_frame(guest, timeout=2)
        assert reply.kind == frames.PROVIDER_REPLY and reply.sequence == 1
        assert bridge.decode_provider_reply(reply.payload) == (200, "application/json", b"opaque")
        frames.write_frame(guest, frames.CONTROL_REPLY, 7, b'{"ok":true}', timeout=2)

    def forward(raw, remaining):
        calls.append(raw)
        assert 0 < remaining <= 2
        return 200, "application/json", b"opaque"

    worker, errors = _thread(peer)
    try:
        assert bridge.owner_exchange_with_provider(
            owner, 7, b'{"op":"model_probe"}', forward, timeout_seconds=2,
        ) == b'{"ok":true}'
    finally:
        worker.join(3)
        owner.close()
        guest.close()
    assert not worker.is_alive() and not errors
    assert calls == [b"opaque canary"]


@pytest.mark.parametrize("kind,sequence", [
    (frames.CONTROL_REPLY, 2), (frames.PROVIDER_REQUEST, 2),
    (frames.PROVIDER_REPLY, 1), (frames.FINAL, 1),
])
def test_owner_pump_rejects_other_kinds_and_sequences_without_forward(kind, sequence):
    owner, guest = socket.socketpair()
    forward = Mock()

    def peer():
        frames.read_frame(guest, timeout=2)
        frames.write_frame(guest, kind, sequence, b"opaque", timeout=2)

    worker, errors = _thread(peer)
    try:
        with pytest.raises(bridge.ProviderBridgeError):
            bridge.owner_exchange_with_provider(owner, 1, b"control", forward, timeout_seconds=2)
        assert owner.fileno() == -1
        forward.assert_not_called()
    finally:
        worker.join(3)
        owner.close()
        guest.close()
    assert not errors and not worker.is_alive()


def test_owner_pump_consumes_request_before_reply_and_rejects_replay():
    owner, guest = socket.socketpair()
    forward = Mock(return_value=(403, "application/json", b'{"error":"rejected"}'))

    def peer():
        frames.read_frame(guest, timeout=2)
        frames.write_frame(guest, frames.PROVIDER_REQUEST, 1, b"opaque", timeout=2)
        frames.read_frame(guest, timeout=2)
        frames.write_frame(guest, frames.PROVIDER_REQUEST, 1, b"opaque", timeout=2)

    worker, errors = _thread(peer)
    try:
        with pytest.raises(bridge.ProviderBridgeError, match="provider_request_sequence_invalid"):
            bridge.owner_exchange_with_provider(owner, 1, b"control", forward, timeout_seconds=2)
        forward.assert_called_once()
        assert owner.fileno() == -1
    finally:
        worker.join(3)
        owner.close()
        guest.close()
    assert not errors and not worker.is_alive()


@pytest.mark.parametrize("timeout", [0, -1, True, 180.1, float("inf"), float("nan"), 10**400])
def test_owner_pump_validates_deadline_before_any_send(timeout):
    connection = Mock()
    with pytest.raises(bridge.ProviderBridgeError, match="timeout_invalid"):
        bridge.owner_exchange_with_provider(connection, 1, b"control", Mock(),
                                             timeout_seconds=timeout)
    connection.sendall.assert_not_called()


def test_owner_pump_does_not_retry_forward_or_send_after_expired_deadline(monkeypatch):
    connection = Mock()
    read = Mock(return_value=frames.Frame(frames.PROVIDER_REQUEST, 1, b"opaque"))
    write = Mock()
    clock = [10.0]
    monkeypatch.setattr(bridge.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(bridge, "_read", read)
    monkeypatch.setattr(bridge.frames, "write_frame", write)

    def forward(_raw, _remaining):
        clock[0] = 12.0
        return 200, "application/json", b"opaque"

    with pytest.raises(bridge.ProviderBridgeError, match="probe_deadline_exceeded"):
        bridge.owner_exchange_with_provider(connection, 1, b"control", forward, timeout_seconds=1)
    write.assert_called_once()
    connection.close.assert_called_once()


@pytest.mark.parametrize("error", [RuntimeError("private-nonce canary"),
                                    KeyboardInterrupt("private-nonce canary")])
def test_owner_callback_failure_never_exposes_payload_or_retries(monkeypatch, error):
    connection = Mock()
    monkeypatch.setattr(bridge, "_read", Mock(return_value=frames.Frame(
        frames.PROVIDER_REQUEST, 1, b"opaque",
    )))
    write = Mock()
    monkeypatch.setattr(bridge.frames, "write_frame", write)
    forward = Mock(side_effect=error)
    expected = (
        KeyboardInterrupt if isinstance(error, KeyboardInterrupt) else bridge.ProviderBridgeError
    )
    with pytest.raises(expected) as caught:
        bridge.owner_exchange_with_provider(connection, 1, b"control", forward, timeout_seconds=1)
    assert "private" not in str(caught.value)
    forward.assert_called_once()
    write.assert_called_once()
    connection.close.assert_called_once()


def _headers():
    return [
        ("Host", "127.0.0.1:4100"), ("Authorization", "Bearer synthetic-nonce"),
        ("Content-Type", "application/json; charset=UTF-8"), ("Content-Length", "12"),
        ("Accept", "*/*"), ("User-Agent", "node"), ("Connection", "keep-alive"),
        ("Accept-Encoding", "gzip, deflate"),
    ]


def test_proxy_loopback_server_binds_without_dns_and_closes_socket(monkeypatch):
    reverse_dns = Mock(side_effect=AssertionError("reverse DNS forbidden"))
    monkeypatch.setattr(socket, "getfqdn", reverse_dns)
    server = bridge._LoopbackHTTPServer(
        (bridge.control.HOST_LOOPBACK, 0), bridge.BaseHTTPRequestHandler,
    )
    address = server.server_address
    try:
        assert address[0] == bridge.control.HOST_LOOPBACK
        assert 0 < address[1] <= 65535
        assert server.server_name == bridge.control.HOST_LOOPBACK
        assert server.server_port == address[1]
        assert server.socket.getsockname() == address
    finally:
        server.server_close()
    reverse_dns.assert_not_called()
    assert server.socket.fileno() == -1
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as rebound:
        rebound.bind(address)
        assert rebound.getsockname() == address


def test_proxy_header_allowlist_and_fixed_probe_route():
    assert bridge.validate_provider_ingress(
        "POST", "/chat/completions", _headers(), "synthetic-nonce",
    ) == 12
    body = json.loads(bridge.fixed_probe_body("session-1"))
    assert body == {
        "model": {"providerID": "deepseek", "modelID": "deepseek-v4-flash"},
        "parts": [{"type": "text", "text": bridge.PROBE_PROMPT}], "tools": {},
    }


@pytest.mark.parametrize("mutation", [
    "method", "route", "transfer", "custom", "duplicate", "auth", "ctype", "length", "bound",
])
def test_proxy_ingress_rejects_extra_headers_replay_association_and_unbounded_body(mutation):
    headers = _headers()
    method, path = "POST", "/chat/completions"
    if mutation == "method":
        method = "GET"
    elif mutation == "route":
        path += "?canary=private"
    elif mutation == "transfer":
        headers.append(("Transfer-Encoding", "chunked"))
    elif mutation == "custom":
        headers.append(("x-stainless-api-key", "private-canary"))
    elif mutation == "duplicate":
        headers.append(("CONTENT-LENGTH", "12"))
    else:
        index = {"auth": 1, "ctype": 2, "length": 3, "bound": 3}[mutation]
        headers[index] = (headers[index][0], {
            "auth": "Bearer private-canary", "ctype": "text/plain",
            "length": "+12", "bound": "262145",
        }[mutation])
    with pytest.raises(bridge.ProviderBridgeError) as caught:
        bridge.validate_provider_ingress(method, path, headers, "synthetic-nonce")
    assert "private" not in str(caught.value)


def test_native_response_projects_actual_info_without_raw_or_private_fields():
    value = _assistant()
    raw = json.dumps(value).encode()
    result = bridge.sanitize_native_response(200, raw, "session-1")
    assert result["model_task_executed"] is True and result["reply_matches"] is True
    assert result["http_response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["http_response_bytes"] == len(raw)
    assert result["assistant"]["tokens"]["output"] == 4
    assert result["assistant"]["time_completed"] == 1234
    rendered = json.dumps(result)
    for canary in ("/private/canary", "private reasoning canary", "message-1", "session-1"):
        assert canary not in rendered
    assert bridge._child_observation(bridge._json_bytes(result), proxy=False) == result


@pytest.mark.parametrize("mutation", [
    "role", "session", "provider", "model", "missing_time", "nonfinite_time", "bool_time",
    "zero_tokens", "missing_tokens", "bool_tokens",
])
def test_native_model_execution_requires_native_binding_completion_and_nonzero_tokens(mutation):
    value = _assistant()
    info = value["info"]
    if mutation in {"role", "session", "provider", "model"}:
        info[{"role": "role", "session": "sessionID", "provider": "providerID",
              "model": "modelID"}[mutation]] = "mismatch"
    elif mutation == "missing_time":
        info.pop("time")
    elif mutation == "nonfinite_time":
        info["time"]["completed"] = float("inf")
    elif mutation == "bool_time":
        info["time"]["completed"] = True
    elif mutation == "zero_tokens":
        info["tokens"].update(output=0, reasoning=0)
    elif mutation == "missing_tokens":
        info.pop("tokens")
    else:
        info["tokens"]["output"] = True
    result = _native_result(value)
    assert result["model_task_executed"] is False
    assert "model_invocation_count" not in result  # Never manufacture a zero invocation verdict.


def test_reasoning_tokens_can_establish_execution_without_fixed_reply_success():
    value = _assistant(output=0, reasoning=2)
    value["parts"][0]["text"] = "different public reply"
    result = _native_result(value)
    assert result["model_task_executed"] is True
    assert result["reply_matches"] is False


def test_child_observation_rejects_untrusted_fields_and_claim_mismatch():
    result = _native_result()
    with pytest.raises(bridge.ProviderBridgeError, match="child_observation_invalid"):
        bridge._child_observation(bridge._json_bytes({**result, "raw": "canary"}), proxy=False)
    result["model_task_executed"] = False
    with pytest.raises(bridge.ProviderBridgeError, match="child_observation_invalid"):
        bridge._child_observation(bridge._json_bytes(result), proxy=False)


def test_child_setup_closes_inherited_vsock_before_entering_host_netns(monkeypatch):
    events = []
    connection = Mock(close=lambda: events.append("vsock_close"))
    unwanted = Mock(close=lambda: events.append("other_close"))
    monkeypatch.setattr(bridge.control, "_set_child_parent_death_signal",
                        lambda: events.append("parent_death"))
    monkeypatch.setattr(bridge.control, "_enter_host_netns",
                        lambda pid: events.append(("netns", pid)))
    bridge._child_setup(123, connection, [unwanted])
    assert events == ["vsock_close", "other_close", "parent_death", ("netns", 123)]


def test_child_cannot_enter_host_netns_with_retained_vsock(monkeypatch):
    connection = Mock(close=Mock(), fileno=Mock(return_value=9))
    enter = Mock()
    monkeypatch.setattr(bridge.control, "_enter_host_netns", enter)
    with pytest.raises(bridge.ProviderBridgeError, match="child_descriptor_close_failed"):
        bridge._child_setup(123, connection, [])
    enter.assert_not_called()


def test_fixed_http_client_uses_only_one_public_message_post_and_bounded_timeout(monkeypatch):
    connection = MagicMock()
    response = connection.getresponse.return_value
    response.status = 200
    response.read.return_value = json.dumps(_assistant()).encode()
    constructor = Mock(return_value=connection)
    monkeypatch.setattr(bridge.http.client, "HTTPConnection", constructor)
    result = bridge._fixed_host_http("session-1", time.monotonic() + 150)
    assert constructor.call_args.args == ("127.0.0.1", 4096)
    assert 0 < constructor.call_args.kwargs["timeout"] <= 120
    connection.request.assert_called_once()
    assert connection.request.call_args.args == ("POST", "/session/session-1/message")
    assert connection.request.call_args.kwargs["body"] == bridge.fixed_probe_body("session-1")
    response.read.assert_called_once_with(bridge.MAX_PROVIDER_REPLY + 1)
    assert result["model_task_executed"]
    connection.close.assert_called_once()


def test_guest_parent_exclusively_bridges_one_provider_then_observes_both_children():
    guest, owner = socket.socketpair()
    proxy, proxy_child = socket.socketpair()
    client, client_child = socket.socketpair()
    delivered = threading.Event()

    def proxy_peer():
        frames.write_frame(proxy_child, frames.PROVIDER_REQUEST, 1, b"opaque", timeout=2)
        reply = frames.read_frame(proxy_child, timeout=2)
        assert bridge.decode_provider_reply(reply.payload) == (200, "application/json", b"opaque")
        frames.write_frame(proxy_child, frames.CONTROL_REPLY, 2,
                           bridge._json_bytes({"admitted": 1, "rejected": 0}), timeout=2)
        delivered.set()

    def owner_peer():
        request = frames.read_frame(owner, timeout=2)
        assert (request.kind, request.sequence, request.payload) == (
            frames.PROVIDER_REQUEST, 1, b"opaque",
        )
        frames.write_frame(
            owner, frames.PROVIDER_REPLY, 1,
            bridge.encode_provider_reply(200, "application/json", b"opaque"), timeout=2,
        )

    def client_peer():
        assert delivered.wait(2)
        frames.write_frame(client_child, frames.CONTROL_REPLY, 1,
                           bridge._json_bytes(_native_result()), timeout=2)

    workers = [_thread(action) for action in (proxy_peer, owner_peer, client_peer)]
    observation = {}
    try:
        result = bridge._pump_guest(guest, proxy, client, time.monotonic() + 3, observation)
        assert result["provider_requests_admitted"] == 1
        assert result["provider_requests_forwarded"] == 1
        assert observation == {"provider_requests_admitted": 1, "provider_requests_forwarded": 1}
        assert result["client"]["model_task_executed"] is True
    finally:
        for worker, _errors in workers:
            worker.join(3)
        for endpoint in (guest, owner, proxy, proxy_child, client, client_child):
            endpoint.close()
    assert all(not worker.is_alive() and not errors for worker, errors in workers)


@pytest.mark.parametrize("failure", [None, "after_forward", "cleanup"])
def test_probe_parent_reaps_every_child_and_retains_failed_forward_counts(monkeypatch, failure):
    pairs = [socket.socketpair(), socket.socketpair()]
    monkeypatch.setattr(bridge.socket, "socketpair", Mock(side_effect=pairs))
    fork = Mock(side_effect=[1001, 1002])
    monkeypatch.setattr(bridge.os, "fork", fork, raising=False)
    monkeypatch.setattr(bridge, "_read", lambda *_args: frames.Frame(
        frames.CONTROL_REPLY, 1, b'{"ready":true}',
    ))
    connection = Mock()
    reaped = []

    def reap(pid):
        reaped.append(pid)
        return {"reaped": not (failure == "cleanup" and pid == 1002), "exit_code": 0}

    def pump(_connection, _proxy, _client, deadline, observation):
        assert 0 < deadline - time.monotonic() <= 120
        observation.update(provider_requests_admitted=1, provider_requests_forwarded=1)
        if failure == "after_forward":
            raise bridge.ProviderBridgeError("provider_reply_sequence_invalid")
        return {"provider_requests_admitted": 1, "provider_requests_forwarded": 1,
                "client": _native_result(), "proxy": {"admitted": 1, "rejected": 0}}

    monkeypatch.setattr(bridge, "_reap_child", reap)
    monkeypatch.setattr(bridge, "_pump_guest", pump)
    result = bridge.run_fixed_model_probe(SimpleNamespace(role="host", pid=123), connection,
                                         session_id="session-1", dummy_nonce="synthetic-nonce",
                                         deadline=time.monotonic() + 150)
    assert reaped == [1001, 1002]
    assert result["provider_requests_admitted"] == result["provider_requests_forwarded"] == 1
    assert result["model_invocation_count"] is None and result["formal_admission"] is False
    assert result["mcp_functional_claim"] is False
    assert result["cleanup_confirmed"] is (failure != "cleanup")
    if failure == "after_forward":
        assert result["gaps"] == ["provider_reply_sequence_invalid"]
        connection.close.assert_called_once()
        assert result["model_task_executed"] is False
    elif failure == "cleanup":
        assert result["gaps"] == ["child_cleanup_gap"]
    else:
        assert result["gaps"] == [] and result["model_task_executed"] is True


def test_probe_without_provider_request_keeps_invocation_count_unavailable(monkeypatch):
    pairs = [socket.socketpair(), socket.socketpair()]
    monkeypatch.setattr(bridge.socket, "socketpair", Mock(side_effect=pairs))
    monkeypatch.setattr(bridge.os, "fork", Mock(side_effect=[1001, 1002]), raising=False)
    monkeypatch.setattr(bridge, "_read", lambda *_args: frames.Frame(
        frames.CONTROL_REPLY, 1, b'{"ready":true}',
    ))
    monkeypatch.setattr(bridge, "_pump_guest", lambda *_args: {
        "provider_requests_admitted": 0, "provider_requests_forwarded": 0,
        "client": {"gap": "client_child_failed"}, "proxy": None,
    })
    monkeypatch.setattr(bridge, "_reap_child", lambda _pid: {"reaped": True, "exit_code": 1})
    result = bridge.run_fixed_model_probe(SimpleNamespace(role="host", pid=123), Mock(),
                                         session_id="session-1", dummy_nonce="synthetic-nonce",
                                         deadline=time.monotonic() + 2)
    assert result["model_invocation_count"] is None
    assert result["model_task_executed"] is False
    assert result["provider_requests_forwarded"] == 0
    assert result["gaps"] == ["client_child_failed", "provider_request_not_observed"]


def test_socketpair_setup_failure_closes_first_pair_without_fork(monkeypatch):
    parent, child = socket.socketpair()
    pair = Mock(side_effect=[(parent, child), OSError("private-path canary")])
    monkeypatch.setattr(bridge.socket, "socketpair", pair)
    fork = Mock()
    monkeypatch.setattr(bridge.os, "fork", fork, raising=False)
    result = bridge.run_fixed_model_probe(SimpleNamespace(role="host", pid=123), Mock(),
                                         session_id="session-1", dummy_nonce="synthetic-nonce",
                                         deadline=time.monotonic() + 2)
    assert parent.fileno() == child.fileno() == -1
    assert result["gaps"] == ["native_probe_failed"] and result["children"] == {}
    fork.assert_not_called()
    assert "canary" not in json.dumps(result)


@pytest.mark.parametrize("status", [0, 7 << 8, 9])
@pytest.mark.skipif(bridge.os.name != "posix", reason="native POSIX process wait status")
def test_child_reaper_records_actual_waitpid_exit_status_without_kill(monkeypatch, status):
    monkeypatch.setattr(bridge.os, "waitpid", lambda pid, _options: (pid, status))
    kill = Mock()
    monkeypatch.setattr(bridge.os, "kill", kill)
    result = bridge._reap_child(1001)
    assert result == {"reaped": True, "exit_code": bridge.os.waitstatus_to_exitcode(status)}
    kill.assert_not_called()
