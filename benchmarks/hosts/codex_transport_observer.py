"""Bounded development observations around the existing Codex JSONL client.

This module is deliberately a thin subclass/factory.  The production client
continues to own framing, response matching, turn correlation, and cleanup;
the observer retains only bounded method labels, sizes, and digests.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import threading
from collections.abc import Mapping
from typing import Any

from benchmarks.hosts.codex_app_server_client import CodexAppServerClient, CodexAppServerError

SCHEMA_VERSION = "deeplaw.codex-transport-observation/v1"
EVIDENCE_CLASS = "development_transport_observation"
_MAX_RECORDS = 256
_MAX_FRAME_BYTES = 128 * 1024
_MAX_METHOD_BYTES = 128
_MAX_WAIT_SECONDS = 5.0
_MAX_IDENTITIES = 32
_MAX_IDENTITY_BYTES = 4096
_METHOD_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./:-"
)

# This is a closed label set, not an execution permission set.  It covers the
# public app-server vocabulary used by the existing client and the native
# notifications that may be observed while its requests are in flight.
# Native labels reference OpenAI Codex commit 3d2ee51ca2d5db578f328aa75e20aa22c0197c9a,
# codex-rs/app-server-protocol/schema/typescript/ServerNotification.ts
# (SHA256 dfd31c72d1319f069fcdf124bcae6368f15aa0dd0033350bf15519d3e3556d54).
# This API-label reference copies no upstream Rust implementation or runtime.
_ALLOWED_METHODS = frozenset(
    {
        "initialize",
        "initialized",
        "hooks/list",
        "config/batchWrite",
        "configWarning",
        "model/list",
        "mcpServerStatus/list",
        "thread/start",
        "thread/started",
        "thread/resume",
        "thread/fork",
        "thread/delete",
        "thread/deleted",
        "thread/status/changed",
        "thread/tokenUsage/updated",
        "thread/compact/start",
        "thread/compacted",
        "turn/start",
        "turn/started",
        "turn/updated",
        "turn/completed",
        "item/started",
        "item/completed",
        "item/agentMessage/delta",
        "item/tool/call",
        "mcpServer/startupStatus/updated",
        "hook/completed",
        "hook/started",
        "error",
        "thread/archived",
        "thread/unarchived",
        "thread/closed",
        "thread/reverted",
        "skills/changed",
        "thread/name/updated",
        "thread/goal/updated",
        "thread/goal/cleared",
        "thread/queue/changed",
        "project/changed",
        "thread/project/updated",
        "thread/environment/connected",
        "thread/environment/disconnected",
        "thread/settings/updated",
        "turn/diff/updated",
        "turn/plan/updated",
        "item/autoApprovalReview/started",
        "item/autoApprovalReview/completed",
        "autoApprovalReview/strictReviewRequired",
        "rawResponseItem/completed",
        "rawResponse/completed",
        "item/plan/delta",
        "command/exec/outputDelta",
        "process/outputDelta",
        "process/exited",
        "item/commandExecution/outputDelta",
        "item/commandExecution/terminalInteraction",
        "item/fileChange/outputDelta",
        "item/fileChange/patchUpdated",
        "serverRequest/resolved",
        "item/mcpToolCall/progress",
        "mcpServer/oauthLogin/completed",
        "mcpServer/event/stream/notification",
        "account/updated",
        "account/rateLimits/updated",
        "app/list/updated",
        "remoteControl/status/changed",
        "externalAgentConfig/import/progress",
        "externalAgentConfig/import/completed",
        "fs/changed",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/textDelta",
        "model/rerouted",
        "model/verification",
        "modelProvider/authRecoveryStarted",
        "modelProvider/authRecoveryCompleted",
        "turn/moderationMetadata",
        "model/safetyBuffering/updated",
        "warning",
        "guardianWarning",
        "deprecationNotice",
        "fuzzyFileSearch/sessionUpdated",
        "fuzzyFileSearch/sessionCompleted",
        "windows/worldWritableWarning",
        "windowsSandbox/setupCompleted",
        "account/login/completed",
        "thread/realtime/started",
        "thread/realtime/itemAdded",
        "thread/realtime/item/started",
        "thread/realtime/item/transcript/delta",
        "thread/realtime/item/completed",
        "thread/realtime/transcript/delta",
        "thread/realtime/transcript/done",
        "thread/realtime/outputAudio/delta",
        "thread/realtime/sdp",
        "thread/realtime/error",
        "thread/realtime/closed",
    }
)
_INTERNAL_METHOD_LABELS = frozenset({"notification", "response"})
_UNSUPPORTED_IDENTITY_METHODS = frozenset({"thread/resume", "thread/fork"})
_PUBLIC_RPC_METHODS = ("model/list", "turn/start")


class ObservedTransportError(RuntimeError):
    """A bounded transport observation or lifecycle request was rejected."""


def _safe_method(value: Any, *, fallback: str | None = None) -> str:
    if not isinstance(value, str) or not value:
        if fallback is not None:
            return fallback
        raise ObservedTransportError("native method label is invalid")
    encoded = value.encode("utf-8")
    if (
        len(encoded) > _MAX_METHOD_BYTES
        or any(char not in _METHOD_CHARS for char in value)
        or (value not in _ALLOWED_METHODS and value not in _INTERNAL_METHOD_LABELS)
    ):
        raise ObservedTransportError("native method label is unsafe")
    return value


def _canonical_json(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as error:
        raise ObservedTransportError("wire message cannot be canonically observed") from error
    if len(encoded) > _MAX_FRAME_BYTES:
        raise ObservedTransportError("wire observation exceeds its byte bound")
    return encoded


def _wire_bytes(message: Mapping[str, Any]) -> bytes:
    payload = {key: value for key, value in message.items() if key != "jsonrpc"}
    return _canonical_json(payload) + b"\n"


def _message_fingerprint(message: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(message)).hexdigest()


def _digest_record(*, method: str, payload: bytes) -> dict[str, Any]:
    if len(payload) > _MAX_FRAME_BYTES:
        raise ObservedTransportError("wire observation exceeds its byte bound")
    return {
        "method": _safe_method(method),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _identity_digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ObservedTransportError(f"{label} identity is malformed")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as error:
        raise ObservedTransportError(f"{label} identity is malformed") from error
    if len(encoded) > _MAX_IDENTITY_BYTES:
        raise ObservedTransportError(f"{label} identity exceeds its byte bound")
    return hashlib.sha256(encoded).hexdigest()


def _thread_record(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    thread = value.get("thread")
    return thread if isinstance(thread, Mapping) else None


def make_observed_client_class(real_base: type[Any]) -> type[Any]:
    """Build a thin observer subclass around one explicit real client class."""

    required = ("_send_message", "_decode_message", "_handle_notification")
    if not isinstance(real_base, type) or any(
        not callable(getattr(real_base, name, None)) for name in required
    ):
        raise TypeError("real_base must expose the existing client transport hooks")

    class ObservedCodexClient(real_base):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._observed_lock = threading.Lock()
            self._observed_outbound: list[dict[str, Any]] = []
            self._observed_inbound: list[dict[str, Any]] = []
            self._observed_pending_notifications: list[
                tuple[str, dict[str, Any]]
            ] = []
            self._observed_native_events: list[dict[str, Any]] = []
            self._observed_close_called = False
            self._observed_status = "running"
            self._observed_failed = False
            self._observed_close: dict[str, Any] | None = None
            self._observed_public_rpc_counts = dict.fromkeys(_PUBLIC_RPC_METHODS, 0)
            self._observed_thread_identities: list[dict[str, str]] = []
            self._observed_session_hashes: list[str] = []

        def _mark_observation_failed(self) -> None:
            with self._observed_lock:
                self._observed_failed = True
                self._observed_status = "failed"

        def _append_record(
            self, target: list[dict[str, Any]], record: dict[str, Any]
        ) -> None:
            with self._observed_lock:
                if len(target) >= _MAX_RECORDS:
                    raise ObservedTransportError("wire observation record limit exceeded")
                target.append(record)

        def _send_message(self, message: Mapping[str, Any]) -> None:
            try:
                method = _safe_method(message.get("method"), fallback="notification")
                if method in _UNSUPPORTED_IDENTITY_METHODS:
                    raise ObservedTransportError(
                        "transport identity observation does not cover resume or fork"
                    )
                payload = _wire_bytes(message)
                with self._observed_lock:
                    if len(self._observed_outbound) >= _MAX_RECORDS:
                        raise ObservedTransportError(
                            "wire observation record limit exceeded"
                        )
                super()._send_message(message)
                self._append_record(
                    self._observed_outbound,
                    _digest_record(method=method, payload=payload),
                )
                # The existing client's request IDs are positive integers.
                # A same-named notification is not an RPC request.
                if (
                    method in self._observed_public_rpc_counts
                    and type(message.get("id")) is int
                    and message["id"] > 0
                ):
                    with self._observed_lock:
                        self._observed_public_rpc_counts[method] += 1
            except (ObservedTransportError, CodexAppServerError):
                self._mark_observation_failed()
                raise

        def _decode_message(self, raw_line: bytes) -> dict[str, Any]:
            try:
                if not isinstance(raw_line, bytes) or len(raw_line) + 1 > _MAX_FRAME_BYTES:
                    raise ObservedTransportError("wire observation exceeds its byte bound")
                value = super()._decode_message(raw_line)
                method = _safe_method(value.get("method"), fallback="response")
                record = _digest_record(method=method, payload=raw_line + b"\n")
                with self._observed_lock:
                    if len(self._observed_inbound) >= _MAX_RECORDS:
                        raise ObservedTransportError(
                            "wire observation record limit exceeded"
                        )
                    self._observed_inbound.append(record)
                    if "method" in value and "id" not in value:
                        if len(self._observed_pending_notifications) >= _MAX_RECORDS:
                            raise ObservedTransportError(
                                "wire observation record limit exceeded"
                            )
                        self._observed_pending_notifications.append(
                            (_message_fingerprint(value), dict(record))
                        )
                return value
            except (ObservedTransportError, CodexAppServerError):
                self._mark_observation_failed()
                raise

        def _handle_notification(self, message: Mapping[str, Any]) -> Any:
            try:
                _safe_method(message.get("method"))
                pending_before = len(getattr(self, "_turn_pending_notifications", ()))
                result = super()._handle_notification(message)
                pending_after = len(getattr(self, "_turn_pending_notifications", ()))
                if pending_after > pending_before:
                    return result
                fingerprint = _message_fingerprint(message)
                with self._observed_lock:
                    pending_index = next(
                        (
                            index
                            for index, (candidate, _record) in enumerate(
                                self._observed_pending_notifications
                            )
                            if candidate == fingerprint
                        ),
                        None,
                    )
                    if pending_index is None:
                        raise ObservedTransportError(
                            "native event lacked a decoded wire frame"
                        )
                    _candidate, record = self._observed_pending_notifications.pop(
                        pending_index
                    )
                    if len(self._observed_native_events) >= _MAX_RECORDS:
                        raise ObservedTransportError(
                            "wire observation record limit exceeded"
                        )
                    self._observed_native_events.append(record)
                return result
            except (ObservedTransportError, CodexAppServerError):
                self._mark_observation_failed()
                raise

        def thread_start(self, *args: Any, **kwargs: Any) -> Any:
            try:
                with self._observed_lock:
                    if len(self._observed_thread_identities) >= _MAX_IDENTITIES:
                        raise ObservedTransportError(
                            "thread identity record limit exceeded"
                        )
                result = super().thread_start(*args, **kwargs)
                thread = _thread_record(result)
                if thread is None:
                    raise ObservedTransportError("thread/start identity is malformed")
                thread_hash = _identity_digest(thread.get("id"), label="thread")
                session_hash = _identity_digest(thread.get("sessionId"), label="session")
                record = {
                    "method": "thread/start",
                    "thread_id_sha256": thread_hash,
                    "session_id_sha256": session_hash,
                }
                with self._observed_lock:
                    self._observed_thread_identities.append(record)
                    self._observed_session_hashes.append(session_hash)
                return result
            except (ObservedTransportError, CodexAppServerError):
                self._mark_observation_failed()
                raise

        start_thread = thread_start

        @property
        def transport_observation(self) -> dict[str, Any]:
            with self._observed_lock:
                session_identity = (
                    hashlib.sha256(_canonical_json(self._observed_session_hashes)).hexdigest()
                    if self._observed_session_hashes
                    else None
                )
                return {
                    "schema_version": SCHEMA_VERSION,
                    "evidence_class": EVIDENCE_CLASS,
                    "formal_admission": False,
                    "claim_eligible": False,
                    "status": self._observed_status,
                    "identity_scope": "thread_start_only",
                    "activity_observation": {
                        "public_rpc_counts": dict(self._observed_public_rpc_counts),
                        "internal_counters": {
                            "model_inventory_count": {
                                "status": "not_executed",
                                "value": None,
                            },
                            "model_invocation_count": {
                                "status": "not_executed",
                                "value": None,
                            },
                            "sampling_count": {
                                "status": "not_executed",
                                "value": None,
                            },
                        },
                    },
                    "counts": {
                        "outbound": len(self._observed_outbound),
                        "inbound": len(self._observed_inbound),
                        "native_events": len(self._observed_native_events),
                    },
                    "outbound": [dict(record) for record in self._observed_outbound],
                    "inbound": [dict(record) for record in self._observed_inbound],
                    "native_events": [
                        dict(record) for record in self._observed_native_events
                    ],
                    "thread_identities": [
                        dict(record) for record in self._observed_thread_identities
                    ],
                    "session_identity_sha256": session_identity,
                    "close": (
                        dict(self._observed_close)
                        if self._observed_close is not None
                        else None
                    ),
                }

        def graceful_close(self, *, timeout_seconds: float = 1.0) -> dict[str, Any]:
            """Close stdin, wait boundedly, then always invoke base cleanup."""

            if (
                type(timeout_seconds) not in (int, float)
                or timeout_seconds <= 0
                or timeout_seconds > _MAX_WAIT_SECONDS
                or not math.isfinite(timeout_seconds)
            ):
                raise ObservedTransportError("graceful close timeout is outside its bound")
            with self._observed_lock:
                if self._observed_close_called:
                    raise ObservedTransportError("graceful close was already requested")
                self._observed_close_called = True
                prior_observation_failure = self._observed_failed
                self._observed_status = (
                    "failed" if prior_observation_failure else "closing"
                )

            process = getattr(self, "_process", None)
            stdin_closed = False
            wait_state = "not_started"
            failure: str | None = None
            pre_close_returncode = process.poll() if process is not None else None
            if process is None:
                failure = "child_process_unavailable"
            elif pre_close_returncode is not None:
                failure = "child_already_exited"
                wait_state = "already_exited"
            else:
                stream = process.stdin
                if stream is None:
                    failure = "stdin_unavailable"
                else:
                    try:
                        stream.close()
                        stdin_closed = True
                    except OSError:
                        failure = "stdin_close_failed"
                    if failure is None:
                        try:
                            process.wait(timeout=float(timeout_seconds))
                            wait_state = "exited"
                        except subprocess.TimeoutExpired:
                            failure = "wait_timeout"
                            wait_state = "timeout"
                        except OSError:
                            failure = "wait_failed"
                            wait_state = "failed"

            cleanup_fallback = False
            cleanup_failure = False
            if process is not None:
                cleanup_fallback = True
                try:
                    super().close()
                except Exception:
                    cleanup_failure = True
                    if failure is None:
                        failure = "cleanup_failed"
            returncode = process.returncode if process is not None else None
            status = (
                "graceful"
                if (
                    not prior_observation_failure
                    and failure is None
                    and stdin_closed
                    and returncode == 0
                )
                else "failed"
            )
            if status == "failed" and failure is None:
                failure = (
                    "observation_failed"
                    if prior_observation_failure
                    else "child_nonzero_exit"
                )
            close = {
                "status": status,
                "stdin_closed": stdin_closed,
                "wait": wait_state,
                "child_returncode": returncode,
                "cleanup_fallback": cleanup_fallback,
                "cleanup_failed": cleanup_failure,
            }
            if failure is not None:
                close["failure"] = failure
            with self._observed_lock:
                self._observed_close = close
                self._observed_status = status
            return self.transport_observation

    ObservedCodexClient.__name__ = "ObservedCodexClient"
    return ObservedCodexClient


ObservedCodexClient = make_observed_client_class(CodexAppServerClient)


__all__ = [
    "EVIDENCE_CLASS",
    "SCHEMA_VERSION",
    "ObservedCodexClient",
    "ObservedTransportError",
    "make_observed_client_class",
]
