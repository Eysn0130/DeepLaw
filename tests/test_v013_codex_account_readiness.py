from __future__ import annotations

import hashlib
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

import benchmarks.hosts.codex_app_server_client as transport
from benchmarks.hosts.codex_account_readiness import CodexAccountReadinessClient
from benchmarks.hosts.codex_app_server_client import (
    CodexAppServerOutputLimitError,
    CodexAppServerProtocolError,
    CodexAppServerTimeoutError,
)


def _server(
    tmp_path: Path,
    *,
    mode: str = "normal",
    account_type: str | None = "chatgpt",
    requires_openai_auth: Any = True,
    extra_account: Any = None,
    stderr: bytes = b"synthetic-private-account-marker\n",
) -> list[str]:
    account = None if account_type is None else {"type": account_type}
    if isinstance(extra_account, dict) and isinstance(account, dict):
        account.update(extra_account)
    result = {"account": account, "requiresOpenaiAuth": requires_openai_auth}
    script = textwrap.dedent(
        """
        import json
        import sys
        import time

        MODE = __MODE__
        RESULT = __RESULT__
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
            return json.loads(line)

        while True:
            request_value = request()
            method = request_value.get("method")
            request_id = request_value.get("id")
            if method == "initialize":
                assert request_value["params"] == {
                    "clientInfo": {
                        "name": "deeplaw-benchmark",
                        "title": "DeepLaw benchmark Codex App Server client",
                        "version": "0",
                    },
                    "capabilities": {"experimentalApi": True},
                }
                notification_method = {
                    "account-updated": "account/updated",
                    "account-rate-limits": "account/rateLimits/updated",
                }.get(MODE, "configWarning")
                send({"method": notification_method, "params": {
                    "message": "synthetic-private-account-marker",
                    "email": "synthetic-only@example.invalid",
                }})
                if MODE == "unknown-notification":
                    send({"method": "thread/started", "params": {
                        "threadId": "synthetic-private-thread-marker",
                    }})
                send({"id": request_id, "result": {
                    "ready": True,
                    "email": "synthetic-only@example.invalid",
                }})
            elif method == "initialized":
                assert "id" not in request_value
                assert "params" not in request_value
            elif method == "account/read":
                assert request_value["params"] == {"refreshToken": False}
                if MODE == "server-request":
                    send({"id": 99, "method": "item/tool/call", "params": {
                        "tool": "secret-tool",
                        "arguments": {"email": "synthetic-only@example.invalid"},
                    }})
                elif MODE == "auth-refresh-request":
                    send({"id": 99, "method": "account/login", "params": {
                        "accessToken": "synthetic-private-token-marker",
                    }})
                elif MODE == "timeout":
                    time.sleep(30)
                else:
                    send({"id": request_id, "result": RESULT})
            else:
                raise SystemExit(3)
        """
    ).replace("__MODE__", repr(mode))
    script = script.replace("__RESULT__", repr(result))
    script = script.replace("__STDERR__", repr(stderr))
    path = tmp_path / "synthetic_account_server.py"
    path.write_text(script, encoding="utf-8")
    return [sys.executable, "-u", str(path)]


def _client(tmp_path: Path, **kwargs: Any) -> CodexAccountReadinessClient:
    server_kwargs = dict(kwargs)
    client_kwargs = {}
    if "dynamic_tool_handler" in server_kwargs:
        client_kwargs["dynamic_tool_handler"] = server_kwargs.pop(
            "dynamic_tool_handler"
        )
    return CodexAccountReadinessClient(
        _server(tmp_path, **server_kwargs),
        environment={"PATH": "/usr/bin", "PYTHONUNBUFFERED": "1"},
        cwd=tmp_path,
        timeout_seconds=3,
        **client_kwargs,
    )


