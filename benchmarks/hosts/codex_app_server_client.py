"""Small, bounded Codex App Server JSON-RPC client for benchmark fixtures.

This module deliberately owns no model runtime.  A caller supplies the app-server
command and a closed environment; the client only speaks the line-delimited
JSON protocol and keeps a minimal, hashed event projection in memory.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, TypeAlias

UNREPORTED = "unreported"
_JSON_VALUE: TypeAlias = dict[str, Any] | list[Any] | str | int | float | bool | None


class CodexAppServerError(RuntimeError):
    """Base error raised by the bounded benchmark client."""


class CodexAppServerProtocolError(CodexAppServerError):
    """The child emitted an invalid or unsupported protocol message."""


class CodexAppServerTimeoutError(CodexAppServerError):
    """The child did not produce the expected response before the deadline."""


class CodexAppServerOutputLimitError(CodexAppServerError):
    """The child exceeded one of the hard output byte limits."""


AppServerError = CodexAppServerError
ProtocolError = CodexAppServerProtocolError
TimeoutError = CodexAppServerTimeoutError
OutputLimitError = CodexAppServerOutputLimitError


DynamicToolHandler: TypeAlias = Callable[..., Mapping[str, Any]]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _canonical_bytes(value: Any) -> bytes:
    """Return deterministic bytes for hashing without retaining the value."""

    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        # A malformed provider payload is still represented by a deterministic
        # digest.  ``repr`` is never placed in an event or error message.
        return type(value).__name__.encode("utf-8")


def _hash_record(value: Any) -> tuple[str, int]:
    encoded = _canonical_bytes(value)
    return _sha256_bytes(encoded), len(encoded)


def _copy_usage(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value.get(key, UNREPORTED) for key in _USAGE_KEYS}


_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)

# Keep inventory requests bounded even when a caller forwards a server-side
# page-size option.  The benchmark client must not become an unbounded
# provider inventory sink.
_MAX_MCP_SERVER_STATUS_LIMIT = 1000


def _empty_usage() -> dict[str, Any]:
    return {key: UNREPORTED for key in _USAGE_KEYS}


def _value_from_keys(value: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            candidate = value[key]
            if isinstance(candidate, bool):
                return None
            if isinstance(candidate, int):
                return candidate
            # The generated schema uses integer counts.  Do not silently
            # coerce floats or strings, because that could turn malformed or
            # missing usage into a misleading number.
    return None


def normalize_token_usage(last: Any) -> dict[str, Any]:
    """Normalize the app-server ``tokenUsage.last`` object.

    Missing fields remain the literal ``"unreported"`` sentinel.  Cached input
    is a component of input usage in the app-server protocol and is therefore
    not added a second time when deriving ``total_tokens``.
    """

    result = _empty_usage()
    if not isinstance(last, Mapping):
        return result
    input_tokens = _value_from_keys(last, "inputTokens", "input_tokens", "input")
    cached_tokens = _value_from_keys(
        last, "cachedInputTokens", "cached_input_tokens", "cachedInput", "cached_input"
    )
    cache_write_tokens = _value_from_keys(
        last, "cacheWriteInputTokens", "cache_write_input_tokens"
    )
    output_tokens = _value_from_keys(last, "outputTokens", "output_tokens", "output")
    reasoning_tokens = _value_from_keys(
        last,
        "reasoningOutputTokens",
        "reasoning_output_tokens",
        "reasoningTokens",
        "reasoning_output",
    )
    explicit_total = _value_from_keys(last, "totalTokens", "total_tokens", "total")
    if input_tokens is not None:
        result["input_tokens"] = input_tokens
    if cached_tokens is not None:
        result["cached_input_tokens"] = cached_tokens
    if cache_write_tokens is not None:
        result["cache_write_input_tokens"] = cache_write_tokens
    if output_tokens is not None:
        result["output_tokens"] = output_tokens
    if reasoning_tokens is not None:
        result["reasoning_output_tokens"] = reasoning_tokens
    if explicit_total is not None:
        result["total_tokens"] = explicit_total
    elif input_tokens is not None and output_tokens is not None:
        # The protocol's input count already includes cached input.  Derive a
        # total only when both required components are actually reported.
        result["total_tokens"] = input_tokens + output_tokens
    return result


def _find_value(value: Any, *keys: str) -> Any:
    """Find a shallow protocol field across common generated-schema shapes."""

    if not isinstance(value, Mapping):
        return None
    for key in keys:
        if key in value:
            return value[key]
    return None


def _safe_label(value: Any) -> str | None:
    """Keep a bounded protocol label while excluding path-like values."""

    if not isinstance(value, str) or not value or len(value) > 200:
        return None
    if value.startswith(("/", "\\")) or (len(value) >= 3 and value[1] == ":" and value[2] in "/\\"):
        return "disallowed"
    if "\x00" in value or "\n" in value or "\r" in value:
        return "disallowed"
    return value


def _thread_or_turn_id(params: Mapping[str, Any], *keys: str) -> str | None:
    value = _find_value(params, *keys)
    if isinstance(value, str) and value:
        return value
    for nested_key in ("thread", "turn", "item"):
        nested = params.get(nested_key)
        value = _find_value(nested, *keys)
        if isinstance(value, str) and value:
            return value
    return None


def _thread_id_from_response(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key in ("threadId", "thread_id", "id"):
            candidate = value.get(key)
            if (
                isinstance(candidate, str)
                and candidate
                and (key != "id" or "turnId" not in value)
            ):
                # A response from thread/fork/start may use ``id`` for the
                # thread itself; ``turn`` is handled below for turn/start.
                return candidate
        for nested_key in ("thread", "result"):
            candidate = _thread_id_from_response(value.get(nested_key))
            if candidate:
                return candidate
    return None


def _turn_id_from_response(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key in ("turnId", "turn_id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        turn = value.get("turn")
        if isinstance(turn, Mapping):
            candidate = _turn_id_from_response(turn)
            if candidate:
                return candidate
        # Some fixtures return ``{"id": ..., "status": ...}`` for turn/start.
        candidate = value.get("id")
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


class TurnResult(dict[str, Any]):
    """Dictionary result returned by :meth:`CodexAppServerClient.turn_start`.

    Attribute access is provided for small benchmark adapters while keeping a
    plain mapping convenient for JSON assertions.
    """

    @property
    def thread_id(self) -> str | None:
        return self.get("thread_id")

    @property
    def turn_id(self) -> str | None:
        return self.get("turn_id")

    @property
    def final_text(self) -> str:
        return self.get("final_text", "")

    @property
    def final_agent_text(self) -> str:
        return self.get("final_agent_text", self.final_text)

    @property
    def tool_outputs(self) -> list[Any]:
        return list(self.get("tool_outputs", []))

    @property
    def usage(self) -> dict[str, Any]:
        return dict(self.get("usage", _empty_usage()))

    @property
    def tool_call_observations(self) -> list[dict[str, Any]]:
        """Return a defensive copy of safe, per-call tool observations."""

        return [
            dict(observation)
            for observation in self.get("tool_call_observations", [])
            if isinstance(observation, Mapping)
        ]


class CodexAppServerClient:
    """Bounded JSONL client for a caller-supplied Codex App Server fixture.

    ``environment`` is intentionally not optional from a process-inheritance
    perspective: ``None`` means an empty environment, never ``os.environ``.
    """

    def __init__(
        self,
        command: Sequence[str],
        environment: Mapping[str, str] | None = None,
        *,
        cwd: str | Path | None = None,
        timeout_seconds: float = 30.0,
        max_output_bytes: int = 1024 * 1024,
        max_stdout_bytes: int | None = None,
        max_stderr_bytes: int | None = None,
        client_name: str = "deeplaw-benchmark",
        client_title: str = "DeepLaw benchmark Codex App Server client",
        client_version: str = "0",
        dynamic_tools: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
        dynamic_tool_handler: DynamicToolHandler | Mapping[str, Callable[..., Any]] | None = None,
        tool_handler: DynamicToolHandler | Mapping[str, Callable[..., Any]] | None = None,
        forbidden_output_values: Sequence[str] = (),
    ) -> None:
        if not command or any(
            not isinstance(argument, str) or not argument for argument in command
        ):
            raise ValueError("command must be a non-empty sequence of strings")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        if max_stdout_bytes is not None and max_stdout_bytes <= 0:
            raise ValueError("max_stdout_bytes must be positive")
        if max_stderr_bytes is not None and max_stderr_bytes <= 0:
            raise ValueError("max_stderr_bytes must be positive")
        if dynamic_tool_handler is not None and tool_handler is not None:
            raise ValueError("provide only one dynamic tool handler")
        self.command = tuple(command)
        self.environment = dict(environment or {})
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.environment.items()
        ):
            raise ValueError("environment must contain string keys and values")
        self.cwd = cwd
        self.timeout_seconds = float(timeout_seconds)
        self.max_stdout_bytes = max_stdout_bytes or max_output_bytes
        self.max_stderr_bytes = max_stderr_bytes or max_output_bytes
        self.max_output_bytes = max_output_bytes
        self.client_name = client_name
        self.client_title = client_title
        self.client_version = client_version
        self.dynamic_tools = dynamic_tools
        self.dynamic_tool_handler = (
            dynamic_tool_handler if dynamic_tool_handler is not None else tool_handler
        )
        self._forbidden_output_values = tuple(
            value.encode("utf-8")
            for value in forbidden_output_values
            if isinstance(value, str) and value
        )
        self._leak_scan_tails = {"stdout": b"", "stderr": b""}
        self._secret_leak = False

        self._process: subprocess.Popen[bytes] | None = None
        self._output_queue_max_chunks = max(
            2,
            (self.max_output_bytes + 4095) // 4096 + 2,
        )
        self._output_queue: queue.Queue[
            tuple[str, bytes | None, BaseException | None]
        ] = queue.Queue(maxsize=self._output_queue_max_chunks)
        self._reader_threads: list[threading.Thread] = []
        self._closed = False
        self._initialized = False
        self._next_request_id = 1
        self._stdout_buffer = bytearray()
        self._stdout_bytes = 0
        self._stderr_digest = hashlib.sha256()
        self._stderr_bytes = 0
        self._events: list[dict[str, Any]] = []
        self._usage_by_key: dict[tuple[str | None, str | None], dict[str, Any]] = {}
        self._latest_usage = _empty_usage()
        self._active_thread_id: str | None = None
        self._active_turn_id: str | None = None
        self._final_text_parts: list[str] = []
        self._completed_item_text: str | None = None
        self._tool_outputs: list[Any] = []
        self._tool_call_observations: list[dict[str, Any]] = []
        self._compacted_notification_keys: set[tuple[str, str]] = set()

    @property
    def process_id(self) -> int | None:
        process = self._process
        if process is None or process.poll() is not None:
            return None
        pid = process.pid
        return pid if isinstance(pid, int) and pid > 0 else None

    @property
    def pid(self) -> int | None:
        return self.process_id

    @property
    def sanitized_events(self) -> list[dict[str, Any]]:
        # Events contain only scalar values and one small usage mapping.  Copy
        # nested mappings so caller mutation cannot alter the projection.
        return [
            {
                key: dict(value) if key == "usage" and isinstance(value, Mapping) else value
                for key, value in event.items()
            }
            for event in self._events
        ]

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.sanitized_events

    @property
    def stderr_metadata(self) -> dict[str, Any]:
        self._drain_available_stderr()
        return {"sha256": self._stderr_digest.hexdigest(), "bytes": self._stderr_bytes}

    @property
    def stderr(self) -> dict[str, Any]:
        return self.stderr_metadata

    @property
    def secret_leak(self) -> bool:
        self._drain_available_stderr()
        return self._secret_leak

    @property
    def usage(self) -> dict[str, Any]:
        return dict(self._latest_usage)

    @property
    def last_usage(self) -> dict[str, Any]:
        return self.usage

    def usage_for(self, thread_id: str | None = None, turn_id: str | None = None) -> dict[str, Any]:
        if thread_id is None:
            thread_id = self._active_thread_id
        if turn_id is None:
            turn_id = self._active_turn_id
        return dict(self._usage_by_key.get((thread_id, turn_id), _empty_usage()))

    def __enter__(self) -> CodexAppServerClient:
        self.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()

    def start(self) -> CodexAppServerClient:
        if self._closed:
            raise CodexAppServerError("client is closed")
        if self._process is not None and self._process.poll() is None:
            return self
        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=dict(self.environment),
                bufsize=0,
                close_fds=True,
            )
        except (OSError, ValueError) as exc:
            self._process = None
            raise CodexAppServerError("unable to start app server") from exc
        self._output_queue = queue.Queue(maxsize=self._output_queue_max_chunks)
        self._reader_threads = []
        for stream_name, stream in (
            ("stdout", self._process.stdout),
            ("stderr", self._process.stderr),
        ):
            if stream is None:
                self._fail_closed()
                raise CodexAppServerError("app server output pipe is unavailable")
            reader = threading.Thread(
                target=self._read_output_stream,
                args=(stream_name, stream),
                daemon=True,
                name=f"deeplaw-app-server-{stream_name}",
            )
            reader.start()
            self._reader_threads.append(reader)
        return self

    launch = start

    def initialize(self) -> dict[str, Any]:
        self.start()
        if self._initialized:
            return {}
        params = {
            "clientInfo": {
                "name": self.client_name,
                "title": self.client_title,
                "version": self.client_version,
            },
            "capabilities": {"experimentalApi": True},
        }
        result = self._request("initialize", params)
        self._send_notification("initialized")
        self._initialized = True
        return result if isinstance(result, dict) else {"result": result}

    def model_list(
        self,
        params: Mapping[str, Any] | None = None,
        *,
        include_hidden: bool | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """List the server's current model inventory without persisting it."""

        payload = self._params(params, kwargs)
        if include_hidden is not None:
            if type(include_hidden) is not bool:
                raise ValueError("include_hidden must be a boolean")
            payload["includeHidden"] = include_hidden
        if "includeHidden" in payload and type(payload["includeHidden"]) is not bool:
            raise ValueError("includeHidden must be a boolean")
        result = self._request_after_initialize("model/list", payload)
        return self._validate_paged_response(result, "model/list")

    list_models = model_list

    def mcp_server_status_list(
        self,
        params: Mapping[str, Any] | None = None,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        detail: Any = None,
        thread_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """List current MCP server status using bounded, read-only paging."""

        payload = self._params(params, kwargs)
        explicit = (
            ("cursor", cursor),
            ("limit", limit),
            ("detail", detail),
            ("threadId", thread_id),
        )
        for key, value in explicit:
            if value is not None:
                payload[key] = value
        if "cursor" in payload and not isinstance(payload["cursor"], str):
            raise ValueError("cursor must be a string")
        if "threadId" in payload and (
            not isinstance(payload["threadId"], str) or not payload["threadId"]
        ):
            raise ValueError("thread_id must be a non-empty string")
        if "detail" in payload and payload["detail"] not in {
            "full",
            "toolsAndAuthOnly",
        }:
            raise ValueError("detail must be full or toolsAndAuthOnly")
        if "limit" in payload:
            page_limit = payload["limit"]
            if (
                type(page_limit) is not int
                or page_limit < 0
                or page_limit > _MAX_MCP_SERVER_STATUS_LIMIT
            ):
                raise ValueError(
                    f"limit must be an integer between 0 and {_MAX_MCP_SERVER_STATUS_LIMIT}"
                )
        result = self._request_after_initialize("mcpServerStatus/list", payload)
        return self._validate_paged_response(result, "mcpServerStatus/list")

    list_mcp_server_status = mcp_server_status_list

    def _validate_paged_response(self, result: Any, method: str) -> dict[str, Any]:
        if (
            not isinstance(result, Mapping)
            or not isinstance(result.get("data"), list)
            or "nextCursor" not in result
            or (
                result.get("nextCursor") is not None
                and not isinstance(result.get("nextCursor"), str)
            )
        ):
            self._fail_closed()
            raise CodexAppServerProtocolError(
                f"{method} response omitted valid data/nextCursor"
            )
        return dict(result)

    def thread_start(
        self,
        params: Mapping[str, Any] | None = None,
        *,
        dynamic_tools: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = self._params(params, kwargs)
        selected_tools = self.dynamic_tools if dynamic_tools is None else dynamic_tools
        if selected_tools is not None:
            payload["dynamicTools"] = self._dynamic_tools_payload(selected_tools)
        result = self._request_after_initialize("thread/start", payload)
        thread_id = _thread_id_from_response(result)
        if thread_id:
            self._active_thread_id = thread_id
        return result

    start_thread = thread_start

    def thread_resume(
        self,
        thread_id: str | Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = self._thread_params(thread_id, params, kwargs)
        result = self._request_after_initialize("thread/resume", payload)
        resumed = _thread_id_from_response(result) or payload.get("threadId")
        if isinstance(resumed, str):
            self._active_thread_id = resumed
        return result

    resume_thread = thread_resume

    def thread_fork(
        self,
        thread_id: str | Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = self._thread_params(thread_id, params, kwargs)
        result = self._request_after_initialize("thread/fork", payload)
        forked = _thread_id_from_response(result)
        if forked:
            self._active_thread_id = forked
        return result

    fork_thread = thread_fork

    def thread_compact_start(
        self,
        thread_id: str | Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = self._thread_params(thread_id, params, kwargs)
        expected_thread_id = payload["threadId"]
        expected_thread_hash = _sha256_text(expected_thread_id)
        deadline = time.monotonic() + self.timeout_seconds
        self._compacted_notification_keys = {
            key
            for key in self._compacted_notification_keys
            if key[0] != expected_thread_hash
        }
        result = self._request_after_initialize("thread/compact/start", payload)
        self._wait_for_compaction(
            expected_thread_hash=expected_thread_hash,
            deadline=deadline,
        )
        return result

    compact_thread = thread_compact_start
    thread_compact = thread_compact_start

    def turn_start(
        self,
        thread_id: str | Mapping[str, Any],
        input: Any = None,
        params: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> TurnResult:
        payload = self._thread_params(thread_id, params, kwargs)
        if input is not None:
            payload["input"] = input
        thread_value = payload.get("threadId")
        if isinstance(thread_value, str):
            self._active_thread_id = thread_value
        self._active_turn_id = None
        self._final_text_parts = []
        self._completed_item_text = None
        self._tool_outputs = []
        self._tool_call_observations = []
        event_start = len(self._events)
        started_at = time.monotonic()
        response = self._request_after_initialize("turn/start", payload)
        turn_id = _turn_id_from_response(response)
        if turn_id:
            self._active_turn_id = turn_id
        completion = self._wait_for_turn_completed(
            deadline=started_at + self.timeout_seconds,
            expected_turn_id=turn_id,
        )
        self._drain_ready_notifications()
        # ``item/completed`` carries the canonical full agent message when a
        # fixture also emitted deltas; prefer it to avoid returning a partial
        # prefix or duplicating the full text.
        final_text = self._completed_item_text or "".join(self._final_text_parts)
        usage = self.usage_for(self._active_thread_id, self._active_turn_id)
        status = completion.get("turn_status") if isinstance(completion, Mapping) else None
        result = TurnResult(
            thread_id=self._active_thread_id,
            turn_id=self._active_turn_id,
            status=status or "completed",
            final_text=final_text,
            final_agent_text=final_text,
            tool_outputs=list(self._tool_outputs),
            tool_call_observations=[dict(item) for item in self._tool_call_observations],
            usage=usage,
            events=self.sanitized_events[event_start:],
        )
        return result

    start_turn = turn_start

    def close(self) -> None:
        process = self._process
        if process is None:
            self._closed = True
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=0.5)
            for reader in self._reader_threads:
                reader.join(timeout=0.2)
            self._drain_available_stderr()
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    with suppress(OSError):
                        stream.close()
            for reader in self._reader_threads:
                reader.join(timeout=0.2)
            self._reader_threads = []
            self._process = None
            self._closed = True

    def _params(
        self, params: Mapping[str, Any] | None, kwargs: Mapping[str, Any]
    ) -> dict[str, Any]:
        if params is not None and not isinstance(params, Mapping):
            raise TypeError("params must be a mapping")
        payload = dict(params or {})
        payload.update(kwargs)
        # Friendly Python spellings are accepted at the adapter boundary, but
        # the wire always uses the generated v2 camel-case names.
        for source, target in (
            ("thread_id", "threadId"),
            ("turn_id", "turnId"),
            ("include_hidden", "includeHidden"),
            ("dynamic_tools", "dynamicTools"),
            ("approval_policy", "approvalPolicy"),
            ("sandbox_mode", "sandbox"),
        ):
            if source in payload and target not in payload:
                payload[target] = payload.pop(source)
        return payload

    def _thread_params(
        self,
        thread_id: str | Mapping[str, Any],
        params: Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
    ) -> dict[str, Any]:
        if isinstance(thread_id, Mapping):
            payload = self._params(thread_id, kwargs)
            if params:
                payload.update(self._params(params, {}))
        else:
            payload = self._params(params, kwargs)
            payload.setdefault("threadId", thread_id)
        if not isinstance(payload.get("threadId"), str) or not payload["threadId"]:
            raise ValueError("thread_id must be a non-empty string")
        return payload

    @staticmethod
    def _dynamic_tools_payload(
        value: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if isinstance(value, Mapping):
            if all(isinstance(item, Mapping) for item in value.values()):
                return [dict(spec, name=name) for name, spec in value.items()]
            return [{"name": str(name)} for name in value]
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise TypeError("dynamic_tools must be a sequence or mapping")
        result = []
        for item in value:
            if not isinstance(item, Mapping):
                raise TypeError("dynamic_tools entries must be mappings")
            result.append(dict(item))
        return result

    def _request_after_initialize(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self.start()
        if not self._initialized:
            self.initialize()
        result = self._request(method, params)
        return result if isinstance(result, dict) else {"result": result}

    def _request(self, method: str, params: Mapping[str, Any]) -> Any:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._send_message({"id": request_id, "method": method, "params": dict(params)})
        return self._wait_for_response(request_id, deadline=time.monotonic() + self.timeout_seconds)

    def _send_notification(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"method": method}
        if params:
            message["params"] = dict(params)
        self._send_message(message)

    def _send_message(self, message: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise CodexAppServerError("app server is not running")
        payload = {key: value for key, value in message.items() if key != "jsonrpc"}
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8") + b"\n"
            process.stdin.write(encoded)
            process.stdin.flush()
        except (BrokenPipeError, OSError, TypeError, ValueError) as exc:
            self._fail_closed()
            raise CodexAppServerProtocolError("unable to write app-server request") from exc

    def _wait_for_response(self, expected_id: int, *, deadline: float) -> Any:
        while True:
            message = self._next_message(deadline)
            if "method" in message and "id" not in message:
                self._handle_notification(message)
                continue
            if "method" in message and "id" in message:
                self._handle_server_request(message)
                continue
            response_id = message.get("id")
            if type(response_id) is not int or response_id != expected_id:
                self._fail_closed()
                raise CodexAppServerProtocolError("response id did not match request")
            if "error" in message:
                self._fail_closed()
                raise CodexAppServerProtocolError("app server returned an error")
            if "result" not in message:
                self._fail_closed()
                raise CodexAppServerProtocolError("response omitted result")
            return message["result"]

    def _wait_for_turn_completed(
        self, *, deadline: float, expected_turn_id: str | None
    ) -> dict[str, Any]:
        while True:
            message = self._next_message(deadline)
            if "method" in message and "id" not in message:
                completion = self._handle_notification(message)
                if (
                    completion is not None
                    and completion.get("kind") == "turn/completed"
                    and isinstance(completion.get("turn_id"), str)
                    and (
                        expected_turn_id is None
                        or completion.get("turn_id") == expected_turn_id
                    )
                ):
                    return completion
                continue
            if "method" in message and "id" in message:
                self._handle_server_request(message)
                continue
            # A response after turn/start completed is not associated with the
            # active request and therefore fails closed under strict matching.
            self._fail_closed()
            raise CodexAppServerProtocolError(
                "unexpected response while waiting for turn completion"
            )

    def _wait_for_compaction(self, *, expected_thread_hash: str, deadline: float) -> None:
        """Wait for one current ``thread/compacted`` notification for a thread."""

        while True:
            matching = next(
                (
                    key
                    for key in self._compacted_notification_keys
                    if key[0] == expected_thread_hash
                ),
                None,
            )
            if matching is not None:
                self._compacted_notification_keys.discard(matching)
                return
            message = self._next_message(deadline)
            if "method" in message and "id" not in message:
                completion = self._handle_notification(message)
                if (
                    completion is not None
                    and completion.get("kind") == "thread/compacted"
                    and completion.get("thread_id_sha256") == expected_thread_hash
                ):
                    self._compacted_notification_keys.discard(
                        (
                            expected_thread_hash,
                            completion["turn_id_sha256"],
                        )
                    )
                    return
                continue
            if "method" in message and "id" in message:
                self._handle_server_request(message)
                continue
            self._fail_closed()
            raise CodexAppServerProtocolError(
                "unexpected response while waiting for thread compaction"
            )

    def _next_message(self, deadline: float) -> dict[str, Any]:
        while True:
            newline = self._stdout_buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._stdout_buffer[:newline])
                del self._stdout_buffer[: newline + 1]
                if not line.strip():
                    continue
                return self._decode_message(line)
            self._pump(deadline)

    def _decode_message(self, line: bytes) -> dict[str, Any]:
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._fail_closed()
            raise CodexAppServerProtocolError("app server emitted invalid JSONL") from exc
        if not isinstance(value, dict):
            self._fail_closed()
            raise CodexAppServerProtocolError("app server JSONL message is not an object")
        return value

    def _read_output_stream(self, stream_name: str, stream: Any) -> None:
        """Copy one blocking subprocess pipe into the bounded main-thread queue.

        Windows selectors accept sockets but not anonymous subprocess pipes. Two
        daemon readers keep the wire transport portable while all parsing,
        accounting, leak detection, and failure decisions remain serialized in
        the client thread.
        """

        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    self._output_queue.put((stream_name, None, None))
                    return
                self._output_queue.put((stream_name, chunk, None))
        except (OSError, ValueError) as error:
            self._output_queue.put((stream_name, None, error))

    def _record_output_chunk(
        self,
        stream_name: str,
        chunk: bytes,
        *,
        fail_on_limit: bool,
    ) -> None:
        self._scan_output_chunk(stream_name, chunk)
        if stream_name == "stdout":
            self._stdout_bytes += len(chunk)
            self._stdout_buffer.extend(chunk)
            limit_exceeded = (
                self._stdout_bytes > self.max_stdout_bytes
                or self._stdout_bytes + self._stderr_bytes > self.max_output_bytes
            )
        else:
            self._stderr_bytes += len(chunk)
            self._stderr_digest.update(chunk)
            limit_exceeded = (
                self._stderr_bytes > self.max_stderr_bytes
                or self._stdout_bytes + self._stderr_bytes > self.max_output_bytes
            )
        if limit_exceeded and fail_on_limit:
            self._fail_closed()
            raise CodexAppServerOutputLimitError(
                f"app server {stream_name} exceeded byte limit"
            )

    def _consume_output_event(
        self,
        event: tuple[str, bytes | None, BaseException | None],
        *,
        fail_on_error: bool,
        fail_on_limit: bool,
    ) -> str:
        stream_name, chunk, error = event
        if error is not None:
            if fail_on_error:
                self._fail_closed()
                raise CodexAppServerProtocolError(
                    "unable to read app-server output"
                ) from error
            return "error"
        if chunk is None:
            return "eof"
        self._record_output_chunk(
            stream_name,
            chunk,
            fail_on_limit=fail_on_limit,
        )
        return "data"

    def _drain_ready_notifications(self) -> None:
        """Consume already-ready notifications after an immediate compact call."""

        process = self._process
        if process is None or process.stdout is None or process.stderr is None:
            return
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            newline = self._stdout_buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._stdout_buffer[:newline])
                del self._stdout_buffer[: newline + 1]
                if not line.strip():
                    continue
                message = self._decode_message(line)
                if "method" in message and "id" not in message:
                    self._handle_notification(message)
                    continue
                if "method" in message and "id" in message:
                    self._handle_server_request(message)
                    continue
                self._fail_closed()
                raise CodexAppServerProtocolError("unexpected response after compact")
            wait_for = max(0.0, min(0.01, deadline - time.monotonic()))
            try:
                event = self._output_queue.get(timeout=wait_for)
            except queue.Empty:
                break
            self._consume_output_event(
                event,
                fail_on_error=True,
                fail_on_limit=True,
            )

    def _pump(self, deadline: float) -> None:
        process = self._process
        if process is None or process.stdout is None or process.stderr is None:
            raise CodexAppServerError("app server is not running")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._fail_closed()
            raise CodexAppServerTimeoutError("app server request timed out")
        while True:
            wait_for = min(remaining, 0.1)
            try:
                event = self._output_queue.get(timeout=wait_for)
            except queue.Empty:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._fail_closed()
                    message = (
                        "app server closed before response"
                        if process.poll() is not None
                        else "app server request timed out"
                    )
                    raise CodexAppServerTimeoutError(message) from None
                continue
            stream_name = event[0]
            outcome = self._consume_output_event(
                event,
                fail_on_error=True,
                fail_on_limit=True,
            )
            if outcome == "eof" and stream_name == "stdout":
                if self._stdout_buffer:
                    self._fail_closed()
                    raise CodexAppServerProtocolError(
                        "app server emitted truncated JSONL"
                    )
                self._fail_closed()
                raise CodexAppServerProtocolError("app server closed stdout")
            if self._stdout_buffer.find(b"\n") >= 0:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._fail_closed()
                raise CodexAppServerTimeoutError("app server request timed out")

    def _drain_available_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        deadline = time.monotonic() + (0.01 if process.poll() is None else 0.0)
        while True:
            try:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    event = self._output_queue.get(timeout=remaining)
                else:
                    event = self._output_queue.get_nowait()
            except queue.Empty:
                return
            self._consume_output_event(
                event,
                fail_on_error=False,
                fail_on_limit=False,
            )

    def _handle_notification(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if not isinstance(method, str) or not method:
            self._fail_closed()
            raise CodexAppServerProtocolError("notification omitted method")
        params = message.get("params")
        if not isinstance(params, Mapping):
            params = {}
        completion = self._capture_notification_state(method, params)
        projected = self._project_event(method, params)
        if projected is not None:
            self._events.append(projected)
        return completion

    def _scan_output_chunk(self, stream: str, chunk: bytes) -> None:
        if not self._forbidden_output_values:
            return
        combined = self._leak_scan_tails[stream] + chunk
        if any(value in combined for value in self._forbidden_output_values):
            self._secret_leak = True
        overlap = max(len(value) for value in self._forbidden_output_values) - 1
        self._leak_scan_tails[stream] = combined[-overlap:] if overlap > 0 else b""

    def _capture_notification_state(
        self, method: str, params: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        if method == "thread/compacted":
            compact_thread_id = _find_value(params, "threadId", "thread_id")
            compact_turn_id = _find_value(params, "turnId", "turn_id")
            if (
                not isinstance(compact_thread_id, str)
                or not compact_thread_id
                or not isinstance(compact_turn_id, str)
                or not compact_turn_id
            ):
                self._fail_closed()
                raise CodexAppServerProtocolError(
                    "thread/compacted omitted required threadId/turnId"
                )
            thread_hash = _sha256_text(compact_thread_id)
            turn_hash = _sha256_text(compact_turn_id)
            self._compacted_notification_keys.add((thread_hash, turn_hash))
            return {
                "kind": "thread/compacted",
                "thread_id_sha256": thread_hash,
                "turn_id_sha256": turn_hash,
            }
        thread_id = _thread_or_turn_id(params, "threadId", "thread_id")
        turn_id = _thread_or_turn_id(params, "turnId", "turn_id")
        if thread_id is None:
            thread_id = self._active_thread_id
        if turn_id is None:
            turn_id = self._active_turn_id
        if method == "thread/tokenUsage/updated":
            token_usage = params.get("tokenUsage")
            last = token_usage.get("last") if isinstance(token_usage, Mapping) else None
            normalized = normalize_token_usage(last)
            self._latest_usage = normalized
            self._usage_by_key[(thread_id, turn_id)] = dict(normalized)
        if "reasoning" not in method.casefold() and "command" not in method.casefold():
            self._capture_agent_text(method, params)
        self._capture_tool_output(method, params)
        if method == "turn/completed":
            status = _safe_label(
                _find_value(params, "status")
                or _find_value(params.get("turn"), "status")
            )
            return {
                "kind": "turn/completed",
                "turn_id": turn_id,
                "turn_status": status or "completed",
            }
        return None

    def _capture_agent_text(self, method: str, params: Mapping[str, Any]) -> None:
        lowered = method.casefold()
        item = params.get("item") if isinstance(params.get("item"), Mapping) else params
        item_type = _find_value(item, "type", "itemType", "item_type")
        item_type_text = item_type.casefold() if isinstance(item_type, str) else ""
        is_agent = "agent" in item_type_text and (
            "message" in item_type_text or "text" in item_type_text
        )
        if not is_agent and "agentmessage" not in lowered and "agent_message" not in lowered:
            return
        if "delta" in lowered:
            value = _find_value(params, "delta", "text")
            if isinstance(value, str):
                self._final_text_parts.append(value)
            return
        if "completed" in lowered or item_type_text:
            value = _find_value(item, "text", "content")
            if isinstance(value, str):
                self._completed_item_text = value

    def _capture_tool_output(self, method: str, params: Mapping[str, Any]) -> None:
        """Keep completed tool output only in the caller-memory turn result."""

        if "completed" not in method.casefold():
            return
        item = params.get("item") if isinstance(params.get("item"), Mapping) else params
        item_type = _find_value(item, "type", "itemType", "item_type")
        if not isinstance(item_type, str) or "tool" not in item_type.casefold():
            return
        output_found = False
        for key in ("result", "output", "content", "contentItems"):
            if isinstance(item, Mapping) and key in item and item[key] is not None:
                self._tool_outputs.append(item[key])
                output_found = True
                break
            if key in params and params[key] is not None:
                self._tool_outputs.append(params[key])
                output_found = True
                break
        observation = self._tool_observation(params, item, include_default_status=True)
        if output_found or observation:
            self._tool_call_observations.append(observation)

    def _tool_observation(
        self,
        params: Mapping[str, Any],
        item: Any,
        *,
        include_default_status: bool = False,
    ) -> dict[str, Any]:
        """Build a scalar-only observation without retaining tool payloads."""

        observation: dict[str, Any] = {}
        call_id = self._first_field(
            params,
            item,
            "callId",
            "call_id",
            "toolCallId",
            "tool_call_id",
            "id",
        )
        if isinstance(call_id, str) and call_id:
            observation["call_id_sha256"] = _sha256_text(call_id)
        server = self._first_field(params, item, "server", "serverName", "server_name")
        safe_server = _safe_label(server)
        if safe_server is not None:
            observation["server"] = safe_server
        tool_name = self._tool_name(params, item)
        if tool_name is not None:
            observation["tool_name"] = tool_name
        status = _safe_label(
            self._first_field(params, item, "status", "toolStatus", "tool_status")
        )
        if status is None and include_default_status:
            status = "completed"
        if status is not None:
            observation["status"] = status

        arguments = self._first_field(params, item, "arguments", "parameters", "input", "args")
        if arguments is not None:
            digest, size = _hash_record(arguments)
            observation["arguments_sha256"] = digest
            observation["arguments_bytes"] = size
            if isinstance(arguments, Mapping):
                operation = _safe_label(arguments.get("operation"))
                if operation is not None:
                    observation["argument_operation"] = operation
                observation["argument_confirm_no_case_data"] = (
                    arguments.get("confirm_no_case_data") is True
                )
                task_binding = arguments.get("task_binding")
                if isinstance(task_binding, Mapping):
                    task_digest, _ = _hash_record(task_binding)
                    observation["argument_task_binding_sha256"] = task_digest
        result = self._first_field(params, item, "result", "output", "contentItems")
        if result is not None:
            digest, size = _hash_record(result)
            observation["result_sha256"] = digest
            observation["result_bytes"] = size
        structured_content = self._structured_content(params, item, result)
        if structured_content is not None:
            digest, size = _hash_record(structured_content)
            observation["structured_content_sha256"] = digest
            observation["structured_content_bytes"] = size
        return observation

    @classmethod
    def _structured_content(cls, params: Mapping[str, Any], item: Any, result: Any) -> Any:
        structured = cls._first_field(
            params,
            item,
            "structuredContent",
            "structured_content",
        )
        if structured is not None:
            return structured
        if isinstance(result, Mapping):
            return _find_value(result, "structuredContent", "structured_content")
        return None

    def _project_event(
        self, method: str, params: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        if method.startswith(("account/", "remoteControl/")):
            return None
        event: dict[str, Any] = {"method": method}
        thread_id = _thread_or_turn_id(params, "threadId", "thread_id")
        turn_id = _thread_or_turn_id(params, "turnId", "turn_id")
        if thread_id is not None:
            event["thread_id_sha256"] = _sha256_text(thread_id)
        if turn_id is not None:
            event["turn_id_sha256"] = _sha256_text(turn_id)

        item = params.get("item") if isinstance(params.get("item"), Mapping) else params
        item_type = _safe_label(_find_value(item, "type", "itemType", "item_type"))
        status = _safe_label(_find_value(item, "status") or _find_value(params, "status"))
        lowered = method.casefold()
        disallowed = any(
            label in lowered for label in ("reasoning", "command", "file", "raw", "delta")
        )
        if (
            disallowed
            or (isinstance(item_type, str) and "reasoning" in item_type.casefold())
        ) and not ("tool" in lowered and "delta" not in lowered):
            event["item_type"] = "disallowed"
        elif item_type is not None:
            event["item_type"] = item_type
        if status is not None:
            event["item_status"] = status
        if method == "turn/completed" or method.startswith("turn/"):
            turn_status = _safe_label(
                _find_value(params, "status") or _find_value(params.get("turn"), "status")
            )
            if turn_status is None and method == "turn/completed":
                turn_status = "completed"
            if turn_status is not None:
                event["turn_status"] = turn_status
        tool_name = self._tool_name(params, item)
        if tool_name is not None:
            event["tool_name"] = tool_name
        if tool_name is not None or "tool" in lowered:
            parameters = self._first_field(params, item, "arguments", "parameters", "input", "args")
            result = self._first_field(params, item, "result", "output", "contentItems")
            if parameters is not None:
                digest, size = _hash_record(parameters)
                event["parameters_sha256"] = digest
                event["parameters_bytes"] = size
            if result is not None:
                digest, size = _hash_record(result)
                event["result_sha256"] = digest
                event["result_bytes"] = size
            if "completed" in lowered:
                event.update(
                    self._tool_observation(
                        params,
                        item,
                        include_default_status=True,
                    )
                )
        if method == "thread/tokenUsage/updated":
            event["usage"] = self.usage_for(thread_id, turn_id)
        if method == "thread/compacted":
            compaction_id = _find_value(params, "compactionId", "compaction_id")
            if isinstance(compaction_id, str) and compaction_id:
                event["compaction_id_sha256"] = _sha256_text(compaction_id)
            compaction_status = _safe_label(
                _find_value(params, "status")
                or _find_value(params.get("item"), "status")
            )
            event["compaction_status"] = compaction_status or "completed"
        return event

    @staticmethod
    def _first_field(params: Mapping[str, Any], item: Any, *keys: str) -> Any:
        for source in (params, item):
            if isinstance(source, Mapping):
                for key in keys:
                    if key in source and source[key] is not None:
                        return source[key]
        return None

    @staticmethod
    def _tool_name(params: Mapping[str, Any], item: Any) -> str | None:
        for source in (params, item):
            if isinstance(source, Mapping):
                value = _find_value(source, "toolName", "tool_name", "name")
                if isinstance(value, str):
                    return _safe_label(value)
                tool = source.get("tool")
                if isinstance(tool, str):
                    return _safe_label(tool)
                if isinstance(tool, Mapping):
                    value = _find_value(tool, "name", "toolName", "tool_name")
                    if isinstance(value, str):
                        return _safe_label(value)
        return None

    def _handle_server_request(self, message: Mapping[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if method != "item/tool/call":
            self._fail_closed()
            raise CodexAppServerProtocolError("unsupported server request")
        if request_id is None:
            self._fail_closed()
            raise CodexAppServerProtocolError("server request omitted id")
        params = message.get("params")
        if not isinstance(params, Mapping):
            params = {}
        name, arguments = self._dynamic_call_fields(params)
        response = self._invoke_dynamic_tool(name, arguments, params)
        self._send_message({"id": request_id, "result": response})
        projected = self._project_event("item/tool/call", params)
        if projected is not None:
            self._events.append(projected)

    @staticmethod
    def _dynamic_call_fields(params: Mapping[str, Any]) -> tuple[str | None, Any]:
        tool = params.get("tool")
        if isinstance(tool, Mapping):
            name = _find_value(tool, "name", "toolName", "tool_name")
            arguments = _find_value(tool, "arguments", "parameters", "input", "args")
        else:
            name = tool if isinstance(tool, str) else _find_value(
                params, "name", "toolName", "tool_name"
            )
            arguments = _find_value(params, "arguments", "parameters", "input", "args")
        return (name if isinstance(name, str) else None), arguments

    def _invoke_dynamic_tool(
        self, name: str | None, arguments: Any, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        handler = self.dynamic_tool_handler
        result: Any
        try:
            if isinstance(handler, Mapping):
                callback = handler.get(name) if name is not None else None
                if not callable(callback):
                    return {"contentItems": [], "success": False}
                result = self._call_handler(callback, name, arguments, params)
            elif callable(handler):
                result = self._call_handler(handler, name, arguments, params)
            else:
                return {"contentItems": [], "success": False}
        except Exception:
            # A dynamic tool failure is returned as a protocol-level failed tool
            # result.  Exception text is deliberately not retained or exposed.
            return {"contentItems": [], "success": False}
        return self._validate_tool_response(result)

    @staticmethod
    def _call_handler(
        callback: Callable[..., Any],
        name: str | None,
        arguments: Any,
        params: Mapping[str, Any],
    ) -> Any:
        # Prefer the documented ``(name, arguments)`` callback.  A one-argument
        # callback is useful for small fixtures and is supported without
        # retaining a traceback or callback result beyond the response.
        try:
            signature = inspect.signature(callback)
            for candidate in ((name, arguments), (arguments,), (params,)):
                try:
                    signature.bind(*candidate)
                except TypeError:
                    continue
                return callback(*candidate)
        except (TypeError, ValueError):
            pass
        return callback(name, arguments)

    @staticmethod
    def _validate_tool_response(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise CodexAppServerProtocolError("dynamic tool handler returned invalid response")
        success = value.get("success")
        content = value.get("contentItems")
        if type(success) is not bool or not isinstance(content, list):
            raise CodexAppServerProtocolError("dynamic tool response omitted required fields")
        clean_content: list[dict[str, str]] = []
        for item in content:
            if (
                not isinstance(item, Mapping)
                or item.get("type") != "inputText"
                or not isinstance(item.get("text"), str)
            ):
                raise CodexAppServerProtocolError("dynamic tool content item is invalid")
            clean_content.append({"type": "inputText", "text": item["text"]})
        return {"contentItems": clean_content, "success": success}

    def _fail_closed(self) -> None:
        with suppress(Exception):
            self.close()


__all__ = [
    "UNREPORTED",
    "AppServerError",
    "CodexAppServerClient",
    "CodexAppServerError",
    "CodexAppServerOutputLimitError",
    "CodexAppServerProtocolError",
    "CodexAppServerTimeoutError",
    "OutputLimitError",
    "ProtocolError",
    "TimeoutError",
    "TurnResult",
    "normalize_token_usage",
]
