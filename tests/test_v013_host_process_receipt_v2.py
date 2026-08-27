from __future__ import annotations

import hashlib
import json
import sys
import textwrap
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from benchmarks.hosts import host_process_receipt_v2 as receipt_v2
from benchmarks.hosts import run_pass13_opencode_continuity_qualification as opencode
from benchmarks.hosts.codex_app_server_client import CodexAppServerClient
from benchmarks.release import kernel_qualification_bundle_v1 as kernel_bundle

_CANDIDATE = {
    "commit": "1" * 40,
    "tree": "2" * 40,
    "lock_sha256": "3" * 64,
    "wheel_sha256": "4" * 64,
    "sdist_sha256": "5" * 64,
}
_RUN_BINDING = {"evidence_run_id": 202, "qualification_run_id": 303}


def _codex_stdio_fixture() -> list[str]:
    script = textwrap.dedent(
        """
        import json
        import sys

        def receive():
            return json.loads(sys.stdin.buffer.readline())

        def send(value):
            sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\\n")
            sys.stdout.flush()

        while True:
            message = receive()
            method = message.get("method")
            request_id = message.get("id")
            if method == "initialize":
                send({"id": request_id, "result": {}})
            elif method == "initialized":
                continue
            elif method == "thread/start":
                send({"id": request_id, "result": {"thread": {
                    "id": "runner-thread", "sessionId": "runner-session"
                }}})
            elif method == "turn/start":
                send({"id": request_id, "result": {"turn": {"id": "runner-turn"}}})
                send({"method": "hook/completed", "params": {
                    "threadId": "runner-thread",
                    "turnId": "runner-turn",
                    "run": {
                        "id": "runner-hook",
                        "eventName": "userPromptSubmit",
                        "handlerType": "command",
                        "source": "plugin",
                        "status": "completed",
                        "entries": []
                    }
                }})
                send({"method": "turn/completed", "params": {
                    "threadId": "runner-thread",
                    "turn": {"id": "runner-turn", "status": "completed"}
                }})
            else:
                raise AssertionError(method)
        """
    )
    return [sys.executable, "-u", "-c", script]


def _host_identity() -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.host-exact-identity/v1",
        "hosts": {
            "codex": {
                "binary_version": "codex-cli 0.149.0-alpha.4.3",
                "binary_sha256": "a" * 64,
                "request_model": "gpt-5.6-luna",
                "reasoning_effort": "max",
                "auth_status_command": "codex login status",
                "auth_material_access": "forbidden",
            },
            "opencode": {
                "version": "1.18.16",
                "source_commit": "b" * 40,
                "config_selector": "deepseek/deepseek-v4-flash",
                "expected_response_model_id": "deepseek-v4-flash",
                "executable_sha256": "c" * 64,
                "package_sha256": "d" * 64,
                "runtime": "host_bun_runtime_only",
                "dotenv_policy": "owner_only_external_strict_parser",
                "secret_visibility": "forbidden",
            },
        },
        "source_sha256": "e" * 64,
        "source_bytes": 512,
    }