def test_account_readiness_has_closed_projection_and_exact_wire_params(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    with client:
        assert client.account_readiness() == {
            "account_type": "chatgpt",
            "requires_openai_auth": True,
            "status": "executed",
            "formal_admission": False,
            "claim_eligible": False,
        }
        assert client.events == []
        assert client.stderr_metadata == {
            "sha256": None,
            "bytes": len(b"synthetic-private-account-marker\n"),
        }


def test_account_readiness_never_constructs_a_payload_hash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    real_sha256 = hashlib.sha256
    calls: list[bytes] = []

    def spy(value: bytes = b"", *args: Any, **kwargs: Any) -> Any:
        calls.append(value)
        return real_sha256(value, *args, **kwargs)

    monkeypatch.setattr(transport.hashlib, "sha256", spy)
    client = _client(tmp_path)
    with client:
        assert client.account_readiness()["account_type"] == "chatgpt"
        assert client.stderr_metadata["sha256"] is None
        assert client.events == []
        assert client._project_event(
            "item/completed",
            {"item": {"type": "tool", "result": "synthetic-private"}},
        ) is None
    assert calls == []


@pytest.mark.parametrize(
    ("wire_type", "expected"),
    [("chatgpt", "chatgpt"), ("apiKey", "api_key")],
)
def test_account_readiness_maps_only_supported_account_types(
    tmp_path: Path,
    wire_type: str,
    expected: str,
) -> None:
    with _client(tmp_path, account_type=wire_type) as client:
        result = client.account_readiness()
    assert result["account_type"] == expected


def test_account_readiness_maps_missing_account_to_none(tmp_path: Path) -> None:
    with _client(tmp_path, account_type=None, requires_openai_auth=False) as client:
        result = client.account_readiness()
    assert result == {
        "account_type": "none",
        "requires_openai_auth": False,
        "status": "executed",
        "formal_admission": False,
        "claim_eligible": False,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"account_type": "unsupported"},
        {"account_type": 7},
        {"requires_openai_auth": "true"},
    ],
)
def test_account_readiness_unknown_or_malformed_types_fail_closed(
    tmp_path: Path,
    kwargs: dict[str, Any],
) -> None:
    client = _client(tmp_path, **kwargs)
    with client, pytest.raises(
        CodexAppServerProtocolError,
        match="account/read response is invalid",
    ) as raised:
        client.account_readiness()
    assert "synthetic-private" not in str(raised.value)


def test_account_readiness_rejects_top_level_response_shape_changes() -> None:
    # The server helper only emits the closed response shape; exercise the
    # parser directly with an added payload field to keep the raw value out of
    # the client transport fixture.
    with pytest.raises(CodexAppServerProtocolError, match="response is invalid"):
        CodexAccountReadinessClient._project_account_readiness(
            {
                "account": {"type": "chatgpt", "email": "secret"},
                "requiresOpenaiAuth": True,
                "private": "synthetic-private-account-marker",
            }
        )


@pytest.mark.parametrize("mode", ["account-updated", "account-rate-limits"])
def test_account_notifications_are_discarded_without_projection(
    tmp_path: Path,
    mode: str,
) -> None:
    with _client(tmp_path, mode=mode) as client:
        assert client.account_readiness()["status"] == "executed"
        assert client.events == []


@pytest.mark.parametrize(
    "mode",
    ["server-request", "auth-refresh-request"],
)
def test_account_readiness_rejects_server_requests_before_dynamic_handler(
    tmp_path: Path,
    mode: str,
) -> None:
    calls: list[Any] = []

    def handler(*args: Any) -> dict[str, Any]:
        calls.append(args)
        return {"contentItems": [], "success": True}

    client = _client(tmp_path, mode=mode, dynamic_tool_handler=handler)
    with client, pytest.raises(
        CodexAppServerProtocolError,
        match="rejects server requests",
    ):
        client.account_readiness()
    assert calls == []


def test_account_readiness_rejects_unknown_notifications(tmp_path: Path) -> None:
    with _client(tmp_path, mode="unknown-notification") as client, pytest.raises(
        CodexAppServerProtocolError,
        match="unsupported notification",
    ):
        client.account_readiness()
    assert client.events == []


