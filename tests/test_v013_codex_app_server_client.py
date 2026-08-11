from __future__ import annotations

import hashlib
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from benchmarks.hosts.codex_app_server_client import (
    UNREPORTED,
    CodexAppServerClient,
    CodexAppServerOutputLimitError,
    CodexAppServerProtocolError,
    CodexAppServerTimeoutError,
)


def _fake_server(
    tmp_path: Path,
    *,
    mode: str = "full",
    stderr: bytes = b"fixture stderr\n",
) -> list[str]:
    script = textwrap.dedent(
        """
        import json
        import sys

        MODE = __MODE__
        STDERR = __STDERR__
        if STDERR:
            sys.stderr.buffer.write(STDERR)
            sys.stderr.flush()

        def send(value):
            sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\\n")
            sys.stdout.flush()

        def request():
            line = sys.stdin.buffer.readline()
            if not line:
                raise SystemExit(0)
            value = json.loads(line)
            assert "jsonrpc" not in value
            return value

        while True:
            message = request()
            method = message.get("method")
            request_id = message.get("id")
            if method == "initialize":
                assert message["params"]["capabilities"]["experimentalApi"] is True
                assert message["params"]["clientInfo"]["name"]
                send({"id": request_id, "result": {"server": "fixture"}})
            elif method == "initialized":
                continue
            elif method == "thread/start":
                if MODE == "full":
                    assert "dynamicTools" in message["params"]
                send({"id": request_id, "result": {"thread": {"id": "thread-1"}}})
            elif method == "thread/resume":
                assert message["params"]["threadId"] == "thread-1"
                send({"id": request_id, "result": {"thread": {"id": "thread-1"}}})
            elif method == "thread/fork":
                assert message["params"]["threadId"] == "thread-1"
                send({"id": request_id, "result": {"thread": {"id": "thread-2"}}})
            elif method == "thread/compact/start":
                assert message["params"]["threadId"] == "thread-2"
                send({"id": request_id, "result": {"status": "started"}})
                send({"method": "contextCompaction/started", "params": {"threadId": "thread-2"}})
                send({"method": "contextCompaction/completed", "params": {"threadId": "thread-2"}})
            elif method == "turn/start":
                send({"id": request_id, "result": {"turn": {"id": "turn-1"}}})
                if MODE == "unknown-request":
                    send({"id": "srv-1", "method": "server/unknown", "params": {}})
                    continue
                send({"method": "item/agentMessage/delta", "params": {
                    "threadId": "thread-1", "turnId": "turn-1", "delta": "hello "
                }})
                if MODE == "full":
                    send({"id": "tool-1", "method": "item/tool/call", "params": {
                        "threadId": "thread-1", "turnId": "turn-1",
                        "callId": "call-1", "tool": "lookup",
                        "arguments": {"query": "fixture"}
                    }})
                    tool_response = request()
                    assert tool_response["id"] == "tool-1"
                    assert tool_response["result"]["success"] is True
                    send({
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "item": {
                                "type": "dynamicToolCall",
                                "status": "completed",
                                "tool": "lookup",
                                "contentItems": [{
                                    "type": "inputText",
                                    "text": "/tmp/fixture-secret raw-output",
                                }],
                            },
                        },
                    })
                if MODE == "mcp":
                    send({
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "item": {
                                "type": "mcpToolCall",
                                "id": "mcp-1",
                                "server": "deeplaw",
                                "tool": "knowledge_support",
                                "arguments": {"operation": "context"},
                                "status": "completed",
                                "result": {
                                    "content": [{"type": "text", "text": "bounded"}],
                                    "structuredContent": {"schema_version": "fixture/v1"},
                                },
                            },
                        },
                    })
                send({
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {
                            "type": "agentMessage",
                            "status": "completed",
                            "text": "hello world",
                        },
                    },
                })
                if MODE in {"full", "mcp"}:
                    send({"method": "thread/tokenUsage/updated", "params": {
                        "threadId": "thread-1", "turnId": "turn-1", "tokenUsage": {"last": {
                            "inputTokens": 10, "cachedInputTokens": 2,
                            "outputTokens": 4, "reasoningOutputTokens": 1
                        }}
                    }})
                send({"method": "turn/completed", "params": {
                    "threadId": "thread-1", "turn": {
                        "id": "turn-1", "status": "completed", "items": []
                    }
                }})
            else:
                raise AssertionError(method)
        """
    ).replace("__MODE__", repr(mode)).replace("__STDERR__", repr(stderr))
    return [sys.executable, "-u", "-c", script]


def _client(
    tmp_path: Path,
    *,
    mode: str = "full",
    stderr: bytes = b"fixture stderr\n",
    **kwargs: Any,
) -> CodexAppServerClient:
    return CodexAppServerClient(
        _fake_server(tmp_path, mode=mode, stderr=stderr),
        environment={"PATH": "/usr/bin", "PYTHONUNBUFFERED": "1"},
        cwd=tmp_path,
        timeout_seconds=3,
        **kwargs,
    )