def _legacy_receipt(host: str) -> dict[str, Any]:
    identity = _host_identity()
    host_item = identity["hosts"][host]
    binary = {
        "version": (
            host_item["binary_version"] if host == "codex" else host_item["version"]
        ),
        "sha256": (
            host_item["binary_sha256"]
            if host == "codex"
            else host_item["executable_sha256"]
        ),
    }
    value = {
        "schema_version": "deeplaw.host-process-receipt/v1",
        "host": host,
        "task_case": "continuity",
        "run_id": f"synthetic-{host}-run",
        "status": "exited",
        "exit_code": 0,
        "host_binary": binary,
        "broker_source": {
            "repository_external": True,
            "owner_only_mode": True,
            "sha256": "f" * 64,
        },
        "isolation": {
            "runner_received_secret": False,
            "mcp_received_secret": False,
            "ambient_auth_forwarded_to_mcp": False,
            "raw_output_retained": False,
        },
        "host_identity_sha256": kernel_bundle.host_identity_sha256(host_item),
        "host_identity_source_sha256": identity["source_sha256"],
        "selector_source_symlink": host == "opencode",
        "execution_target_regular": True,
        "execution_target_single_link": True,
    }
    value["record_sha256"] = kernel_bundle.record_sha256(value)
    return value


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _v2_receipt(host: str, *, index: int = 1) -> dict[str, Any]:
    identity = _host_identity()
    host_item = identity["hosts"][host]
    process_identity = _digest(f"{host}:{index}:process")
    native_binding = {
        "event_sequence_sha256": _digest(f"{host}:{index}:event-sequence"),
        "session_identity_sha256": _digest(f"{host}:{index}:session-identity"),
        "lifecycle_record_sha256": _digest(f"{host}:{index}:lifecycle-record"),
    }
    if host == "codex":
        proof = {
            "proof_kind": "codex_stdio_hook_correlation",
            "process_identity_sha256": process_identity,
            "connection_sha256": _digest(f"{host}:{index}:connection"),
            "initialize_request_sha256": _digest(f"{host}:{index}:initialize-request"),
            "initialized_notification_sha256": _digest(
                f"{host}:{index}:initialized-notification"
            ),
            "initialized_connection_count": 1,
            "hook_session_sha256": _digest(f"{host}:{index}:hook-session"),
            "hook_event_sha256": _digest(f"{host}:{index}:hook-event"),
            "native_event_sequence_sha256": native_binding["event_sequence_sha256"],
            "native_session_identity_sha256": native_binding[
                "session_identity_sha256"
            ],
            "native_lifecycle_record_sha256": native_binding[
                "lifecycle_record_sha256"
            ],
            "same_process": True,
            "same_connection": True,
        }
        proof["connection_correlation_sha256"] = receipt_v2.correlation_sha256(
            {
                key: proof[key]
                for key in (
                    "process_identity_sha256",
                    "connection_sha256",
                    "initialize_request_sha256",
                    "initialized_notification_sha256",
                    "initialized_connection_count",
                    "hook_session_sha256",
                    "hook_event_sha256",
                    "native_event_sequence_sha256",
                    "native_session_identity_sha256",
                    "native_lifecycle_record_sha256",
                )
            }
        )
    else:
        child = _digest(f"{host}:{index}:child-session")
        proof = {
            "proof_kind": "opencode_public_fork_route_correlation",
            "process_identity_sha256": process_identity,
            "request_method": "POST",
            "route_observation_sha256": _digest(f"{host}:{index}:actual-route"),
            "request_body_sha256": hashlib.sha256(b"{}").hexdigest(),
            "response_sha256": _digest(f"{host}:{index}:response"),
            "parent_session_sha256": _digest(f"{host}:{index}:parent-session"),
            "child_session_sha256": child,
            "child_plugin_event_sha256": _digest(f"{host}:{index}:plugin-event"),
            "child_plugin_session_sha256": child,
            "native_event_sequence_sha256": native_binding["event_sequence_sha256"],
            "native_session_identity_sha256": native_binding[
                "session_identity_sha256"
            ],
            "native_lifecycle_record_sha256": native_binding[
                "lifecycle_record_sha256"
            ],
            "same_process": True,
            "actual_route_observed": True,
        }
        proof["route_correlation_sha256"] = receipt_v2.correlation_sha256(
            {
                key: proof[key]
                for key in (
                    "process_identity_sha256",
                    "request_method",
                    "route_observation_sha256",
                    "request_body_sha256",
                    "response_sha256",
                    "parent_session_sha256",
                    "child_session_sha256",
                    "child_plugin_event_sha256",
                    "child_plugin_session_sha256",
                    "native_event_sequence_sha256",
                    "native_session_identity_sha256",
                    "native_lifecycle_record_sha256",
                )
            }
        )
    return receipt_v2.build_receipt(
        host=host,
        task_case="continuity",
        run_id=f"formal-{host}-{index}",
        candidate_binding=_CANDIDATE,
        run_binding=_RUN_BINDING,
        host_binary={
            "version": (
                host_item["binary_version"] if host == "codex" else host_item["version"]
            ),
            "sha256": (
                host_item["binary_sha256"]
                if host == "codex"
                else host_item["executable_sha256"]
            ),
        },
        broker_source={
            "repository_external": True,
            "owner_only_mode": True,
            "sha256": _digest(f"{host}:{index}:broker-source"),
        },
        host_identity_sha256=kernel_bundle.host_identity_sha256(host_item),
        host_identity_source_sha256=identity["source_sha256"],
        process_identity_sha256=process_identity,
        broker_instance_sha256=_digest(f"{host}:{index}:broker-instance"),
        nonce_sha256=_digest(f"{host}:{index}:nonce"),
        issued_at="2026-08-27T00:00:00Z",
        expires_at="2026-08-27T00:05:00Z",
        validation_reference_time="2026-08-27T00:02:00Z",
        selector_source_symlink=host == "opencode",
        execution_target_regular=True,
        execution_target_single_link=True,
        status="exited",
        exit_code=0,
        native_event_binding=native_binding,
        proof=proof,
        isolation={
            "runner_received_secret": False,
            "mcp_received_secret": False,
            "ambient_auth_forwarded_to_mcp": False,
            "raw_output_retained": False,
        },
    )


