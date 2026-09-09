from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest

from benchmarks.hosts import host_process_receipt_set_v1 as receipt_set
from benchmarks.hosts import host_process_receipt_v2 as receipt_v2

_CANDIDATE = {
    "commit": "1" * 40,
    "tree": "2" * 40,
    "lock_sha256": "3" * 64,
    "wheel_sha256": "4" * 64,
    "sdist_sha256": "5" * 64,
}
_RUN_BINDING = {"evidence_run_id": 202, "qualification_run_id": 303}
_HOST_IDENTITY = {
    "codex": "a" * 64,
    "opencode": "b" * 64,
}
_HOST_BINARY = {
    "codex": {"version": "codex-cli 0.149.0-alpha.4.3", "sha256": "c" * 64},
    "opencode": {"version": "1.18.16", "sha256": "d" * 64},
}
_BROKER_SOURCE = {
    "repository_external": True,
    "owner_only_mode": True,
    "sha256": "e" * 64,
}


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _v2_receipt(host: str, *, index: int, run_id: str | None = None) -> dict[str, Any]:
    process_identity = _digest(f"{host}:{index}:process")
    native = {
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
            "native_event_sequence_sha256": native["event_sequence_sha256"],
            "native_session_identity_sha256": native["session_identity_sha256"],
            "native_lifecycle_record_sha256": native["lifecycle_record_sha256"],
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
            "native_event_sequence_sha256": native["event_sequence_sha256"],
            "native_session_identity_sha256": native["session_identity_sha256"],
            "native_lifecycle_record_sha256": native["lifecycle_record_sha256"],
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
        run_id=run_id or "task-run",
        candidate_binding=_CANDIDATE,
        run_binding=_RUN_BINDING,
        host_binary=_HOST_BINARY[host],
        broker_source=_BROKER_SOURCE,
        host_identity_sha256=_HOST_IDENTITY[host],
        host_identity_source_sha256="f" * 64,
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
        native_event_binding=native,
        proof=proof,
        isolation={
            "runner_received_secret": False,
            "mcp_received_secret": False,
            "ambient_auth_forwarded_to_mcp": False,
            "raw_output_retained": False,
        },
    )


def _set(
    host: str = "codex",
    *,
    count: int = 2,
    rows: list[dict[str, Any]] | None = None,
    task_native_event_binding: dict[str, str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    run_id = "task-run"
    if rows is None:
        rows = [
            {
                "sequence_index": index,
                "process_role": receipt_set.PROCESS_ROLES[host],
                "receipt": _v2_receipt(host, index=index, run_id=run_id),
            }
            for index in range(1, count + 1)
        ]
    return receipt_set.build_receipt_set(
        host=host,
        task_case="continuity",
        run_id=run_id,
        candidate_binding=_CANDIDATE,
        run_binding=_RUN_BINDING,
        host_binary=_HOST_BINARY[host],
        broker_source=_BROKER_SOURCE,
        host_identity_sha256=_HOST_IDENTITY[host],
        host_identity_source_sha256="f" * 64,
        expected_process_count=count,
        task_native_event_binding=task_native_event_binding
        or {
            "event_sequence_sha256": _digest(f"{host}:task:event-sequence"),
            "session_identity_sha256": _digest(f"{host}:task:session-identity"),
            "lifecycle_record_sha256": _digest(f"{host}:task:lifecycle-record"),
        },
        processes=rows,
        **kwargs,
    )


def _rehash(value: dict[str, Any]) -> dict[str, Any]:
    value["record_sha256"] = receipt_set.record_sha256(value)
    return value


def _validate(value: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    expected_task_native_event_binding = kwargs.pop(
        "expected_task_native_event_binding", value["task_native_event_binding"]
    )
    return receipt_set.validate_receipt_set(
        value,
        expected_host=value["host"],
        expected_task_case=value["task_case"],
        expected_run_id=value["run_id"],
        expected_candidate=value["candidate_binding"],
        expected_run_binding=value["run_binding"],
        expected_broker_sha256=value["broker_source"]["sha256"],
        expected_host_identity_sha256=value["host_identity_sha256"],
        expected_host_identity_source_sha256=value["host_identity_source_sha256"],
        expected_host_binary=value["host_binary"],
        expected_task_native_event_binding=expected_task_native_event_binding,
        seen_nonce_sha256s=set(),
        **kwargs,
    )


@pytest.mark.parametrize("host", ("codex", "opencode"))
def test_valid_multi_process_sets_are_closed_path_free_and_identity_bound(host: str) -> None:
    value = _set(host)
    assert _validate(value) == value
    assert value["expected_process_count"] == value["observed_process_count"] == len(
        value["processes"]
    )
    assert value["coverage_complete"] is True
    assert [row["sequence_index"] for row in value["processes"]] == [1, 2]
    assert all(
        row["process_role"] == receipt_set.PROCESS_ROLES[host]
        for row in value["processes"]
    )
    assert value["record_sha256"] == receipt_set.record_sha256(value)
    rendered = json.dumps(value, sort_keys=True)
    for forbidden in (
        "/Users/",
        "C:\\",
        "path",
        "command",
        "stdout",
        "stderr",
        "prompt",
        "transcript",
        "reasoning",
        "credential",
    ):
        assert forbidden not in rendered.casefold()


def test_builder_accepts_receipt_members_and_normalizes_rows() -> None:
    receipts = [_v2_receipt("codex", index=index, run_id="task-run") for index in (1, 2)]
    value = receipt_set.build_receipt_set(
        host="codex",
        task_case="continuity",
        run_id="task-run",
        candidate_binding=_CANDIDATE,
        run_binding=_RUN_BINDING,
        host_binary=_HOST_BINARY["codex"],
        broker_source=_BROKER_SOURCE,
        host_identity_sha256=_HOST_IDENTITY["codex"],
        host_identity_source_sha256="f" * 64,
        task_native_event_binding={
            "event_sequence_sha256": _digest("task-event"),
            "session_identity_sha256": _digest("task-session"),
            "lifecycle_record_sha256": _digest("task-lifecycle"),
        },
        processes=receipts,
    )
    assert [row["sequence_index"] for row in value["processes"]] == [1, 2]
    assert [row["process_role"] for row in value["processes"]] == [
        "codex_app_server",
        "codex_app_server",
    ]


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value["processes"].clear(),
        lambda value: value.__setitem__("expected_process_count", 1),
        lambda value: value.__setitem__("observed_process_count", 1),
    ),
)
def test_empty_and_count_mismatch_fail_closed(mutation: Any) -> None:
    value = copy.deepcopy(_set())
    mutation(value)
    with pytest.raises(receipt_set.HostProcessReceiptSetV1Error):
        _validate(_rehash(value))


@pytest.mark.parametrize(
    "field", ("receipt", "nonce_sha256", "process_identity_sha256", "broker_instance_sha256")
)
def test_duplicate_member_identity_fields_fail_closed(field: str) -> None:
    value = copy.deepcopy(_set())
    first = value["processes"][0]["receipt"]
    second = value["processes"][1]["receipt"]
    if field == "receipt":
        value["processes"][1]["receipt"] = copy.deepcopy(first)
    else:
        second[field] = first[field]
        _rehash(second)
    with pytest.raises(receipt_set.HostProcessReceiptSetV1Error):
        _validate(_rehash(value))


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("sequence_index", 3),
        ("process_role", "opencode_run"),
    ),
)
def test_process_rows_are_ordered_and_host_specific(field: str, replacement: Any) -> None:
    value = copy.deepcopy(_set())
    value["processes"][1][field] = replacement
    with pytest.raises(receipt_set.HostProcessReceiptSetV1Error):
        _validate(_rehash(value))


