from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from deeplaw.native_host import (
    MAX_EVENT_BYTES,
    NativeHostObservationError,
    load_native_host_event,
    observe_native_host_event,
    parse_native_host_event,
)
from deeplaw.util import canonical_json

CODEX_IDENTITY = {
    "binary_version": "codex-cli 0.148.0-alpha.9",
    "binary_sha256": "6170ff5578170ee9b74ad92bfcff96e6186f41d02b60815a7c2b01ad424c754f",
    "request_model": "gpt-5.6-luna",
    "reasoning": "max",
}
OPENCODE_IDENTITY = {
    "version": "1.18.16",
    "source_commit": "a3647eb025c7615159d417dcc49fc39fdaeba65b",
    "config_selector": "deepseek/deepseek-v4-flash",
    "expected_response_model_id": "deepseek-v4-flash",
    "executable_sha256": "a" * 64,
    "package_sha256": "b" * 64,
}
EXACT_ROUTE = {
    "status": "exact",
    "binding_sha256": "3" * 64,
    "task_handle_sha256": "4" * 64,
    "project_sha256": "5" * 64,
    "repository_sha256": "6" * 64,
    "worktree_sha256": "7" * 64,
}


def _codex_event(
    event_type: str = "SessionStart",
    *,
    provenance: str = "native_plugin_hook",
    route: dict[str, object] | None = None,
    parent: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "deeplaw.native-host-event/v2",
        "provenance_level": provenance,
        "host": "codex",
        "host_identity": dict(CODEX_IDENTITY),
        "event_type": event_type,
        "event_sequence": {"index": 7, "sequence_sha256": "8" * 64},
        "session_sha256": "1" * 64,
        "parent_session_sha256": parent,
        "observation": {
            "methods_observed": ["hook.received"],
            "status": "received",
        },
        **({"route": route} if route is not None else {}),
    }


def _opencode_event(
    event_type: str = "chat.message",
    *,
    provenance: str = "native_plugin_hook",
    route: dict[str, object] | None = None,
    parent: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "deeplaw.native-host-event/v2",
        "provenance_level": provenance,
        "host": "opencode",
        "host_identity": dict(OPENCODE_IDENTITY),
        "event_type": event_type,
        "event_sequence": {"index": 9},
        "session_sha256": "2" * 64,
        "parent_session_sha256": parent,
        "observation": {
            "methods_observed": ["chat.message.received"],
            "status": "completed",
        },
        **({"route": route} if route is not None else {}),
    }