def _rehash(value: dict[str, Any]) -> dict[str, Any]:
    value["record_sha256"] = receipt_v2.record_sha256(value)
    return value


@pytest.mark.parametrize("host", ("codex", "opencode"))
def test_valid_v2_receipts_are_closed_path_free_and_exactly_bound(host: str) -> None:
    value = _v2_receipt(host)
    identity = _host_identity()
    admitted = receipt_v2.validate_receipt(
        value,
        expected_host=host,
        expected_task_case="continuity",
        expected_run_id=f"formal-{host}-1",
        expected_candidate=_CANDIDATE,
        expected_run_binding=_RUN_BINDING,
        expected_broker_sha256=value["broker_source"]["sha256"],
        expected_host_identity_sha256=value["host_identity_sha256"],
        expected_host_identity_source_sha256=identity["source_sha256"],
        expected_host_binary=value["host_binary"],
        seen_nonce_sha256s=set(),
    )
    assert admitted == value
    rendered = json.dumps(value, sort_keys=True)
    for forbidden in (
        "/Users/",
        "C:\\",
        "pid",
        "stdout",
        "stderr",
            "prompt",
            "transcript",
            "reasoning",
            "credential",
        ):
            assert forbidden not in rendered.casefold()
    assert all(value["isolation"].values()) is False


def test_v2_wrong_expected_bindings_and_record_tamper_fail_closed() -> None:
    value = _v2_receipt("codex")
    for kwargs in (
        {"expected_host": "opencode"},
        {"expected_task_case": "living_wiki"},
        {"expected_run_id": "wrong-run"},
        {"expected_candidate": {**_CANDIDATE, "tree": "f" * 40}},
        {"expected_run_binding": {**_RUN_BINDING, "qualification_run_id": 304}},
        {"expected_broker_sha256": "f" * 64},
        {"expected_host_identity_sha256": "f" * 64},
    ):
        with pytest.raises(receipt_v2.HostProcessReceiptV2Error):
            receipt_v2.validate_receipt(value, seen_nonce_sha256s=set(), **kwargs)
    changed = {**value, "run_id": "changed-run"}
    with pytest.raises(receipt_v2.HostProcessReceiptV2Error, match="record digest"):
        receipt_v2.validate_receipt(changed, seen_nonce_sha256s=set())
    zero_nonce = deepcopy(value)
    zero_nonce["nonce_sha256"] = "0" * 64
    with pytest.raises(receipt_v2.HostProcessReceiptV2Error):
        receipt_v2.validate_receipt(_rehash(zero_nonce), seen_nonce_sha256s=set())


def test_v2_time_window_and_nonce_replay_are_permanently_reproducible() -> None:
    value = _v2_receipt("codex")
    seen: set[str] = set()
    assert receipt_v2.validate_receipt(value, seen_nonce_sha256s=seen) == value
    with pytest.raises(receipt_v2.HostProcessReceiptV2Error, match="replayed"):
        receipt_v2.validate_receipt(value, seen_nonce_sha256s=seen)

    future = deepcopy(value)
    future["validation_reference_time"] = "2026-08-26T23:59:59Z"
    with pytest.raises(receipt_v2.HostProcessReceiptV2Error, match="future"):
        receipt_v2.validate_receipt(_rehash(future), seen_nonce_sha256s=set())
    expired = deepcopy(value)
    expired["validation_reference_time"] = "2026-08-27T00:05:01Z"
    with pytest.raises(receipt_v2.HostProcessReceiptV2Error, match="expired"):
        receipt_v2.validate_receipt(_rehash(expired), seen_nonce_sha256s=set())
    overlong = deepcopy(value)
    overlong["expires_at"] = "2026-08-27T00:05:01Z"
    with pytest.raises(receipt_v2.HostProcessReceiptV2Error, match="lifetime"):
        receipt_v2.validate_receipt(_rehash(overlong), seen_nonce_sha256s=set())


