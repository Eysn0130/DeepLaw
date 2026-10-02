"""Engineering-only, finite IPC around a private owner's Provider forwarder.

This module neither loads credentials nor implements Provider policy. A private,
owner-only Python entry calls ``serve_authority(forward)``; that callback may load
the existing owner authority inside the child and use the existing RequestGuard.
The parent sends the original Host body once and returns the original bounded
response in memory. Its receipts contain hashes and fixed diagnostics only.

The child is a separate local process, not an OS sandbox or native isolation
receipt. Process commitments below bind local Popen PIDs to a fresh challenge;
they are not independently observed process-start or executable identities.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from benchmarks.hosts import native_slot_frames as frames
from benchmarks.hosts.native_provider_bridge import (
    ProviderBridgeError,
    decode_provider_reply,
    encode_provider_reply,
)

SCHEMA_VERSION: Final = "deeplaw.owner-provider-authority-protocol/v1"
MODEL_PIN: Final = "deepseek/deepseek-flash"
PROFILES: Final = MappingProxyType({"fixed_probe": (1, 120.0), "maintenance": (6, 180.0)})
MAX_METADATA_BYTES: Final = 4096
MAX_ENTRY_BYTES: Final = 65536
CLEANUP_SECONDS: Final = 2.0
SOURCE_ROOT: Final = Path(__file__).resolve().parents[2]
_HASH: Final = re.compile(r"[0-9a-f]{64}")
_STAGES: Final = frozenset({
    "authority_start", "authority_protocol", "authority_forward", "authority_cleanup",
})
_CODES: Final = frozenset({
    "authority_failed", "authority_config_invalid", "authority_entry_invalid",
    "authority_platform_unsupported",
    "authority_start_failed", "authority_not_started", "authority_closed",
    "authority_reader_busy", "authority_deadline_exceeded", "authority_frame_timeout",
    "authority_frame_truncated", "authority_frame_bound", "authority_frame_invalid",
    "authority_pipe_failed", "authority_binding_invalid", "authority_sequence_invalid",
    "authority_request_bound", "authority_request_budget", "authority_response_invalid",
    "authority_callback_failed", "authority_child_failed", "authority_stop_unconfirmed",
    "authority_kill_failed", "authority_reap_failed", "authority_pipe_close_failed",
    "authority_group_unconfirmed",
})
Forward = Callable[[bytes, float], tuple[int, str, bytes]]


class AuthorityError(RuntimeError):
    """A closed diagnostic; exception text, paths and payloads never escape."""

    def __init__(self, code: str, stage: str = "authority_protocol") -> None:
        self.code = code if code in _CODES else "authority_failed"
        self.stage = stage if stage in _STAGES else "authority_protocol"
        super().__init__(self.code)

    def diagnostic(self) -> dict[str, str]:
        return {"stage": self.stage, "code": self.code}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise AuthorityError(code) from None


def _posix() -> None:
    _require(os.name == "posix" and hasattr(os, "getuid") and hasattr(os, "killpg"),
             "authority_platform_unsupported")


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _encode(value: Mapping[str, Any]) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    _require(len(raw) <= MAX_METADATA_BYTES, "authority_frame_bound")
    return raw


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        _require(key not in result, "authority_frame_invalid")
        result[key] = value
    return result


def _decode(raw: bytes) -> dict[str, Any]:
    _require(isinstance(raw, bytes) and len(raw) <= MAX_METADATA_BYTES, "authority_frame_bound")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: _require(False, "authority_frame_invalid"))
    except (ValueError, UnicodeError, RecursionError):
        raise AuthorityError("authority_frame_invalid") from None
    _require(type(value) is dict, "authority_frame_invalid")
    return value


def _seconds(value: object, maximum: float) -> float:
    _require(type(value) in {int, float}, "authority_config_invalid")
    _require(math.isfinite(value) and 0 < value <= maximum, "authority_config_invalid")
    return float(value)


def _remaining(deadline_ns: int) -> float:
    seconds = (deadline_ns - time.monotonic_ns()) / 1_000_000_000
    _require(seconds > 0, "authority_deadline_exceeded")
    return min(seconds, 180.0)


def _commit(role: str, pid: int, config: Mapping[str, Any]) -> str:
    return _hash(_encode({"role": role, "pid": pid, "nonce": config["nonce"],
                          "binding_sha256": config["binding_sha256"]}))


def _message(config: Mapping[str, Any], kind: str, **fields: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "message_kind": kind,
            "nonce": config["nonce"], "binding_sha256": config["binding_sha256"], **fields}


def _bound_message(value: Mapping[str, Any], config: Mapping[str, Any], kind: str,
                   extra_fields: set[str]) -> None:
    _require(set(value) == {"schema_version", "message_kind", "nonce", "binding_sha256"}
             | extra_fields, "authority_frame_invalid")
    _require(value["schema_version"] == SCHEMA_VERSION and value["message_kind"] == kind,
             "authority_frame_invalid")
    _require(value["nonce"] == config["nonce"]
             and value["binding_sha256"] == config["binding_sha256"],
             "authority_binding_invalid")


def _config(value: Mapping[str, Any]) -> dict[str, Any]:
    _require(set(value) == {
        "schema_version", "message_kind", "profile", "model_pin", "nonce",
        "execution_binding_sha256", "deadline_monotonic_ns", "binding_sha256",
    }, "authority_config_invalid")
    _require(value["schema_version"] == SCHEMA_VERSION
             and value["message_kind"] == "configure" and value["model_pin"] == MODEL_PIN,
             "authority_config_invalid")
    _require(type(value["profile"]) is str and value["profile"] in PROFILES,
             "authority_config_invalid")
    for field in ("nonce", "execution_binding_sha256", "binding_sha256"):
        _require(type(value[field]) is str and _HASH.fullmatch(value[field]) is not None,
                 "authority_binding_invalid")
    _require(type(value["deadline_monotonic_ns"]) is int
             and 0 < value["deadline_monotonic_ns"] < 2**63, "authority_config_invalid")
    _remaining(value["deadline_monotonic_ns"])
    _require((value["deadline_monotonic_ns"] - time.monotonic_ns()) / 1e9
             <= PROFILES[value["profile"]][1],
             "authority_config_invalid")
    unsigned = {key: item for key, item in value.items() if key != "binding_sha256"}
    _require(value["binding_sha256"] == _hash(_encode(unsigned)), "authority_binding_invalid")
    return dict(value)


class _PipeConnection:
    """Nonblocking pipes exposing only the existing finite socket-frame seam."""

    def __init__(self, reader: Any, writer: Any) -> None:
        _posix()
        self.reader, self.writer = reader, writer
        self.timeout = 10.0
        self.closed = False
        self.close_failed = False
        os.set_blocking(reader.fileno(), False)
        os.set_blocking(writer.fileno(), False)

    def gettimeout(self) -> float:
        return self.timeout

    def settimeout(self, seconds: float) -> None:
        self.timeout = _seconds(seconds, frames.MAX_TIMEOUT_SECONDS)

    def _ready(self, stream: Any, event: int, deadline: float) -> None:
        with selectors.DefaultSelector() as selector:
            selector.register(stream, event)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError from None

    def recv(self, size: int) -> bytes:
        deadline = time.monotonic() + self.timeout
        while True:
            self._ready(self.reader, selectors.EVENT_READ, deadline)
            try:
                return os.read(self.reader.fileno(), size)
            except BlockingIOError:
                continue

    def sendall(self, packet: bytes) -> None:
        deadline = time.monotonic() + self.timeout
        view = memoryview(packet)
        while view:
            self._ready(self.writer, selectors.EVENT_WRITE, deadline)
            try:
                size = os.write(self.writer.fileno(), view[:frames.RECV_CHUNK_BYTES])
            except BlockingIOError:
                continue
            if size <= 0:
                raise BrokenPipeError from None
            view = view[size:]

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        failed = False
        for stream in (self.reader, self.writer):
            try:
                stream.close()
            except Exception:
                failed = True
        if failed:
            self.close_failed = True
            raise AuthorityError("authority_pipe_close_failed", "authority_cleanup") from None


def _frame_error(error: BaseException) -> AuthorityError:
    kinds = ((frames.FrameTimeoutError, "authority_frame_timeout"),
             (frames.FrameTruncatedError, "authority_frame_truncated"),
             (frames.FrameSizeError, "authority_frame_bound"),
             (frames.FrameProtocolError, "authority_frame_invalid"))
    for kind, code in kinds:
        if isinstance(error, kind):
            return AuthorityError(code)
    return AuthorityError("authority_pipe_failed")


def _read(connection: _PipeConnection, deadline_ns: int) -> frames.Frame:
    try:
        frame = frames.read_frame(connection, timeout=_remaining(deadline_ns))
        _remaining(deadline_ns)
        return frame
    except frames.FrameError as error:
        raise _frame_error(error) from None


def _write(connection: _PipeConnection, kind: frames.FrameKind, sequence: int,
           payload: bytes, deadline_ns: int) -> None:
    try:
        frames.write_frame(connection, kind, sequence, payload, timeout=_remaining(deadline_ns))
        _remaining(deadline_ns)
    except frames.FrameError as error:
        raise _frame_error(error) from None


def _entry(path: Path, expected_sha256: str) -> Path:
    _posix()
    try:
        _require(isinstance(path, Path) and path.is_absolute(), "authority_entry_invalid")
        _require(type(expected_sha256) is str and _HASH.fullmatch(expected_sha256) is not None,
                 "authority_entry_invalid")
        _require(path.resolve(strict=True) == path, "authority_entry_invalid")
        details = path.stat()
        parent = path.parent.stat()
        _require(stat.S_ISREG(details.st_mode) and details.st_nlink == 1
                 and details.st_uid == os.getuid() and details.st_mode & 0o077 == 0
                 and 0 < details.st_size <= MAX_ENTRY_BYTES, "authority_entry_invalid")
        _require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid()
                 and parent.st_mode & 0o077 == 0, "authority_entry_invalid")
        with path.open("rb") as stream:
            raw = stream.read(MAX_ENTRY_BYTES + 1)
        _require(len(raw) == details.st_size and _hash(raw) == expected_sha256,
                 "authority_entry_invalid")
        return path
    except (OSError, ValueError, TypeError):
        raise AuthorityError("authority_entry_invalid", "authority_start") from None


class OwnerProviderAuthority:
    """One parent reader and one killable child, with two frozen budget profiles.

    ``entry_script`` is an exact private owner entry, never an arbitrary command
    or extra argv. The closed environment contains public PATH/PYTHONPATH only.
    No launch path, body, response, key, inherited environment or exception text
    is exported in a receipt. ``timeout_seconds`` may only shorten a profile.
    """

    def __init__(self, entry_script: Path, entry_sha256: str, *, profile: str,
                 execution_binding_sha256: str, timeout_seconds: float | None = None,
                 parent_role: str = "observer") -> None:
        _require(type(profile) is str and profile in PROFILES, "authority_config_invalid")
        _require(type(parent_role) is str and parent_role in {"observer", "runner"},
                 "authority_config_invalid")
        _require(type(execution_binding_sha256) is str
                 and _HASH.fullmatch(execution_binding_sha256) is not None,
                 "authority_binding_invalid")
        self.entry_script = _entry(entry_script, entry_sha256)
        self.entry_sha256 = entry_sha256
        self.profile = profile
        self.max_requests, maximum = PROFILES[profile]
        self.timeout = maximum if timeout_seconds is None else _seconds(timeout_seconds, maximum)
        self.execution_binding_sha256 = execution_binding_sha256
        self.parent_role = parent_role
        self.process: subprocess.Popen[bytes] | None = None
        self.connection: _PipeConnection | None = None
        self.config: dict[str, Any] = {}
        self.requests: list[dict[str, Any]] = []
        self.first_failure: dict[str, str] | None = None
        self.cleanup_failures: list[dict[str, str]] = []
        self.cleanup_confirmed: bool | None = None
        self.child_reaped = False
        self.authority_group_empty: bool | None = None
        self.authority_killed = False
        self._started = False
        self._closed = False
        self._process_cleanup_started = False
        self._operation = threading.Lock()
        self._evidence = threading.Lock()
        self._cleanup = threading.Lock()
        self._watchdog_stop = threading.Event()
        self._watchdog: threading.Thread | None = None

    def _record(self, error: AuthorityError, *, cleanup: bool = False) -> None:
        with self._evidence:
            if cleanup:
                if len(self.cleanup_failures) < 8:
                    self.cleanup_failures.append(error.diagnostic())
            elif self.first_failure is None:
                self.first_failure = error.diagnostic()

    def _failure(self, fallback: AuthorityError) -> AuthorityError:
        with self._evidence:
            if self.first_failure is not None:
                return AuthorityError(**self.first_failure)
        return fallback

    def _kill_and_reap(self) -> None:
        # This lock does not compete with the reader lock: the watchdog must be
        # able to interrupt the authority even while an exchange is in flight.
        with self._cleanup:
            self._closed = True
            process = self.process
            if process is not None and not self._process_cleanup_started:
                # A PID/PGID may be reused after this finite cleanup attempt.
                # Never signal or observe it again, even if group absence was
                # unknown. Preserve that gap instead of retrying a closed role.
                # An early abort with no Popen instance does not consume this.
                self._process_cleanup_started = True
                group_permission_ambiguous = False
                # The leader may already have exited while an inherited-pipe
                # descendant still performs a forward. Reaping only the leader
                # cannot establish that this new session group is gone.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    self.authority_killed = True
                except ProcessLookupError:
                    self.authority_group_empty = True
                except PermissionError:
                    # macOS may report EPERM for a group containing only its
                    # unreaped leader. This is not evidence of absence: force
                    # another group attempt and certify only after wait plus
                    # an actual ESRCH group observation below.
                    group_permission_ambiguous = True
                    with suppress(Exception):
                        os.killpg(process.pid, signal.SIGKILL)
                    with suppress(Exception):
                        process.kill()
                except Exception:
                    self._record(AuthorityError("authority_kill_failed", "authority_cleanup"),
                                 cleanup=True)
                    # A transient group-signal failure must not reduce cleanup
                    # to the leader alone. Preserve it and make one final group
                    # kill attempt before the individual-process fallback.
                    with suppress(Exception):
                        os.killpg(process.pid, signal.SIGKILL)
                    with suppress(Exception):
                        process.kill()
                try:
                    process.wait(timeout=CLEANUP_SECONDS)
                    self.child_reaped = True
                except Exception:
                    self._record(AuthorityError("authority_reap_failed", "authority_cleanup"),
                                 cleanup=True)
                    # Preserve that first cleanup failure, but make one final
                    # forced kill/reap attempt rather than leaving HTTPS alive.
                    with suppress(Exception):
                        process.kill()
                    try:
                        process.wait(timeout=CLEANUP_SECONDS)
                        self.child_reaped = True
                    except Exception:
                        self._record(
                            AuthorityError("authority_reap_failed", "authority_cleanup"),
                            cleanup=True,
                        )
                group_deadline = time.monotonic() + CLEANUP_SECONDS
                while self.authority_group_empty is not True:
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        self.authority_group_empty = True
                        break
                    except (PermissionError, InterruptedError):
                        # A dying group may temporarily lack a signalable live
                        # member. Continue bounded observation; do not certify
                        # cleanup from that permission/interrupt ambiguity.
                        pass
                    except Exception:
                        break
                    if time.monotonic() >= group_deadline:
                        break
                    time.sleep(min(0.01, max(0.0, group_deadline - time.monotonic())))
                if self.authority_group_empty is not True:
                    self.authority_group_empty = False
                    if group_permission_ambiguous:
                        self._record(
                            AuthorityError("authority_kill_failed", "authority_cleanup"),
                            cleanup=True,
                        )
                    self._record(
                        AuthorityError("authority_group_unconfirmed", "authority_cleanup"),
                        cleanup=True,
                    )
            if self.connection is not None:
                try:
                    self.connection.close()
                except Exception:
                    self._record(
                        AuthorityError("authority_pipe_close_failed", "authority_cleanup"),
                        cleanup=True,
                    )
                if self.connection.close_failed and not any(
                    item["code"] == "authority_pipe_close_failed" for item in self.cleanup_failures
                ):
                    self._record(
                        AuthorityError("authority_pipe_close_failed", "authority_cleanup"),
                        cleanup=True,
                    )
            elif process is not None:
                # A failure while configuring nonblocking pipes still owns the
                # freshly-created Popen streams and must close them after reap.
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            self._record(
                                AuthorityError("authority_pipe_close_failed", "authority_cleanup"),
                                cleanup=True,
                            )
            self.cleanup_confirmed = (self.child_reaped and self.authority_group_empty is True
                                      and not self.cleanup_failures)
            self._watchdog_stop.set()

    def _abort(self, error: AuthorityError) -> None:
        self._record(error)
        self._kill_and_reap()

    def _deadline_watchdog(self) -> None:
        wait = max(0.0, (self.config["deadline_monotonic_ns"] - time.monotonic_ns()) / 1e9)
        if not self._watchdog_stop.wait(wait):
            self._abort(AuthorityError("authority_deadline_exceeded", "authority_forward"))

    def start(self) -> OwnerProviderAuthority:
        if not self._operation.acquire(blocking=False):
            fixed = AuthorityError("authority_reader_busy")
            self._abort(fixed)
            raise self._failure(fixed) from None
        try:
            return self._start()
        except BaseException as error:
            fixed = error if isinstance(error, AuthorityError) else AuthorityError(
                "authority_start_failed", "authority_start")
            self._abort(fixed)
            raise self._failure(fixed) from None
        finally:
            self._operation.release()

    def _start(self) -> OwnerProviderAuthority:
        _require(not self._started and not self._closed, "authority_closed")
        self._started = True
        nonce = os.urandom(32).hex()
        unsigned = {"schema_version": SCHEMA_VERSION, "message_kind": "configure",
                    "profile": self.profile, "model_pin": MODEL_PIN, "nonce": nonce,
                    "execution_binding_sha256": self.execution_binding_sha256,
                    "deadline_monotonic_ns": time.monotonic_ns() + int(self.timeout * 1e9)}
        self.config = {**unsigned, "binding_sha256": _hash(_encode(unsigned))}
        try:
            # Recheck exact entry bytes immediately before launch; the caller
            # must keep this private source immutable throughout the instance.
            _entry(self.entry_script, self.entry_sha256)
            _require(not self._closed, "authority_closed")
            self.process = subprocess.Popen(
                [sys.executable, str(self.entry_script)], cwd=self.entry_script.parent,
                env={"PATH": os.defpath, "PYTHONPATH": str(SOURCE_ROOT)},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=0, close_fds=True, start_new_session=True,
            )
            # A concurrent abort can precede Popen's return. That early cleanup
            # had no process to own; now reap and close this late-created child
            # before installing a reader or permitting any private callback.
            _require(not self._closed, "authority_closed")
            self.connection = _PipeConnection(self.process.stdout, self.process.stdin)
            self._watchdog = threading.Thread(target=self._deadline_watchdog, daemon=True)
            self._watchdog.start()
            deadline = self.config["deadline_monotonic_ns"]
            _write(self.connection, frames.CONTROL_REQUEST, 1, _encode(self.config), deadline)
            frame = _read(self.connection, deadline)
            _require(frame.kind == frames.CONTROL_REPLY and frame.sequence == 1,
                     "authority_sequence_invalid")
            ready = _decode(frame.payload)
            _bound_message(ready, self.config, "ready", {"role", "process_identity_sha256",
                                                        "formal_admission"})
            _require(ready["role"] == "credential_authority"
                     and ready["formal_admission"] is False
                     and ready["process_identity_sha256"]
                     == _commit("credential_authority", self.process.pid, self.config),
                     "authority_binding_invalid")
            return self
        except BaseException as error:
            fixed = error if isinstance(error, AuthorityError) else AuthorityError(
                "authority_start_failed", "authority_start")
            self._abort(fixed)
            raise self._failure(fixed) from None

    def forward(self, body: bytes, timeout_seconds: float) -> tuple[int, str, bytes]:
        """The existing bridge callback seam; consume once before any possible send."""
        if not self._operation.acquire(blocking=False):
            fixed = AuthorityError("authority_reader_busy")
            self._abort(fixed)
            raise self._failure(fixed) from None
        record: dict[str, Any] | None = None
        try:
            _require(self._started and self.connection is not None, "authority_not_started")
            _require(not self._closed, "authority_closed")
            _require(isinstance(body, bytes) and 0 < len(body)
                     <= frames.MAX_PROVIDER_REQUEST_PAYLOAD, "authority_request_bound")
            seconds = _seconds(timeout_seconds, PROFILES[self.profile][1])
            deadline = min(self.config["deadline_monotonic_ns"],
                           time.monotonic_ns() + int(seconds * 1e9))
            _remaining(deadline)
            _require(len(self.requests) < self.max_requests, "authority_request_budget")
            sequence = len(self.requests) + 1
            record = {"sequence": sequence, "request_sha256": _hash(body),
                      "request_bytes": len(body), "response_state": "unknown",
                      "response_sha256": None, "response_bytes": None}
            self.requests.append(record)  # Consumed before even the dispatch write.
            dispatch = _message(self.config, "dispatch", request_sha256=record["request_sha256"],
                                request_bytes=len(body), deadline_monotonic_ns=deadline)
            _write(self.connection, frames.CONTROL_REQUEST, sequence + 1,
                   _encode(dispatch), deadline)
            _write(self.connection, frames.PROVIDER_REQUEST, sequence, body, deadline)
            frame = _read(self.connection, deadline)
            if frame.kind == frames.FINAL:
                failure = _decode(frame.payload)
                _bound_message(failure, self.config, "failure", {"diagnostic", "formal_admission"})
                _require(frame.sequence == sequence and failure["formal_admission"] is False,
                         "authority_sequence_invalid")
                diagnostic = failure["diagnostic"]
                _require(type(diagnostic) is dict and set(diagnostic) == {"stage", "code"}
                         and diagnostic["code"] in _CODES and diagnostic["stage"] in _STAGES,
                         "authority_frame_invalid")
                raise AuthorityError(**diagnostic) from None
            _require(frame.kind == frames.PROVIDER_REPLY and frame.sequence == sequence,
                     "authority_sequence_invalid")
            try:
                reply = decode_provider_reply(frame.payload)
            except ProviderBridgeError:
                raise AuthorityError("authority_response_invalid", "authority_forward") from None
            _remaining(deadline)
            record.update(response_state="complete", response_sha256=_hash(reply[2]),
                          response_bytes=len(reply[2]))
            return reply
        except BaseException as error:
            fixed = error if isinstance(error, AuthorityError) else AuthorityError(
                "authority_failed", "authority_forward")
            self._abort(fixed)
            raise self._failure(fixed) from None
        finally:
            self._operation.release()

    def close(self) -> dict[str, Any]:
        """Obtain a closed stop reply and exit 0, or force kill/reap with a fixed gap."""
        with self._operation:
            if not self._closed:
                try:
                    _require(self.connection is not None and self.process is not None,
                             "authority_not_started")
                    deadline = self.config["deadline_monotonic_ns"]
                    sequence = len(self.requests) + 2
                    _write(self.connection, frames.CONTROL_REQUEST, sequence,
                           _encode(_message(self.config, "stop")), deadline)
                    frame = _read(self.connection, deadline)
                    _require(frame.kind == frames.CONTROL_REPLY and frame.sequence == sequence,
                             "authority_stop_unconfirmed")
                    stopped = _decode(frame.payload)
                    _bound_message(stopped, self.config, "stopped",
                                   {"consumed_requests", "formal_admission"})
                    _require(stopped["consumed_requests"] == len(self.requests)
                             and type(stopped["consumed_requests"]) is int
                             and stopped["formal_admission"] is False,
                             "authority_stop_unconfirmed")
                    self.process.wait(timeout=min(CLEANUP_SECONDS, _remaining(deadline)))
                    _require(self.process.returncode == 0, "authority_child_failed")
                    self._watchdog_stop.set()
                except BaseException as error:
                    fixed = error if isinstance(error, AuthorityError) else AuthorityError(
                        "authority_stop_unconfirmed", "authority_cleanup")
                    self._record(fixed, cleanup=True)
                finally:
                    self._kill_and_reap()
            receipt = self.receipt()
            if self.cleanup_confirmed is not True:
                raise AuthorityError("authority_stop_unconfirmed", "authority_cleanup") from None
            return receipt

    def receipt(self) -> dict[str, Any]:
        """Metadata only; local process commitments do not establish native isolation."""
        with self._evidence:
            config = self.config
            actors = []
            if config and self.process is not None:
                actors = [{"role": self.parent_role,
                           "process_identity_sha256": _commit(
                               self.parent_role, os.getpid(), config)},
                          {"role": "credential_authority", "source_sha256": self.entry_sha256,
                           "process_identity_sha256": _commit(
                               "credential_authority", self.process.pid, config)}]
            return {"schema_version": SCHEMA_VERSION, "message_kind": "receipt",
                    "profile": self.profile, "model_pin": MODEL_PIN,
                    "execution_binding_sha256": self.execution_binding_sha256,
                    "binding_sha256": config.get("binding_sha256"),
                    "nonce_sha256": _hash(config["nonce"].encode()) if config else None,
                    "process_identity_source": "local_popen_pid_nonce_binding",
                    "actors": actors, "consumed_requests": len(self.requests),
                    "requests": [dict(item) for item in self.requests],
                    "first_failure": dict(self.first_failure) if self.first_failure else None,
                    "cleanup_failures": [dict(item) for item in self.cleanup_failures],
                    "cleanup_confirmed": self.cleanup_confirmed,
                    "child_reaped": self.child_reaped,
                    "authority_group_empty": self.authority_group_empty,
                    "child_exit_code": self.process.returncode if self.process else None,
                    "authority_killed": self.authority_killed, "formal_admission": False}

    def __enter__(self) -> OwnerProviderAuthority:
        return self.start()

    def __exit__(self, *_args: object) -> None:
        self.close()


def serve_authority(forward: Forward) -> dict[str, Any]:
    """Child entry seam; load owner credentials only inside the supplied callback.

    The private entry must exit immediately after this returns (0 when its
    ``first_failure`` is null, otherwise nonzero). Nothing here reads a key,
    owner file, environment, Host store or network. A callback failure is
    ambiguous and terminal, and is never retried. Parent deadlines forcibly
    end a callback blocked in open/read; the child alone cannot interrupt it.
    """
    _posix()
    _require(callable(forward), "authority_config_invalid")
    connection = _PipeConnection(sys.stdin.buffer, sys.stdout.buffer)
    startup_deadline = time.monotonic_ns() + 10_000_000_000
    config: dict[str, Any] = {}
    consumed = 0
    failure: dict[str, str] | None = None
    try:
        first = _read(connection, startup_deadline)
        _require(first.kind == frames.CONTROL_REQUEST and first.sequence == 1,
                 "authority_sequence_invalid")
        config = _config(_decode(first.payload))
        deadline = config["deadline_monotonic_ns"]
        ready = _message(config, "ready", role="credential_authority", formal_admission=False,
                         process_identity_sha256=_commit(
                             "credential_authority", os.getpid(), config))
        _write(connection, frames.CONTROL_REPLY, 1, _encode(ready), deadline)
        while True:
            control = _read(connection, deadline)
            expected = consumed + 1
            _require(control.kind == frames.CONTROL_REQUEST and control.sequence == expected + 1,
                     "authority_sequence_invalid")
            value = _decode(control.payload)
            if value.get("message_kind") == "stop":
                _bound_message(value, config, "stop", set())
                reply = _message(config, "stopped", consumed_requests=consumed,
                                 formal_admission=False)
                _write(connection, frames.CONTROL_REPLY, control.sequence, _encode(reply), deadline)
                break
            _bound_message(value, config, "dispatch", {
                "request_sha256", "request_bytes", "deadline_monotonic_ns",
            })
            _require(consumed < PROFILES[config["profile"]][0], "authority_request_budget")
            _require(type(value["request_sha256"]) is str
                     and _HASH.fullmatch(value["request_sha256"]) is not None,
                     "authority_binding_invalid")
            _require(type(value["request_bytes"]) is int
                     and 0 < value["request_bytes"] <= frames.MAX_PROVIDER_REQUEST_PAYLOAD,
                     "authority_request_bound")
            request_deadline = value["deadline_monotonic_ns"]
            _require(type(request_deadline) is int and 0 < request_deadline <= deadline,
                     "authority_config_invalid")
            body = _read(connection, request_deadline)
            _require(body.kind == frames.PROVIDER_REQUEST and body.sequence == expected,
                     "authority_sequence_invalid")
            _require(len(body.payload) == value["request_bytes"]
                     and _hash(body.payload) == value["request_sha256"],
                     "authority_binding_invalid")
            consumed += 1  # Before callback: neither failure nor ambiguous send can replay it.
            try:
                response = forward(body.payload, _remaining(request_deadline))
            except BaseException:
                raise AuthorityError("authority_callback_failed", "authority_forward") from None
            _remaining(request_deadline)
            try:
                _require(type(response) is tuple and len(response) == 3,
                         "authority_response_invalid")
                encoded = encode_provider_reply(*response)
            except (ProviderBridgeError, TypeError, ValueError):
                raise AuthorityError("authority_response_invalid", "authority_forward") from None
            _write(connection, frames.PROVIDER_REPLY, expected, encoded, request_deadline)
            # No retained raw-body history; the next iteration replaces references.
            del body, response, encoded
    except BaseException as error:
        fixed = error if isinstance(error, AuthorityError) else AuthorityError("authority_failed")
        failure = fixed.diagnostic()
        if config:
            with suppress(Exception):
                reply = _message(config, "failure", diagnostic=failure, formal_admission=False)
                _write(connection, frames.FINAL, max(1, consumed), _encode(reply),
                       config["deadline_monotonic_ns"])
    finally:
        with suppress(Exception):
            connection.close()
    return {"formal_admission": False, "consumed_requests": consumed, "first_failure": failure}
