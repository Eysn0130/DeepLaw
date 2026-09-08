"""Owner-deployed, bounded OpenCode producer; never a model or credential host.

The executable has separate install/prepare/run/reopen/proxy commands. Only run
starts OpenCode. A read proxy enforces the per-turn tool budget before forwarding
MCP calls, and the supervisor correlates its observations with native tool parts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Mapping, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.util import assert_provider_output_safe, canonical_json, strict_json_loads

CONTROL = "deeplaw.opencode-supervised-task-control/v1"
OBSERVATION = "deeplaw.host-mcp-observation/v1"
RESULT = "deeplaw.v013-host-task-result/v3"
ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 4 * 1024 * 1024
TOOL = "deeplaw_knowledge_knowledge_support"


class ProducerError(ValueError):
    """A bounded producer observation could not be established."""


def encoded(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else encoded(value)).hexdigest()


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ProducerError(reason)


def read_json(path: Path, *, maximum: int = MAX_BYTES) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), "input is not a regular file")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    require(0 < len(raw) <= maximum, "input exceeds its byte bound")
    value = strict_json_loads(raw.decode("utf-8"))
    require(isinstance(value, dict), "input is not an object")
    return value


def write_json(path: Path, value: Any) -> None:
    raw = encoded(value)
    require(len(raw) <= MAX_BYTES, "retained observation exceeds its byte bound")
    with path.open("xb") as stream:
        stream.write(raw + b"\n")
    path.chmod(0o600)


def contract(name: str, value: Any) -> None:
    schema = read_json(ROOT / "contracts" / name)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def safe(value: Any) -> None:
    assert_provider_output_safe(value, interface="knowledge_support")


def exact_file(path: Path, expected: str) -> Path:
    # An explicitly selected Host symlink is allowed only after binding its real
    # single-link executable; no other indirect deployment input is accepted.
    selected = path.resolve(strict=True)
    details = selected.stat()
    require(selected.is_file() and details.st_nlink == 1, "execution target is not regular")
    require(digest(selected.read_bytes()) == expected, "execution bytes changed")
    return selected


def validate_control(value: Mapping[str, Any], *, now: datetime | None = None) -> None:
    contract("opencode-supervised-task-control.v1.schema.json", value)
    instant = now or datetime.now(UTC)
    issued = datetime.fromisoformat(value["issued_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
    require(issued <= instant <= expires, "control challenge expired")
    require(0 < (expires - issued).total_seconds() <= 900, "control lifetime exceeds bound")


def advertised_receipt(tools: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    require(
        len(tools) == 1 and tools[0].get("name") == "knowledge_support", "tool inventory differs"
    )
    tool = tools[0]
    schema = tool.get("inputSchema", {})
    operations = sorted(
        branch.get("$ref", "").rsplit("/", 1)[-1] for branch in schema.get("oneOf", [])
    )
    require(operations == ["context", "explain", "query", "read"], "advertised operations differ")
    require(schema.get("title") == "DeepLaw Knowledge Support Provider Input v8", "input is not v8")
    require(len(encoded(tool)) <= 12288, "tool definition exceeds v8 bound")
    return {
        "input_schema_version": "v8",
        "output_schema_version": "v7",
        "operations": operations,
        "input_schema_bytes": len(encoded(schema)),
        "input_schema_sha256": digest(schema),
        "tool_definition_bytes": len(encoded(tool)),
        "tool_definition_sha256": digest(tool),
    }


def visible_targets(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if isinstance(value, dict):
        knowledge = value.get("knowledge_id")
        revision = value.get("knowledge_revision_id")
        if isinstance(knowledge, str) and isinstance(revision, str):
            result.append({"kind": "knowledge", "knowledge_id": knowledge, "revision_id": revision})
        for child in value.values():
            result.extend(visible_targets(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(visible_targets(child))
    return list({encoded(item): item for item in result}.values())


class ReadBudget:
    """Enforce two ordered successful reads, including attempted excess calls."""

    def __init__(self) -> None:
        self.count = 0
        self.targets: list[dict[str, str]] = []
        self.content_bytes = 0
        self.failed = False
        self.requested_target: dict[str, Any] | None = None

    def request(self, arguments: Mapping[str, Any]) -> None:
        require(not self.failed and self.count < 2, "turn tool budget exhausted")
        contract("knowledge-support.input.v8.schema.json", arguments)
        operation = arguments.get("operation")
        require(arguments.get("scope") == "project", "read scope differs")
        require(arguments.get("max_sensitivity") == "public", "read sensitivity differs")
        require(arguments.get("max_chars", 4000) <= 4000, "read character limit differs")
        if self.count == 0:
            require(operation in {"query", "context"}, "first call must discover a reference")
        else:
            require(operation == "read", "second call must read an exact reference")
            require(
                arguments.get("target") in self.targets, "target was not delivered by discovery"
            )
            require(arguments.get("offset", 0) == 0, "single-task continuation is not authorized")
            self.requested_target = dict(arguments["target"])
        self.count += 1

    def response(
        self,
        result: Mapping[str, Any],
        *,
        expected_target: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result_raw = encoded(result)
        require(len(result_raw) <= 197632, "CallToolResult exceeds bound")
        require(not result.get("isError", False), "MCP call failed")
        content = result.get("content")
        require(isinstance(content, list) and len(content) == 1, "MCP text channel differs")
        text = content[0].get("text")
        require(isinstance(text, str), "MCP text is unavailable")
        outer = result.get("structuredContent")
        require(isinstance(outer, dict), "MCP structured result is unavailable")
        contract("knowledge-support.output.v7.schema.json", outer)
        safe(outer)
        raw = text.encode("utf-8")
        self.content_bytes += len(raw)
        require(self.content_bytes <= 65536, "turn provider budget exhausted")
        if self.count == 1:
            require(outer.get("operation") in {"query", "context"}, "discovery response differs")
            capsule = outer["result"]["capsule"]
            require(text == canonical_json(capsule), "capsule channel bytes differ")
            self.targets = visible_targets(capsule)
            require(bool(self.targets), "discovery returned no exact knowledge reference")
        else:
            require(outer.get("operation") == "read", "exact response differs")
            require(text == canonical_json(outer), "read channel bytes differ")
            require(
                expected_target == self.requested_target
                and outer["result"]["target"] == expected_target,
                "read target differs",
            )
        return {
            "content_sha256": digest(raw),
            "content_bytes": len(raw),
            "structured_sha256": digest(outer),
            "structured_bytes": len(encoded(outer)),
            "result_sha256": digest(result_raw),
            "result_bytes": len(result_raw),
            "targets": self.targets,
            "response_target": outer["result"]["target"] if self.count == 2 else None,
        }


def correlate_calls(
    parts: Sequence[Mapping[str, Any]], records: Sequence[Mapping[str, Any]], *, session: str
) -> list[dict[str, Any]]:
    """Bind actual OpenCode completed tool parts to exact proxy transport bytes."""
    calls = [part for part in parts if part.get("type") == "tool"]
    require(len(calls) == len(records) == 2, "Host did not execute the two bounded calls")
    seen: set[str] = set()
    result = []
    for part, record in zip(calls, records, strict=True):
        state = part.get("state", {})
        call_id = part.get("callID")
        require(isinstance(call_id, str) and call_id not in seen, "duplicate native tool call")
        seen.add(call_id)
        require(part.get("sessionID") == session, "native tool session differs")
        require(
            part.get("tool") == TOOL and state.get("status") == "completed", "native tool failed"
        )
        require(digest(state.get("input")) == record["arguments_sha256"], "native arguments differ")
        output = state.get("output")
        require(isinstance(output, str), "native tool output is not text")
        if digest(output.encode()) != record["content_sha256"]:
            # Fixed legacy processor fallback is JSON.stringify(CallToolResult).
            # Accept that exact documented representation, not arbitrary wrappers.
            try:
                decoded = strict_json_loads(output)
            except ValueError as exc:
                raise ProducerError("Host tool result representation differs") from exc
            require(isinstance(decoded, dict), "Host tool result representation differs")
            require(digest(decoded) == record["result_sha256"], "Host MCP result differs")
            require(len(encoded(decoded)) == record["result_bytes"], "Host MCP result size differs")
            require(
                digest(decoded.get("structuredContent")) == record["structured_sha256"],
                "Host structured MCP result differs",
            )
            output = decoded["content"][0]["text"]
        require(digest(output.encode()) == record["content_sha256"], "Host tool text differs")
        require(len(output.encode()) == record["content_bytes"], "Host tool result size differs")
        result.append(
            {
                **record,
                "call_id_sha256": digest(call_id.encode()),
                "message_sha256": digest(str(part["messageID"]).encode()),
                "session_sha256": digest(session.encode()),
                "native_part_sha256": digest(part),
                "correlation": "opencode_completed_tool_part_and_mcp_proxy",
            }
        )
    return result


def validate_launch_prefix(value: Mapping[str, Any]) -> None:
    require(set(value) == {"argv", "files"}, "MCP prefix fields differ")
    require(
        isinstance(value["argv"], list) and len(value["argv"]) <= 8,
        "MCP prefix argument count differs",
    )
    require(
        all(isinstance(arg, str) and 0 < len(arg) <= 1024 for arg in value["argv"]),
        "MCP prefix argument differs",
    )
    require(
        isinstance(value["files"], list) and len(value["files"]) <= 8, "MCP prefix resources differ"
    )
    pinned = set()
    for item in value["files"]:
        require(set(item) == {"path", "sha256"}, "MCP prefix resource fields differ")
        path = Path(item["path"])
        require(path.is_absolute() and not path.is_symlink(), "MCP prefix path differs")
        exact_file(path, item["sha256"])
        pinned.add(str(path))
    if value["argv"]:
        require(value["argv"][0] in pinned, "MCP prefix executable is not pinned")
        require(
            all(arg in pinned for arg in value["argv"] if arg.startswith("/")),
            "MCP prefix absolute resource is not pinned",
        )


def validate_proxy_launch(config: Mapping[str, Any]) -> None:
    frozen = config["expected_launch"]
    prefix = config["mcp_launch_prefix"]
    require(digest(prefix) == frozen["prefix_sha256"], "frozen MCP prefix differs")
    deployment = verify_deployment()
    require(
        deployment["source_closure_sha256"] == frozen["source_closure_sha256"],
        "frozen MCP source closure differs",
    )
    validate_launch_prefix(prefix)
    exact_file(Path(config["deeplaw"]), frozen["deeplaw_sha256"])


@contextmanager
def proxy_child(config: Mapping[str, Any]):
    from deeplaw import bounded_subprocess
    from deeplaw.closed_mcp_launcher import closed_mcp_environment

    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}

    def interrupted(signum: int, frame: Any) -> None:
        raise ProducerError("MCP proxy interrupted")

    try:
        for signum in previous:
            signal.signal(signum, interrupted)
        with closed_mcp_environment(
            surface="knowledge_support", vault_path=config["vault"]
        ) as closed:
            child, guard = bounded_subprocess.spawn_process(
                [
                    *config["mcp_launch_prefix"]["argv"],
                    config["deeplaw"],
                    "knowledge",
                    "mcp",
                    "--closed-environment",
                    "--stdio",
                ],
                cwd=closed.cwd,
                env=closed.environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                **({"start_new_session": True} if os.name == "posix" else {}),
            )
            try:
                yield child
            finally:
                cleanup = False
                with suppress(Exception):
                    cleanup = bounded_subprocess._kill(child, guard)
                try:
                    exited = child.wait(timeout=5) is not None
                except Exception:
                    exited = False
                require(cleanup and exited, "MCP process cleanup unconfirmed")
                with suppress(OSError):
                    if child.stdin is not None:
                        child.stdin.close()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def proxy_requests(fault: threading.Event, response_done: threading.Event):
    incoming: queue.Queue[bytes] = queue.Queue(maxsize=1)
    finished = threading.Event()
    stopping = threading.Event()

    def receive() -> None:
        try:
            for raw in iter(lambda: sys.stdin.buffer.readline(MAX_BYTES + 1), b""):
                while not stopping.is_set():
                    try:
                        incoming.put(raw, timeout=0.1)
                        break
                    except queue.Full:
                        pass
                if stopping.is_set():
                    return
        except Exception:
            fault.set()
        finally:
            finished.set()

    reader = threading.Thread(target=receive, daemon=True)
    reader.start()
    try:
        while True:
            require(not fault.is_set(), "MCP proxy input or response failed")
            if response_done.is_set() or (finished.is_set() and incoming.empty()):
                return
            with suppress(queue.Empty):
                yield incoming.get(timeout=0.1)
    finally:
        stopping.set()
        # A pipe read can remain blocked after a signal. This daemon owns no
        # child process and ends with the one-shot proxy process.
        reader.join(timeout=0.1)


def proxy(config_path: Path) -> None:
    """Forward a fixed read-only MCP child with no Host secrets in its environment."""
    config = read_json(config_path)
    validate_proxy_launch(config)
    # Never unlink this claim: reconnecting/restarting MCP must not reset the
    # per-turn budget. A failed connection consumes this one-shot deployment.
    write_json(Path(config["proxy_claim"]), {"nonce_sha256": config["nonce_sha256"]})
    state_path = Path(config["turn_state"])
    log_path = Path(config["proxy_log"])
    budgets: dict[str, ReadBudget] = {}
    pending: dict[Any, tuple[str, dict[str, Any]]] = {}
    lock = threading.Lock()
    fault = threading.Event()
    response_done = threading.Event()
    reader: threading.Thread | None = None
    try:
        with proxy_child(config) as child:
            require(child.stdin is not None and child.stdout is not None, "MCP child pipes missing")

            def log(value: Any) -> None:
                safe(value)
                with log_path.open("ab") as stream:
                    stream.write(encoded(value) + b"\n")
                require(log_path.stat().st_size <= MAX_BYTES, "proxy log exceeds bound")

            def responses() -> None:
                try:
                    for raw in iter(lambda: child.stdout.readline(MAX_BYTES + 1), b""):
                        require(len(raw) <= MAX_BYTES, "MCP response exceeds bound")
                        response = strict_json_loads(raw.decode())
                        with lock:
                            selected = pending.pop(response.get("id"), None)
                            if selected is not None:
                                turn, request = selected
                                require("result" in response, "MCP response failed")
                                measured = budgets[turn].response(
                                    response["result"],
                                    expected_target=request.get("target"),
                                )
                                log(
                                    {
                                        "turn": turn,
                                        "operation": request["operation"],
                                        "arguments_sha256": digest(request),
                                        "arguments_bytes": len(encoded(request)),
                                        "target": request.get("target"),
                                        **measured,
                                    }
                                )
                            elif "result" in response and isinstance(response["result"], dict):
                                tools = response["result"].get("tools")
                                if tools is not None:
                                    log({"advertisement": advertised_receipt(tools)})
                        sys.stdout.buffer.write(raw)
                        sys.stdout.buffer.flush()
                except Exception:
                    fault.set()
                finally:
                    if pending:
                        fault.set()
                    response_done.set()

            reader = threading.Thread(target=responses, daemon=True)
            reader.start()
            for raw in proxy_requests(fault, response_done):
                require(not fault.is_set() and len(raw) <= MAX_BYTES, "proxy transport rejected")
                request = strict_json_loads(raw.decode())
                if request.get("method") == "tools/call":
                    params = request.get("params", {})
                    require(params.get("name") == "knowledge_support", "proxy tool is forbidden")
                    state = read_json(state_path)
                    require(
                        state.get("nonce_sha256") == config["nonce_sha256"],
                        "turn challenge differs",
                    )
                    turn = str(state["turn"])
                    require(turn in {"1", "2"} and state.get("active") is True, "tool outside turn")
                    with lock:
                        require(not pending, "concurrent tool calls are forbidden")
                        arguments = params.get("arguments", {})
                        budgets.setdefault(turn, ReadBudget()).request(arguments)
                        pending[request["id"]] = (turn, arguments)
                elif request.get("method") not in {
                    "initialize",
                    "notifications/initialized",
                    "ping",
                    "tools/list",
                    "notifications/cancelled",
                }:
                    raise ProducerError("proxy method is forbidden")
                child.stdin.write(raw)
                child.stdin.flush()
    finally:
        if reader is not None:
            reader.join(timeout=2)
            require(not reader.is_alive(), "MCP response cleanup unconfirmed")
    require(not fault.is_set(), "MCP proxy failed closed")


class RequestGuard:
    """Inspect every request on the configured Provider route before forwarding.

    The real key exists only in this owner-external guard, never in Host/MCP
    environments. This is a limit on this route, not an OS network sandbox.
    """

    def __init__(self, *, key: str, nonce: str, forward: bool = False) -> None:
        self.key = key
        self.nonce = nonce
        self.forward = forward
        self.active = False
        self.requests: list[dict[str, Any]] = []
        self.rejected = 0
        self.lock = threading.Lock()
        self.server: Any = None
        self.thread: threading.Thread | None = None

    def inspect(self, body: bytes, *, path: str, authorization: str) -> dict[str, Any]:
        require(self.active, "Provider request outside an authorized turn")
        require(path == "/chat/completions", "Provider route is forbidden")
        require(authorization == "Bearer " + self.nonce, "Provider guard association differs")
        require(0 < len(body) <= 262144, "Provider request exceeds bound")
        value = strict_json_loads(body.decode("utf-8"))
        require(isinstance(value, dict), "Provider request is not an object")
        require(
            set(value)
            <= {
                "model",
                "messages",
                "temperature",
                "top_p",
                "max_tokens",
                "stream",
                "stream_options",
                "tools",
                "tool_choice",
                "parallel_tool_calls",
                "thinking",
                "frequency_penalty",
                "presence_penalty",
                "stop",
                "response_format",
            },
            "Provider request shape is unknown",
        )
        require(value.get("model") == "deepseek-v4-flash", "Provider model differs")
        require(isinstance(value.get("messages"), list), "Provider messages are unavailable")
        require(1 <= len(value["messages"]) <= 32, "Provider message count exceeds bound")
        if "tools" in value:
            require(
                isinstance(value["tools"], list) and len(value["tools"]) == 1,
                "Provider tool inventory differs",
            )
            tool = value["tools"][0]
            require(
                isinstance(tool, dict)
                and set(tool) == {"type", "function"}
                and tool["type"] == "function",
                "Provider tool shape differs",
            )
            function = tool["function"]
            require(
                isinstance(function, dict)
                and set(function) <= {"name", "description", "parameters", "strict"}
                and function.get("name") == TOOL,
                "Provider tool name differs",
            )
            require(isinstance(function.get("parameters"), dict), "Provider tool schema missing")
            require(len(encoded(tool)) <= 16384, "Provider tool schema exceeds bound")
        safe(value)  # Full request, including Host environment/system/tool content.
        decoded_body = canonical_json(value)
        require(self.key not in decoded_body and self.nonce not in decoded_body, "canary in body")
        for message in value["messages"]:
            require(isinstance(message, dict), "Provider message is invalid")
            require(
                set(message)
                <= {
                    "role",
                    "content",
                    "tool_calls",
                    "tool_call_id",
                    "reasoning_content",
                    "name",
                },
                "Provider message shape is unknown",
            )
            require(
                message.get("role") in {"system", "user", "assistant", "tool"},
                "message role differs",
            )
            require(
                message.get("content") is None or isinstance(message.get("content"), str),
                "Provider multimodal message is forbidden",
            )
            for call in message.get("tool_calls", []):
                require(
                    isinstance(call, dict)
                    and set(call) == {"id", "type", "function"}
                    and call["type"] == "function",
                    "Provider call shape differs",
                )
                require(
                    set(call["function"]) == {"name", "arguments"}
                    and call["function"]["name"] == TOOL,
                    "Provider call name differs",
                )
                safe(strict_json_loads(call["function"]["arguments"]))
        return value

    def start(self) -> str:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        guard = self

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args: Any, **kwargs: Any) -> None:
                raise ProducerError("Provider redirect is forbidden")

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def do_POST(self) -> None:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    require(0 < length <= 262144, "Provider body length differs")
                    body = self.rfile.read(length)
                    with guard.lock:
                        guard.inspect(
                            body,
                            path=self.path,
                            authorization=self.headers.get("Authorization", ""),
                        )
                        require(len(guard.requests) < 6, "guard request budget exhausted")
                        require(guard.forward, "real Provider forwarding not enabled")
                        guard.requests.append({"sha256": digest(body), "bytes": len(body)})
                    request = urllib.request.Request(
                        "https://api.deepseek.com/chat/completions",
                        data=body,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer " + guard.key,
                        },
                    )
                    with opener.open(request, timeout=300) as response:
                        data = response.read(MAX_BYTES + 1)
                        require(len(data) <= MAX_BYTES, "Provider response exceeds bound")
                        content_type = response.headers.get("Content-Type", "application/json")
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception:
                    guard.rejected += 1
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b'{"error":"supervised_provider_request_rejected"}')

            def do_GET(self) -> None:
                guard.rejected += 1
                self.send_response(403)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.active = False
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)
            require(not self.thread.is_alive(), "Provider guard cleanup unconfirmed")


def validate_host_observation(
    value: Mapping[str, Any],
    *,
    result: Mapping[str, Any],
    envelope: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> None:
    """Reopen source evidence; never promote the local service-driver seam."""
    contract("host-mcp-observation.v1.schema.json", value)
    safe(value)
    control = value["control"]
    require(control["run_id"] == result["run_id"], "Host observation run differs")
    require(control["workflow_run_id"] == result["workflow_run_id"], "Host workflow differs")
    require(control["candidate_binding"] == envelope["candidate_binding"], "Host candidate differs")
    require(result["host"] == value["host"] == "opencode", "Host observation host differs")
    from benchmarks.hosts import v013_native_event_adapter as adapter
    from deeplaw.native_host import derive_native_host_receipt

    proof = value["public_fork_receipts"][0]
    process = proof["process_binding"]
    for field in (
        "run_id",
        "candidate_binding",
        "host_identity_sha256",
        "host_identity_source_sha256",
        "nonce_sha256",
    ):
        require(process[field] == control[field], "fork control binding differs")
    require(
        process["broker_source"]["sha256"] == control["broker_source_sha256"],
        "fork broker binding differs",
    )
    fork_events = [event for event in events if event.get("event_type") == "fork"]
    require(len(fork_events) == 1, "single task fork count differs")
    fork = fork_events[0]
    require(
        proof["parent_session_sha256"] != proof["child_session_sha256"]
        and fork["parent_session_sha256"] != fork["session_sha256"],
        "fork sessions must be distinct",
    )
    require(
        process["run_binding"] == {
            "evidence_run_id": control["workflow_run_id"],
            "qualification_run_id": control["workflow_run_id"],
        },
        "fork run binding differs",
    )
    require(
        process["host_binary"] == {
            "version": fork["host_identity"]["version"],
            "sha256": fork["host_identity"]["executable_sha256"],
        },
        "fork binary binding differs",
    )
    require(
        digest(fork["host_identity"]) == control["host_identity_sha256"],
        "fork identity binding differs",
    )
    require(
        proof["parent_session_sha256"] == fork["parent_session_sha256"]
        and proof["child_session_sha256"] == fork["session_sha256"],
        "fork session proof differs",
    )
    require(proof["request_body_sha256"] == digest(b"{}"), "fork request body differs")
    native_binding = adapter._public_fork_native_binding(fork, derive_native_host_receipt(fork))
    rebuilt = adapter._public_fork_process_proof(
        process=process,
        route_sha256=proof["route_observation_sha256"],
        request_body_sha256=proof["request_body_sha256"],
        response_sha256=proof["response_sha256"],
        parent_session_sha256=proof["parent_session_sha256"],
        child_session_sha256=proof["child_session_sha256"],
        child_plugin_event_sha256=proof["child_plugin_event_sha256"],
        native_binding=native_binding,
    )
    require(rebuilt == proof["process_proof"], "fork correlation proof differs")
    seen: set[str] = set()
    for index, turn in enumerate(value["turns"], 1):
        require(turn["index"] == index, "Host turn order differs")
        require(turn["ledger_before"] == turn["ledger_after"], "read turn changed Ledger")
        event_index = turn["event_index"]
        require(event_index < len(events), "Host message event missing")
        event = events[event_index]
        require(event["schema_version"] == "deeplaw.native-host-event/v3", "Host event is not v3")
        require(
            digest(event["host_identity"]) == control["host_identity_sha256"],
            "Host binary identity binding differs",
        )
        require(event["event_type"] == "chat.message", "Host event is not a message")
        require(event["session_sha256"] == turn["session_sha256"], "Host session differs")
        require(event.get("route", {}).get("status") == "exact", "Host route is not exact")
        calls = turn["calls"]
        require(calls[0]["operation"] in {"query", "context"}, "discovery call missing")
        require(calls[1]["operation"] == "read", "exact Host read missing")
        require(calls[1]["target"] in calls[0]["targets"], "read lineage differs")
        require(calls[0]["response_target"] is None
                and calls[1]["response_target"] == calls[1]["target"],
                "read response target pairing differs")
        require(sum(call["content_bytes"] for call in calls) <= 65536, "Host content exceeds bound")
        for call in calls:
            require(call["turn"] == str(index), "tool belongs to another turn")
            require(call["session_sha256"] == turn["session_sha256"], "tool session differs")
            require(call["call_id_sha256"] not in seen, "Host call replayed")
            seen.add(call["call_id_sha256"])
    first = value["turns"][0]
    last = value["turns"][-1]
    require(first["session_sha256"] != last["session_sha256"], "fork session did not change")
    require(
        any(
            event.get("event_type") == "fork"
            and event.get("session_sha256") == last["session_sha256"]
            and event.get("parent_session_sha256") == first["session_sha256"]
            for event in events
        ),
        "actual fork event is missing",
    )
    require(
        result["first_correct_action"]["event_index"] == first["event_index"], "task event differs"
    )
    require(
        result["first_correct_action"]["observed"] == first["next_action_matches"],
        "task action differs",
    )
    require(
        result["decision_preservation"]["observed"]
        == all(turn["decision_matches"] for turn in value["turns"]),
        "task decision differs",
    )


def privacy_wrapper(source_import: str, *, directory: str, worktree: str) -> str:
    """Wrap only the fixed native environment block after the original hook."""
    known = json.dumps([directory, worktree])
    return f"""import native from {json.dumps(source_import)};