def test_codex_receipt_binds_exact_identity_and_not_legacy_fact() -> None:
    event = _codex_event()
    raw = json.dumps(event, separators=(",", ":")).encode("utf-8")

    receipt = observe_native_host_event(raw)

    assert receipt["schema_version"] == "deeplaw.native-host-lifecycle-receipt/v2"
    assert receipt["provenance_level"] == "native_plugin_hook"
    assert receipt["host_identity"] == CODEX_IDENTITY
    assert receipt["operation"] == "start"
    assert receipt["raw_observation_digest"] == {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    assert receipt["session_sha256"] == "1" * 64
    assert receipt["route_binding_provenance"]["status"] == "unbound"
    assert receipt["status"] == "gap"
    assert receipt["gap"] == {"code": "route_unbound"}
    assert receipt["claim_eligible"] is False
    assert "native_seam_received" not in receipt

    receipt_hash = receipt["receipt_sha256"]
    unsigned = dict(receipt)
    del unsigned["receipt_sha256"]
    assert receipt_hash == hashlib.sha256(
        canonical_json(unsigned).encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize(
    ("event_type", "operation"),
    (
        ("SessionStart", "start"),
        ("UserPromptSubmit", "message"),
        ("PreCompact", "compaction_pre"),
        ("PostCompact", "compaction_post"),
        ("SessionEnd", "end"),
    ),
)
def test_codex_event_vocabulary_is_closed(event_type: str, operation: str) -> None:
    receipt = observe_native_host_event(_codex_event(event_type))
    assert receipt["event_type"] == event_type
    assert receipt["operation"] == operation


@pytest.mark.parametrize(
    ("event_type", "operation"),
    (
        ("chat.message", "message"),
        ("session", "resume"),
        ("fork", "fork"),
        ("compaction", "compaction"),
    ),
)
def test_opencode_event_vocabulary_requires_observed_artifact_identity(
    event_type: str,
    operation: str,
) -> None:
    parent = "f" * 64 if event_type == "fork" else None
    receipt = observe_native_host_event(_opencode_event(event_type, parent=parent))
    assert receipt["event_type"] == event_type
    assert receipt["operation"] == operation
    assert receipt["host_identity"] == OPENCODE_IDENTITY
    assert receipt["parent_session_sha256"] == parent


def test_fork_parent_digest_is_never_fabricated() -> None:
    absent = observe_native_host_event(_opencode_event("fork"))
    present = observe_native_host_event(
        _opencode_event("fork", parent="f" * 64)
    )

    assert absent["parent_session_sha256"] is None
    assert present["parent_session_sha256"] == "f" * 64


def test_route_statuses_are_explicit_gaps_until_exact_binding_is_complete() -> None:
    for status in ("unbound", "mismatch", "stale", "forgotten", "ambiguous"):
        receipt = observe_native_host_event(
            _codex_event(route={"status": status})
        )
        assert receipt["status"] == "gap"
        assert receipt["gap"] == {"code": f"route_{status}"}
        assert receipt["route_binding_provenance"]["verified"] is False

    incomplete = observe_native_host_event(_codex_event(route={"status": "exact"}))
    assert incomplete["status"] == "gap"
    assert incomplete["gap"] == {"code": "route_incomplete"}

    exact = observe_native_host_event(_codex_event(route=EXACT_ROUTE))
    assert exact["status"] == "gap"
    assert exact["gap"] == {"code": "route_unverified"}
    assert exact["route_binding_provenance"]["source"] == "host_observed"
    assert exact["route_binding_provenance"]["verified"] is False


@pytest.mark.parametrize(
    "field",
    ("transcript_path", "raw_prompt", "transcript", "reasoning_content", "auth", "api_secret"),
)
def test_content_and_auth_fields_are_rejected(field: str) -> None:
    event = _codex_event()
    event[field] = "must not cross the seam"
    with pytest.raises(NativeHostObservationError):
        parse_native_host_event(event)


def test_additional_properties_duplicate_keys_and_nonfinite_numbers_are_rejected() -> None:
    extra = _codex_event()
    extra["unexpected"] = True
    with pytest.raises(NativeHostObservationError):
        parse_native_host_event(extra)

    duplicate = (
        b'{"schema_version":"deeplaw.native-host-event/v2",'
        b'"schema_version":"deeplaw.native-host-event/v2"}'
    )
    with pytest.raises(NativeHostObservationError):
        parse_native_host_event(duplicate)

    nonfinite = b'{"schema_version":"deeplaw.native-host-event/v2","value":NaN}'
    with pytest.raises(NativeHostObservationError):
        parse_native_host_event(nonfinite)


def test_opencode_placeholder_artifact_digest_is_rejected() -> None:
    event = _opencode_event()
    identity = event["host_identity"]
    assert isinstance(identity, dict)
    identity["executable_sha256"] = "0" * 64
    with pytest.raises(NativeHostObservationError):
        parse_native_host_event(event)


def test_regular_non_symlink_file_and_size_bound_are_enforced(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(_codex_event()), encoding="utf-8", newline="\n"
    )
    assert load_native_host_event(event_path)["event_type"] == "SessionStart"

    symlink = tmp_path / "event-link.json"
    symlink.symlink_to(event_path)
    with pytest.raises(NativeHostObservationError):
        load_native_host_event(symlink)

    directory = tmp_path / "event-directory"
    directory.mkdir()
    with pytest.raises(NativeHostObservationError):
        load_native_host_event(directory)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b"x" * MAX_EVENT_BYTES + b"}")
    with pytest.raises(NativeHostObservationError):
        load_native_host_event(oversized)


def test_raw_observation_is_hashed_but_not_returned() -> None:
    event = _codex_event()
    raw_observation = b'{"prompt":"private input that must not be returned"}'
    receipt = observe_native_host_event(event)
    derived = observe_native_host_event(json.dumps(event).encode("utf-8"))
    assert raw_observation.decode() not in canonical_json(receipt)
    assert derived["raw_observation_digest"]["bytes"] == len(json.dumps(event).encode())

    # The lower-level derivation path also accepts an opaque raw digest source
    # without copying it into the receipt.
    from deeplaw.native_host import derive_native_host_receipt

    opaque = derive_native_host_receipt(event, raw_observation=raw_observation)
    assert opaque["raw_observation_digest"] == {
        "bytes": len(raw_observation),
        "sha256": hashlib.sha256(raw_observation).hexdigest(),
    }
    assert raw_observation.decode() not in canonical_json(opaque)


def test_event_mapping_is_not_mutated() -> None:
    event = _codex_event()
    before = deepcopy(event)
    observe_native_host_event(event)
    assert event == before
