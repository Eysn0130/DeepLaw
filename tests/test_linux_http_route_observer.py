"""Packet fixtures validate parsing only, not native capture authority."""

import hashlib
import json
import struct

import pytest

from benchmarks.hosts import linux_http_route_observer as observer

ADDRESS = ("lo", 0x0800, 0, 772, b"")


def packet(sequence, flags, body=b"", *, client=True):
    tcp = bytearray(20)
    struct.pack_into("!HHI", tcp, 0, *((45000, 4096) if client else (4096, 45000)), sequence)
    tcp[12] = 0x50
    tcp[13] = flags
    ip = bytearray(20)
    ip[0] = 0x45
    struct.pack_into("!H", ip, 2, 40 + len(body))
    ip[9] = 6
    ip[12:20] = b"\x7f\0\0\1" * 2
    return bytes(ip + tcp + body)


def complete(body):
    capture = observer.RouteCapture()
    capture.feed(packet(100, 2), ADDRESS)
    capture.feed(packet(200, 18, client=False), ADDRESS)
    capture.feed(packet(101, 24, body), ADDRESS)
    capture.feed(packet(101 + len(body), 17), ADDRESS)
    capture.feed(packet(201, 17, client=False), ADDRESS)
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
    ("POST", "/session/ses_abc/message", b"private-payload", "forbidden"),
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
