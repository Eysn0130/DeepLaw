from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from benchmarks.hosts.codex_app_server_client import (
    CodexAppServerError,
    CodexAppServerProtocolError,
    CodexOwnerExternalBrokerError,
    build_codex_zero_model_preflight_request,
    validate_codex_zero_model_preflight_response,
)
from benchmarks.hosts.codex_transport_observer import (
    ObservedCodexClient,
    ObservedTransportError,
    _safe_method,
)


def _fixture_command(mode: str = "eof", *, newline: bytes = b"\n") -> list[str]:
    script = r'''
import json
import sys
import time

MODE = __MODE__
NEWLINE = __NEWLINE__

def emit(value):
    sys.stdout.buffer.write(
        json.dumps(value, separators=(",", ":")).encode("utf-8") + NEWLINE
    )
    sys.stdout.buffer.flush()

for raw_line in sys.stdin.buffer:
    message = json.loads(raw_line)
    method = message.get("method")
    if method == "initialize":
        emit({"method": "thread/status/changed", "params": {"status": "idle"}})
        emit({"id": message["id"], "result": {"ready": True}})
    elif method == "initialized":
        continue
    elif method == "model/list":
        emit({"id": message["id"], "result": {"data": [], "nextCursor": None}})
    elif method == "thread/start":
        if MODE == "missing-session":
            thread = {"id": "thread-1"}
        elif MODE == "oversized-identity":
            thread = {"id": "x" * 4097, "sessionId": "session-1"}
        else:
            thread = {"id": "thread-1", "sessionId": "session-1", "forkedFromId": None}
        emit({"id": message["id"], "result": {"thread": thread}})
    elif method == "turn/start":
        for delta in ("a", "b"):
            emit({"method": "item/agentMessage/delta", "params": {
                "threadId": "thread-1", "turnId": "turn-1", "delta": delta,
            }})
        emit({"method": "turn/completed", "params": {
            "threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"},
        }})
        emit({"id": message["id"], "result": {"turn": {"id": "turn-1"}}})

if MODE == "timeout":
    time.sleep(10)
elif MODE == "nonzero":
    raise SystemExit(7)
'''.replace("__MODE__", repr(mode)).replace("__NEWLINE__", repr(newline))
    return [sys.executable, "-u", "-c", script]


