from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from benchmarks.hosts import run_v013_host_task_executor as executor


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


CANDIDATE = {
    "commit": "1" * 40,
    "tree": "2" * 40,
    "lock_sha256": "3" * 64,
    "wheel_sha256": "4" * 64,
    "sdist_sha256": "5" * 64,
}
IDENTITY = {
    "schema_version": "deeplaw.host-exact-identity/v1",
    "hosts": {
        "codex": {
            "binary_version": "codex-cli test",
            "binary_sha256": "6" * 64,
        },
        "opencode": {
            "version": "1.18.16",
            "executable_sha256": "7" * 64,
        },
    },
    "source_sha256": "8" * 64,
    "source_bytes": 10,
}
BROKER_BYTES = {"codex": b"codex broker\n", "opencode": b"opencode broker\n"}
BROKER_SHA = {host: hashlib.sha256(raw).hexdigest() for host, raw in BROKER_BYTES.items()}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _native(host: str, task: str) -> dict[str, str]:
    return {
        "event_sequence_sha256": _sha(f"{host}:{task}:event"),
        "session_identity_sha256": _sha(f"{host}:{task}:session"),
        "lifecycle_record_sha256": _sha(f"{host}:{task}:lifecycle"),
    }


def _make_source(root: Path) -> None:
    if os.name != "nt":
        root.chmod(0o700)
    execution = {
        "schema_version": executor.EXECUTION_IDENTITY_SCHEMA,
        "hosts": {
            host: {
                "selector_source_symlink": host == "opencode",
                "execution_target_regular": True,
                "execution_target_single_link": True,
                "host_identity_sha256": executor.host_preflight_receipt.host_identity_sha256(
                    IDENTITY["hosts"][host]
                ),
                "host_identity_source_sha256": IDENTITY["source_sha256"],
            }
            for host in executor.HOSTS
        },
    }
    _write_json(
        root / "candidate-inventory" / "host-execution-identity.json",
        execution,
    )
    for host, raw in BROKER_BYTES.items():
        path = root / "retained-broker-source" / f"{host}.launcher-source"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    for host in executor.HOSTS:
        binary = executor.host_preflight_receipt.host_binary_identity(IDENTITY, host)
        for task in executor.TASK_CASES:
            payload: dict[str, Any] = {}
            for source_key in executor.TYPED_SOURCE_SLOTS:
                relative = f"sources/{source_key}.json"
                raw = json.dumps(
                    {"host": host, "task": task, "source": source_key},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                source_path = root / "slots" / host / task / relative
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_bytes(raw)
                payload[source_key] = {
                    "relative_path": relative,
                    "byte_size": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "media_type": "application/json",
                }
            _write_json(
                root / "slots" / host / task / "host-event-sequence.json",
                {
                    "schema_version": executor.TYPED_SCHEMA,
                    "kind": "host_event_sequence",
                    "corpus": {"role": "host_qualification", "sha256": _sha("corpus")},
                    "payload": payload,
                    "record_sha256": _sha(f"{host}:{task}:record"),
                },
            )
            _write_json(
                root / "receipts" / host / task / "host-preflight.json",
                {
                    "status": "passed",
                    "host": {
                        "name": host,
                        "version": binary["version"],
                        "sha256": binary["sha256"],
                    },
                    "broker_source": {
                        "repository_external": True,
                        "owner_only_mode": True,
                        "sha256": BROKER_SHA[host],
                        "expected_sha256": BROKER_SHA[host],
                    },
                },
            )
            _write_json(
                root / "receipts" / host / task / "host-process.json",
                {
                    "host": host,
                    "task_case": task,
                    "run_id": f"run-{host}-{task}",
                    "nonce_sha256": _sha(f"{host}:{task}:nonce"),
                    "selector_source_symlink": host == "opencode",
                    "execution_target_regular": True,
                    "execution_target_single_link": True,
                    "native_event_binding": _native(host, task),
                },
            )


def _patch_validator_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        executor,
        "validate_external_collector_handoff",
        lambda *args, **kwargs: {"status": "not_executed"},
    )
    monkeypatch.setattr(
        executor,
        "load_exact_candidate_binding",
        lambda *args, **kwargs: dict(CANDIDATE),
    )
    monkeypatch.setattr(
        executor.host_preflight_receipt,
        "load_host_identity_input_with_bytes",
        lambda *args, **kwargs: (dict(IDENTITY), b"identity\n"),
    )
    monkeypatch.setattr(
        executor.host_preflight_receipt,
        "validate_receipt",
        lambda value: dict(value),
    )

    def retained(path: Path, **_: Any) -> dict[str, Any]:
        task = path.parent.name
        host = path.parent.parent.name
        envelope = json.loads(path.read_text(encoding="utf-8"))
        return {
            "kind": "host_event_sequence",
            "status": "passed",
            "evidence_record_sha256": envelope["record_sha256"],
            "metrics": {
                "host": host,
                "task_case": task,
                "run_id": f"run-{host}-{task}",
                **_native(host, task),
            },
        }

    monkeypatch.setattr(executor, "validate_retained_manifest", retained)
    monkeypatch.setattr(
        executor,
        "validate_host_task_matrix",
        lambda results, **kwargs: {
            "status": "derived",
            "result_count": len(results),
        },
    )

    def process_receipt(value: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        kwargs["seen_nonce_sha256s"].add(value["nonce_sha256"])
        return dict(value)

    monkeypatch.setattr(
        executor.host_process_receipt_v2,
        "validate_receipt",
        process_receipt,
    )


def _arguments(tmp_path: Path) -> dict[str, Any]:
    return {
        "handoff": tmp_path / "handoff.json",
        "candidate_binding_input": tmp_path / "candidate.json",
        "evidence_run_id": 202,
        "qualification_run_id": 303,
        "host_identity_input": tmp_path / "identity.json",
        "codex_broker_sha256": BROKER_SHA["codex"],
        "opencode_broker_sha256": BROKER_SHA["opencode"],
    }


def test_admits_exact_six_slot_tree_transactionally(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"

    result = executor.admit_host_task_staging(
        source,
        output,
        **_arguments(tmp_path),
    )

    assert result == {
        "schema_version": "deeplaw.v013-host-task-executor-admission/v1",
        "status": "admitted",
        "slot_count": 6,
        "host_count": 2,
        "task_count_per_host": 3,
        "candidate_binding": CANDIDATE,
        "evidence_run_id": 202,
        "qualification_run_id": 303,
        "claim_eligible": False,
        "formal_admission": False,
        "release_ready": False,
    }
    assert executor._inventory(output) == executor._inventory(source)


def test_stable_reader_accepts_windows_cross_interface_mode_difference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_bytes(b'{"safe":true}\n')
    real_fstat = os.fstat

    def windows_style_fstat(descriptor: int) -> os.stat_result:
        observed = real_fstat(descriptor)
        fields = list(observed)
        fields[0] = observed.st_mode ^ stat.S_IXUSR
        return os.stat_result(fields)

    monkeypatch.setattr(executor.os, "fstat", windows_style_fstat)

    assert executor._read_stable_file(source, maximum_bytes=1024) == b'{"safe":true}\n'


def test_missing_slot_fails_without_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    (source / "slots" / "codex" / "living_wiki" / "host-event-sequence.json").unlink()
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()


def test_symlink_and_hardlink_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_validator_seams(monkeypatch)
    for kind in ("symlink", "hardlink"):
        source = tmp_path / kind
        source.mkdir()
        _make_source(source)
        original = source / "retained-broker-source" / "codex.launcher-source"
        extra = source / "extra.json"
        if kind == "symlink":
            extra.symlink_to(original)
        else:
            os.link(original, extra)
        output = tmp_path / f"final-{kind}"
        with pytest.raises(executor.HostTaskExecutorError):
            executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
        assert not output.exists()


def test_native_process_binding_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    receipt = source / "receipts" / "opencode" / "professional_evidence" / "host-process.json"
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["native_event_binding"]["event_sequence_sha256"] = "f" * 64
    _write_json(receipt, value)
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()


def test_process_topology_must_match_execution_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    receipt = source / "receipts" / "opencode" / "continuity" / "host-process.json"
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["selector_source_symlink"] = False
    _write_json(receipt, value)
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()


def test_second_validation_failure_rolls_back_temporary_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    original = executor._validate_staging
    calls = 0

    def fail_second(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise executor.HostTaskExecutorError("injected staged-byte rejection")
        return original(*args, **kwargs)

    monkeypatch.setattr(executor, "_validate_staging", fail_second)
    output = tmp_path / "final"

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()
    assert not list(tmp_path.glob(".final.admit-*"))


def test_existing_output_is_never_replaced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"
    output.mkdir()

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert output.is_dir()


def test_atomic_promotion_never_replaces_racing_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"
    original = executor._promote_no_replace

    def race(staging: Path, target: Path) -> None:
        target.mkdir()
        (target / "winner.txt").write_text("independent winner", encoding="utf-8")
        original(staging, target)

    monkeypatch.setattr(executor, "_promote_no_replace", race)

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert (output / "winner.txt").read_text(encoding="utf-8") == "independent winner"
    assert not list(tmp_path.glob(".final.admit-*"))


def test_post_rename_fsync_failure_is_reported_as_committed_uncertain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    output = tmp_path / "final"

    def fail_parent_fsync(descriptor: int) -> None:
        raise OSError("injected post-rename fsync failure")

    monkeypatch.setattr(executor, "_fsync_promoted_parent", fail_parent_fsync)

    if os.name == "nt":
        result = executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
        assert result["status"] == "admitted"
        assert executor._inventory(output) == executor._inventory(source)
        return

    with pytest.raises(executor.HostTaskPromotionCommittedError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert executor._inventory(output) == executor._inventory(source)
    assert not list(tmp_path.glob(".final.admit-*"))


@pytest.mark.parametrize("kind", ("file", "directory", "forbidden_directory"))
def test_slot_topology_rejects_every_extra_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    slot = source / "slots" / "codex" / "continuity"
    if kind == "file":
        (slot / "extra.json").write_text("{}", encoding="utf-8")
    elif kind == "directory":
        (slot / "extra").mkdir()
    else:
        (slot / "secret-material").mkdir()
    _patch_validator_seams(monkeypatch)

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(
            source,
            tmp_path / f"final-{kind}",
            **_arguments(tmp_path),
        )


@pytest.mark.parametrize("failure", ("status", "record"))
def test_failed_or_record_unbound_typed_evidence_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    retained = executor.validate_retained_manifest

    def invalid(path: Path, **kwargs: Any) -> dict[str, Any]:
        value = retained(path, **kwargs)
        if path.parent.parent.name == "codex" and path.parent.name == "continuity":
            if failure == "status":
                value["status"] = "failed"
            else:
                value["evidence_record_sha256"] = "f" * 64
        return value

    monkeypatch.setattr(executor, "validate_retained_manifest", invalid)
    output = tmp_path / "final"

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(source, output, **_arguments(tmp_path))
    assert not output.exists()


def test_identity_change_between_public_reads_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    reads = iter((b"identity-one\n", b"identity-two\n"))
    monkeypatch.setattr(
        executor.host_preflight_receipt,
        "load_host_identity_input_with_bytes",
        lambda *args, **kwargs: (dict(IDENTITY), next(reads)),
    )

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **_arguments(tmp_path),
        )


def test_construction_v2_candidate_is_rejected_by_real_exact_loader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    real_loader = executor.load_exact_candidate_binding
    _patch_validator_seams(monkeypatch)
    monkeypatch.setattr(executor, "load_exact_candidate_binding", real_loader)
    candidate = tmp_path / "candidate.json"
    _write_json(
        candidate,
        {"schema_version": "deeplaw.v013-external-kit-manifest/v2"},
    )
    arguments = _arguments(tmp_path)
    arguments["candidate_binding_input"] = candidate

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **arguments,
        )


def test_source_symlink_race_after_snapshot_cannot_escape_captured_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    outside = tmp_path / "outside.txt"
    outside.write_text("authorization: bearer EXTERNAL_SECRET_123456789", encoding="utf-8")
    broker = source / "retained-broker-source" / "codex.launcher-source"
    original_write = executor._write_snapshot

    def mutate_then_write(snapshot: dict[str, bytes], target: Path) -> None:
        broker.unlink()
        broker.symlink_to(outside)
        original_write(snapshot, target)

    monkeypatch.setattr(executor, "_write_snapshot", mutate_then_write)
    output = tmp_path / "final"

    executor.admit_host_task_staging(source, output, **_arguments(tmp_path))

    assert (
        output / "retained-broker-source" / "codex.launcher-source"
    ).read_bytes() == BROKER_BYTES["codex"]


def test_chmod_failure_rolls_back_temporary_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    _patch_validator_seams(monkeypatch)
    original = Path.chmod

    def fail_staging(path: Path, mode: int, *args: Any, **kwargs: Any) -> None:
        if path.name.startswith(".final.admit-"):
            raise OSError("injected chmod failure")
        original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", fail_staging)

    if os.name == "nt":
        result = executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **_arguments(tmp_path),
        )
        assert result["status"] == "admitted"
        assert not list(tmp_path.glob(".final.admit-*"))
        return

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **_arguments(tmp_path),
        )
    assert not list(tmp_path.glob(".final.admit-*"))


