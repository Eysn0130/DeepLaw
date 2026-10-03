"""Packet fixtures validate parsing only, not native capture authority."""

import hashlib
import json
import struct
from unittest.mock import MagicMock

import pytest

from benchmarks.hosts import linux_http_route_observer as observer

ADDRESS = ("lo", 0x0800, 0, 772, b"")


def packet(sequence, flags, body=b"", *, client=True, server_port=4096, client_port=45000):
    tcp = bytearray(20)
    ports = (client_port, server_port) if client else (server_port, client_port)
    struct.pack_into("!HHI", tcp, 0, *ports, sequence)
    tcp[12] = 0x50
    tcp[13] = flags
    ip = bytearray(20)
    ip[0] = 0x45
    struct.pack_into("!H", ip, 2, 40 + len(body))
    ip[9] = 6
    ip[12:20] = b"\x7f\0\0\1" * 2
    return bytes(ip + tcp + body)


def complete(body, *, capture=None, server_port=4096, client_port=45000):
    if capture is None:
        capture = observer.RouteCapture()
    options = {"server_port": server_port, "client_port": client_port}
    capture.feed(packet(100, 2, **options), ADDRESS)
    capture.feed(packet(200, 18, client=False, **options), ADDRESS)
    capture.feed(packet(101, 24, body, **options), ADDRESS)
    capture.feed(packet(101 + len(body), 17, **options), ADDRESS)
    capture.feed(packet(201, 17, client=False, **options), ADDRESS)
    return capture


def finish(capture):
    return capture.finish(
        kernel_packets=capture.packets, kernel_drops=0, namespace_sha256="a" * 64,
    )


