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
        import time

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
            elif method == "model/list":
                assert MODE in {"inventory", "inventory-invalid-model"}
                assert message["params"] == {"includeHidden": True}
                if MODE == "inventory-invalid-model":
                    send({"id": request_id, "result": {"data": {}}})
                else:
                    send({
                        "id": request_id,
                        "result": {
                            "data": [{"id": "model-fixture", "name": "Fixture"}],
                            "nextCursor": "model-next",
                        },
                    })
            elif method == "mcpServerStatus/list":
                assert MODE in {"inventory", "inventory-invalid-mcp"}
                assert message["params"] == {
                    "cursor": "cursor-1",
                    "limit": 2,
                    "detail": "full",
                    "threadId": "thread-1",
                }
                if MODE == "inventory-invalid-mcp":
                    send({"id": request_id, "result": {"data": [], "nextCursor": 1}})
                else:
                    send({
                        "id": request_id,
                        "result": {
                            "data": [{"name": "deeplaw", "status": "ready"}],
                            "nextCursor": None,
                        },
                    })
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
                if MODE in {"compact-current", "lifecycle"}:
                    time.sleep(0.08)
                    send({
                        "method": "thread/compacted",
                        "params": {
                            "threadId": "thread-2",
                            "turnId": "turn-compact-1",
                            "compactionId": "compact-1",
                            "summary": "/tmp/compaction-secret bounded",
                        },
                    })
                elif MODE == "compact-timeout":
                    continue
                else:
                    send({
                        "method": "contextCompaction/started",
                        "params": {"threadId": "thread-2"},
                    })
                    send({
                        "method": "contextCompaction/completed",
                        "params": {"threadId": "thread-2"},
                    })
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
                if MODE == "mcp-multi":
                    for call_id, tool_name, query in (
                        ("mcp-1", "knowledge_support", "/tmp/tool-args-one"),
                        ("mcp-2", "knowledge_search", "/tmp/tool-args-two"),
                    ):
                        send({
                            "method": "item/completed",
                            "params": {
                                "threadId": "thread-1",
                                "turnId": "turn-1",
                                "item": {
                                    "type": "mcpToolCall",
                                    "callId": call_id,
                                    "server": "deeplaw",
                                    "tool": tool_name,
                                    "arguments": {"query": query},
                                    "status": "completed",
                                    "result": {
                                        "content": [{"type": "text", "text": "bounded"}],
                                        "structuredContent": {"path": query},
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
    timeout_seconds: float = 3,
    **kwargs: Any,
) -> CodexAppServerClient:
    return CodexAppServerClient(
        _fake_server(tmp_path, mode=mode, stderr=stderr),
        environment={"PATH": "/usr/bin", "PYTHONUNBUFFERED": "1"},
        cwd=tmp_path,
        timeout_seconds=timeout_seconds,
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
            "cache_write_input_tokens": "unreported",
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


def test_model_list_uses_exact_params_and_validates_page(tmp_path: Path) -> None:
    client = _client(tmp_path, mode="inventory")
    with client:
        assert client.model_list(include_hidden=True) == {
            "data": [{"id": "model-fixture", "name": "Fixture"}],
            "nextCursor": "model-next",
        }


def test_model_list_invalid_page_fails_closed(tmp_path: Path) -> None:
    client = _client(tmp_path, mode="inventory-invalid-model")
    with client:
        with pytest.raises(CodexAppServerProtocolError, match="model/list response"):
            client.model_list(include_hidden=True)
        assert client.process_id is None


def test_mcp_server_status_list_uses_exact_params_and_validates_page(tmp_path: Path) -> None:
    client = _client(tmp_path, mode="inventory")
    with client:
        assert client.mcp_server_status_list(
            cursor="cursor-1",
            limit=2,
            detail="full",
            thread_id="thread-1",
        ) == {
            "data": [{"name": "deeplaw", "status": "ready"}],
            "nextCursor": None,
        }


def test_mcp_server_status_list_rejects_invalid_page_and_limit(tmp_path: Path) -> None:
    client = _client(tmp_path, mode="inventory-invalid-mcp")
    with client:
        with pytest.raises(CodexAppServerProtocolError, match="mcpServerStatus/list response"):
            client.mcp_server_status_list(
                cursor="cursor-1",
                limit=2,
                detail="full",
                thread_id="thread-1",
            )
        assert client.process_id is None

    client = _client(tmp_path, mode="inventory")
    with pytest.raises(ValueError, match="limit"):
        client.mcp_server_status_list(limit=-1)
    with pytest.raises(ValueError, match="detail"):
        client.mcp_server_status_list(detail="everything")


def test_compact_waits_for_current_thread_compacted_notification(tmp_path: Path) -> None:
    client = _client(tmp_path, mode="compact-current")
    with client:
        client.initialize()
        client.thread_start()
        client.thread_fork("thread-1")
        assert client.compact_thread("thread-2") == {"status": "started"}
        compacted = [event for event in client.events if event["method"] == "thread/compacted"]
        assert len(compacted) == 1
        assert compacted[0]["thread_id_sha256"] == hashlib.sha256(b"thread-2").hexdigest()
        assert compacted[0]["turn_id_sha256"] == hashlib.sha256(b"turn-compact-1").hexdigest()
        assert compacted[0]["compaction_id_sha256"] == hashlib.sha256(b"compact-1").hexdigest()
        assert "/tmp/compaction-secret" not in repr(compacted)
        assert "bounded" not in repr(compacted)


def test_compact_timeout_fails_closed(tmp_path: Path) -> None:
    client = _client(tmp_path, mode="compact-timeout", timeout_seconds=0.1)
    with client:
        client.initialize()
        client.thread_start()
        client.thread_fork("thread-1")
        with pytest.raises(CodexAppServerTimeoutError):
            client.thread_compact_start("thread-2")
        assert client.process_id is None


def test_mcp_tool_observations_are_per_call_and_safe(tmp_path: Path) -> None:
    client = _client(tmp_path, mode="mcp-multi")
    with client:
        client.initialize()
        client.thread_start()
        result = client.turn_start("thread-1", "hello")
        assert len(result.tool_outputs) == 2
        observations = result.tool_call_observations
        assert len(observations) == 2
        assert [item["call_id_sha256"] for item in observations] == [
            hashlib.sha256(b"mcp-1").hexdigest(),
            hashlib.sha256(b"mcp-2").hexdigest(),
        ]
        assert [item["server"] for item in observations] == ["deeplaw", "deeplaw"]
        assert [item["tool_name"] for item in observations] == [
            "knowledge_support",
            "knowledge_search",
        ]
        for item in observations:
            assert item["status"] == "completed"
            for field in (
                "arguments_sha256",
                "arguments_bytes",
                "result_sha256",
                "result_bytes",
                "structured_content_sha256",
                "structured_content_bytes",
            ):
                assert item[field]
        rendered = repr(observations) + repr(client.events)
        assert "bounded" not in rendered
        assert "/tmp/tool-args" not in rendered
        observations[0]["tool_name"] = "mutated"
        assert result.tool_call_observations[0]["tool_name"] == "knowledge_support"


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
        assert client.events[-1]["method"] == "thread/compacted"
        assert client.events[-1]["thread_id_sha256"] == hashlib.sha256(
            b"thread-2"
        ).hexdigest()


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
        assert result.usage == {
            key: UNREPORTED
            for key in (
                "input_tokens",
                "cached_input_tokens",
                "cache_write_input_tokens",
                "output_tokens",
                "reasoning_output_tokens",
                "total_tokens",
            )
        }


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