@pytest.mark.parametrize(
    ("path", "replacement"),
    (
        ("host", "opencode"),
        ("task_case", "living_wiki"),
        ("run_id", "other-run"),
        ("candidate_binding", {**_CANDIDATE, "tree": "6" * 40}),
        ("run_binding", {**_RUN_BINDING, "qualification_run_id": 304}),
        ("host_identity_sha256", "6" * 64),
        ("host_identity_source_sha256", "7" * 64),
        ("host_binary", {"version": "other", "sha256": "8" * 64}),
        ("broker_source", {**_BROKER_SOURCE, "sha256": "9" * 64}),
    ),
)
def test_member_cross_bindings_fail_closed(path: str, replacement: Any) -> None:
    value = copy.deepcopy(_set())
    value["processes"][1]["receipt"][path] = replacement
    _rehash(value["processes"][1]["receipt"])
    with pytest.raises(receipt_set.HostProcessReceiptSetV1Error):
        _validate(_rehash(value))


def test_task_native_event_binding_expected_cross_binding_and_tamper_fail_closed() -> None:
    value = _set()
    expected = copy.deepcopy(value["task_native_event_binding"])
    changed = copy.deepcopy(value)
    changed["task_native_event_binding"]["event_sequence_sha256"] = _digest("tampered")
    with pytest.raises(receipt_set.HostProcessReceiptSetV1Error):
        _validate(
            _rehash(changed),
            expected_task_native_event_binding=expected,
        )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value.__setitem__("unexpected", True),
        lambda value: value["task_native_event_binding"].__setitem__(
            "secret_value", "synthetic"
        ),
        lambda value: value["processes"][0]["receipt"].__setitem__(
            "path_hint", "/synthetic/forbidden"
        ),
    ),
)
def test_unknown_path_and_secret_fields_fail_closed(mutation: Any) -> None:
    value = copy.deepcopy(_set())
    mutation(value)
    with pytest.raises(receipt_set.HostProcessReceiptSetV1Error):
        _validate(_rehash(value))


def test_validator_calls_existing_v2_validator_for_each_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _set("opencode")
    original = receipt_v2.validate_receipt
    calls: list[dict[str, Any]] = []

    def recording_validator(receipt: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return original(receipt, **kwargs)

    monkeypatch.setattr(receipt_v2, "validate_receipt", recording_validator)
    _validate(value)
    assert len(calls) == 2
    assert all(call["expected_host"] == "opencode" for call in calls)
    assert all(call["expected_run_id"] == "task-run" for call in calls)
    assert all(call["seen_nonce_sha256s"] is not None for call in calls)