const known = {known};
export function transform(system) {{
  if (!Array.isArray(system) || system.some(x => typeof x !== "string"))
    throw new Error("supervised system shape differs");
  const all = system.join("\\n");
  if (all.includes("<available_references>") || all.includes("</available_references>"))
    throw new Error("supervised references forbidden");
  if ((all.match(/<env>/g) || []).length !== 1 || (all.match(/<\\/env>/g) || []).length !== 1)
    throw new Error("supervised env count differs");
  let count = 0;
  const pattern = [
    "<env>",
    "  Working directory: ([^\\n]+)",
    "  Workspace root folder: ([^\\n]+)",
    "  Is directory a git repo: (yes|no)",
    "  Platform: ([a-z0-9]+)",
    "  Today's date: ([^\\n]+)",
    "</env>",
  ].join("\\n");
  const result = system.map(text => text.replace(new RegExp(pattern, "g"),
    (whole, dir, root, git, platform, date) => {{
      if (dir !== known[0] || root !== known[1]) throw new Error("supervised env path differs");
      count++;
      return whole
        .replace("Working directory: " + dir, "Working directory: supervised-task")
        .replace("Workspace root folder: " + root, "Workspace root folder: supervised-task");
    }}));
  if (count !== 1) throw new Error("supervised env shape differs");
  return result;
}}
export default {{ id: "deeplaw-native-supervised", server: async input => {{
  const hooks = await native.server(input);
  const original = hooks["experimental.chat.system.transform"];
  if (typeof original !== "function") throw new Error("native system hook missing");
  return {{ ...hooks, "experimental.chat.system.transform": async (input, output) => {{
    await original(input, output);
    output.system = transform(output.system);
  }} }};
}} }};
"""


def install_privacy_wrapper(repository: Path) -> dict[str, str]:
    plugin = repository / ".opencode/plugins/deeplaw-native.ts"
    require(plugin.is_file() and not plugin.is_symlink(), "exact native plugin missing")
    source_dir = repository / ".opencode/supervised-source"
    source_dir.mkdir(mode=0o700)
    source = source_dir / "deeplaw-native.ts"
    original_hash = digest(plugin.read_bytes())
    plugin.rename(source)
    wrapper = privacy_wrapper(
        "../supervised-source/deeplaw-native.ts",
        directory=str(repository),
        worktree=str(repository),
    )
    with plugin.open("x") as stream:
        stream.write(wrapper)
    return {
        "original_plugin_sha256": original_hash,
        "privacy_wrapper_sha256": digest(wrapper.encode()),
    }


def guard_process(config_path: Path) -> None:
    """The sole process that reads the owner-external credential file."""
    config = read_json(config_path)
    key_file = Path(config["key_file"])
    require(key_file.is_absolute() and not key_file.is_symlink(), "guard key file differs")
    require(key_file.resolve(strict=True) == key_file, "guard key file has an indirect ancestor")
    require(
        not key_file.is_relative_to(ROOT) and not key_file.is_relative_to(config_path.parent),
        "guard key must remain outside deployment and task state",
    )
    require(
        not any((parent / ".git").exists() for parent in key_file.parents),
        "guard key must remain outside repositories",
    )
    details = key_file.stat()
    require(
        key_file.is_file() and details.st_nlink == 1 and details.st_mode & 0o077 == 0,
        "guard key file is not owner-only",
    )
    require(
        details.st_uid == os.getuid() and 0 < details.st_size <= 4096,
        "guard key file ownership or size differs",
    )
    # This private deployment input is a single raw API key, not a shell/env file.
    key = key_file.read_text().strip()
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]{16,256}", key)), "guard key syntax differs")
    guard = RequestGuard(key=key, nonce=config["nonce"], forward=True)
    try:
        print(canonical_json({"url": guard.start(), "pid": os.getpid()}), flush=True)
        for raw in sys.stdin:
            require(len(raw) <= 1024, "guard command exceeds bound")
            command = strict_json_loads(raw)
            require(
                command in ({"active": True}, {"active": False}, {"stop": True}),
                "guard command differs",
            )
            if command == {"stop": True}:
                break
            guard.active = command["active"]
            print(
                canonical_json({"requests": guard.requests, "rejected": guard.rejected}), flush=True
            )
    finally:
        guard.stop()
    print(
        canonical_json(
            {"requests": guard.requests, "rejected": guard.rejected, "cleanup_confirmed": True}
        ),
        flush=True,
    )


class ExternalGuard:
    def __init__(self, config: Path) -> None:
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self.url = ""
        self.receipt: dict[str, Any] = {}

    def start(self) -> None:
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "benchmarks.hosts.opencode_single_task_producer",
                "guard",
                "--input",
                str(self.config),
            ],
            cwd=ROOT,
            env={"PATH": os.defpath, "PYTHONPATH": str(ROOT)},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.receipt = self._response()
        self.url = self.receipt["url"]
        require(
            re.fullmatch(r"http://127\.0\.0\.1:[0-9]+", self.url) is not None,
            "external guard route differs",
        )

    def _response(self) -> dict[str, Any]:
        import selectors

        require(self.process is not None and self.process.stdout is not None, "guard pipe missing")
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            require(bool(selector.select(timeout=10)), "guard response timeout")
        value = strict_json_loads(self.process.stdout.readline(4097))
        require(isinstance(value, dict), "guard response differs")
        return value

    def active(self, enabled: bool) -> None:
        require(self.process is not None and self.process.stdin is not None, "guard not started")
        self.process.stdin.write(canonical_json({"active": enabled}) + "\n")
        self.process.stdin.flush()
        self.receipt = self._response()
        require(self.receipt.get("rejected") == 0, "guard has rejected a request")

    def stop(self) -> None:
        if self.process is None:
            return
        try:
            require(self.process.stdin is not None, "guard input unavailable")
            self.process.stdin.write('{"stop":true}\n')
            self.process.stdin.flush()
            self.receipt = self._response()
            self.process.wait(timeout=10)
            require(
                self.process.returncode == 0 and self.receipt.get("cleanup_confirmed") is True,
                "guard cleanup unconfirmed",
            )
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
            if self.process.stdin:
                self.process.stdin.close()
            if self.process.stdout:
                self.process.stdout.close()


def deployment_sources() -> list[Path]:
    """Static local Python import closure plus bounded public contract/fixture data."""
    import ast

    pending = [Path(__file__).resolve()]
    selected: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in selected:
            continue
        selected.add(source)
        for node in ast.walk(ast.parse(source.read_text())):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module, *(node.module + "." + alias.name for alias in node.names)]
            for module in modules:
                if not module.startswith("benchmarks."):
                    continue
                relative = Path(*module.split("."))
                candidates = [ROOT / relative.with_suffix(".py"), ROOT / relative / "__init__.py"]
                pending.extend(
                    path for path in candidates if path.is_file() and path not in selected
                )
    selected.update((ROOT / "contracts").glob("*.json"))
    # Data loaded by the imported qualification helpers; no generated evidence,
    # workspace state, credentials, binaries or user material enters the bundle.
    selected.update(
        ROOT / "benchmarks/hosts" / name
        for name in (
            "pass16-continuity-task-cases-v1.json",
            "pass17-development-diagnostic-v1.json",
            "v013-host-task-cases-v1.json",
        )
    )
    selected.update((ROOT / "benchmarks/release").glob("v013-gate-classification-v*.json"))
    selected.update((ROOT / "benchmarks/release").glob("platform-core-test-manifest-v*.json"))
    return sorted(selected)


def install(destination: Path) -> None:
    destination = destination.absolute()
    require(destination.resolve() == destination, "deployment has a symlink ancestor")
    require(
        not destination.exists() and not destination.is_relative_to(ROOT),
        "deployment must be a fresh external directory",
    )
    destination.mkdir(mode=0o700)
    files = []
    for source in deployment_sources():
        require(not source.is_symlink(), "source closure contains a symlink")
        raw = source.read_bytes()
        require(len(raw) <= MAX_BYTES, "source closure file exceeds bound")
        relative = source.relative_to(ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
        files.append({"path": relative.as_posix(), "sha256": digest(raw), "bytes": len(raw)})
    write_json(
        destination / "deployment.json",
        {
            "schema_version": "deeplaw.supervised-producer-deployment/v1",
            "files": files,
            "source_closure_sha256": digest(files),
            "formal_admission": False,
            "credential_delivery_mode": "owner_external_guard_host_nonce_mcp_no_key",
            "gaps": [
                "os_isolation_unobserved",
                "global_network_and_cost_unobserved",
                "six_slot_formal_qualification_not_executed",
            ],
        },
    )


def verify_deployment() -> dict[str, Any]:
    require(
        ROOT.stat().st_mode & 0o077 == 0 and ROOT.stat().st_uid == os.getuid(),
        "deployment root is not owner-only",
    )
    require(ROOT.resolve() == ROOT, "deployment has a symlink ancestor")
    receipt = read_json(ROOT / "deployment.json")
    require(
        receipt["source_closure_sha256"] == digest(receipt["files"]), "deployment closure differs"
    )
    for item in receipt["files"]:
        relative = Path(item["path"])
        require(
            not relative.is_absolute() and ".." not in relative.parts, "deployment path differs"
        )
        source = ROOT / relative
        require(source.resolve() == source and source.is_file(), "deployment file unavailable")
        raw = source.read_bytes()
        require(
            len(raw) == item["bytes"] and digest(raw) == item["sha256"], "deployment bytes changed"
        )
    return receipt



def validate_runtime_entries(prepared: Mapping[str, Any]) -> None:
    for name in ("deeplaw", "node"):
        path = Path(prepared[name])
        require(path.is_absolute() and path.resolve() == path, "runtime entry path changed")
        exact_file(path, prepared["runtime_entry_hashes"][name])

def prepare(input_path: Path) -> None:
    """Owner invocation creates only a fresh synthetic task and its checkpoint."""
    from benchmarks.hosts import pass16_continuity_cases as cases
    from benchmarks.hosts import run_pass13_opencode_continuity_qualification as legacy

    deployment = verify_deployment()
    value = read_json(input_path)
    require(
        (set(value) - {"mcp_launch_prefix"})
        == {
            "root",
            "deeplaw",
            "opencode",
            "node",
            "host_identity_source",
            "candidate_binding",
            "key_file",
            "run_id",
            "workflow_run_id",
        },
        "deployment input fields differ",
    )
    validate_launch_prefix(value.get("mcp_launch_prefix", {"argv": [], "files": []}))
    root = Path(value["root"]).absolute()
    require(
        not root.exists() and not root.is_relative_to(ROOT), "task root must be fresh and external"
    )
    root.mkdir(mode=0o700)
    root = root.resolve()
    identity_path = Path(value["host_identity_source"])
    identity, identity_raw = legacy.host_preflight_receipt.load_host_identity_input_with_bytes(
        identity_path, repository=ROOT
    )
    host = identity["hosts"]["opencode"]
    binary = exact_file(Path(value["opencode"]), host["executable_sha256"])
    deeplaw = Path(value["deeplaw"]).resolve(strict=True)
    node = Path(value["node"]).resolve(strict=True)
    runtime_entry_hashes = {
        "deeplaw": digest(deeplaw.read_bytes()), "node": digest(node.read_bytes()),
    }
    validate_runtime_entries({"deeplaw": str(deeplaw), "node": str(node),
                              "runtime_entry_hashes": runtime_entry_hashes})
    case = cases.task_case("resume_fork")
    repository, concurrent, primary, other = legacy._create_git_task_repository(
        root,
        task_line="supervised-continuity",
        development=True,
    )
    # Ignore only runtime plugin artifacts through local Git metadata, without
    # modifying the committed synthetic task or its checkpoint identity.
    with (repository / ".git/info/exclude").open("a") as stream:
        stream.write("\n.opencode/\n")
    base = legacy.build_host_environment(root=root, opencode_binary=binary, node_binary=node)
    environment, _, plugin = legacy._prepare_scenario_state(
        base_environment=base,
        run_root=root / "runtime",
        repository=repository,
        deeplaw_executable=deeplaw,
        node_binary=node,
        expected_version=host["version"],
    )
    wrapper = install_privacy_wrapper(repository)
    fixture = legacy._seed_continuity_fixture(
        deeplaw,
        vault=repository / "vault",
        case=case,
        primary_binding=primary,
        concurrent_binding=other,
        concurrent_workspace=concurrent,
        environment=environment,
        cwd=repository,
    )
    checkpoint = case["current_checkpoint"]
    public = legacy._run_sink_request(
        deeplaw,
        vault=repository / "vault",
        grant_id=fixture["grant_id"],
        request={
            "operation": "remember",
            "idempotency_key": "supervised-public-procedure",
            "confirm_no_case_data": True,
            "title": "Supervised continuity procedure",
            "body": canonical_json(
                {"decision": checkpoint["decision"], "next_action": checkpoint["next_action"]}
            ),
            "kind": "procedure",
            "semantic_key": "supervised:continuity:procedure",
            "scope": "project",
            "sensitivity": "public",
        },
        environment=environment,
        cwd=repository,
    )
    write_json(
        root / "prepared.json",
        {
            **value,
            "root": str(root),
            "opencode": str(binary),
            "deeplaw": str(deeplaw),
            "node": str(node),
            "runtime_entry_hashes": runtime_entry_hashes,
            "mcp_launch_prefix_sha256": digest(
                value.get("mcp_launch_prefix", {"argv": [], "files": []})
            ),
            "repository": str(repository),
            "environment": environment,
            "fixture": fixture,
            "public_seed_receipt_sha256": digest(public),
            "case": case,
            "primary_binding": primary,
            "host_identity": host,
            "host_identity_source_sha256": digest(identity_raw),
            "selector_source_symlink": Path(value["opencode"]).is_symlink(),
            "deployment_sha256": deployment["source_closure_sha256"],
            "plugin": plugin,
            "privacy": wrapper,
        },
    )


def json_lines(path: Path) -> list[dict[str, Any]]:
    require(
        path.is_file() and path.stat().st_size <= MAX_BYTES, "observation log missing or too large"
    )
    return [strict_json_loads(line) for line in path.read_text().splitlines() if line]


def replace_state(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".pending")
    write_json(temporary, value)
    temporary.replace(path)


def api(server: Any, method: str, path: str, payload: Any = None) -> tuple[Any, bytes]:
    require(
        re.fullmatch(r"/(?:session(?:/ses_[A-Za-z0-9]+(?:/(?:message|fork))?)?)", path) is not None,
        "public API route differs",
    )
    body = None if payload is None else encoded(payload)
    request = urllib.request.Request(
        server.base_url + path,
        method=method,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=300) as response:
        require(response.status == 200, "public API failed")
        raw = response.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "public API response exceeds bound")
    return strict_json_loads(raw), raw


def fork_request(server: Any, parent: str, log: Path) -> tuple[Any, bytes, int]:
    """Hold the supervisor-facing response until the child plugin event exists."""
    started = time.monotonic()
    value, raw = api(server, "POST", f"/session/{parent}/fork", {})
    child = value.get("id")
    require(isinstance(child, str) and child != parent, "fork child identity differs")
    while time.monotonic() - started <= 30:
        observed = [
            item
            for item in json_lines(log)
            if item.get("event_type") == "session.created"
            and item.get("session_sha256") == digest(child.encode())
        ]
        if observed:
            require(len(observed) == 1, "fork child plugin event is duplicated")
            return value, raw, int((time.monotonic() - started) * 1000)
        time.sleep(0.05)
    raise ProducerError("fork child plugin barrier expired")


class RssSampler:
    """Sample visible process-tree RSS; this is not a kernel memory high-water mark."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.peak = 0
        self.stopping = threading.Event()
        self.failed = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self) -> None:
        try:
            while not self.stopping.is_set():
                snapshot = subprocess.run(
                    ["/bin/ps", "-axo", "pid=,ppid=,rss="],
                    env={"PATH": os.defpath, "LC_ALL": "C"},
                    capture_output=True,
                    timeout=5,
                    check=True,
                )
                rows = [tuple(map(int, line.split())) for line in snapshot.stdout.splitlines()]
                pids = {self.pid}
                for _ in range(32):
                    expanded = pids | {pid for pid, parent, _ in rows if parent in pids}
                    if expanded == pids:
                        break
                    pids = expanded
                self.peak = max(self.peak, sum(rss * 1024 for pid, _, rss in rows if pid in pids))
                self.stopping.wait(0.1)
        except Exception:
            self.failed.set()

    def stop(self) -> int:
        self.stopping.set()
        self.thread.join(timeout=6)
        require(
            not self.thread.is_alive() and not self.failed.is_set() and self.peak > 0,
            "RSS observation unavailable",
        )
        return self.peak


