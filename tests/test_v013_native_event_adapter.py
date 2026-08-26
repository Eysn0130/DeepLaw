from __future__ import annotations

import json
from copy import deepcopy

import pytest

from benchmarks.hosts.v013_native_event_adapter import (
    NativeEventAdapterError,
    adapt_codex_hook_observation,
    adapt_codex_hook_sequence,
    adapt_opencode_plugin_observation,
    adapt_opencode_plugin_sequence,
)
from deeplaw.util import canonical_json, sha256_bytes

CODEX_IDENTITY = {
    "binary_version": "codex-cli 0.149.0-alpha.4.3",
    "binary_sha256": "d" * 64,
    "request_model": "gpt-5.6-luna",
    "reasoning_effort": "max",
    "auth_status_command": "codex login status",
    "auth_material_access": "forbidden",
}
OPENCODE_IDENTITY = {
    "version": "1.18.16",
    "source_commit": "a3647eb025c7615159d417dcc49fc39fdaeba65b",
    "config_selector": "deepseek/deepseek-v4-flash",
    "expected_response_model_id": "deepseek-v4-flash",
    "executable_sha256": "a" * 64,
    "package_sha256": "b" * 64,
    "runtime": "host_bun_runtime_only",
    "dotenv_policy": "owner_only_external_strict_parser",
    "secret_visibility": "forbidden",
}
FROZEN_IDENTITY = {
    "schema_version": "deeplaw.host-exact-identity/v1",
    "hosts": {
        "codex": CODEX_IDENTITY,
        "opencode": OPENCODE_IDENTITY,
    },
}
CODEX_EXECUTION = {
    "selector_source_symlink": False,
    "execution_target_regular": True,
    "execution_target_single_link": True,
}
OPENCODE_EXECUTION = {
    "selector_source_symlink": True,
    "execution_target_regular": True,
    "execution_target_single_link": True,
}
EXACT_ROUTE = {
    "status": "exact",
    "binding_sha256": "1" * 64,
    "task_handle_sha256": "2" * 64,
    "project_sha256": "3" * 64,
    "repository_sha256": "4" * 64,
    "worktree_sha256": "5" * 64,
}


def _codex_hook(
    event_name: str = "userPromptSubmit", *, turn: bool = True
) -> dict[str, object]:
    value: dict[str, object] = {
        "method": "hook/completed",
        "hook_event_name": event_name,
        "hook_status": "completed",
        "hook_source": "plugin",
        "hook_handler_type": "command",
        "hook_id_sha256": "6" * 64,
        "thread_id_sha256": "9" * 64,
        "turn_id_sha256": "a" * 64,
        "continuity_context_sha256": "7" * 64,
        "continuity_context_bytes": 42,
        "continuity_status": "admitted",
        "continuity_statement_count": 1,
        "continuity_gap_codes": [],
        "continuity_conflict_count": 0,
    }
    if not turn:
        del value["turn_id_sha256"]
    return value


def _opencode_observation(
    event_type: str = "session.created",
    *,
    parent: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "deeplaw.opencode-native-event-observation/v1",
        "event_type": event_type,
        "session_sha256": "8" * 64,
        "parent_session_sha256": parent,
        "parent_gap": None if parent is not None else "parent_absent",
        "status": "observed",
        "gap": None,
    }


def _opencode_model_observation() -> dict[str, object]:
    return {
        "schema_version": "deeplaw.opencode-model-observation/v1",
        "event_type": "message.updated",
        "session_sha256": "8" * 64,
        "message_sha256": "b" * 64,
        "role": "assistant",
        "provider_id": "deepseek",
        "model_id": "deepseek-v4-flash",
        "summary": False,
        "mode": None,
        "finish": "stop",
        "tokens": {
            "input": 10,
            "output": 2,
            "reasoning": 1,
            "total": 17,
            "cache": {"read": 3, "write": 1},
        },
    }


def _fork_receipt(
    *, child: str = "8" * 64, parent: str = "a" * 64
) -> dict[str, object]:
    return {
        "operation": "fork",
        "status": "forked",
        "session_sha256": child,
        "parent_session_sha256": parent,
        "request_sha256": sha256_bytes(
            canonical_json({"operation": "session.fork"}).encode("utf-8")
        ),
        "response_sha256": sha256_bytes(
            canonical_json(
                {
                    "child_session_sha256": child,
                    "forked_from_id_sha256": parent,
                }
            ).encode("utf-8")
        ),
        "gap_codes": [],
    }


