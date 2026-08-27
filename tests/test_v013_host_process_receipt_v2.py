from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import textwrap
import threading
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from benchmarks.hosts import codex_app_server_client as codex_client
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


def _write_executable_script(path: Path, source: str) -> None:
    path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    path.chmod(0o700)


def _process_exists(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    return True


def _codex_control_request() -> dict[str, Any]:
    value = _v2_receipt("codex")
    return codex_client.build_codex_zero_model_preflight_request(
        task_case="continuity",
        run_id="formal-codex-1",
        candidate_binding=_CANDIDATE,
        run_binding=_RUN_BINDING,
        host_binary=value["host_binary"],
        broker_source_sha256=value["broker_source"]["sha256"],
        host_identity_sha256=value["host_identity_sha256"],
        host_identity_source_sha256=value["host_identity_source_sha256"],
        nonce_sha256=value["nonce_sha256"],
        issued_at="2026-08-27T00:00:00Z",
        expires_at="2026-08-27T00:05:00Z",
    )


def _codex_control_response(
    receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    process_receipt = receipt if receipt is not None else _v2_receipt("codex")
    return {
        "schema_version": codex_client.CODEX_BROKER_CONTROL_SCHEMA_VERSION,
        "operation": "zero_model_preflight",
        "status": "observed",
        "observed_sequence": list(codex_client.CODEX_ZERO_MODEL_SEQUENCE),
        "fresh_ephemeral_thread": True,
        "turn_start_count": 1,
        "model_inventory_count": 0,
        "model_invocation_count": 0,
        "provider_request_count": 0,
        "sampling_count": 0,
        "session_start_hook": {
            "event_name": "SessionStart",
            "status": "completed",
            "owner": "broker",
            "handler_type": "command",
            "execution_mode": "sync",
            "response": {"continue": False},
            "stop_phase": "before_sampling",
            "event_sha256": process_receipt["proof"]["hook_event_sha256"],
        },
        "host_process_receipt": process_receipt,
    }


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


def test_v2_codex_cross_connection_and_hook_session_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
    aliased = deepcopy(value)
    aliased["proof"]["hook_session_sha256"] = aliased["native_event_binding"][
        "session_identity_sha256"
    ]
    aliased["proof"]["connection_correlation_sha256"] = receipt_v2.correlation_sha256(
        {
            key: aliased["proof"][key]
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
    _rehash(aliased)
    with pytest.raises(
        receipt_v2.HostProcessReceiptV2Error,
        match="Hook session and native session",
    ):
        receipt_v2.validate_receipt(aliased, seen_nonce_sha256s=set())
    with pytest.raises(
        kernel_bundle.KernelQualificationBundleError,
        match="structural or cross-binding",
    ):
        kernel_bundle._validate_process_receipt(
            aliased,
            relative="receipts/codex/process.json",
            host_identity=_host_identity(),
            candidate=_CANDIDATE,
            run_ids={"candidate_run_id": 101, **_RUN_BINDING},
            seen_nonce_sha256s=set(),
        )
    request = _codex_control_request()
    response = _codex_control_response()
    seen: set[str] = set()
    observation = codex_client.validate_codex_zero_model_preflight_response(
        response,
        request=request,
        observed_at="2026-08-27T00:03:00Z",
        seen_nonce_sha256s=seen,
    )
    assert observation["host_process_receipt"] == response["host_process_receipt"]
    assert observation["schema_version"] == codex_client.CODEX_BROKER_CONTROL_SCHEMA_VERSION
    assert observation["turn_start_count"] == 1
    assert observation["sampling_count"] == 0
    assert observation["session_start_hook"]["status"] == "completed"
    assert observation["session_start_hook"]["response"] == {"continue": False}
    assert seen == {response["host_process_receipt"]["nonce_sha256"]}

    for field, replacement in (
        ("observed_sequence", ["initialize", "initialized", "thread/start", "shutdown"]),
        ("turn_start_count", 0),
        ("model_inventory_count", 1),
        ("model_invocation_count", 1),
        ("provider_request_count", 1),
        ("sampling_count", 1),
    ):
        changed = deepcopy(response)
        changed[field] = replacement
        with pytest.raises(codex_client.CodexOwnerExternalBrokerError):
            codex_client.validate_codex_zero_model_preflight_response(
                changed,
                request=request,
                observed_at="2026-08-27T00:03:00Z",
                seen_nonce_sha256s=set(),
            )

    changed = deepcopy(response)
    changed["session_start_hook"]["response"] = {"continue": 0}
    with pytest.raises(
        codex_client.CodexOwnerExternalBrokerError,
        match="stopping SessionStart hook",
    ):
        codex_client.validate_codex_zero_model_preflight_response(
            changed,
            request=request,
            observed_at="2026-08-27T00:03:00Z",
            seen_nonce_sha256s=set(),
        )

    changed = deepcopy(response)
    changed["session_start_hook"]["event_sha256"] = _digest("other-hook-event")
    with pytest.raises(
        codex_client.CodexOwnerExternalBrokerError,
        match="differs from the process receipt",
    ):
        codex_client.validate_codex_zero_model_preflight_response(
            changed,
            request=request,
            observed_at="2026-08-27T00:03:00Z",
            seen_nonce_sha256s=set(),
        )

    invalid_request = {
        **request,
        "zero_model_constraints": {
            **request["zero_model_constraints"],
            "turn_start_count": True,
        },
    }
    with pytest.raises(
        codex_client.CodexOwnerExternalBrokerError,
        match="zero-model constraints differ",
    ):
        codex_client.validate_codex_zero_model_preflight_response(
            response,
            request=invalid_request,
            observed_at="2026-08-27T00:03:00Z",
            seen_nonce_sha256s=set(),
        )
    request = _codex_control_request()
    response = _codex_control_response()
    for observed_at, mutation in (
        ("2026-08-27T00:05:01Z", lambda value: None),
        (
            "2026-08-27T00:03:00Z",
            lambda value: value["host_process_receipt"]["candidate_binding"].update(
                tree="f" * 40
            ),
        ),
        (
            "2026-08-27T00:03:00Z",
            lambda value: value["host_process_receipt"]["run_binding"].update(
                qualification_run_id=404
            ),
        ),
        (
            "2026-08-27T00:03:00Z",
            lambda value: value["host_process_receipt"]["proof"].update(
                hook_session_sha256=value["host_process_receipt"][
                    "native_event_binding"
                ]["session_identity_sha256"]
            ),
        ),
    ):
        changed = deepcopy(response)
        mutation(changed)
        receipt = changed["host_process_receipt"]
        proof = receipt["proof"]
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
        _rehash(receipt)
        with pytest.raises(codex_client.CodexOwnerExternalBrokerError):
            codex_client.validate_codex_zero_model_preflight_response(
                changed,
                request=request,
                observed_at=observed_at,
                seen_nonce_sha256s=set(),
            )

    seen = {response["host_process_receipt"]["nonce_sha256"]}
    with pytest.raises(codex_client.CodexOwnerExternalBrokerError, match="replayed"):
        codex_client.validate_codex_zero_model_preflight_response(
            response,
            request=request,
            observed_at="2026-08-27T00:03:00Z",
            seen_nonce_sha256s=seen,
        )
    issued = datetime.now(UTC).replace(microsecond=0)
    expires = issued + timedelta(seconds=60)
    receipt = deepcopy(_v2_receipt("codex"))
    receipt["nonce_sha256"] = _digest("direct-ipc-nonce")
    receipt["issued_at"] = issued.strftime("%Y-%m-%dT%H:%M:%SZ")
    receipt["validation_reference_time"] = receipt["issued_at"]
    receipt["expires_at"] = expires.strftime("%Y-%m-%dT%H:%M:%SZ")
    _rehash(receipt)
    request = codex_client.build_codex_zero_model_preflight_request(
        task_case="continuity",
        run_id=receipt["run_id"],
        candidate_binding=receipt["candidate_binding"],
        run_binding=receipt["run_binding"],
        host_binary=receipt["host_binary"],
        broker_source_sha256=receipt["broker_source"]["sha256"],
        host_identity_sha256=receipt["host_identity_sha256"],
        host_identity_source_sha256=receipt["host_identity_source_sha256"],
        nonce_sha256=receipt["nonce_sha256"],
        issued_at=receipt["issued_at"],
        expires_at=receipt["expires_at"],
    )
    response = _codex_control_response(receipt)
    launcher = tmp_path / "owner-external-broker"
    response_raw = json.dumps(response, separators=(",", ":")).encode("utf-8")
    _write_executable_script(
        launcher,
        textwrap.dedent(
            f"""
            import sys
            if sys.argv[1:] != [{codex_client.CODEX_BROKER_CONTROL_ARGUMENT!r}]:
                raise SystemExit(91)
            sys.stdin.buffer.read()
            sys.stdout.buffer.write({response_raw!r})
            sys.stdout.buffer.flush()
            """
        ),
    )
    observation = codex_client.consume_codex_zero_model_preflight(
        launcher,
        request=request,
        seen_nonce_sha256s=set(),
    )
    assert observation["host_process_receipt"] == receipt

    duplicate = json.dumps(response, separators=(",", ":"))[:-1]
    duplicate += ',"status":"observed"}'
    _write_executable_script(
        launcher,
        textwrap.dedent(
            f"""
            import sys
            sys.stdin.buffer.read()
            sys.stdout.buffer.write({duplicate.encode("utf-8")!r})
            sys.stdout.buffer.flush()
            """
        ),
    )
    with pytest.raises(
        codex_client.CodexOwnerExternalBrokerError,
        match="duplicate",
    ):
        codex_client.consume_codex_zero_model_preflight(
            launcher,
            request=request,
            seen_nonce_sha256s=set(),
        )

    for stream_name in ("stdout", "stderr"):
        child_pid_path = tmp_path / f"{stream_name}-child.pid"
        _write_executable_script(
            launcher,
            textwrap.dedent(
                f"""
                import pathlib
                import subprocess
                import sys
                import time

                child = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(20)"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid))
                stream = sys.{stream_name}.buffer
                stream.write(b"x" * ({codex_client._MAX_BROKER_CONTROL_BYTES} + 1))
                stream.flush()
                time.sleep(5)
                """
            ),
        )
        started = time.monotonic()
        child_pid: int | None = None
        try:
            with pytest.raises(
                codex_client.CodexOwnerExternalBrokerError,
                match="output limit",
            ) as overflow:
                codex_client.consume_codex_zero_model_preflight(
                    launcher,
                    request=request,
                    timeout_seconds=10,
                    seen_nonce_sha256s=set(),
                )
            elapsed = time.monotonic() - started
            assert elapsed < 2.5
            assert "x" * 32 not in str(overflow.value)
            child_pid = int(child_pid_path.read_text())
            deadline = time.monotonic() + 2
            while _process_exists(child_pid) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not _process_exists(child_pid)
        finally:
            if child_pid is None and child_pid_path.exists():
                child_pid = int(child_pid_path.read_text())
            if child_pid is not None and _process_exists(child_pid):
                os.kill(child_pid, signal.SIGKILL)


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