def measure_usage(
    observations: Sequence[Mapping[str, Any]],
    session: str,
    *,
    message_sha256s: set[str] | None = None,
) -> tuple[dict[str, int], dict[str, Any]]:
    messages = {}
    for item in observations:
        if item.get("schema_version") == "deeplaw.opencode-model-observation/v1" and (
            item.get("session_sha256") == digest(session.encode())
        ):
            if message_sha256s is not None and item.get("message_sha256") not in message_sha256s:
                continue
            require(
                item.get("provider_id") == "deepseek"
                and item.get("model_id") == "deepseek-v4-flash",
                "response model identity differs",
            )
            require(item.get("summary") is False, "unexpected summary model turn")
            messages[item["message_sha256"]] = item
    require(bool(messages), "actual model usage is missing")
    totals = dict.fromkeys(("input_tokens", "output_tokens", "cache_tokens", "reasoning_tokens"), 0)
    for item in messages.values():
        tokens = item["tokens"]
        values = {
            "input_tokens": tokens.get("input"),
            "output_tokens": tokens.get("output"),
            "cache_tokens": tokens.get("cache", {}).get("read"),
            "reasoning_tokens": tokens.get("reasoning"),
        }
        require(
            all(type(value) is int and value >= 0 for value in values.values()),
            "usage is missing; unreported is not zero",
        )
        for field, value in values.items():
            totals[field] += value
    return totals, dict(list(messages.values())[-1])