def test_codex_actual_hook_maps_to_v3_and_derives_ineligible_receipt() -> None:
    result = adapt_codex_hook_observation(
        _codex_hook(),
        host_identity=CODEX_IDENTITY,
        execution_identity=CODEX_EXECUTION,
        route=EXACT_ROUTE,
        event_sequence=0,
        session_sha256="9" * 64,
    )

    assert set(result) == {"event", "receipt"}
    assert result["event"]["schema_version"] == "deeplaw.native-host-event/v3"
    assert result["event"]["event_type"] == "UserPromptSubmit"
    assert result["event"]["host_identity"] == CODEX_IDENTITY
    assert result["event"]["execution_identity"] == CODEX_EXECUTION
    assert result["event"]["route"] == EXACT_ROUTE
    assert "thread_id_sha256" not in repr(result)
    assert "turn_id_sha256" not in repr(result)
    assert result["receipt"]["schema_version"] == (
        "deeplaw.native-host-lifecycle-receipt/v3"
    )
    assert result["receipt"]["claim_eligible"] is False
    assert result["receipt"]["status"] == "gap"
    assert result["receipt"]["gap"] == {"code": "route_unverified"}


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ("sessionStart", "SessionStart"),
        ("UserPromptSubmit", "UserPromptSubmit"),
        ("preCompact", "PreCompact"),
        ("PostCompact", "PostCompact"),
        ("sessionEnd", "SessionEnd"),
    ),
)
def test_codex_hook_event_vocabulary_is_closed(source: str, expected: str) -> None:
    result = adapt_codex_hook_observation(
        _codex_hook(source),
        host_identity=CODEX_IDENTITY,
        execution_identity=CODEX_EXECUTION,
        route={"status": "unbound"},
        event_sequence=0,
        session_sha256="9" * 64,
    )
    assert result["event"]["event_type"] == expected


@pytest.mark.parametrize("event_name", ("SessionStart", "SessionEnd"))
def test_codex_actual_session_hooks_allow_missing_optional_turn_digest(
    event_name: str,
) -> None:
    result = adapt_codex_hook_observation(
        _codex_hook(event_name, turn=False),
        host_identity=CODEX_IDENTITY,
        execution_identity=CODEX_EXECUTION,
        route={"status": "unbound"},
        event_sequence=0,
        session_sha256="9" * 64,
    )
    assert result["event"]["event_type"] == event_name


@pytest.mark.parametrize("event_name", ("UserPromptSubmit", "PreCompact", "PostCompact"))
def test_codex_turn_hooks_require_turn_digest(event_name: str) -> None:
    with pytest.raises(NativeEventAdapterError, match="requires an observed turn"):
        adapt_codex_hook_observation(
            _codex_hook(event_name, turn=False),
            host_identity=CODEX_IDENTITY,
            execution_identity=CODEX_EXECUTION,
            route={"status": "unbound"},
            event_sequence=0,
            session_sha256="9" * 64,
        )


def test_codex_actual_projector_thread_identity_must_match_caller_session() -> None:
    with pytest.raises(NativeEventAdapterError, match="observed thread"):
        adapt_codex_hook_observation(
            _codex_hook(),
            host_identity=CODEX_IDENTITY,
            execution_identity=CODEX_EXECUTION,
            route=EXACT_ROUTE,
            event_sequence=0,
            session_sha256="b" * 64,
        )


def test_durable_host_identity_document_is_exactly_projected_and_bound() -> None:
    result = adapt_codex_hook_observation(
        _codex_hook(),
        host_identity=FROZEN_IDENTITY,
        execution_identity=CODEX_EXECUTION,
        route=EXACT_ROUTE,
        event_sequence=0,
        session_sha256="9" * 64,
    )
    assert result["event"]["host_identity"] == CODEX_IDENTITY

    for mutator in (
        lambda value: value.update({"source_sha256": "c" * 64}),
        lambda value: value.update({"schema_version": "wrong"}),
        lambda value: value["hosts"].update({"unexpected": {}}),
    ):
        invalid = deepcopy(FROZEN_IDENTITY)
        mutator(invalid)
        with pytest.raises(NativeEventAdapterError):
            adapt_codex_hook_observation(
                _codex_hook(),
                host_identity=invalid,
                execution_identity=CODEX_EXECUTION,
                route=EXACT_ROUTE,
                event_sequence=0,
                session_sha256="9" * 64,
            )