def _client(
    tmp_path: Path,
    *,
    mode: str = "eof",
    newline: bytes = b"\n",
    timeout_seconds: float = 2.0,
):
    return ObservedCodexClient(
        _fixture_command(mode, newline=newline),
        environment={"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        cwd=tmp_path,
        timeout_seconds=timeout_seconds,
    )


def _cleanup(client: ObservedCodexClient) -> None:
    if not client._closed:
        client.close()


def _schema() -> dict[str, object]:
    return json.loads(
        Path("contracts/codex-transport-observation.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )


def _assert_actual_wire_roundtrip(
    tmp_path: Path, *, inbound_newline: bytes
) -> None:
    client = _client(tmp_path, newline=inbound_newline)
    client.start()
    try:
        assert client.initialize() == {"ready": True}
        thread = client.start_thread(params={"ephemeral": True})
        assert client.model_list() == {"data": [], "nextCursor": None}
        result = client.turn_start(
            thread["thread"]["id"],
            [{"type": "text", "text": "private-prompt-marker"}],
        )
        observation = client.graceful_close()
    finally:
        _cleanup(client)

    assert result.final_text == "ab"
    assert observation["status"] == "graceful"
    assert observation["formal_admission"] is False
    assert observation["claim_eligible"] is False
    assert observation["identity_scope"] == "thread_start_only"
    assert observation["activity_observation"]["public_rpc_counts"] == {
        "model/list": 1,
        "turn/start": 1,
    }
    for counter in observation["activity_observation"]["internal_counters"].values():
        assert counter == {"status": "not_executed", "value": None}
    thread_hash = hashlib.sha256(b"thread-1").hexdigest()
    session_hash = hashlib.sha256(b"session-1").hexdigest()
    assert observation["thread_identities"] == [
        {
            "method": "thread/start",
            "thread_id_sha256": thread_hash,
            "session_id_sha256": session_hash,
        }
    ]
    aggregate_input = json.dumps(
        [session_hash], sort_keys=True, separators=(",", ":")
    ).encode()
    assert observation["session_identity_sha256"] == hashlib.sha256(aggregate_input).hexdigest()
    assert observation["close"]["child_returncode"] == 0

    expected_initialize = {
        "id": 1,
        "method": "initialize",
        "params": {
            "capabilities": {"experimentalApi": True},
            "clientInfo": {
                "name": client.client_name,
                "title": client.client_title,
                "version": client.client_version,
            },
        },
    }
    expected_initialize_wire = (
        json.dumps(
            expected_initialize,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    initialize_record = next(
        record for record in observation["outbound"] if record["method"] == "initialize"
    )
    assert initialize_record == {
        "method": "initialize",
        "bytes": len(expected_initialize_wire),
        "sha256": hashlib.sha256(expected_initialize_wire).hexdigest(),
    }
    expected_idle_wire = (
        b'{"method":"thread/status/changed","params":{"status":"idle"}}'
        + inbound_newline
    )
    idle_record = next(
        record
        for record in observation["inbound"]
        if record["method"] == "thread/status/changed"
    )
    assert idle_record == {
        "method": "thread/status/changed",
        "bytes": len(expected_idle_wire),
        "sha256": hashlib.sha256(expected_idle_wire).hexdigest(),
    }
    delta_inbound = [
        record
        for record in observation["inbound"]
        if record["method"] == "item/agentMessage/delta"
    ]
    delta_native = [
        record
        for record in observation["native_events"]
        if record["method"] == "item/agentMessage/delta"
    ]
    assert len(delta_inbound) == len(delta_native) == 2
    assert delta_native == delta_inbound
    assert len({record["sha256"] for record in delta_inbound}) == 2

    serialized = json.dumps(observation, sort_keys=True)
    for forbidden in ("thread-1", "session-1", "private-prompt-marker", str(tmp_path)):
        assert forbidden not in serialized
    Draft202012Validator(_schema()).validate(observation)


def test_actual_wire_roundtrip_has_bounded_observation_and_identity_scope(
    tmp_path: Path,
) -> None:
    for inbound_newline in (b"\n", b"\r\n"):
        _assert_actual_wire_roundtrip(tmp_path, inbound_newline=inbound_newline)


def test_before_start_has_no_identity_or_internal_zero_claim(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        observation = client.transport_observation
        assert observation["status"] == "running"
        assert observation["thread_identities"] == []
        assert observation["session_identity_sha256"] is None
        assert observation["activity_observation"]["public_rpc_counts"] == {
            "model/list": 0,
            "turn/start": 0,
        }
        assert observation["close"] is None
        Draft202012Validator(_schema()).validate(observation)
    finally:
        _cleanup(client)


def test_failed_send_does_not_count_as_successful_public_rpc(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        with pytest.raises(CodexAppServerError):
            client._send_message({"method": "model/list", "params": {}})
        assert client.transport_observation["status"] == "failed"
        assert client.transport_observation["activity_observation"]["public_rpc_counts"] == {
            "model/list": 0,
            "turn/start": 0,
        }
    finally:
        _cleanup(client)


def test_notification_is_not_counted_as_a_public_rpc_request(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.start()
    try:
        client.initialize()
        client._send_notification("model/list", {})
        assert client.transport_observation["activity_observation"]["public_rpc_counts"] == {
            "model/list": 0,
            "turn/start": 0,
        }
    finally:
        _cleanup(client)


def test_method_allowlist_and_identity_scope_reject_unsafe_or_uncovered_methods(
    tmp_path: Path,
) -> None:
    with pytest.raises(ObservedTransportError, match="unsafe"):
        _safe_method("/synthetic/private/path")
    client = _client(tmp_path)
    try:
        with pytest.raises(ObservedTransportError, match="resume or fork"):
            client._send_message({"method": "thread/resume", "params": {}})
        assert client.transport_observation["outbound"] == []
    finally:
        _cleanup(client)


def test_malformed_identity_fails_closed_without_digest(tmp_path: Path) -> None:
    client = _client(tmp_path, mode="missing-session")
    client.start()
    try:
        client.initialize()
        with pytest.raises(CodexAppServerProtocolError):
            client.thread_start(params={"ephemeral": True})
        assert client.transport_observation["status"] == "failed"
        assert client.transport_observation["thread_identities"] == []
        assert client.transport_observation["session_identity_sha256"] is None
    finally:
        _cleanup(client)

    oversized = _client(tmp_path, mode="oversized-identity")
    oversized.start()
    try:
        oversized.initialize()
        with pytest.raises(ObservedTransportError, match="byte bound"):
            oversized.thread_start(params={"ephemeral": True})
        assert oversized.transport_observation["thread_identities"] == []
    finally:
        _cleanup(oversized)


def test_inbound_frame_bound_is_checked_before_retention(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        raw = b'{"method":"thread/status/changed","params":{"blob":"' + b"x" * 131072 + b'"}}'
        with pytest.raises(ObservedTransportError, match="byte bound"):
            client._decode_message(raw)
        assert client.transport_observation["inbound"] == []
    finally:
        _cleanup(client)


def test_observation_failure_cannot_be_washed_into_graceful_eof(tmp_path: Path) -> None:
    client = _client(tmp_path)
    client.start()
    try:
        raw = b'{"method":"thread/status/changed","params":{"blob":"' + b"x" * 131072 + b'"}}'
        with pytest.raises(ObservedTransportError, match="byte bound"):
            client._decode_message(raw)
        observation = client.graceful_close()
        assert observation["status"] == "failed"
        assert observation["close"]["failure"] == "observation_failed"
    finally:
        _cleanup(client)


def test_graceful_close_failure_states_remain_failed(tmp_path: Path) -> None:
    nonzero = _client(tmp_path, mode="nonzero")
    nonzero.start()
    try:
        nonzero.initialize()
        observation = nonzero.graceful_close()
        assert observation["status"] == "failed"
        assert observation["close"]["child_returncode"] == 7
        assert observation["close"]["failure"] == "child_nonzero_exit"
    finally:
        _cleanup(nonzero)

    timeout = _client(tmp_path, mode="timeout")
    timeout.start()
    try:
        timeout.initialize()
        observation = timeout.graceful_close(timeout_seconds=0.05)
        assert observation["status"] == "failed"
        assert observation["close"]["failure"] == "wait_timeout"
        assert observation["close"]["wait"] == "timeout"
    finally:
        _cleanup(timeout)


def test_nonfinite_close_timeout_is_rejected_before_state_change(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        with pytest.raises(ObservedTransportError, match="outside its bound"):
            client.graceful_close(timeout_seconds=float("nan"))
        assert client._observed_close_called is False
    finally:
        _cleanup(client)


def test_development_summary_is_not_accepted_by_legacy_v4_consumer(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    development = client.transport_observation
    _cleanup(client)
    request = build_codex_zero_model_preflight_request(
        task_case="continuity",
        run_id="transport-observer-test",
        candidate_binding={
            "commit": "a" * 40,
            "tree": "b" * 40,
            "lock_sha256": "c" * 64,
            "wheel_sha256": "d" * 64,
            "sdist_sha256": "e" * 64,
        },
        run_binding={"evidence_run_id": 1, "qualification_run_id": 1},
        host_binary={"version": "synthetic", "sha256": "f" * 64},
        broker_source_sha256="1" * 64,
        host_identity_sha256="2" * 64,
        host_identity_source_sha256="3" * 64,
        nonce_sha256="4" * 64,
        issued_at="2026-08-27T00:00:00Z",
        expires_at="2026-08-27T00:01:00Z",
    )
    with pytest.raises(CodexOwnerExternalBrokerError):
        validate_codex_zero_model_preflight_response(
            development,
            request=request,
            observed_at="2026-08-27T00:00:10Z",
            seen_nonce_sha256s=set(),
        )


def test_transport_schema_rejects_internal_counter_value_or_claim_flip(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    value = client.transport_observation
    _cleanup(client)
    validator = Draft202012Validator(_schema())
    value["claim_eligible"] = True
    with pytest.raises(ValidationError):
        validator.validate(value)

    client = _client(tmp_path)
    empty_identity = client.transport_observation
    _cleanup(client)
    empty_identity["session_identity_sha256"] = ""
    with pytest.raises(ValidationError):
        validator.validate(empty_identity)

    client = _client(tmp_path)
    counter_value = client.transport_observation
    _cleanup(client)
    counter_value["activity_observation"]["internal_counters"]["sampling_count"][
        "value"
    ] = 0
    with pytest.raises(ValidationError):
        validator.validate(counter_value)