@pytest.mark.parametrize(
    "operation",
    [
        lambda client: client.thread_start(params={"ephemeral": True}),
        lambda client: client.start_thread(params={"ephemeral": True}),
        lambda client: client.model_list(),
        lambda client: client.list_models(),
        lambda client: client.mcp_server_status_list(),
        lambda client: client.list_mcp_server_status(),
        lambda client: client.thread_resume("thread-1"),
        lambda client: client.resume_thread("thread-1"),
        lambda client: client.thread_fork("thread-1"),
        lambda client: client.fork_thread("thread-1"),
        lambda client: client.thread_delete("thread-1"),
        lambda client: client.delete_thread("thread-1"),
        lambda client: client.turn_start("thread-1", "input"),
        lambda client: client.start_turn("thread-1", "input"),
        lambda client: client.thread_compact_start("thread-1"),
        lambda client: client.compact_thread("thread-1"),
        lambda client: client.thread_compact("thread-1"),
        lambda client: client._send_message(
            {"id": 1, "method": "send", "params": {"secret": "payload"}}
        ),
    ],
)
def test_account_readiness_has_no_thread_model_or_send_bypass(
    tmp_path: Path,
    operation: Any,
) -> None:
    client = _client(tmp_path)
    with pytest.raises(
        CodexAppServerProtocolError,
        match="outbound method is not allowed",
    ):
        operation(client)
    assert client.process_id is None


def test_account_readiness_rejects_refresh_token_truthy_integer_before_write(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    with pytest.raises(
        CodexAppServerProtocolError,
        match="account/read request is invalid",
    ):
        client._send_message(
            {"id": 1, "method": "account/read", "params": {"refreshToken": 0}}
        )
    assert client.process_id is None


@pytest.mark.parametrize(
    "message",
    [
        {"method": "initialized", "private": "synthetic-only"},
        {"id": 3, "method": "account/read", "params": {"refreshToken": False},
         "private": "synthetic-only"},
        {"id": 0, "method": "account/read", "params": {"refreshToken": False}},
        {"id": 3, "method": "initialize", "params": {}, "private": "synthetic-only"},
        {"id": 3, "method": "initialize", "params": {"private": "synthetic-only"}},
    ],
)
def test_account_readiness_rejects_nonclosed_outbound_envelopes(
    tmp_path: Path, message: dict[str, Any],
) -> None:
    client = _client(tmp_path)
    with client, pytest.raises(
        CodexAppServerProtocolError, match=r"request is invalid|notification",
    ):
        client.initialize()
        client._send_message(message)
    assert client.process_id is None


def test_account_readiness_preserves_stderr_byte_limit_without_a_digest(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, stderr=b"x" * 256)
    client.max_output_bytes = 32
    with pytest.raises(CodexAppServerOutputLimitError):
        client.account_readiness()
    assert client.process_id is None
    assert client.stderr_metadata["sha256"] is None


def test_account_readiness_preserves_stdout_byte_limit(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        extra_account={"email": "x" * 4096},
    )
    client.max_output_bytes = 128
    with pytest.raises(CodexAppServerOutputLimitError):
        client.account_readiness()
    assert client.process_id is None


def test_account_readiness_preserves_timeout_and_clears_transient_buffers(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path, mode="timeout")
    client.timeout_seconds = 0.05
    with pytest.raises(CodexAppServerTimeoutError):
        client.account_readiness()
    assert client.process_id is None
    assert client._stdout_buffer == bytearray()
    assert client._leak_scan_tails == {"stdout": b"", "stderr": b""}


def test_account_readiness_discards_email_and_plan_without_hashing_or_returning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[bytes] = []
    real_sha256 = hashlib.sha256

    def spy(value: bytes = b"", *args: Any, **kwargs: Any) -> Any:
        calls.append(value)
        return real_sha256(value, *args, **kwargs)

    monkeypatch.setattr(transport.hashlib, "sha256", spy)
    client = _client(
        tmp_path,
        extra_account={
            "email": "synthetic-only@example.invalid",
            "planType": "pro",
        },
    )
    with client:
        result = client.account_readiness()
        assert result == {
            "account_type": "chatgpt",
            "requires_openai_auth": True,
            "status": "executed",
            "formal_admission": False,
            "claim_eligible": False,
        }
        assert client.events == []
    assert calls == []
    assert "synthetic-only@example.invalid" not in repr(result)
    assert "pro" not in repr(result)
