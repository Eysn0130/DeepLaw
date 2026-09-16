from __future__ import annotations

import os
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.hosts import native_fork_observation as observation
from deeplaw.util import canonical_json, sha256_bytes

PARENT_ID = "parent-session"
CHILD_ID = "child-session"


def _event(event_type: str, session_id: str) -> dict[str, object]:
    return {
        "schema_version": "deeplaw.opencode-native-event-observation/v1",
        "event_type": event_type,
        "session_sha256": sha256_bytes(session_id.encode("utf-8")),
        "parent_session_sha256": None,
        "parent_gap": "parent_absent",
        "status": "observed",
        "gap": None,
    }


def _line(event_type: str, session_id: str) -> bytes:
    return canonical_json(_event(event_type, session_id)).encode("utf-8") + b"\n"


def _route() -> dict[str, object]:
    return {
        "method": "POST",
        "path": f"/session/{PARENT_ID}/fork",
        "status_code": 200,
    }


def _record_digest(value: dict[str, object]) -> str:
    return sha256_bytes(
        canonical_json(
            {key: item for key, item in value.items() if key != "record_sha256"}
        ).encode("utf-8")
    )


def test_capture_fork_response_preserves_original_response_bytes() -> None:
    response = b' { "id": "child-session", "parentID": "parent-session" } \n'
    projection = observation.capture_fork_response(
        **_route(), request_body=b"{}", response=response
    )

    assert set(projection) == {
        "route_observation_sha256",
        "request_body_sha256",
        "response_sha256",
        "parent_session_sha256",
        "child_session_sha256",
        "formal_admission",
        "record_sha256",
    }
    assert projection["response_sha256"] == sha256_bytes(response)
    assert projection["request_body_sha256"] == sha256_bytes(b"{}")
    assert projection["route_observation_sha256"] == sha256_bytes(
        canonical_json(_route()).encode("utf-8")
    )
    assert projection["parent_session_sha256"] == sha256_bytes(PARENT_ID.encode())
    assert projection["child_session_sha256"] == sha256_bytes(CHILD_ID.encode())
    assert projection["formal_admission"] is False
    assert projection["record_sha256"] == _record_digest(projection)


@pytest.mark.parametrize(
    ("changes", "code"),
    (
        ({"method": "GET"}, "fork_route_invalid"),
        ({"path": "/session/unsafe parent/fork"}, "fork_route_invalid"),
        ({"status_code": 201}, "fork_route_invalid"),
        ({"request_body": b" {}"}, "fork_request_body_invalid"),
        ({"request_body": bytearray(b"{}")}, "fork_request_body_invalid"),
        ({"response": b'{"id":"parent-session"}'}, "fork_response_invalid"),
        ({"response": b'{"event_type":"message.updated"}'}, "fork_response_invalid"),
    ),
)
def test_capture_fork_response_rejects_unbound_inputs(
    changes: dict[str, object], code: str
) -> None:
    arguments: dict[str, object] = {
        **_route(),
        "request_body": b"{}",
        "response": b'{"id":"child-session","parentID":"parent-session"}',
    }
    arguments.update(changes)

    with pytest.raises(observation.NativeForkObservationError) as raised:
        observation.capture_fork_response(**arguments)  # type: ignore[arg-type]
    assert raised.value.code == code
    assert str(raised.value) == code
    assert "/session/" not in str(raised.value)
    assert "parent-session" not in str(raised.value)