def run(prepared_path: Path) -> None:
    import secrets

    from benchmarks.hosts import run_pass13_opencode_continuity_qualification as legacy
    from benchmarks.hosts import v013_native_event_adapter as adapter
    from deeplaw.task_continuity import resolve_host_session

    deployment = verify_deployment()
    prepared = read_json(prepared_path)
    require(
        deployment["source_closure_sha256"] == prepared["deployment_sha256"],
        "prepared source drift",
    )
    validate_runtime_entries(prepared)
    require(digest(prepared.get("mcp_launch_prefix", {"argv": [], "files": []}))
            == prepared["mcp_launch_prefix_sha256"], "prepared MCP prefix drift")
    root = Path(prepared["root"])
    write_json(root / "run-claimed.json", {"run_id": prepared["run_id"]})
    repository = Path(prepared["repository"])
    deeplaw = Path(prepared["deeplaw"])
    binary = exact_file(Path(prepared["opencode"]), prepared["host_identity"]["executable_sha256"])
    vault = repository / "vault"
    nonce = secrets.token_hex(32)
    issued = datetime.now(UTC)
    require(
        digest((repository / ".opencode/plugins/deeplaw-native.ts").read_bytes())
        == prepared["privacy"]["privacy_wrapper_sha256"],
        "privacy wrapper bytes changed",
    )
    require(
        digest((repository / ".opencode/supervised-source/deeplaw-native.ts").read_bytes())
        == prepared["privacy"]["original_plugin_sha256"],
        "native plugin bytes changed",
    )
    control = {
        "schema_version": CONTROL,
        "host": "opencode",
        "task_case": "continuity",
        "run_id": prepared["run_id"],
        "workflow_run_id": prepared["workflow_run_id"],
        "candidate_binding": prepared["candidate_binding"],
        "host_identity_sha256": digest(prepared["host_identity"]),
        "host_identity_source_sha256": prepared["host_identity_source_sha256"],
        "broker_source_sha256": digest(Path(__file__).read_bytes()),
        "deployment_source_closure_sha256": deployment["source_closure_sha256"],
        "mcp_launch_prefix_sha256": prepared["mcp_launch_prefix_sha256"],
        **prepared["privacy"],
        "nonce_sha256": digest(nonce.encode()),
        "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(seconds=900)).isoformat(),
        "max_turns": 2,
        "max_calls_per_turn": 2,
        "max_read_chars": 4000,
        "max_guard_requests": 6,
        "formal_admission": False,
        "credential_delivery_mode": "owner_external_guard_host_nonce_mcp_no_key",
        "formal_gaps": [
            "os_isolation_unobserved",
            "global_network_and_cost_unobserved",
            "six_slot_formal_qualification_not_executed",
        ],
    }
    validate_control(control)
    write_json(root / "control.json", control)
    state = root / "turn-state.json"
    replace_state(state, {"turn": "1", "active": False, "nonce_sha256": control["nonce_sha256"]})
    proxy_config = root / "proxy.json"
    write_json(
        proxy_config,
        {
            "deeplaw": str(deeplaw),
            "vault": str(vault),
            "turn_state": str(state),
            "proxy_log": str(root / "proxy.jsonl"),
            "proxy_claim": str(root / "proxy-claimed.json"),
            "mcp_launch_prefix": prepared.get("mcp_launch_prefix", {"argv": [], "files": []}),
            "expected_launch": {
                "prefix_sha256": control["mcp_launch_prefix_sha256"],
                "source_closure_sha256": control["deployment_source_closure_sha256"],
                "deeplaw_sha256": prepared["runtime_entry_hashes"]["deeplaw"],
            },
            "nonce_sha256": control["nonce_sha256"],
        },
    )
    guard_config = root / "guard.json"
    write_json(guard_config, {"key_file": prepared["key_file"], "nonce": nonce})
    guard = ExternalGuard(guard_config)
    server = None
    turns, events, lifecycle, fork_receipts = [], [], [], []
    stopped = False
    try:
        guard.start()
        environment = prepared["environment"]
        environment["DEEPSEEK_API_KEY"] = nonce
        config_path = Path(environment["OPENCODE_CONFIG"])
        config = read_json(config_path)
        config["provider"]["deepseek"]["options"]["baseURL"] = guard.url
        config["agent"]["qualification"]["steps"] = 3
        config["compaction"] = {"auto": False, "prune": False}
        config["mcp"]["deeplaw_knowledge"]["command"] = [
            sys.executable,
            "-m",
            "benchmarks.hosts.opencode_single_task_producer",
            "proxy",
            "--input",
            str(proxy_config),
        ]
        environment["PYTHONPATH"] = str(ROOT)
        replace_state(config_path, config)
        server = legacy._OpenCodeLocalServer(
            binary=binary, environment=environment, cwd=repository, root=root
        )
        server.start()
        require(server.process is not None, "Host process missing")
        process_identity = digest(
            {
                "pid": server.process.pid,
                "nonce_sha256": control["nonce_sha256"],
                "binary_sha256": prepared["host_identity"]["executable_sha256"],
            }
        )
        create_body = {"title": "DeepLaw supervised continuity"}
        created, create_raw = api(server, "POST", "/session", create_body)
        require(created.get("title") == create_body["title"], "non-default title not applied")
        create_observation = {
            "request_sha256": digest(encoded(create_body)),
            "response_sha256": digest(create_raw),
            "title": create_body["title"],
            "observed": True,
        }
        session = created["id"]
        execution = {
            "selector_source_symlink": prepared["selector_source_symlink"],
            "execution_target_regular": True,
            "execution_target_single_link": True,
        }
        log_path = Path(environment["DEEPLAW_OPENCODE_MODEL_RECEIPT"])
        parent = None
        for index in (1, 2):
            validate_control(control)
            fork_raw = None
            fork_started = datetime.now(UTC)
            observation_offset = len(json_lines(log_path))
            if index == 2:
                parent = session
                fork_value, fork_raw, barrier_elapsed_ms = fork_request(server, parent, log_path)
                session = fork_value["id"]
                require(parent != session, "fork did not create a distinct session")
            legacy._bind_public_host_session(
                deeplaw,
                vault=vault,
                session_id=session,
                task_handle=prepared["fixture"]["task_handle"],
                grant_id=prepared["fixture"]["grant_id"],
                workspace=repository,
                environment=environment,
                cwd=repository,
                idempotency_key=f"supervised-bind-{index}",
            )
            resolved = resolve_host_session(
                vault_path=vault,
                host="opencode",
                session_sha256=digest(session.encode()),
                workspace=repository,
            )
            require(resolved["status"] == "exact", "public Host route unavailable")
            route = {
                "status": "exact",
                "binding_sha256": resolved["binding_sha256"],
                "task_handle_sha256": resolved["task_handle_sha256"],
                "project_sha256": prepared["primary_binding"]["project_sha256"],
                "repository_sha256": prepared["primary_binding"]["repository_sha256"],
                "worktree_sha256": prepared["primary_binding"]["worktree_sha256"],
            }
            if fork_raw is not None:
                require(server.process.poll() is None, "fork Host is no longer running")
                # The public-fork adapter validates the actual HTTP/plugin proof
                # against the same native event and process receipt binding.
                fork_event, fork_receipt = public_fork_event(
                    adapter,
                    prepared,
                    control,
                    execution,
                    route,
                    events,
                    json_lines(log_path)[observation_offset:],
                    parent=parent,
                    child=session,
                    raw=fork_raw,
                    process_identity=process_identity,
                    started=fork_started,
                    barrier_elapsed_ms=barrier_elapsed_ms,
                )
                events.append(fork_event["event"])
                lifecycle.append(fork_event["receipt"])
                fork_receipts.append(fork_receipt)
            before_messages, _ = api(server, "GET", f"/session/{session}/message")
            old_ids = {message["info"]["id"] for message in before_messages}
            before = legacy._ledger_head(deeplaw, vault, environment=environment, cwd=repository)
            replace_state(
                state, {"turn": str(index), "active": True, "nonce_sha256": control["nonce_sha256"]}
            )
            guard.active(True)
            sampler = RssSampler(server.process.pid)
            sampler.thread.start()
            start = time.monotonic()
            try:
                api(
                    server,
                    "POST",
                    f"/session/{session}/message",
                    {
                        "agent": "qualification",
                        "model": {"providerID": "deepseek", "modelID": "deepseek-v4-flash"},
                        "parts": [
                            {
                                "type": "text",
                                "text": (
                                    "Continue this task from its governed checkpoint. First call "
                                    "knowledge_support with "
                                    '{"operation":"query","query":"Supervised continuity '
                                    'procedure","scope":"project",'
                                    '"max_sensitivity":"public","max_chars":4000}. Then call '
                                    "knowledge_support once with "
                                    "operation read, the exact knowledge reference "
                                    "returned by that query, scope project, "
                                    "max_sensitivity public and max_chars 4000. Make no other tool "
                                    "calls. "
                                    "Return only JSON with decision and next_action "
                                    "copied from the governed procedure."
                                ),
                            }
                        ],
                    },
                )
            finally:
                elapsed = (time.monotonic() - start) * 1000
                rss = sampler.stop()
                replace_state(
                    state,
                    {"turn": str(index), "active": False, "nonce_sha256": control["nonce_sha256"]},
                )
                guard.active(False)
            after = legacy._ledger_head(deeplaw, vault, environment=environment, cwd=repository)
            require(before == after, "measured Host turn changed Ledger")
            messages, _ = api(server, "GET", f"/session/{session}/message")
            fresh = [message for message in messages if message["info"]["id"] not in old_ids]
            parts = [part for message in fresh for part in message["parts"]]
            records = [
                record
                for record in json_lines(root / "proxy.jsonl")
                if record.get("turn") == str(index)
            ]
            calls = correlate_calls(parts, records, session=session)
            observations = json_lines(log_path)[observation_offset:]
            usage, model = measure_usage(
                observations,
                session,
                message_sha256s={
                    digest(message["info"]["id"].encode())
                    for message in fresh
                    if message["info"].get("role") == "assistant"
                },
            )
            delivery = [
                item
                for item in observations
                if item.get("event_type") == "experimental.chat.system.transform"
                and item.get("session_sha256") == digest(session.encode())
            ]
            require(
                bool(delivery)
                and all(
                    item.get("status") == "admitted" and 0 < item.get("context_bytes", 0) <= 2048
                    for item in delivery
                ),
                "native checkpoint delivery unavailable",
            )
            require(
                len({item["context_sha256"] for item in delivery}) == 1,
                "native checkpoint delivery changed within turn",
            )
            adapted = adapter.adapt_opencode_plugin_observation(
                model,
                host_identity=prepared["host_identity"],
                execution_identity=execution,
                route=route,
                event_sequence=len(events),
            )
            event_index = len(events)
            events.append(adapted["event"])
            lifecycle.append(adapted["receipt"])
            texts = [
                part["text"]
                for part in parts
                if part.get("type") == "text"
                and any(
                    message["info"].get("role") == "assistant" and part in message["parts"]
                    for message in fresh
                )
            ]
            final = strict_json_loads(texts[-1]) if texts else {}
            safe(final)
            checkpoint = prepared["case"]["current_checkpoint"]
            turns.append(
                {
                    "index": index,
                    "event_index": event_index,
                    "session_sha256": digest(session.encode()),
                    "message_sha256": model["message_sha256"],
                    "calls": calls,
                    "native_context_sha256": delivery[-1]["context_sha256"],
                    "native_context_bytes": delivery[-1]["context_bytes"],
                    "next_action_sha256": digest(final.get("next_action")),
                    "next_action_matches": final.get("next_action") == checkpoint["next_action"],
                    "decision_sha256": digest(final.get("decision")),
                    "decision_matches": final.get("decision") == checkpoint["decision"],
                    "ledger_before": before,
                    "ledger_after": after,
                    "elapsed_ms": elapsed,
                    "rss_peak_bytes": rss,
                    "usage": usage,
                }
            )
        host_process = server.process
        server.stop()
        guard.stop()
        require(
            host_process is not None and host_process.poll() is not None, "Host exit unavailable"
        )
        stopped = True
        advertisements = [
            item["advertisement"]
            for item in json_lines(root / "proxy.jsonl")
            if "advertisement" in item
        ]
        require(
            advertisements and all(item == advertisements[0] for item in advertisements),
            "advertisement missing or changed",
        )
        observation = {
            "schema_version": OBSERVATION,
            "control": control,
            "host": "opencode",
            "task_case": "continuity",
            "advertisement": advertisements[0],
            "turns": turns,
            "public_fork_receipts": fork_receipts,
            "session_create": create_observation,
            "process_exit": {
                "host_exit_code": host_process.returncode,
                "guard_exit_code": guard.process.returncode,
                "formal_v2_eligible": False,
            },
            "guard_requests": guard.receipt["requests"],
            "guard_rejected": guard.receipt["rejected"],
            "cleanup_confirmed": True,
            "measurement_scope": "host_turn_visible_process_tree_sampled_rss_guard_route_only",
            "claim_eligible": False,
        }
        contract("host-mcp-observation.v1.schema.json", observation)
        write_sources(root / "evidence", prepared, observation, events, lifecycle, fork_receipts)
    except Exception:
        write_json(
            root / "stop.json",
            {
                "status": "STOP",
                "formal_admission": False,
                "reason": "supervised_observation_incomplete",
            },
        )
        raise
    finally:
        if not stopped:
            try:
                if server is not None:
                    server.stop()
            finally:
                guard.stop()