def test_broker_source_rejects_credential_literal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    secret = b'api_key="ABCDEFGHIJKLMNOPQRSTUVWX"\n'
    (source / "retained-broker-source" / "codex.launcher-source").write_bytes(secret)
    _patch_validator_seams(monkeypatch)
    arguments = _arguments(tmp_path)
    arguments["codex_broker_sha256"] = hashlib.sha256(secret).hexdigest()

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **arguments,
        )


@pytest.mark.parametrize("relative", (".env", "credentials/input.json", "secrets.json"))
def test_forbidden_source_component_is_rejected_before_content_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relative: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _make_source(source)
    slot = source / "slots" / "codex" / "continuity"
    manifest = slot / "host-event-sequence.json"
    envelope = json.loads(manifest.read_text(encoding="utf-8"))
    original = slot / envelope["payload"]["event_source"]["relative_path"]
    forbidden = slot / relative
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    original.replace(forbidden)
    envelope["payload"]["event_source"]["relative_path"] = relative
    _write_json(manifest, envelope)
    _patch_validator_seams(monkeypatch)
    stable_read = executor._read_stable_file
    forbidden_read = False

    def observe(path: Path, *, maximum_bytes: int) -> bytes:
        nonlocal forbidden_read
        if path == forbidden:
            forbidden_read = True
        return stable_read(path, maximum_bytes=maximum_bytes)

    monkeypatch.setattr(executor, "_read_stable_file", observe)

    with pytest.raises(executor.HostTaskExecutorError):
        executor.admit_host_task_staging(
            source,
            tmp_path / "final",
            **_arguments(tmp_path),
        )
    assert forbidden_read is False
