"""Bounded opaque binary frames for an owner-controlled native slot bridge.

The wire format is deliberately smaller than the protocol carried by a frame:
the payload is opaque bytes and this module never decodes or logs it.  A
caller owns the session binding, ordering, and replay checks.  In particular,
``sequence`` is range-checked but replay or monotonicity is not enforced here.

Every read and write requires a finite positive socket timeout, or an explicit
timeout of at most 300 seconds.  Protocol and I/O errors close the socket so a
caller cannot accidentally continue using a partially consumed connection.
"""

from __future__ import annotations

import math
import struct
import time
from contextlib import suppress
from dataclasses import dataclass
from enum import IntEnum
from typing import Final

MAGIC: Final[bytes] = b"DLS1"
HEADER_FORMAT: Final[str] = "!4sHHII"
HEADER_SIZE: Final[int] = struct.calcsize(HEADER_FORMAT)
MAX_SEQUENCE: Final[int] = (1 << 32) - 1
MAX_CONTROL_PAYLOAD: Final[int] = 262_144
MAX_PROVIDER_REQUEST_PAYLOAD: Final[int] = 262_144
MAX_PROVIDER_REPLY_PAYLOAD: Final[int] = 4 * 1024 * 1024
MAX_TIMEOUT_SECONDS: Final[float] = 300.0
RECV_CHUNK_BYTES: Final[int] = 64 * 1024


class FrameError(Exception):
    """Base class for all frame codec failures."""


class FrameTypeError(TypeError, FrameError):
    """An API argument or socket result has an invalid Python type."""


class FrameProtocolError(FrameError):
    """The wire header or frame kind violates the frame protocol."""


class FrameSizeError(FrameProtocolError):
    """A frame payload exceeds the limit for its kind."""


class FrameTruncatedError(EOFError, FrameError):
    """The peer closed the socket before a complete frame was received."""


class FrameTimeoutError(TimeoutError, FrameError):
    """The bounded frame operation exceeded its finite timeout."""


class FrameIOError(OSError, FrameError):
    """The socket raised an I/O error while reading or writing a frame."""


class FrameKind(IntEnum):
    CONTROL_REQUEST = 1
    CONTROL_REPLY = 2
    PROVIDER_REQUEST = 3
    PROVIDER_REPLY = 4
    FINAL = 5


CONTROL_REQUEST: Final[FrameKind] = FrameKind.CONTROL_REQUEST
CONTROL_REPLY: Final[FrameKind] = FrameKind.CONTROL_REPLY
PROVIDER_REQUEST: Final[FrameKind] = FrameKind.PROVIDER_REQUEST
PROVIDER_REPLY: Final[FrameKind] = FrameKind.PROVIDER_REPLY
FINAL: Final[FrameKind] = FrameKind.FINAL

_MAX_PAYLOAD_BY_KIND: Final[dict[FrameKind, int]] = {
    FrameKind.CONTROL_REQUEST: MAX_CONTROL_PAYLOAD,
    FrameKind.CONTROL_REPLY: MAX_CONTROL_PAYLOAD,
    FrameKind.PROVIDER_REQUEST: MAX_PROVIDER_REQUEST_PAYLOAD,
    FrameKind.PROVIDER_REPLY: MAX_PROVIDER_REPLY_PAYLOAD,
    FrameKind.FINAL: MAX_CONTROL_PAYLOAD,
}


@dataclass(frozen=True, slots=True)
class Frame:
    """A validated frame returned by :func:`read_frame`."""

    kind: FrameKind
    sequence: int
    payload: bytes


def _close_quietly(connection: object) -> None:
    close = getattr(connection, "close", None)
    if not callable(close):
        return
    with suppress(Exception):
        close()


def _validate_socket(connection: object, *, writable: bool) -> None:
    required = ["close", "gettimeout", "recv", "settimeout"]
    if writable:
        required.append("sendall")
    if any(not callable(getattr(connection, name, None)) for name in required):
        raise FrameTypeError("connection must provide the socket frame operations")