def test_snapshot_plugin_log_is_frozen_and_missing_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "tmp" / "native-events.jsonl"
    missing = observation.snapshot_plugin_log(path)
    assert missing.existed is False
    assert missing.data == b""
    assert missing.sha256 == sha256_bytes(b"")

    path.parent.mkdir()
    content = _line("session.updated", PARENT_ID)
    path.write_bytes(content)
    snapshot = observation.snapshot_plugin_log(path)
    assert snapshot.existed is True
    assert snapshot.raw_bytes == content
    assert snapshot.byte_size == len(content)
    assert snapshot.sha256 == sha256_bytes(content)
    assert snapshot.file_identity is not None
    assert snapshot.directory_identity


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW"),
    reason="requires POSIX no-follow filesystem checks",
)
def test_snapshot_rejects_symlink_ancestors_and_nonregular_files(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(observation.NativeForkObservationError, match="symlink"):
        observation.snapshot_plugin_log(alias / "native-events.jsonl")

    target = real / "native-events.jsonl"
    target.write_bytes(b"safe\n")
    leaf = tmp_path / "leaf"
    leaf.symlink_to(target)
    with pytest.raises(observation.NativeForkObservationError, match="symlink"):
        observation.snapshot_plugin_log(leaf)

    oversized = real / "oversized"
    oversized.write_bytes(b"x" * (observation.MAX_PLUGIN_LOG_BYTES + 1))
    with pytest.raises(observation.NativeForkObservationError, match="oversize"):
        observation.snapshot_plugin_log(oversized)

    if hasattr(os, "mkfifo"):
        fifo = real / "events.fifo"
        os.mkfifo(fifo)
        with pytest.raises(observation.NativeForkObservationError, match="invalid"):
            observation.snapshot_plugin_log(fifo)


def test_await_child_event_uses_new_complete_line_and_original_hash(
    tmp_path: Path,
) -> None:
    path = tmp_path / "native-events.jsonl"
    path.write_bytes(_line("session.updated", PARENT_ID))
    snapshot = observation.snapshot_plugin_log(path)
    child_line = (
        b"  "
        + canonical_json(_event("session.created", CHILD_ID)).encode("utf-8")
        + b" \n"
    )

    def append_events() -> None:
        time.sleep(0.03)
        with path.open("ab") as output:
            output.write(_line("session.updated", "other-session"))
            output.write(child_line)

    writer = threading.Thread(target=append_events)
    started_ns = time.monotonic_ns()
    writer.start()
    result = observation.await_child_event(
        path,
        snapshot,
        sha256_bytes(CHILD_ID.encode("utf-8")),
        timeout_seconds=1,
    )
    writer.join(timeout=1)

    assert set(result) == {
        "child_plugin_event_sha256",
        "child_plugin_session_sha256",
        "elapsed_ms",
        "observed_at_ns",
        "formal_admission",
        "claim_eligible",
        "record_sha256",
    }
    assert result["child_plugin_event_sha256"] == sha256_bytes(child_line[:-1])
    assert result["child_plugin_session_sha256"] == sha256_bytes(CHILD_ID.encode())
    assert result["observed_at_ns"] >= started_ns
    assert result["elapsed_ms"] >= 0
    assert result["formal_admission"] is False
    assert result["claim_eligible"] is False
    assert result["record_sha256"] == _record_digest(result)


def test_await_child_event_owner_callback_receives_record_without_newline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "native-events.jsonl"
    path.write_bytes(b"")
    snapshot = observation.snapshot_plugin_log(path)
    child_line = (
        b"  "
        + canonical_json(_event("session.created", CHILD_ID)).encode("utf-8")
        + b" \n"
    )
    path.write_bytes(child_line)
    delivered: list[bytes] = []

    result = observation.await_child_event(
        path,
        snapshot,
        sha256_bytes(CHILD_ID.encode()),
        timeout_seconds=1,
        source_callback=delivered.append,
    )

    assert delivered == [child_line[:-1]]
    assert b"\n" not in delivered[0]
    assert "source_callback" not in result
    assert result["child_plugin_event_sha256"] == sha256_bytes(child_line[:-1])
    assert result["record_sha256"] == _record_digest(result)


def test_await_child_event_owner_callback_failure_is_a_typed_gap(
    tmp_path: Path,
) -> None:
    path = tmp_path / "native-events.jsonl"
    path.write_bytes(b"")
    snapshot = observation.snapshot_plugin_log(path)
    path.write_bytes(_line("session.created", CHILD_ID))

    def fail(_record: bytes) -> None:
        raise RuntimeError("secret plugin record")

    with pytest.raises(observation.NativeForkObservationError) as raised:
        observation.await_child_event(
            path,
            snapshot,
            sha256_bytes(CHILD_ID.encode()),
            timeout_seconds=1,
            source_callback=fail,
        )
    assert raised.value.code == "plugin_source_callback_failed"
    assert "secret plugin record" not in str(raised.value)


def test_await_child_event_owner_callback_cannot_outlive_deadline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "native-events.jsonl"
    path.write_bytes(b"")
    snapshot = observation.snapshot_plugin_log(path)
    path.write_bytes(_line("session.created", CHILD_ID))

    def slow(_record: bytes) -> None:
        time.sleep(0.08)

    with pytest.raises(observation.NativeForkObservationError) as raised:
        observation.await_child_event(
            path,
            snapshot,
            sha256_bytes(CHILD_ID.encode()),
            timeout_seconds=0.03,
            source_callback=slow,
        )
    assert raised.value.code == "plugin_source_callback_timeout"


def test_await_child_event_rejects_settled_event_after_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "native-events.jsonl"
    path.write_bytes(b"")
    snapshot = observation.snapshot_plugin_log(path)
    path.write_bytes(_line("session.created", CHILD_ID))
    ticks = iter((0.0, 0.1, 1.1))
    monkeypatch.setattr(observation.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(observation.time, "sleep", lambda _: None)
    with pytest.raises(observation.NativeForkObservationError) as raised:
        observation.await_child_event(
            path, snapshot, sha256_bytes(CHILD_ID.encode()), timeout_seconds=1
        )
    assert raised.value.code == "plugin_event_timeout"


def test_await_child_event_does_not_reuse_old_child_event(tmp_path: Path) -> None:
    path = tmp_path / "native-events.jsonl"
    path.write_bytes(_line("session.created", CHILD_ID))
    snapshot = observation.snapshot_plugin_log(path)
    with pytest.raises(observation.NativeForkObservationError) as raised:
        observation.await_child_event(
            path,
            snapshot,
            sha256_bytes(CHILD_ID.encode()),
            timeout_seconds=0.03,
        )
    assert raised.value.code == "plugin_event_timeout"


@pytest.mark.parametrize(
    "suffix",
    (b"not-json\n", _line("message.updated", CHILD_ID)),
)
def test_await_child_event_rejects_malformed_or_unknown_rows(
    tmp_path: Path, suffix: bytes
) -> None:
    path = tmp_path / "native-events.jsonl"
    prefix = _line("session.updated", PARENT_ID)
    path.write_bytes(prefix)
    snapshot = observation.snapshot_plugin_log(path)
    path.write_bytes(prefix + suffix)
    with pytest.raises(observation.NativeForkObservationError) as raised:
        observation.await_child_event(
            path,
            snapshot,
            sha256_bytes(CHILD_ID.encode()),
            timeout_seconds=0.2,
        )
    assert raised.value.code == "plugin_event_invalid"


def test_await_child_event_rejects_duplicate_child_and_prefix_tampering(
    tmp_path: Path,
) -> None:
    path = tmp_path / "native-events.jsonl"
    prefix = _line("session.updated", PARENT_ID)
    child = _line("session.created", CHILD_ID)
    path.write_bytes(prefix)
    snapshot = observation.snapshot_plugin_log(path)
    path.write_bytes(prefix + child + child)
    with pytest.raises(observation.NativeForkObservationError) as raised:
        observation.await_child_event(
            path,
            snapshot,
            sha256_bytes(CHILD_ID.encode()),
            timeout_seconds=0.2,
        )
    assert raised.value.code == "plugin_event_duplicate"

    path.write_bytes(b"changed\n" + child)
    with pytest.raises(observation.NativeForkObservationError) as raised:
        observation.await_child_event(
            path,
            snapshot,
            sha256_bytes(CHILD_ID.encode()),
            timeout_seconds=0.2,
        )
    assert raised.value.code == "plugin_log_prefix_changed"


def test_await_child_event_rejects_snapshot_tamper_and_unbounded_timeout(
    tmp_path: Path,
) -> None:
    path = tmp_path / "native-events.jsonl"
    path.write_bytes(_line("session.updated", PARENT_ID))
    snapshot = observation.snapshot_plugin_log(path)
    tampered = replace(snapshot, data=b"tampered\n")
    with pytest.raises(observation.NativeForkObservationError) as raised:
        observation.await_child_event(
            path,
            tampered,
            sha256_bytes(CHILD_ID.encode()),
            timeout_seconds=1,
        )
    assert raised.value.code == "plugin_log_snapshot_tampered"

    with pytest.raises(observation.NativeForkObservationError) as raised:
        observation.await_child_event(
            path,
            snapshot,
            sha256_bytes(CHILD_ID.encode()),
            timeout_seconds=31,
        )
    assert raised.value.code == "timeout_invalid"


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "O_NOFOLLOW"),
    reason="requires POSIX no-follow filesystem checks",
)
def test_await_child_event_rejects_directory_replacement_and_oversize(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    path = parent / "native-events.jsonl"
    snapshot = observation.snapshot_plugin_log(path)
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    parent.rename(tmp_path / "old-parent")
    replacement.rename(parent)
    with pytest.raises(observation.NativeForkObservationError) as raised:
        observation.await_child_event(
            path,
            snapshot,
            sha256_bytes(CHILD_ID.encode()),
            timeout_seconds=0.2,
        )
    assert raised.value.code == "plugin_log_directory_changed"

    path.write_bytes(b"x" * (observation.MAX_PLUGIN_LOG_BYTES + 1))
    missing_parent = tmp_path / "missing-parent"
    missing_parent.mkdir()
    missing_path = missing_parent / "native-events.jsonl"
    missing_snapshot = observation.snapshot_plugin_log(missing_path)
    missing_path.write_bytes(b"x" * (observation.MAX_PLUGIN_LOG_BYTES + 1))
    with pytest.raises(observation.NativeForkObservationError) as raised:
        observation.await_child_event(
            missing_path,
            missing_snapshot,
            sha256_bytes(CHILD_ID.encode()),
            timeout_seconds=0.2,
        )
    assert raised.value.code == "plugin_log_oversize"
