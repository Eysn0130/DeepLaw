from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from benchmarks.hosts import native_slot_frames as frames


def _socketpair() -> tuple[socket.socket, socket.socket]:
    left, right = socket.socketpair()
    left.settimeout(1)
    right.settimeout(1)
    return left, right


def _raw_header(
    *,
    magic: bytes = frames.MAGIC,
    kind: int = int(frames.FrameKind.CONTROL_REQUEST),
    reserved: int = 0,
    sequence: int = 1,
    payload_length: int = 0,
) -> bytes:
    return struct.pack(
        frames.HEADER_FORMAT,
        magic,
        kind,
        reserved,
        sequence,
        payload_length,
    )


def test_roundtrip_uses_opaque_bytes_and_restores_timeout() -> None:
    left, right = _socketpair()
    try:
        original_timeout = right.gettimeout()
        frames.write_frame(
            left,
            frames.FrameKind.PROVIDER_REPLY,
            7,
            b"\x00opaque\xff",
        )
        result = frames.read_frame(right)
        assert result == frames.Frame(frames.FrameKind.PROVIDER_REPLY, 7, b"\x00opaque\xff")
        assert right.gettimeout() == original_timeout
    finally:
        left.close()
        right.close()


def test_reader_handles_fragmented_header_and_payload() -> None:
    left, right = _socketpair()
    packet = _raw_header(
        kind=int(frames.FrameKind.CONTROL_REPLY),
        sequence=2,
        payload_length=11,
    ) + b"fragmented!"
    errors: list[BaseException] = []

    def send_fragments() -> None:
        try:
            for byte in packet:
                left.send(bytes([byte]))
                time.sleep(0.001)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=send_fragments)
    thread.start()
    try:
        result = frames.read_frame(right)
        thread.join(2)
        assert not errors
        assert result == frames.Frame(frames.FrameKind.CONTROL_REPLY, 2, b"fragmented!")
    finally:
        left.close()
        right.close()
        thread.join(2)


def test_truncated_header_closes_reader_socket() -> None:
    left, right = _socketpair()
    try:
        left.sendall(frames.MAGIC[:2])
        left.shutdown(socket.SHUT_WR)
        with pytest.raises(frames.FrameTruncatedError):
            frames.read_frame(right)
        assert right.fileno() == -1
    finally:
        left.close()
        right.close()


def test_truncated_payload_closes_reader_socket() -> None:
    left, right = _socketpair()
    try:
        left.sendall(_raw_header(payload_length=4) + b"xy")
        left.shutdown(socket.SHUT_WR)
        with pytest.raises(frames.FrameTruncatedError):
            frames.read_frame(right)
        assert right.fileno() == -1
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize(
    ("kind", "payload_length"),
    [
        (frames.FrameKind.CONTROL_REQUEST, frames.MAX_CONTROL_PAYLOAD + 1),
        (frames.FrameKind.CONTROL_REPLY, frames.MAX_CONTROL_PAYLOAD + 1),
        (frames.FrameKind.PROVIDER_REQUEST, frames.MAX_PROVIDER_REQUEST_PAYLOAD + 1),
        (frames.FrameKind.PROVIDER_REPLY, frames.MAX_PROVIDER_REPLY_PAYLOAD + 1),
        (frames.FrameKind.FINAL, frames.MAX_CONTROL_PAYLOAD + 1),
    ],
)
def test_reader_rejects_oversized_payload_before_allocation(
    kind: frames.FrameKind,
    payload_length: int,
) -> None:
    left, right = _socketpair()
    try:
        left.sendall(_raw_header(kind=int(kind), payload_length=payload_length))
        with pytest.raises(frames.FrameSizeError):
            frames.read_frame(right)
        assert right.fileno() == -1
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize(
    "header",
    [
        _raw_header(magic=b"BAD1"),
        _raw_header(kind=99),
        _raw_header(reserved=1),
        _raw_header(sequence=0),
    ],
)
def test_reader_rejects_invalid_header_and_closes_socket(header: bytes) -> None:
    left, right = _socketpair()
    try:
        left.sendall(header)
        with pytest.raises(frames.FrameProtocolError):
            frames.read_frame(right)
        assert right.fileno() == -1
    finally:
        left.close()
        right.close()


def test_timeout_is_typed_and_closes_socket() -> None:
    left, right = _socketpair()
    right.settimeout(0.02)
    try:
        with pytest.raises(frames.FrameTimeoutError):
            frames.read_frame(right)
        assert right.fileno() == -1
    finally:
        left.close()
        right.close()


def test_blocking_socket_requires_explicit_bounded_timeout() -> None:
    left, right = socket.socketpair()
    try:
        with pytest.raises(frames.FrameTimeoutError):
            frames.read_frame(right)
        assert right.fileno() == -1
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize(
    "timeout",
    [0, -1, float("inf"), 301],
)
def test_explicit_timeout_is_bounded(timeout: float) -> None:
    left, right = _socketpair()
    try:
        with pytest.raises((frames.FrameTimeoutError, frames.FrameTypeError)):
            frames.read_frame(right, timeout=timeout)
        assert right.fileno() == -1
    finally:
        left.close()
        right.close()


def test_write_rejects_bad_types_and_closes_socket() -> None:
    left, right = _socketpair()
    try:
        with pytest.raises(frames.FrameTypeError):
            frames.write_frame(left, frames.CONTROL_REQUEST, 1, "text")  # type: ignore[arg-type]
        assert left.fileno() == -1
    finally:
        left.close()
        right.close()


def test_write_rejects_oversized_payload_and_closes_socket() -> None:
    left, right = _socketpair()
    try:
        with pytest.raises(frames.FrameSizeError):
            frames.write_frame(
                left,
                frames.PROVIDER_REPLY,
                1,
                b"x" * (frames.MAX_PROVIDER_REPLY_PAYLOAD + 1),
            )
        assert left.fileno() == -1
    finally:
        left.close()
        right.close()


def test_write_accepts_explicit_timeout_on_blocking_socket() -> None:
    left, right = socket.socketpair()
    right.settimeout(1)
    try:
        frames.write_frame(
            left,
            frames.FINAL,
            1,
            b"done",
            timeout=1,
        )
        assert frames.read_frame(right) == frames.Frame(frames.FINAL, 1, b"done")
    finally:
        left.close()
        right.close()
