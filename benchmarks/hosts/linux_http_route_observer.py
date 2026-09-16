"""Bounded passive observation of HTTP requests on the isolated Host loopback.

Engineering only. Unsupported packets, stream gaps, capture loss, or an open
window prevent an observed result. No packet bodies or literal routes are
exported. This is not an observation of non-network model execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import select
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_PACKETS = 8192
MAX_CONNECTIONS = 128
MAX_STREAM_BYTES = 16384
MAX_SECONDS = 60
PORT = 4096
SOL_PACKET = 263
PACKET_STATISTICS = 6
_SESSION = rb"[A-Za-z0-9_-]{1,256}"


class RouteObservationGap(ValueError):
    pass


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise RouteObservationGap(code)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _requests(raw: bytes) -> list[dict[str, object]]:
    rows = []
    while raw:
        split = raw.find(b"\r\n\r\n")
        _require(0 < split <= 8192, "http_header_gap")
        lines = raw[:split].split(b"\r\n")
        parts = lines[0].split(b" ")
        _require(len(parts) == 3 and parts[2] == b"HTTP/1.1", "http_request_line_gap")
        method, target, _ = parts
        _require(re.fullmatch(rb"[A-Z]{1,16}", method) is not None, "http_method_gap")
        _require(0 < len(target) <= 2048, "http_target_gap")
        headers: dict[bytes, bytes] = {}
        for line in lines[1:]:
            name, separator, value = line.partition(b":")
            name = name.lower()
            _require(
                bool(separator) and re.fullmatch(rb"[a-z0-9-]+", name) is not None
                and name not in headers,
                "http_header_gap",
            )
            headers[name] = value.strip()
        _require(b"transfer-encoding" not in headers, "http_transfer_encoding_gap")
        length = headers.get(b"content-length", b"0")
        _require(re.fullmatch(rb"[0-9]{1,5}", length) is not None, "http_length_gap")
        size = int(length)
        end = split + 4 + size
        _require(size <= MAX_STREAM_BYTES and end <= len(raw), "http_body_gap")
        body = raw[split + 4:end]
        route = "forbidden"
        if method == b"GET" and target == b"/global/health" and not body:
            route = "health"
        elif method == b"POST" and target == b"/session" and body == b"{}":
            route = "new_session"
        elif (
            method == b"POST" and re.fullmatch(rb"/session/" + _SESSION + rb"/fork", target)
            and body == b"{}"
        ):
            route = "fork"
        elif method == b"GET" and target == b"/mcp" and not body:
            route = "mcp_status"
        rows.append({
            "route": route,
            "method_sha256": _digest(method),
            "target_sha256": _digest(target),
            "body_sha256": _digest(body),
            "request_sha256": _digest(raw[:end]),
        })
        raw = raw[end:]
    return rows


@dataclass
class _Stream:
    start: int
    order: int
    data: bytearray = field(default_factory=bytearray)
    client_fin: bool = False
    server_fin: bool = False
    reset: bool = False
    data_order: int | None = None


class RouteCapture:
    """Parse a finite cooked AF_PACKET capture; never infer completeness."""

    def __init__(self) -> None:
        self.streams: dict[int, _Stream] = {}
        self.packets = 0
        self.outgoing = 0

    def feed(self, raw: bytes, address: tuple, *, truncated: bool = False) -> None:
        self.packets += 1
        _require(self.packets <= MAX_PACKETS, "packet_budget_exceeded")
        _require(not truncated, "packet_truncated")
        _require(len(address) >= 3 and address[0] == "lo", "packet_interface_gap")
        if address[2] == 4:  # PACKET_OUTGOING duplicates loopback reception.
            self.outgoing += 1
            return
        _require(address[2] == 0 and address[1] == 0x0800, "packet_protocol_gap")
        _require(len(raw) >= 40 and raw[0] >> 4 == 4, "ipv4_header_gap")
        ip_size = (raw[0] & 15) * 4
        total = int.from_bytes(raw[2:4], "big")
        _require(20 <= ip_size <= 60 and total == len(raw), "ipv4_size_gap")
        _require(int.from_bytes(raw[6:8], "big") & 0x3FFF == 0, "ipv4_fragment_gap")
        _require(raw[9] == 6 and raw[12:20] == b"\x7f\0\0\1" * 2, "ipv4_route_gap")
        tcp = raw[ip_size:]
        _require(len(tcp) >= 20, "tcp_header_gap")
        source, destination, sequence = struct.unpack_from("!HHI", tcp)
        client = destination == PORT and source != PORT
        _require(client or (source == PORT and destination != PORT), "tcp_port_gap")
        port = source if client else destination
        header_size = (tcp[12] >> 4) * 4
        _require(20 <= header_size <= len(tcp), "tcp_size_gap")
        flags = tcp[13]
        _require(not flags & 32, "tcp_urgent_gap")
        payload = tcp[header_size:]
        if client and flags & 2:
            _require(not payload and not flags & 5, "tcp_syn_gap")
            if port in self.streams:
                _require(self.streams[port].start == (sequence + 1) % 2**32,
                         "tcp_port_reuse_gap")
            else:
                _require(len(self.streams) < MAX_CONNECTIONS, "connection_budget_exceeded")
                self.streams[port] = _Stream((sequence + 1) % 2**32, self.packets)
            return
        _require(port in self.streams, "tcp_start_gap")
        stream = self.streams[port]
        if flags & 4:
            _require(not payload and not stream.data, "tcp_reset_gap")
            stream.reset = True
            return
        if client:
            offset = (sequence - stream.start) % 2**32
            if payload:
                if stream.data_order is None:
                    stream.data_order = self.packets
                _require(not stream.client_fin and not stream.reset, "tcp_closed_data_gap")
                _require(offset <= len(stream.data), "tcp_sequence_gap")
                overlap = min(len(stream.data) - offset, len(payload))
                _require(stream.data[offset:offset + overlap] == payload[:overlap],
                         "tcp_retransmit_gap")
                _require(len(stream.data) + len(payload) - overlap <= MAX_STREAM_BYTES,
                         "stream_budget_exceeded")
                stream.data.extend(payload[overlap:])
            if flags & 1:
                _require(offset + len(payload) == len(stream.data), "tcp_fin_gap")
                stream.client_fin = True
        elif flags & 1:
            stream.server_fin = True

    def finish(
        self, *, kernel_packets: int, kernel_drops: int, namespace_sha256: str
    ) -> dict[str, object]:
        _require(isinstance(namespace_sha256, str)
                 and re.fullmatch(r"[0-9a-f]{64}", namespace_sha256) is not None,
                 "namespace_binding_gap")
        _require(type(kernel_packets) is int and type(kernel_drops) is int,
                 "packet_statistics_gap")
        _require(kernel_drops == 0 and kernel_packets == self.packets,
                 "packet_capture_loss")
        rows = []
        for stream in sorted(
            self.streams.values(), key=lambda item: item.data_order or item.order
        ):
            _require(stream.reset or (stream.client_fin and stream.server_fin),
                     "tcp_window_open")
            parsed = _requests(bytes(stream.data))
            _require(len(parsed) <= 1, "http_connection_reuse_gap")
            rows.extend(parsed)
        result: dict[str, object] = {
            "schema_version": "deeplaw.linux-http-route-observation/v1",
            "formal_admission": False,
            "claim_eligible": False,
            "status": "observed",
            "packet_count": self.packets,
            "kernel_packet_count": kernel_packets,
            "kernel_drop_count": kernel_drops,
            "outgoing_duplicate_count": self.outgoing,
            "connection_count": len(self.streams),
            "requests": rows,
            "observation_scope": "host_loopback_ipv4_tcp_4096",
            "namespace_sha256": namespace_sha256,
        }
        result["record_sha256"] = _digest(_json(result))
        return result

    def clear(self) -> None:
        for stream in self.streams.values():
            stream.data.clear()
        self.streams.clear()


def validate_observation(value: object) -> dict[str, object]:
    """Check the closed projection; source authority still belongs to the caller."""
    keys = {
        "schema_version", "formal_admission", "claim_eligible", "status", "packet_count",
        "kernel_packet_count", "kernel_drop_count", "outgoing_duplicate_count",
        "connection_count", "requests", "observation_scope", "record_sha256",
        "namespace_sha256",
    }
    _require(isinstance(value, dict) and set(value) == keys, "route_receipt_shape_gap")
    _require(value["schema_version"] == "deeplaw.linux-http-route-observation/v1"
             and value["formal_admission"] is False and value["claim_eligible"] is False
             and value["status"] == "observed"
             and value["observation_scope"] == "host_loopback_ipv4_tcp_4096",
             "route_receipt_status_gap")
    _require(isinstance(value["namespace_sha256"], str)
             and re.fullmatch(r"[0-9a-f]{64}", value["namespace_sha256"]) is not None,
             "namespace_binding_gap")
    for key in ("packet_count", "kernel_packet_count", "kernel_drop_count",
                "outgoing_duplicate_count", "connection_count"):
        _require(type(value[key]) is int and 0 <= value[key] <= MAX_PACKETS,
                 "route_receipt_count_gap")
    _require(value["kernel_drop_count"] == 0
             and value["kernel_packet_count"] == value["packet_count"]
             and value["outgoing_duplicate_count"] <= value["packet_count"]
             and value["connection_count"] <= MAX_CONNECTIONS, "route_receipt_count_gap")
    rows = value["requests"]
    _require(isinstance(rows, list) and len(rows) <= value["connection_count"],
             "route_receipt_requests_gap")
    for row in rows:
        _require(isinstance(row, dict) and set(row) == {
            "route", "method_sha256", "target_sha256", "body_sha256", "request_sha256",
        }, "route_receipt_request_gap")
        _require(row["route"] in {"health", "new_session", "fork", "mcp_status", "forbidden"},
                 "route_receipt_request_gap")
        for key in ("method_sha256", "target_sha256", "body_sha256", "request_sha256"):
            _require(isinstance(row[key], str) and re.fullmatch(r"[0-9a-f]{64}", row[key])
                     is not None, "route_receipt_digest_gap")
    _require(value["record_sha256"] == _digest(_json({
        key: item for key, item in value.items() if key != "record_sha256"
    })), "route_receipt_digest_gap")
    return value


def observe(host_pid: int) -> int:
    """Run as external guest root; stdin finish must follow actual Host exit."""
    _require(sys.platform == "linux" and os.geteuid() == 0, "linux_root_required")
    _require(type(host_pid) is int and host_pid > 1, "host_pid_invalid")
    # The launcher supplies the live PID before releasing its entry gate.
    with (Path("/proc") / str(host_pid) / "ns/net").open("rb") as namespace:
        target = os.readlink(f"/proc/self/fd/{namespace.fileno()}")
        _require(re.fullmatch(r"net:\[[0-9]{1,20}\]", target) is not None,
                 "namespace_binding_gap")
        os.setns(namespace.fileno(), 0x40000000)
        _require(os.readlink("/proc/self/ns/net") == target, "namespace_binding_gap")
    namespace_digest = _digest(target.encode("ascii"))
    capture = RouteCapture()
    deadline = time.monotonic() + MAX_SECONDS
    try:
        with socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(3)) as source:
            source.bind(("lo", 0))
            source.setblocking(False)
            print('{"ready":true,"formal_admission":false}', flush=True)
            stopping = False
            while True:
                remaining = deadline - time.monotonic()
                _require(remaining > 0, "capture_timeout")
                ready, _, _ = select.select(
                    [source] if stopping else [source, sys.stdin.buffer], [], [],
                    0 if stopping else remaining,
                )
                if source in ready:
                    raw, _, flags, address = source.recvmsg(65536)
                    capture.feed(raw, address, truncated=bool(flags & socket.MSG_TRUNC))
                if not stopping and sys.stdin.buffer in ready:
                    _require(os.read(sys.stdin.fileno(), 32) == b'{"op":"finish"}\n',
                             "finish_request_invalid")
                    stopping = True
                if stopping and not ready:
                    stats = source.getsockopt(SOL_PACKET, PACKET_STATISTICS, 8)
                    _require(len(stats) == 8, "packet_statistics_gap")
                    packets, drops = struct.unpack("=II", stats)
                    result = capture.finish(
                        kernel_packets=packets, kernel_drops=drops,
                        namespace_sha256=namespace_digest,
                    )
                    print(_json(result).decode(), flush=True)
                    return 0
    except (RouteObservationGap, OSError) as error:
        code = str(error) if isinstance(error, RouteObservationGap) else "capture_os_error"
        print(_json({"status": "gap", "failure": code, "formal_admission": False}).decode(),
              flush=True)
        return 1
    finally:
        capture.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-pid", type=int, required=True)
    try:
        return observe(parser.parse_args().host_pid)
    except (RouteObservationGap, OSError):
        print('{"status":"gap","failure":"capture_start_failed","formal_admission":false}',
              flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