def test_v2_codex_cross_connection_and_hook_session_fail_closed() -> None:
    value = _v2_receipt("codex")
    for field, replacement in (
        ("same_connection", False),
        ("initialized_connection_count", 2),
        ("process_identity_sha256", _digest("other-process")),
        ("hook_session_sha256", value["proof"]["connection_sha256"]),
        ("native_event_sequence_sha256", _digest("other-native-event")),
        ("native_session_identity_sha256", _digest("other-native-session")),
        ("native_lifecycle_record_sha256", _digest("other-native-lifecycle")),
    ):
        changed = deepcopy(value)
        changed["proof"][field] = replacement
        with pytest.raises(receipt_v2.HostProcessReceiptV2Error):
            receipt_v2.validate_receipt(
                _rehash(changed), seen_nonce_sha256s=set()
            )


def test_v2_opencode_requires_actual_route_parent_child_and_plugin_event() -> None:
    value = _v2_receipt("opencode")
    synthetic_request = hashlib.sha256(
        opencode._encoded({"operation": "session.fork"})
    ).hexdigest()
    for field, replacement in (
        ("route_observation_sha256", synthetic_request),
        ("request_body_sha256", synthetic_request),
        ("actual_route_observed", False),
        ("process_identity_sha256", _digest("other-process")),
        ("child_session_sha256", value["proof"]["parent_session_sha256"]),
        ("child_plugin_session_sha256", _digest("other-child")),
        ("native_event_sequence_sha256", _digest("other-native-event")),
        ("native_session_identity_sha256", _digest("other-native-session")),
        ("native_lifecycle_record_sha256", _digest("other-native-lifecycle")),
    ):
        changed = deepcopy(value)
        changed["proof"][field] = replacement
        with pytest.raises(receipt_v2.HostProcessReceiptV2Error):
            receipt_v2.validate_receipt(
                _rehash(changed), seen_nonce_sha256s=set()
            )


def test_production_seams_reject_unattested_codex_connection_and_synthetic_opencode_fork(
    tmp_path: Path,
) -> None:
    client = CodexAppServerClient(
        _codex_stdio_fixture(),
        environment={"PATH": "/usr/bin"},
        cwd=tmp_path,
        timeout_seconds=3,
    )
    with client:
        client.initialize()
        client.thread_start()
        client.turn_start("runner-thread", "fixture")
    codex_hooks = [
        event for event in client.events if event.get("method") == "hook/completed"
    ]
    assert len(codex_hooks) == 1
    assert "session_id_sha256" not in codex_hooks[0]

    requests: list[tuple[str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append((self.path, body))
            raw = b'{"id":"child-session","parentID":"parent-session"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        local = opencode._OpenCodeLocalServer(
            binary=tmp_path / "unused",
            environment={},
            cwd=tmp_path,
            root=tmp_path,
        )
        local.base_url = f"http://127.0.0.1:{server.server_port}"
        child_session = local.fork("parent-session")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert child_session == "child-session"
    assert requests == [("/session/parent-session/fork", b"{}")]
    synthetic_fork = {
        "child_session_sha256": hashlib.sha256(child_session.encode()).hexdigest(),
        "forked_from_id_sha256": hashlib.sha256(b"parent-session").hexdigest(),
        "request_sha256": hashlib.sha256(
            opencode._encoded({"operation": "session.fork"})
        ).hexdigest(),
    }
    assert synthetic_fork["request_sha256"] != hashlib.sha256(b"{}").hexdigest()

    admitted: list[str] = []
    identity = _host_identity()
    for host in ("codex", "opencode"):
        try:
            kernel_bundle._validate_process_receipt(
                _legacy_receipt(host),
                relative=f"receipts/{host}/process.json",
                host_identity=identity,
                candidate=_CANDIDATE,
                run_ids={"candidate_run_id": 101, **_RUN_BINDING},
                seen_nonce_sha256s=set(),
            )
        except kernel_bundle.KernelQualificationBundleError:
            continue
        admitted.append(host)
    assert admitted == [], (
        "final Kernel admission accepted runner-only Codex lifecycle and synthetic "
        "OpenCode fork shapes without owner-external correlation"
    )
