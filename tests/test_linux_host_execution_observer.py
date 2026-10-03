from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.hosts import linux_host_execution_observer as execution
from benchmarks.hosts import native_slot_owner_preflight as owner
from benchmarks.hosts.linux_guest_observer import ObservationGap
from benchmarks.hosts.linux_guest_slot_control import GuestSlotControl, GuestSlotControlError


@pytest.fixture
def snapshot_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    binary = tmp_path / "actual-executed-binary"
    binary.write_bytes(b"actual executable bytes")
    metadata = {
        "pid": 123, "start_ticks": 456, "uid": 1000,
        "namespaces": {name: hashlib.sha256(name.encode()).hexdigest()
                       for name in ("mnt", "pid", "net", "ipc")},
    }
    monkeypatch.setattr(execution, "_process_metadata", lambda *_args: deepcopy(metadata))
    monkeypatch.setattr(execution, "_open_executable", lambda _proc: binary.open("rb"))
    arguments = {
        "role": "host", "pid": 123,
        "expected_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "binding_sha256": hashlib.sha256(b"candidate-run-fresh-challenge").hexdigest(),
    }
    return binary, metadata, arguments


def test_execution_snapshot_hashes_actual_open_inode_and_minimizes_metadata(snapshot_inputs):
    binary, _metadata, arguments = snapshot_inputs
    result = execution.observe_executed_binary(**arguments)
    assert result["executable_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert result["executable_bytes"] == binary.stat().st_size
    assert result["formal_admission"] is False
    assert result["claim_eligible"] is False
    encoded = json.dumps(result)
    assert str(binary) not in encoded
    assert "pid" not in result and "start_ticks" not in result
    assert all(
        isinstance(value, str) and len(value) == 64 for value in result["namespaces"].values()
    )
    detached = execution.validate_execution_snapshot(result)
    detached["namespaces"]["mnt"] = "changed"
    assert result["namespaces"]["mnt"] != "changed"


def test_expected_path_digest_does_not_replace_actual_execution_bytes(snapshot_inputs):
    _binary, _metadata, arguments = snapshot_inputs
    arguments["expected_sha256"] = hashlib.sha256(b"configured other executable").hexdigest()
    with pytest.raises(ObservationGap, match="execution_bytes_mismatch"):
        execution.observe_executed_binary(**arguments)


def test_execution_rejects_pid_reuse_during_byte_read(snapshot_inputs, monkeypatch):
    _binary, metadata, arguments = snapshot_inputs
    reads = iter([deepcopy(metadata), {**metadata, "start_ticks": 457}])
    monkeypatch.setattr(execution, "_process_metadata", lambda *_args: next(reads))
    with pytest.raises(ObservationGap, match="execution_process_changed"):
        execution.observe_executed_binary(**arguments)


def test_execution_rejects_target_replacement_during_byte_read(snapshot_inputs, monkeypatch):
    binary, _metadata, arguments = snapshot_inputs
    replacement = binary.with_name("replaced-inode")
    replacement.write_bytes(binary.read_bytes())
    paths = iter([binary, replacement])
    monkeypatch.setattr(execution, "_open_executable", lambda _proc: next(paths).open("rb"))
    with pytest.raises(ObservationGap, match="execution_target_changed"):
        execution.observe_executed_binary(**arguments)


def test_execution_rejects_wrong_role_uid(snapshot_inputs):
    _binary, metadata, arguments = snapshot_inputs
    metadata["uid"] = 1001
    with pytest.raises(ObservationGap, match="execution_role_uid_mismatch"):
        execution.observe_executed_binary(**arguments)


@pytest.mark.parametrize("mutation", ["private_path", "boolean_size", "tamper_digest"])
def test_reopened_execution_snapshot_rejects_unsafe_or_tampered_record(snapshot_inputs, mutation):
    result = execution.observe_executed_binary(**snapshot_inputs[2])
    if mutation == "private_path":
        result["path"] = "private"
    elif mutation == "boolean_size":
        result["executable_bytes"] = True
    else:
        result["executable_sha256"] = hashlib.sha256(b"tampered").hexdigest()
    with pytest.raises(ObservationGap):
        execution.validate_execution_snapshot(result)


def test_owner_consumer_binds_snapshots_to_launcher_namespaces_and_exact_host(snapshot_inputs):
    host = execution.observe_executed_binary(**snapshot_inputs[2])
    snapshot_inputs[1].update(pid=124, uid=1001)
    mcp = execution.observe_executed_binary(**{**snapshot_inputs[2], "role": "mcp", "pid": 124})
    snapshots = {"host": host, "mcp": mcp}
    observation = {
        "formal_admission": False, "claim_eligible": False,
        "binding_sha256": host["binding_sha256"], "roles": {"host": host, "mcp": mcp},
    }
    roles = [
        {"role": name, "uid": uid,
         "process_start_identity_sha256": snapshots[name]["process_start_identity_sha256"],
         "observation": {
            prefix + "_namespace_sha256": host["namespaces"][key]
            for key, prefix in {"mnt": "mount", "pid": "pid", "net": "net", "ipc": "ipc"}.items()
        }} for name, uid in (("host", 1000), ("mcp", 1001))
    ]
    assert owner._validate_execution_observation(
        observation, roles, host["executable_sha256"],
        host["binding_sha256"],
    )["formal_admission"] is False
    with pytest.raises(owner.NativePreflightError, match="execution_host_bytes_gap"):
        owner._validate_execution_observation(
            observation, roles, hashlib.sha256(b"other").hexdigest(),
            host["binding_sha256"],
        )
    with pytest.raises(owner.NativePreflightError, match="execution_observation_invalid"):
        owner._validate_execution_observation(
            observation, roles, host["executable_sha256"], hashlib.sha256(b"other run").hexdigest(),
        )
    original_mcp = deepcopy(mcp)
    mcp["process_start_identity_sha256"] = hashlib.sha256(
        b"same namespace other process"
    ).hexdigest()
    mcp["record_sha256"] = execution._digest({k: v for k, v in mcp.items() if k != "record_sha256"})
    with pytest.raises(owner.NativePreflightError, match="execution_role_binding_gap"):
        owner._validate_execution_observation(
            observation, roles, host["executable_sha256"], host["binding_sha256"],
        )
    observation["roles"]["mcp"] = original_mcp
    roles[0]["observation"]["net_namespace_sha256"] = hashlib.sha256(b"wrong namespace").hexdigest()
    with pytest.raises(owner.NativePreflightError, match="execution_role_binding_gap"):
        owner._validate_execution_observation(
            observation, roles, host["executable_sha256"], host["binding_sha256"],
        )


@pytest.mark.parametrize("mutation", [None, "private_path", "zero_commit", "missing_wheel"])
def test_owner_execution_context_requires_exact_candidate_binding(mutation):
    context = {
        "run_id": "native-run-1", "candidate_id": "candidate-1",
        "candidate_binding": {
            "commit": "a" * 40, "tree": "b" * 40,
            "lock_sha256": "c" * 64, "wheel_sha256": "d" * 64, "sdist_sha256": "e" * 64,
        },
    }
    if mutation == "private_path":
        context["path"] = "private"
    elif mutation == "zero_commit":
        context["candidate_binding"]["commit"] = "0" * 40
    elif mutation == "missing_wheel":
        del context["candidate_binding"]["wheel_sha256"]
    if mutation:
        with pytest.raises(owner.NativePreflightError, match="execution_context_invalid"):
            owner._validate_execution_context(context)
    else:
        validated = owner._validate_execution_context(context)
        validated["candidate_binding"]["commit"] = "changed"
        assert context["candidate_binding"]["commit"] == "a" * 40


def test_host_execution_observer_runs_after_ready_and_before_health(monkeypatch, tmp_path):
    from benchmarks.hosts import linux_guest_slot_control as control
    from benchmarks.hosts import native_fork_observation as fork

    operations = []
    handle = SimpleNamespace(pid=123, workdir=tmp_path)
    monkeypatch.setattr(fork, "snapshot_plugin_log", lambda _path: SimpleNamespace(
        data=b"opencode server listening on http://127.0.0.1:4096\n",
    ))
    monkeypatch.setattr(control, "_run_host_http", lambda *_args, **_kwargs: (
        operations.append("health") or {"healthy": True}
    ))
    instance = GuestSlotControl(
        require_host_ready=True, on_host_ready=lambda observed: operations.append(observed.pid),
    )
    instance._host = handle
    assert instance._host_operation({"op": "health"}, time.monotonic() + 2)["result"]["healthy"]
    assert operations == [123, "health"]
    instance._on_host_ready = lambda _handle: (_ for _ in ()).throw(ObservationGap("unavailable"))
    with pytest.raises(GuestSlotControlError, match="host_execution_observation_gap"):
        instance._host_operation({"op": "health"}, time.monotonic() + 2)
    assert operations == [123, "health"]