def test_opencode_actual_plugin_observation_maps_to_v3() -> None:
    result = adapt_opencode_plugin_observation(
        _opencode_observation("session.updated"),
        host_identity=OPENCODE_IDENTITY,
        execution_identity=OPENCODE_EXECUTION,
        route=EXACT_ROUTE,
        event_sequence=0,
    )
    assert result["event"]["host"] == "opencode"
    assert result["event"]["event_type"] == "session"
    assert result["event"]["observation"] == {
        "methods_observed": ["opencode.plugin.event"],
        "status": "completed",
    }
    assert result["receipt"]["claim_eligible"] is False


def test_opencode_actual_model_observation_maps_to_chat_message_without_usage() -> None:
    result = adapt_opencode_plugin_observation(
        _opencode_model_observation(),
        host_identity=OPENCODE_IDENTITY,
        execution_identity=OPENCODE_EXECUTION,
        route=EXACT_ROUTE,
        event_sequence=0,
    )
    assert result["event"]["event_type"] == "chat.message"
    assert result["event"]["observation"] == {
        "methods_observed": ["message.updated"],
        "status": "completed",
    }
    rendered = repr(result)
    assert "message_sha256" not in rendered
    assert "tokens" not in rendered
    assert result["receipt"]["claim_eligible"] is False


@pytest.mark.parametrize(
    "mutator",
    (
        lambda value: value.update({"summary": True}),
        lambda value: value.update({"title": "must not cross"}),
        lambda value: value.update({"provider_id": "other"}),
        lambda value: value.update({"model_id": "other-model"}),
        lambda value: value["tokens"].update({"total": 16}),
        lambda value: value["tokens"].update({"unexpected": 0}),
    ),
)
def test_opencode_model_observation_rejects_wrong_identity_summary_tokens_and_unknown(
    mutator,
) -> None:
    observation = _opencode_model_observation()
    mutator(observation)
    with pytest.raises(NativeEventAdapterError):
        adapt_opencode_plugin_observation(
            observation,
            host_identity=OPENCODE_IDENTITY,
            execution_identity=OPENCODE_EXECUTION,
            route=EXACT_ROUTE,
            event_sequence=0,
        )


def test_opencode_model_observation_duplicate_key_is_rejected() -> None:
    observation = _opencode_model_observation()
    encoded = json.dumps(observation, separators=(",", ":"))
    duplicate = encoded.replace(
        '"event_type":"message.updated"',
        '"event_type":"message.updated","event_type":"message.updated"',
        1,
    ).encode()
    with pytest.raises(NativeEventAdapterError, match="strict JSON"):
        adapt_opencode_plugin_observation(
            duplicate,
            host_identity=OPENCODE_IDENTITY,
            execution_identity=OPENCODE_EXECUTION,
            route=EXACT_ROUTE,
            event_sequence=0,
        )


def test_opencode_fork_requires_exact_runner_receipt() -> None:
    observation = _opencode_observation("session.created")
    result = adapt_opencode_plugin_observation(
        observation,
        host_identity=OPENCODE_IDENTITY,
        execution_identity=OPENCODE_EXECUTION,
        route=EXACT_ROUTE,
        event_sequence=0,
        supervisor_parent_observation=_fork_receipt(),
    )
    assert result["event"]["event_type"] == "fork"
    assert result["event"]["parent_session_sha256"] == "a" * 64

    with pytest.raises(NativeEventAdapterError, match="parent identity"):
        adapt_opencode_plugin_observation(
            _opencode_observation("session.created", parent="a" * 64),
            host_identity=OPENCODE_IDENTITY,
            execution_identity=OPENCODE_EXECUTION,
            route=EXACT_ROUTE,
            event_sequence=0,
        )
    with pytest.raises(NativeEventAdapterError, match="cannot carry a parent"):
        adapt_opencode_plugin_observation(
            _opencode_observation("session.created", parent="a" * 64),
            host_identity=OPENCODE_IDENTITY,
            execution_identity=OPENCODE_EXECUTION,
            route=EXACT_ROUTE,
            event_sequence=0,
            supervisor_parent_observation=_fork_receipt(),
        )