def _validate_timeout(value: object, *, source: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrameTypeError(f"{source} must be a finite positive number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise FrameTimeoutError(f"{source} must be in (0, {MAX_TIMEOUT_SECONDS:g}] seconds")
    return timeout


def _operation_timeout(connection: object, timeout: float | None) -> tuple[object, float]:
    try:
        original = connection.gettimeout()  # type: ignore[attr-defined]
    except Exception as error:
        raise FrameIOError("unable to read socket timeout") from error

    if timeout is not None:
        return original, _validate_timeout(timeout, source="timeout")
    if original is None:
        raise FrameTimeoutError(
            "socket timeout must be finite and positive or an explicit timeout is required"
        )
    return original, _validate_timeout(original, source="socket timeout")


def _restore_timeout(connection: object, original: object) -> None:
    try:
        connection.settimeout(original)  # type: ignore[attr-defined]
    except Exception as error:
        _close_quietly(connection)
        raise FrameIOError("unable to restore socket timeout") from error


def _normalize_kind(kind: object) -> FrameKind:
    if isinstance(kind, bool) or not isinstance(kind, (int, FrameKind)):
        raise FrameTypeError("kind must be a FrameKind or integer kind value")
    try:
        return FrameKind(kind)
    except ValueError as error:
        raise FrameProtocolError("unknown frame kind") from error


def _normalize_sequence(sequence: object) -> int:
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise FrameTypeError("sequence must be an integer")
    if not 1 <= sequence <= MAX_SEQUENCE:
        raise FrameProtocolError("sequence must be in 1..2^32-1")
    return sequence


def _validate_payload(kind: FrameKind, payload: object) -> bytes:
    if not isinstance(payload, bytes):
        raise FrameTypeError("payload must be bytes")
    maximum = _MAX_PAYLOAD_BY_KIND[kind]
    if len(payload) > maximum:
        raise FrameSizeError(f"payload exceeds the {maximum}-byte limit for {kind.name}")
    return payload


def _recv_exact(connection: object, size: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        seconds_left = deadline - time.monotonic()
        if seconds_left <= 0:
            raise FrameTimeoutError("frame read exceeded its timeout")
        try:
            connection.settimeout(seconds_left)  # type: ignore[attr-defined]
            chunk = connection.recv(min(remaining, RECV_CHUNK_BYTES))  # type: ignore[attr-defined]
        except TimeoutError as error:
            raise FrameTimeoutError("frame read timed out") from error
        except OSError as error:
            raise FrameIOError("frame read failed") from error
        if not isinstance(chunk, bytes):
            raise FrameTypeError("socket recv must return bytes")
        if not chunk:
            raise FrameTruncatedError("peer closed the socket before the frame was complete")
        if len(chunk) > remaining:
            raise FrameProtocolError("socket recv returned more bytes than requested")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(connection: object, *, timeout: float | None = None) -> Frame:
    """Read one bounded frame, closing ``connection`` on every failure.

    ``timeout`` is a total operation bound and must be in ``(0, 300]``.  When
    omitted, the connection must already have a finite positive socket
    timeout.  The original socket timeout is restored after a successful read.
    Sequence replay and ordering are intentionally left to the caller.
    """

    _validate_socket(connection, writable=False)
    original: object = None
    try:
        original, effective_timeout = _operation_timeout(connection, timeout)
        deadline = time.monotonic() + effective_timeout
        header = _recv_exact(connection, HEADER_SIZE, deadline)
        magic, raw_kind, reserved, sequence, payload_length = struct.unpack(HEADER_FORMAT, header)
        if magic != MAGIC:
            raise FrameProtocolError("invalid frame magic")
        if reserved != 0:
            raise FrameProtocolError("reserved header field must be zero")
        kind = _normalize_kind(raw_kind)
        if sequence == 0:
            raise FrameProtocolError("sequence must not be zero")
        maximum = _MAX_PAYLOAD_BY_KIND[kind]
        if payload_length > maximum:
            raise FrameSizeError(f"payload exceeds the {maximum}-byte limit for {kind.name}")
        payload = _recv_exact(connection, payload_length, deadline)
    except FrameError:
        _close_quietly(connection)
        raise
    except Exception as error:
        _close_quietly(connection)
        raise FrameIOError("frame read failed") from error
    _restore_timeout(connection, original)
    return Frame(kind=kind, sequence=sequence, payload=payload)


def write_frame(
    connection: object,
    kind: FrameKind | int,
    sequence: int,
    payload: bytes,
    *,
    timeout: float | None = None,
) -> None:
    """Write one bounded frame with one complete ``sendall`` operation.

    ``timeout`` has the same finite bound as :func:`read_frame`; when omitted,
    the connection must already have a finite positive socket timeout.  The
    original socket timeout is restored after a successful write.  Sequence
    replay and ordering are intentionally left to the caller.
    """

    _validate_socket(connection, writable=True)
    try:
        normalized_kind = _normalize_kind(kind)
        normalized_sequence = _normalize_sequence(sequence)
        normalized_payload = _validate_payload(normalized_kind, payload)
        original, effective_timeout = _operation_timeout(connection, timeout)
        if timeout is not None:
            connection.settimeout(effective_timeout)  # type: ignore[attr-defined]
        header = struct.pack(
            HEADER_FORMAT,
            MAGIC,
            int(normalized_kind),
            0,
            normalized_sequence,
            len(normalized_payload),
        )
        connection.sendall(header + normalized_payload)  # type: ignore[attr-defined]
    except FrameError:
        _close_quietly(connection)
        raise
    except TimeoutError as error:
        _close_quietly(connection)
        raise FrameTimeoutError("frame write timed out") from error
    except OSError as error:
        _close_quietly(connection)
        raise FrameIOError("frame write failed") from error
    except Exception as error:
        _close_quietly(connection)
        raise FrameIOError("frame write failed") from error
    _restore_timeout(connection, original)


__all__ = [
    "CONTROL_REPLY",
    "CONTROL_REQUEST",
    "FINAL",
    "HEADER_FORMAT",
    "HEADER_SIZE",
    "MAGIC",
    "MAX_CONTROL_PAYLOAD",
    "MAX_PROVIDER_REPLY_PAYLOAD",
    "MAX_PROVIDER_REQUEST_PAYLOAD",
    "MAX_SEQUENCE",
    "MAX_TIMEOUT_SECONDS",
    "PROVIDER_REPLY",
    "PROVIDER_REQUEST",
    "Frame",
    "FrameError",
    "FrameIOError",
    "FrameKind",
    "FrameProtocolError",
    "FrameSizeError",
    "FrameTimeoutError",
    "FrameTruncatedError",
    "FrameTypeError",
    "read_frame",
    "write_frame",
]