@pytest.mark.parametrize("method,target,body,route", [
    ("GET", "/global/health", b"", "health"),
    ("POST", "/session", b"{}", "new_session"),
    ("POST", "/session/ses_abc/fork", b"{}", "fork"),
    ("GET", "/mcp", b"", "mcp_status"),
    ("POST", "/session/ses_abc/message", b"private-payload", "model_message"),
    ("POST", "/session/ses_abc/unapproved", b"private-payload", "forbidden"),
])
def test_complete_request_exports_only_fixed_category_and_digests(method, target, body, route):
    raw = (f"{method} {target} HTTP/1.1\r\nHost: localhost\r\n"
           f"Content-Length: {len(body)}\r\n\r\n").encode() + body
    result = finish(complete(raw))
    assert result["requests"] == [{
        "route": route,
        "method_sha256": hashlib.sha256(method.encode()).hexdigest(),
        "target_sha256": hashlib.sha256(target.encode()).hexdigest(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "request_sha256": hashlib.sha256(raw).hexdigest(),
    }]
    assert result["formal_admission"] is False
    assert target not in json.dumps(result)
    assert "private-payload" not in json.dumps(result)


def test_segmented_request_and_identical_retransmit_count_once():
    raw = b"GET /global/health HTTP/1.1\r\nHost: localhost\r\n\r\n"
    capture = observer.RouteCapture()
    capture.feed(packet(100, 2), ADDRESS)
    capture.feed(packet(101, 24, raw[:12]), ADDRESS)
    capture.feed(packet(101, 24, raw[:12]), ADDRESS)
    capture.feed(packet(113, 24, raw[12:]), ADDRESS)
    capture.feed(packet(101 + len(raw), 17), ADDRESS)
    capture.feed(packet(500, 17, client=False), ADDRESS)
    assert len(finish(capture)["requests"]) == 1


@pytest.mark.parametrize("changed,code", [
    (lambda: packet(105, 24, b"gap"), "tcp_sequence_gap"),
    (lambda: packet(101, 24, b"changed"), "tcp_retransmit_gap"),
])
def test_stream_gaps_rejected(changed, code):
    capture = observer.RouteCapture()
    capture.feed(packet(100, 2), ADDRESS)
    capture.feed(packet(101, 24, b"GET"), ADDRESS)
    with pytest.raises(observer.RouteObservationGap, match=code):
        capture.feed(changed(), ADDRESS)


@pytest.mark.parametrize("count_delta,drops", [(1, 0), (0, 1)])
def test_kernel_loss_or_unread_packets_prevent_observed_result(count_delta, drops):
    capture = complete(b"GET /global/health HTTP/1.1\r\n\r\n")
    with pytest.raises(observer.RouteObservationGap, match="packet_capture_loss"):
        capture.finish(
            kernel_packets=capture.packets + count_delta, kernel_drops=drops,
            namespace_sha256="a" * 64,
        )


def test_open_connection_is_a_gap_even_with_complete_http():
    capture = observer.RouteCapture()
    capture.feed(packet(100, 2), ADDRESS)
    capture.feed(packet(101, 24, b"GET /global/health HTTP/1.1\r\n\r\n"), ADDRESS)
    with pytest.raises(observer.RouteObservationGap, match="tcp_window_open"):
        finish(capture)


@pytest.mark.parametrize("server_port", [4096, 4100])
def test_reset_after_captured_request_preserves_bytes_and_closes_window(server_port):
    raw = b"GET /global/health HTTP/1.1\r\n\r\n"
    capture = observer.RouteCapture(model_probe=server_port == 4100)
    options = {"server_port": server_port}
    capture.feed(packet(100, 2, **options), ADDRESS)
    capture.feed(packet(101, 24, raw, **options), ADDRESS)
    capture.feed(packet(201, 17, client=False, **options), ADDRESS)
    capture.feed(packet(101 + len(raw), 4, **options), ADDRESS)
    result = finish(capture)
    assert observer.validate_observation(result) == result
    assert result["formal_admission"] is False
    if server_port == 4100:
        flow = result["auxiliary_flows"][0]
        assert flow["client_bytes"] == len(raw)
        assert flow["client_sha256"] == hashlib.sha256(raw).hexdigest()
        assert flow["reset"] is True
    else:
        assert len(result["requests"]) == 1
    with pytest.raises(observer.RouteObservationGap, match="tcp_closed_data_gap"):
        capture.feed(packet(101 + len(raw), 24, b"later", **options), ADDRESS)


def test_reset_does_not_make_incomplete_host_request_admissible():
    capture = observer.RouteCapture()
    capture.feed(packet(100, 2), ADDRESS)
    capture.feed(packet(101, 24, b"GET"), ADDRESS)
    capture.feed(packet(104, 4), ADDRESS)
    with pytest.raises(observer.RouteObservationGap, match="http_header_gap"):
        finish(capture)


@pytest.mark.parametrize("raw,code", [
    (b"GET /global/health HTTP/1.1\r\nHost: a\r\nHost: b\r\n\r\n", "http_header_gap"),
    (b"POST /session HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n", "http_transfer_encoding_gap"),
    (b"POST /session HTTP/1.1\r\nContent-Length: 2\r\n\r\n{", "http_body_gap"),
    (b"GET /global/health HTTP/1.1\r\n\r\n" * 2, "http_connection_reuse_gap"),
])
def test_unsupported_http_framing_never_becomes_zero_requests(raw, code):
    with pytest.raises(observer.RouteObservationGap, match=code):
        finish(complete(raw))


def test_truncation_and_packet_budget_are_gaps(monkeypatch):
    capture = observer.RouteCapture()
    with pytest.raises(observer.RouteObservationGap, match="packet_truncated"):
        capture.feed(packet(100, 2), ADDRESS, truncated=True)
    monkeypatch.setattr(observer, "MAX_PACKETS", 1)
    with pytest.raises(observer.RouteObservationGap, match="packet_budget_exceeded"):
        capture.feed(packet(100, 2), ADDRESS)


def test_outgoing_loopback_copy_does_not_duplicate_request():
    raw = b"GET /global/health HTTP/1.1\r\n\r\n"
    capture = complete(raw)
    capture.feed(packet(101, 24, raw), ("lo", 0x0800, 4, 772, b""))
    result = finish(capture)
    assert len(result["requests"]) == 1
    assert result["outgoing_duplicate_count"] == 1


def test_projection_validator_preserves_all_observed_requests_and_rejects_tampering():
    result = finish(complete(b"GET /global/health HTTP/1.1\r\n\r\n"))
    assert observer.validate_observation(result) == result
    changed = json.loads(json.dumps(result))
    changed["requests"][0]["route"] = "fork"
    with pytest.raises(observer.RouteObservationGap, match="route_receipt_digest_gap"):
        observer.validate_observation(changed)
    changed = {**result, "raw_path": "/private/unknown"}
    with pytest.raises(observer.RouteObservationGap, match="route_receipt_shape_gap"):
        observer.validate_observation(changed)


def resign(value):
    value["record_sha256"] = hashlib.sha256(json.dumps(
        {key: item for key, item in value.items() if key != "record_sha256"},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return value


def auxiliary_result():
    capture = observer.RouteCapture(model_probe=True)
    complete(b"GET /global/health HTTP/1.1\r\n\r\n", capture=capture)
    complete(b"opaque auxiliary bytes", capture=capture, server_port=4100)
    return finish(capture)


def test_model_profile_separates_same_ephemeral_port_and_exports_only_auxiliary_metadata():
    raw = (b"POST /chat/completions HTTP/1.1\r\nAuthorization: Bearer dummy-secret\r\n"
           b"Content-Length: 17\r\n\r\nprivate-probe-body")
    capture = observer.RouteCapture(model_probe=True)
    complete(b"GET /global/health HTTP/1.1\r\n\r\n", capture=capture)
    complete(raw, capture=capture, server_port=4100)
    result = finish(capture)
    assert result["schema_version"] == "deeplaw.linux-http-route-observation/v2"
    assert result["observation_scope"] == "host_loopback_ipv4_tcp_4096_with_auxiliary_tcp_4100"
    assert result["connection_count"] == 2
    assert result["auxiliary_connection_count"] == 1
    assert result["auxiliary_flows"] == [{
        "server_port": 4100, "client_port": 45000,
        "client_bytes": len(raw), "client_sha256": hashlib.sha256(raw).hexdigest(),
        "client_fin": True, "server_fin": True, "reset": False,
    }]
    assert [row["route"] for row in result["requests"]] == ["health"]
    assert observer.validate_observation(result) == result
    serialized = json.dumps(result)
    for private in ("/chat/completions", "Authorization", "dummy-secret", "private-probe-body"):
        assert private not in serialized
    assert result["formal_admission"] is False and result["claim_eligible"] is False


def test_auxiliary_stream_does_not_change_precise_host_request_sequence():
    capture = observer.RouteCapture(model_probe=True)
    host_raws = [
        b"GET /global/health HTTP/1.1\r\n\r\n",
        b"POST /session HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}",
        b"POST /session/ses_public/message HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}",
    ]
    for index, raw in enumerate(host_raws):
        complete(raw, capture=capture, client_port=45000 + index)
        if index == 1:
            complete(b"not parsed as HTTP", capture=capture, server_port=4100)
    result = finish(capture)
    assert [row["route"] for row in result["requests"]] == [
        "health", "new_session", "model_message",
    ]
    assert [row["request_sha256"] for row in result["requests"]] == [
        hashlib.sha256(raw).hexdigest() for raw in host_raws
    ]
    assert result["connection_count"] == 4
    assert result["auxiliary_connection_count"] == 1


@pytest.mark.parametrize("model_probe,port", [(False, 4100), (True, 4101)])
@pytest.mark.parametrize("outgoing", [False, True])
def test_unknown_server_port_is_a_gap_including_outgoing_duplicates(model_probe, port, outgoing):
    capture = observer.RouteCapture(model_probe=model_probe)
    address = ("lo", 0x0800, 4 if outgoing else 0, 772, b"")
    with pytest.raises(observer.RouteObservationGap, match="tcp_port_gap"):
        capture.feed(packet(100, 2, server_port=port), address)


@pytest.mark.parametrize("mutate,code", [
    (lambda raw: raw[:15], "ipv4_header_gap"),
    (lambda raw: raw[:12] + b"\x7f\0\0\2" + raw[16:], "ipv4_route_gap"),
])
def test_outgoing_duplicates_must_have_valid_loopback_structure(mutate, code):
    capture = observer.RouteCapture()
    with pytest.raises(observer.RouteObservationGap, match=code):
        capture.feed(mutate(packet(100, 2)), ("lo", 0x0800, 4, 772, b""))


def test_default_receipt_remains_closed_v1():
    result = finish(complete(b"GET /global/health HTTP/1.1\r\n\r\n"))
    assert result["schema_version"] == "deeplaw.linux-http-route-observation/v1"
    assert result["observation_scope"] == "host_loopback_ipv4_tcp_4096"
    assert set(result) == {
        "schema_version", "formal_admission", "claim_eligible", "status", "packet_count",
        "kernel_packet_count", "kernel_drop_count", "outgoing_duplicate_count",
        "connection_count", "requests", "observation_scope", "namespace_sha256", "record_sha256",
    }
    assert observer.validate_observation(result) == result


@pytest.mark.parametrize("model_probe", [False, True])
def test_closed_connection_cannot_reopen_even_with_same_initial_sequence(model_probe):
    capture = observer.RouteCapture(model_probe=model_probe)
    port = 4100 if model_probe else 4096
    complete(b"opaque" if model_probe else b"GET /mcp HTTP/1.1\r\n\r\n",
             capture=capture, server_port=port)
    with pytest.raises(observer.RouteObservationGap, match="tcp_port_reuse_gap"):
        capture.feed(packet(100, 2, server_port=port), ADDRESS)


def test_auxiliary_connection_limit_is_one_even_after_first_closes():
    capture = observer.RouteCapture(model_probe=True)
    complete(b"opaque", capture=capture, server_port=4100)
    with pytest.raises(observer.RouteObservationGap, match="auxiliary_connection_budget_exceeded"):
        capture.feed(packet(100, 2, server_port=4100, client_port=45001), ADDRESS)


@pytest.mark.parametrize("case,code", [
    ("start", "tcp_start_gap"), ("sequence", "tcp_sequence_gap"),
    ("retransmit", "tcp_retransmit_gap"), ("reset", "tcp_reset_gap"),
    ("fin", "tcp_fin_gap"), ("overflow", "stream_budget_exceeded"),
])
def test_auxiliary_client_stream_preserves_all_existing_bounds(case, code):
    capture = observer.RouteCapture(model_probe=True)
    options = {"server_port": 4100}
    if case != "start":
        capture.feed(packet(100, 2, **options), ADDRESS)
    if case in {"retransmit", "reset", "fin"}:
        capture.feed(packet(101, 24, b"opaque", **options), ADDRESS)
    packets = {
        "start": packet(101, 24, b"opaque", **options),
        "sequence": packet(102, 24, b"gap", **options),
        "retransmit": packet(101, 24, b"changed", **options),
        "reset": packet(101, 4, **options),
        "fin": packet(102, 17, **options),
        "overflow": packet(101, 24, b"x" * (observer.MAX_STREAM_BYTES + 1), **options),
    }
    with pytest.raises(observer.RouteObservationGap, match=code):
        capture.feed(packets[case], ADDRESS)


def test_auxiliary_open_window_loss_and_duplicate_statistics_are_not_hidden():
    capture = observer.RouteCapture(model_probe=True)
    capture.feed(packet(100, 2, server_port=4100), ADDRESS)
    capture.feed(packet(101, 24, b"opaque", server_port=4100), ADDRESS)
    with pytest.raises(observer.RouteObservationGap, match="tcp_window_open"):
        finish(capture)
    capture.feed(packet(107, 17, server_port=4100), ADDRESS)
    capture.feed(packet(201, 17, client=False, server_port=4100), ADDRESS)
    capture.feed(packet(101, 24, b"opaque", server_port=4100), ("lo", 0x0800, 4, 772, b""))
    for packets, drops in ((capture.packets - 1, 0), (capture.packets, 1)):
        with pytest.raises(observer.RouteObservationGap, match="packet_capture_loss"):
            capture.finish(kernel_packets=packets, kernel_drops=drops, namespace_sha256="a" * 64)
    result = finish(capture)
    assert result["packet_count"] == 5
    assert result["kernel_packet_count"] == 5
    assert result["outgoing_duplicate_count"] == 1
    assert result["requests"] == []
    assert result["auxiliary_connection_count"] == 1


@pytest.mark.parametrize("profile", [0, 1, None, "model", [], {}])
def test_profile_requires_explicit_bool(profile):
    with pytest.raises(observer.RouteObservationGap, match="model_probe_invalid"):
        observer.RouteCapture(model_probe=profile)


@pytest.mark.parametrize("model_probe,seconds", [(False, 60), (True, 190)])
def test_observe_uses_two_fixed_time_limits_without_real_capture(monkeypatch, model_probe, seconds):
    monkeypatch.setattr(observer.sys, "platform", "linux")
    monkeypatch.setattr(observer.os, "geteuid", lambda: 0)
    namespace = MagicMock()
    namespace.__enter__.return_value.fileno.return_value = 123
    monkeypatch.setattr(observer.Path, "open", lambda *args: namespace)
    monkeypatch.setattr(observer.os, "readlink", lambda path: "net:[123]")
    monkeypatch.setattr(observer.os, "setns", lambda *args: None, raising=False)
    source = MagicMock()
    monkeypatch.setattr(observer.socket, "AF_PACKET", 17, raising=False)
    monkeypatch.setattr(observer.socket, "socket", lambda *args: source)
    monkeypatch.setattr(observer.time, "monotonic", lambda: 100.0)
    waits = []

    def select_wait(readers, writers, errors, timeout):
        waits.append(timeout)
        raise observer.RouteObservationGap("synthetic_stop")

    monkeypatch.setattr(observer.select, "select", select_wait)
    assert observer.observe(123, model_probe=model_probe) == 1
    assert waits == [seconds]


def test_cli_model_profile_is_explicit(monkeypatch):
    seen = []
    monkeypatch.setattr(observer.sys, "argv", ["observer", "--host-pid", "123", "--model-probe"])
    monkeypatch.setattr(observer, "observe",
                        lambda pid, **options: seen.append((pid, options)) or 0)
    assert observer.main() == 0
    assert seen == [(123, {"model_probe": True})]


@pytest.mark.parametrize("change,code", [
    (lambda value: value.update(raw_headers="private"), "route_receipt_shape_gap"),
    (lambda value: value.update(observation_scope="all_loopback"), "route_receipt_status_gap"),
    (lambda value: value.update(auxiliary_connection_count=True), "route_receipt_count_gap"),
    (lambda value: value.update(auxiliary_connection_count=0), "route_receipt_auxiliary_gap"),
    (lambda value: value.update(connection_count=1), "route_receipt_requests_gap"),
    (lambda value: value.update(connection_count=True), "route_receipt_count_gap"),
    (lambda value: value["auxiliary_flows"][0].update(raw_body="private"),
     "route_receipt_auxiliary_gap"),
    (lambda value: value["auxiliary_flows"][0].update(server_port=4101),
     "route_receipt_auxiliary_gap"),
    (lambda value: value["auxiliary_flows"][0].update(client_port=True),
     "route_receipt_auxiliary_gap"),
    (lambda value: value["auxiliary_flows"][0].update(client_bytes=True),
     "route_receipt_auxiliary_gap"),
    (lambda value: value["auxiliary_flows"][0].update(client_bytes=float("inf")),
     "route_receipt_auxiliary_gap"),
    (lambda value: value["auxiliary_flows"][0].update(client_bytes=observer.MAX_STREAM_BYTES + 1),
     "route_receipt_auxiliary_gap"),
    (lambda value: value["auxiliary_flows"][0].update(client_sha256="bad"),
     "route_receipt_auxiliary_gap"),
    (lambda value: value["auxiliary_flows"][0].update(client_fin=False),
     "route_receipt_auxiliary_gap"),
    (lambda value: value["auxiliary_flows"][0].update(server_fin=1),
     "route_receipt_auxiliary_gap"),
    (lambda value: value["auxiliary_flows"][0].update(reset=True, server_fin=False),
     "route_receipt_auxiliary_gap"),
])
def test_v2_validator_fails_closed_even_with_recomputed_record_digest(change, code):
    value = auxiliary_result()
    change(value)
    with pytest.raises(observer.RouteObservationGap, match=code):
        observer.validate_observation(resign(value))


def test_v2_auxiliary_digest_is_bound_to_record():
    value = auxiliary_result()
    value["auxiliary_flows"][0]["client_sha256"] = "b" * 64
    with pytest.raises(observer.RouteObservationGap, match="route_receipt_digest_gap"):
        observer.validate_observation(value)