@pytest.mark.parametrize("event_type", ("session.updated", "session.compacted"))
def test_opencode_non_created_events_cannot_claim_fork(event_type: str) -> None:
    with pytest.raises(NativeEventAdapterError, match=r"requires session\.created"):
        adapt_opencode_plugin_observation(
            _opencode_observation(event_type),
            host_identity=OPENCODE_IDENTITY,
            execution_identity=OPENCODE_EXECUTION,
            route=EXACT_ROUTE,
            event_sequence=0,
            supervisor_parent_observation=_fork_receipt(),
        )


@pytest.mark.parametrize(
    "mutator",
    (
        lambda value: value.update({"operation": "session.get"}),
        lambda value: value.update({"status": "observed"}),
        lambda value: value.update({"request_sha256": "f" * 64}),
        lambda value: value.update({"response_sha256": "f" * 64}),
        lambda value: value.update({"gap_codes": ["gap"]}),
        lambda value: value.update({"unexpected": True}),
    ),
)
def test_opencode_fork_receipt_is_closed_and_digest_bound(mutator) -> None:
    receipt = _fork_receipt()
    mutator(receipt)
    with pytest.raises(NativeEventAdapterError):
        adapt_opencode_plugin_observation(
            _opencode_observation("session.created"),
            host_identity=OPENCODE_IDENTITY,
            execution_identity=OPENCODE_EXECUTION,
            route=EXACT_ROUTE,
            event_sequence=0,
            supervisor_parent_observation=receipt,
        )


@pytest.mark.parametrize(
    "mutator",
    (
        lambda value: value.update({"schema_version": "deeplaw.native-host-event/v2"}),
        lambda value: value.update({"passed": True}),
        lambda value: value.update({"raw_prompt": "must not cross"}),
        lambda value: value.update({"unexpected": True}),
    ),
)
def test_codex_rejects_legacy_receipts_caller_authored_status_and_unknown_fields(mutator) -> None:
    observation = _codex_hook()
    mutator(observation)
    with pytest.raises(NativeEventAdapterError):
        adapt_codex_hook_observation(
            observation,
            host_identity=CODEX_IDENTITY,
            execution_identity=CODEX_EXECUTION,
            route=EXACT_ROUTE,
            event_sequence=0,
            session_sha256="9" * 64,
        )


def test_opencode_rejects_legacy_v2_and_unknown_fields() -> None:
    for field, value in (
        ("schema_version", "deeplaw.native-host-event/v2"),
        ("claim_eligible", False),
        ("transcript", "must not cross"),
    ):
        observation = _opencode_observation()
        observation[field] = value
        with pytest.raises(NativeEventAdapterError):
            adapt_opencode_plugin_observation(
                observation,
                host_identity=OPENCODE_IDENTITY,
                execution_identity=OPENCODE_EXECUTION,
                route=EXACT_ROUTE,
                event_sequence=0,
            )


def test_duplicate_json_keys_are_rejected_before_adapter_shape_validation() -> None:
    duplicate = (
        b'{"method":"hook/completed","hook_event_name":"userPromptSubmit",'
        b'"hook_event_name":"preCompact"}'
    )
    with pytest.raises(NativeEventAdapterError, match="strict JSON"):
        adapt_codex_hook_observation(
            duplicate,
            host_identity=CODEX_IDENTITY,
            execution_identity=CODEX_EXECUTION,
            route=EXACT_ROUTE,
            event_sequence=0,
            session_sha256="9" * 64,
        )