def test_full_lifecycle_dynamic_tool_usage_and_minimal_projection(tmp_path: Path) -> None:
    def handler(name: str, arguments: Any) -> dict[str, Any]:
        assert name == "lookup"
        assert arguments == {"query": "fixture"}
        return {"contentItems": [{"type": "inputText", "text": "tool-answer"}], "success": True}

    client = _client(
        tmp_path,
        dynamic_tools=[{"name": "lookup", "description": "fixture"}],
        dynamic_tool_handler=handler,
    )
    with client:
        assert isinstance(client.process_id, int) and client.process_id > 0
        client.initialize()
        assert isinstance(client.process_id, int) and client.process_id > 0
        thread = client.thread_start()
        assert thread["thread"]["id"] == "thread-1"
        result = client.turn_start("thread-1", [{"type": "text", "text": "hello"}])
        assert result.final_agent_text == "hello world"
        assert result.tool_outputs == [
            [{"type": "inputText", "text": "/tmp/fixture-secret raw-output"}]
        ]
        assert result.usage == {
            "input_tokens": 10,
            "cached_input_tokens": 2,
            "output_tokens": 4,
            "reasoning_output_tokens": 1,
            "total_tokens": 14,
        }
        rendered = repr(client.sanitized_events)
        assert "/tmp/fixture-secret" not in rendered
        assert "raw-output" not in rendered
        assert "tool-answer" not in rendered
        assert any(event.get("usage", {}).get("input_tokens") == 10 for event in client.events)
        assert any(event.get("tool_name") == "lookup" for event in client.events)
        assert client.stderr_metadata == {
            "sha256": hashlib.sha256(b"fixture stderr\n").hexdigest(),
            "bytes": len(b"fixture stderr\n"),
        }
    assert client.process_id is None


def test_resume_fork_and_compact_use_exact_v2_methods(tmp_path: Path) -> None:
    client = _client(tmp_path, mode="lifecycle")
    with client:
        client.initialize()
        client.thread_start()
        client.thread_resume("thread-1")
        forked = client.thread_fork("thread-1")
        assert forked["thread"]["id"] == "thread-2"
        compacted = client.thread_compact_start("thread-2")
        assert compacted == {"status": "started"}
        # Lifecycle events contain no source payload and retain only method and
        # hashed identity fields.
        assert [event["method"] for event in client.events[-2:]] == [
            "contextCompaction/started",
            "contextCompaction/completed",
        ]


def test_completed_mcp_result_is_memory_only_and_hashed_in_projection(tmp_path: Path) -> None:
    client = _client(tmp_path, mode="mcp")
    with client:
        client.initialize()
        client.thread_start()
        result = client.turn_start("thread-1", "hello")
        assert result.tool_outputs == [
            {
                "content": [{"type": "text", "text": "bounded"}],
                "structuredContent": {"schema_version": "fixture/v1"},
            }
        ]
        completed = [
            event
            for event in client.events
            if event.get("item_type") == "mcpToolCall"
        ]
        assert len(completed) == 1
        assert completed[0]["tool_name"] == "knowledge_support"
        assert completed[0]["result_bytes"] > 0
        assert "bounded" not in repr(completed)


def test_unknown_server_request_fails_closed(tmp_path: Path) -> None:
    client = _client(tmp_path, mode="unknown-request")
    with client:
        client.initialize()
        client.thread_start()
        with pytest.raises(CodexAppServerProtocolError, match="unsupported server request"):
            client.turn_start("thread-1", "hello")
        assert client.process_id is None


def test_missing_usage_is_unreported_not_zero(tmp_path: Path) -> None:
    client = _client(tmp_path, mode="missing-usage")
    with client:
        client.initialize()
        client.thread_start()
        result = client.turn_start("thread-1", "hello")
        assert result.usage == {key: UNREPORTED for key in (
            "input_tokens", "cached_input_tokens", "output_tokens",
            "reasoning_output_tokens", "total_tokens",
        )}


def test_output_limit_and_timeout_fail_closed(tmp_path: Path) -> None:
    client = _client(tmp_path, stderr=b"x" * 100, max_output_bytes=16)
    with pytest.raises(CodexAppServerOutputLimitError):
        client.initialize()
    assert client.process_id is None

    hanging = CodexAppServerClient(
        [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
        environment={},
        cwd=tmp_path,
        timeout_seconds=0.05,
    )
    with pytest.raises(CodexAppServerTimeoutError):
        hanging.initialize()
    assert hanging.process_id is None


def test_forbidden_output_value_is_detected_without_entering_events(tmp_path: Path) -> None:
    canary = "qualification-canary-value"
    client = _client(
        tmp_path,
        mode="missing-usage",
        stderr=f"prefix {canary} suffix".encode(),
        forbidden_output_values=(canary,),
    )
    with client:
        client.initialize()
        client.thread_start()
        client.turn_start("thread-1", "hello")
        assert client.secret_leak is True
        assert canary not in repr(client.events)