def public_fork_event(
    adapter: Any,
    prepared: Mapping[str, Any],
    control: Mapping[str, Any],
    execution: Mapping[str, Any],
    route: Mapping[str, Any],
    events: Sequence[Any],
    observations: Sequence[Mapping[str, Any]],
    *,
    parent: str,
    child: str,
    raw: bytes,
    process_identity: str,
    started: datetime,
    barrier_elapsed_ms: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    child_hash = digest(child.encode())
    log = Path(prepared["environment"]["DEEPLAW_OPENCODE_MODEL_RECEIPT"])
    observed_raw = []
    while (datetime.now(UTC) - started).total_seconds() <= 30:
        require(log.stat().st_size <= MAX_BYTES, "fork plugin log exceeds bound")
        observed_raw = [
            line
            for line in log.read_bytes().splitlines()
            if (item := strict_json_loads(line)).get("event_type") == "session.created"
            and item.get("session_sha256") == child_hash
        ]
        if observed_raw:
            break
        time.sleep(0.05)
    require(len(observed_raw) == 1, "fork child plugin barrier not satisfied")
    elapsed = int((datetime.now(UTC) - started).total_seconds() * 1000)
    require(0 <= elapsed <= 30000, "fork child barrier expired")
    process = {
        "task_case": "continuity",
        "run_id": control["run_id"],
        "candidate_binding": control["candidate_binding"],
        "run_binding": {
            "evidence_run_id": control["workflow_run_id"],
            "qualification_run_id": control["workflow_run_id"],
        },
        "host_binary": {
            "version": prepared["host_identity"]["version"],
            "sha256": prepared["host_identity"]["executable_sha256"],
        },
        "broker_source": {
            "repository_external": True,
            "owner_only_mode": True,
            "sha256": control["broker_source_sha256"],
        },
        "host_identity_sha256": control["host_identity_sha256"],
        "host_identity_source_sha256": control["host_identity_source_sha256"],
        "process_identity_sha256": process_identity,
        "broker_instance_sha256": digest({"pid": os.getpid(), "nonce": control["nonce_sha256"]}),
        "nonce_sha256": control["nonce_sha256"],
        "issued_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (started + timedelta(seconds=300)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "validation_reference_time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **execution,
        "status": "running",
        "exit_code": None,
        "isolation": {
            "runner_received_secret": False,
            "mcp_received_secret": False,
            "ambient_auth_forwarded_to_mcp": False,
            "raw_output_retained": False,
        },
    }
    proof = {
        "schema_version": "deeplaw.opencode-public-fork-proof/v1",
        "route_observation": {
            "method": "POST",
            "path": f"/session/{parent}/fork",
            "status_code": 200,
        },
        "request_body": b"{}",
        "response": raw,
        "child_plugin_observation": observed_raw[0],
        "event_barrier": {
            "status": "satisfied",
            "response_release": "after_child_plugin_event",
            "timed_out": False,
            "child_plugin_event_count": 1,
            "event_type": "session.created",
            "timeout_seconds": 30,
            "elapsed_ms": barrier_elapsed_ms,
            "parent_source": "actual_ingress_route",
        },
        "process_binding": process,
    }
    adapted = adapter.adapt_opencode_public_fork_observation(
        proof,
        host_identity=prepared["host_identity"],
        execution_identity=execution,
        route=route,
        event_sequence=len(events),
        expected_process_binding=process,
    )
    require("process_receipt" not in adapted, "active Host cannot have terminal process receipt")
    return adapted, adapted["public_fork_proof"]


def source_ref(root: Path, name: str, value: Any) -> dict[str, Any]:
    safe(value)
    path = root / name
    write_json(path, value)
    raw = path.read_bytes()
    return {
        "relative_path": name,
        "byte_size": len(raw),
        "sha256": digest(raw),
        "media_type": "application/json",
    }


def write_sources(
    root: Path,
    prepared: Mapping[str, Any],
    observation: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    lifecycle: Sequence[Mapping[str, Any]],
    fork_receipts: Sequence[Mapping[str, Any]],
) -> Path:
    from benchmarks.release import typed_qualification_evidence_v3_host_tasks as typed

    root.mkdir(mode=0o700)
    control = observation["control"]
    meta = {
        "schema_version": "deeplaw.v013-host-task-evidence/v1",
        "run_id": control["run_id"],
        "workflow_run_id": control["workflow_run_id"],
        "host": "opencode",
        "task_case": "continuity",
    }
    expected = {
        **meta,
        "artifact_kind": "expected_task",
        "required_duties": list(typed.TASK_DUTIES["continuity"]),
        "duty_expectations": [
            {"duty": duty, "allowed_statuses": ["observed"], "required_gap_code": None}
            for duty in typed.TASK_DUTIES["continuity"]
        ],
        "rows": [
            {
                "case_id": case,
                "required_duties": list(typed.TASK_DUTIES["continuity"]),
                "required_wrong_states": ["stale", "wrong_task_line", "wrong_worktree"],
                "required_operations": list(typed.TASK_OPERATIONS["continuity"]),
            }
            for case in ("cold_start", "resume_fork", "compaction_forget")
        ],
        "hard_failure_ids": list(typed.HARD_FAILURE_IDS),
    }
    expected_ref = source_ref(root, "expected.json", expected)
    model_meta = {**meta, "actual_response_model_id": "deepseek-v4-flash"}
    event_ref = source_ref(
        root, "events.json", {**model_meta, "artifact_kind": "event_sequence", "events": events}
    )
    lifecycle_ref = source_ref(
        root,
        "lifecycle.json",
        {**model_meta, "artifact_kind": "lifecycle_sequence", "receipts": lifecycle},
    )
    host_ref = source_ref(root, "host-observation.json", observation)
    runner = {
        "identity": "opencode-supervised-single-task-v1",
        "sha256": digest(Path(__file__).read_bytes()),
    }
    scorer = {
        "identity": "typed-v3-host-tasks",
        "sha256": digest(Path(typed.__file__).read_bytes()),
    }
    rows = []
    for turn in observation["turns"]:
        rows.append(
            {
                **{key: meta[key] for key in ("run_id", "workflow_run_id", "task_case", "host")},
                "actual_response_model_id": "deepseek-v4-flash",
                "host_identity_sha256": control["host_identity_sha256"],
                "candidate_commit": control["candidate_binding"]["commit"],
                "candidate_tree": control["candidate_binding"]["tree"],
                "corpus_sha256": expected_ref["sha256"],
                "runner_identity": runner["identity"],
                "runner_sha256": runner["sha256"],
                **turn["usage"],
                "provider_bytes": sum(call["content_bytes"] for call in turn["calls"]),
                "provider_sha256": digest([call["content_sha256"] for call in turn["calls"]]),
                "latency_ms": turn["elapsed_ms"],
                "rss_peak_bytes": turn["rss_peak_bytes"],
            }
        )
    usage_ref = source_ref(
        root, "usage.json", {**model_meta, "artifact_kind": "usage_receipt", "rows": rows}
    )
    selected = [{"kind": "task_binding", "identity_sha256": events[0]["route"]["binding_sha256"]}]
    first = observation["turns"][0]
    no_mutation = {
        "observed": False,
        "operation": None,
        "owner_authorized": False,
        "receipt_sha256": None,
    }
    measured_audit = digest(
        [
            {key: turn[key] for key in ("ledger_before", "ledger_after")}
            for turn in observation["turns"]
        ]
    )
    result = {
        **meta,
        "schema_version": RESULT,
        "artifact_kind": "task_result",
        "host_observation_source": host_ref,
        "first_correct_action": {
            "observed": first["next_action_matches"],
            "event_index": first["event_index"],
            "seam": "knowledge_support",
        },
        "decision_preservation": {
            "observed": all(turn["decision_matches"] for turn in observation["turns"]),
            "identity_sha256": digest(selected),
        },
        "wrong_state_admission": [
            {"state": state, "observed": False, "admitted": None}
            for state in typed.TASK_WRONG_STATES["continuity"]
        ],
        "duties": [
            {"duty": duty, "status": "not_executed", "gap_code": None}
            for duty in typed.TASK_DUTIES["continuity"]
        ],
        "provider": {
            "capsule_sha256": digest([row["provider_sha256"] for row in rows]),
            **{
                field: sum(row[field] for row in rows)
                for field in (
                    "provider_bytes",
                    "input_tokens",
                    "output_tokens",
                    "cache_tokens",
                    "reasoning_tokens",
                )
            },
        },
        "selected_identities": selected,
        "duplicate_distractor": [
            {"state": state, "observed": False, "admitted": None}
            for state in ("duplicate", "distractor")
        ],
        "no_hidden_mutation": {
            "hidden_mutation": False,
            "write_performed": False,
            "ledger_before_sha256": measured_audit,
            "ledger_after_sha256": measured_audit,
            "authorized_mutation": no_mutation,
            "process_receipt_observed": bool(fork_receipts),
        },
        "query_trace": {
            "in_capsule": False,
            "sha256": digest([turn["calls"] for turn in observation["turns"]]),
            "entry_count": 4,
        },
        "ledger": {"in_capsule": False, "sha256": measured_audit, "entry_count": 4},
        "lifecycle_steps": [
            {
                "step": step,
                "observed": step == "fork",
                "gap_code": None if step == "fork" else "single_task_not_executed",
            }
            for step in typed.CONTINUITY_LIFECYCLE
        ],
        "observed_public_seams": ["knowledge_support", "native_capsule"],
        "claim_eligible": False,
    }
    observed_duties = {
        "first_correct_action": first["next_action_matches"],
        "decision_preservation": result["decision_preservation"]["observed"],
        "bounded_read_only_context": True,
    }
    for duty in result["duties"]:
        if observed_duties.get(duty["duty"]):
            duty["status"] = "observed"
    contract("v013-host-task-result.v3.schema.json", result)
    result_ref = source_ref(root, "result.json", result)
    binding = {
        "candidate_binding": control["candidate_binding"],
        "run_binding": {key: control[key] for key in ("run_id", "workflow_run_id")},
        "corpus": {"sha256": expected_ref["sha256"], "role": "host_qualification"},
        "runner": runner,
        "scorer": scorer,
    }
    # These are observations of this supervised topology. In particular the
    # producer parent did not hold the key, and it DID inspect native messages.
    # Old Formal expectations must fail on these honest values.
    isolation = {
        **{key: meta[key] for key in ("schema_version", "host", "task_case")},
        **binding,
        "artifact_kind": "isolation_receipt",
        "secret_boundary": {
            "parent_secret_present": False,
            "child_secret_present": False,
            "auth_read": False,
            "transcript_read": True,
            "prompt_read": True,
            "reasoning_read": True,
            "secret_read": False,
        },
        "process_boundary": {
            "native_receipt_observed": bool(fork_receipts),
            "host_process_separated": True,
            "mcp_process_separated": True,
        },
        "write_observation": {
            "hidden_mutation": False,
            "write_performed": False,
            "authorized_mutation": no_mutation,
            "audit_head_before_sha256": measured_audit,
            "audit_head_after_sha256": measured_audit,
        },
        "claim_eligible": False,
    }
    isolation_ref = source_ref(root, "isolation.json", isolation)
    envelope = {
        "schema_version": "deeplaw.typed-qualification-evidence/v3",
        "profile": "kernel_release_core",
        "reference_provenance": "deterministic_expected_evidence",
        "human_authenticity": "not_claimed",
        "kind": "host_event_sequence",
        **binding,
        "payload": {
            "event_source": event_ref,
            "lifecycle_source": lifecycle_ref,
            "usage_source": usage_ref,
            "expected_source": expected_ref,
            "continuity_source": result_ref,
            "isolation_source": isolation_ref,
        },
    }
    envelope["record_sha256"] = digest(envelope)
    path = root / "manifest.json"
    write_json(path, envelope)
    reopen(path)
    return path


def reopen(path: Path) -> dict[str, Any]:
    from benchmarks.hosts.run_v013_host_task_qualification import validate_retained_manifest

    envelope = read_json(path)
    result = validate_retained_manifest(
        path,
        root=path.parent,
        expected_candidate=envelope["candidate_binding"],
        expected_corpus_sha256=envelope["corpus"]["sha256"],
    )
    require(
        result.get("claim_eligible") is not True, "single task cannot establish formal eligibility"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("install", "prepare", "run", "reopen", "proxy", "guard")
    )
    parser.add_argument("--input", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.command == "run":

        def interrupted(signum: int, frame: Any) -> None:
            raise ProducerError("owner interrupted supervised run")

        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
    actions = {
        "install": install,
        "prepare": prepare,
        "run": run,
        "reopen": reopen,
        "proxy": proxy,
        "guard": guard_process,
    }
    try:
        result = actions[arguments.command](arguments.input)
        if arguments.command == "reopen":
            print(
                canonical_json(
                    {
                        "status": "reopened",
                        "metrics": result.get("metrics"),
                        "formal_admission": False,
                    }
                )
            )
    except Exception:
        # Do not print native output, private deployment paths or exception text.
        print(
            "supervised producer stopped: observation or deployment validation failed",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