def test_missing_identity_topology_and_route_are_fail_closed() -> None:
    with pytest.raises(NativeEventAdapterError):
        adapt_codex_hook_observation(
            _codex_hook(),
            host_identity={},
            execution_identity=CODEX_EXECUTION,
            route=EXACT_ROUTE,
            event_sequence=0,
            session_sha256="9" * 64,
        )
    with pytest.raises(NativeEventAdapterError):
        adapt_codex_hook_observation(
            _codex_hook(),
            host_identity=CODEX_IDENTITY,
            execution_identity={"execution_target_regular": True},
            route=EXACT_ROUTE,
            event_sequence=0,
            session_sha256="9" * 64,
        )
    with pytest.raises(NativeEventAdapterError, match="five digests"):
        adapt_codex_hook_observation(
            _codex_hook(),
            host_identity=CODEX_IDENTITY,
            execution_identity=CODEX_EXECUTION,
            route={"status": "exact"},
            event_sequence=0,
            session_sha256="9" * 64,
        )


def test_wrong_route_state_is_explicit_gap_and_never_observed() -> None:
    result = adapt_codex_hook_observation(
        _codex_hook(),
        host_identity=CODEX_IDENTITY,
        execution_identity=CODEX_EXECUTION,
        route={"status": "mismatch"},
        event_sequence=0,
        session_sha256="9" * 64,
    )
    assert result["receipt"]["status"] == "gap"
    assert result["receipt"]["gap"] == {"code": "route_mismatch"}
    assert result["receipt"]["claim_eligible"] is False


def test_noncontiguous_event_sequence_is_rejected() -> None:
    observations = [_codex_hook(), _codex_hook("preCompact")]
    with pytest.raises(NativeEventAdapterError, match="contiguous"):
        adapt_codex_hook_sequence(
            observations,
            host_identity=CODEX_IDENTITY,
            execution_identity=CODEX_EXECUTION,
            route=EXACT_ROUTE,
            session_sha256="9" * 64,
            event_sequences=[0, 2],
        )


@pytest.mark.parametrize("sequence_sha256", (("f" * 64), "not-a-digest"))
def test_event_sequence_digest_without_frozen_derivation_is_rejected(
    sequence_sha256: str,
) -> None:
    with pytest.raises(NativeEventAdapterError, match="no frozen derivation"):
        adapt_codex_hook_observation(
            _codex_hook(),
            host_identity=CODEX_IDENTITY,
            execution_identity=CODEX_EXECUTION,
            route=EXACT_ROUTE,
            event_sequence={"index": 0, "sequence_sha256": sequence_sha256},
            session_sha256="9" * 64,
        )


def test_null_event_sequence_digest_is_normalized_away() -> None:
    result = adapt_codex_hook_observation(
        _codex_hook(),
        host_identity=CODEX_IDENTITY,
        execution_identity=CODEX_EXECUTION,
        route=EXACT_ROUTE,
        event_sequence={"index": 0, "sequence_sha256": None},
        session_sha256="9" * 64,
    )
    assert result["event"]["event_sequence"] == {"index": 0}


def test_sequence_accepts_actual_style_observations_and_preserves_input() -> None:
    observations = [_codex_hook(), _codex_hook("preCompact")]
    before = deepcopy(observations)
    results = adapt_codex_hook_sequence(
        observations,
        host_identity=CODEX_IDENTITY,
        execution_identity=CODEX_EXECUTION,
        route=EXACT_ROUTE,
        session_sha256="9" * 64,
    )
    assert [result["event"]["event_sequence"]["index"] for result in results] == [0, 1]
    assert observations == before


def test_codex_mismatched_hook_event_is_rejected() -> None:
    observation = _codex_hook("not-a-real-hook")
    with pytest.raises(NativeEventAdapterError, match="unsupported"):
        adapt_codex_hook_observation(
            observation,
            host_identity=CODEX_IDENTITY,
            execution_identity=CODEX_EXECUTION,
            route=EXACT_ROUTE,
            event_sequence=0,
            session_sha256="9" * 64,
        )


def test_opencode_sequence_rejects_parent_mismatch() -> None:
    observation = _opencode_observation("session.created")
    supervisor = _fork_receipt(child="f" * 64)
    with pytest.raises(NativeEventAdapterError, match="child identity"):
        adapt_opencode_plugin_sequence(
            [observation],
            host_identity=OPENCODE_IDENTITY,
            execution_identity=OPENCODE_EXECUTION,
            route=EXACT_ROUTE,
            supervisor_parent_observations=[supervisor],
        )
