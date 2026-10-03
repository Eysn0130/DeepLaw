from __future__ import annotations

import copy
import json
import os
from itertools import product
from pathlib import Path

import pytest

from benchmarks.hosts import run_maintenance_host_tasks as runner
from benchmarks.hosts.maintenance_task_cases import public_task_projection
from benchmarks.hosts.run_maintenance_host_tasks import outcome_commit_eligible
from deeplaw.context_compiler import verify_capsule
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.util import canonical_json, sha256_bytes


def _completed() -> dict:
    return {"failure": None, "failure_stage": None, "forced_kill": False, "host_exit_code": 0,
            "model_task_executed": True,
            "guard": {"cleanup_confirmed": True, "first_failure": None,
                      "cleanup_failure": None, "rejected": 0, "in_flight": 0}}


def test_completed_host_allows_outcome_commit() -> None:
    assert outcome_commit_eligible(_completed())


@pytest.mark.parametrize("field,value", [
    ("failure", "ModelIdentityError"), ("forced_kill", True),
    ("host_exit_code", 1), ("host_exit_code", None), ("host_exit_code", False),
    ("model_task_executed", False), ("model_task_executed", None),
])
def test_host_failure_prevents_success_commit(field: str, value: object) -> None:
    result = _completed()
    result[field] = value
    assert not outcome_commit_eligible(result)


@pytest.mark.parametrize("field,value", [
    ("cleanup_confirmed", False), ("first_failure", {"stage": "inspect"}),
    ("cleanup_failure", {"stage": "stop"}), ("rejected", 1), ("in_flight", 1),
])
def test_guard_failure_prevents_success_commit(field: str, value: object) -> None:
    result = copy.deepcopy(_completed())
    result["guard"][field] = value
    assert not outcome_commit_eligible(result)


def _mock_host(monkeypatch: pytest.MonkeyPatch, *, failure_stage: str | None,
               observations: list[dict]) -> None:
    class Process:
        returncode = None

        def poll(self):
            return self.returncode

        def send_signal(self, signal):
            pass

        def wait(self, timeout):
            self.returncode = 0

        def kill(self):
            self.returncode = -9

    class Guard:
        url = "http://127.0.0.1:1"
        receipt = _completed()["guard"]

        def __init__(self, path):
            pass

        def start(self):
            if failure_stage == "host":
                raise RuntimeError("private exception text must not be retained")

        def active(self, enabled):
            pass

        def stop(self):
            pass

    def environment(*, root, **kwargs):
        if failure_stage == "setup":
            raise RuntimeError("private exception text must not be retained")
        keys = ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                "XDG_STATE_HOME", "TMPDIR", "APPDATA", "LOCALAPPDATA", "OPENCODE_CONFIG_DIR")
        return {**{key: str(root / key) for key in keys},
                "OPENCODE_CONFIG": str(root / "opencode.json")}

    def control(base_url, method, path, value=None):
        if path == "/global/health" and failure_stage == "readiness":
            raise RuntimeError("private exception text must not be retained")
        if path == "/session":
            return {"id": "mock-session"}
        if path.endswith("/prompt_async") and failure_stage == "dispatch":
            raise RuntimeError("private exception text must not be retained")
        if path == "/mcp":
            return {"maintenance_environment": {"status": "connected"}}
        return None

    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(runner.shared, "ExternalGuard", Guard)
    monkeypatch.setattr(runner.host, "build_host_environment", environment)
    monkeypatch.setattr(runner.host, "_freeze_local_plugin_dependency_state",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_install_observer", lambda *args: None)
    monkeypatch.setattr(runner, "_control", control)
    monkeypatch.setattr(runner.shared, "json_lines", lambda path: observations)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)


def _execute_mock_case(tmp_path: Path, *, zero_model: bool = False) -> dict:
    return runner.execute_case(
        tmp_path / "case", configuration="no_memory", scenario="source_update",
        context={"capsule": {"capsule_id": "mock-capsule"}, "provider_capsule": {}},
        binary=Path("mock-opencode"), node=Path("mock-node"), key_file=Path("unused-key"),
        run_id="mock-run", candidate_id="mock-candidate", zero_model=zero_model,
    )


@pytest.mark.parametrize("stage,attempted", [
    ("setup", False), ("host", False), ("readiness", False), ("dispatch", True),
])
def test_pre_execution_failure_does_not_claim_model_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, attempted: bool,
) -> None:
    _mock_host(monkeypatch, failure_stage=stage, observations=[])
    result = _execute_mock_case(tmp_path)
    assert result["failure"] == "RuntimeError"
    assert result["model_task_executed"] is False
    assert result["prompt_dispatch_attempted"] is attempted
    assert result["prompt_dispatched"] is False
    assert result["status"] == ("failed" if attempted else "not_executed")
    assert result["score"] is None
    assert "private exception text" not in json.dumps(result)


def test_accepted_dispatch_without_observation_is_not_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_host(monkeypatch, failure_stage=None, observations=[])
    monkeypatch.setattr(runner, "MAX_SECONDS", 0)
    result = _execute_mock_case(tmp_path)
    assert result["prompt_dispatch_attempted"] is True
    assert result["prompt_dispatched"] is True
    assert result["model_task_executed"] is False
    assert result["model_execution_status"] == "unverified_after_dispatch"
    assert result["status"] == "failed"


@pytest.mark.parametrize("session,model,output,observed", [
    ("mock-session", "deepseek-flash", 1, True),
    ("other-session", "deepseek-flash", 1, False),
    ("mock-session", "other-model", 1, False),
    ("mock-session", "deepseek-flash", 0, False),
    ("mock-session", "deepseek-flash", None, False),
    ("mock-session", "deepseek-flash", True, False),
])
def test_model_execution_requires_bound_completed_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    session: str, model: str, output: object, observed: bool,
) -> None:
    session_sha = runner.shared.digest(session.encode())
    observations = [
        {"event": "message.updated", "session_sha256": session_sha, "finished": True,
         "provider": "deepseek", "model": model, "tokens": {"output": output}},
        {"event": "session.idle", "session_sha256": session_sha},
    ]
    _mock_host(monkeypatch, failure_stage=None, observations=observations)
    if session != "mock-session":
        monkeypatch.setattr(runner, "MAX_SECONDS", 0)
    result = _execute_mock_case(tmp_path)
    assert result["model_task_executed"] is observed
    assert result["formal_admission"] is False
    assert result["status"] == "failed"  # No action trace was supplied by this mock.


def test_zero_model_preflight_does_not_claim_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_host(monkeypatch, failure_stage=None, observations=[])
    result = _execute_mock_case(tmp_path, zero_model=True)
    assert result["model_task_executed"] is False
    assert result["prompt_dispatch_attempted"] is False
    assert result["prompt_dispatched"] is False
    assert result["status"] == "not_executed"


@pytest.mark.parametrize("stage", [
    "setup", "context", "context_write", "context_snapshot", "execution", "outcome",
    "evidence_binding",
])
def test_matrix_preserves_all_slots_after_bounded_stage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    root = tmp_path / "run"
    value = {"root": str(root), "binary": "mock-opencode", "binary_sha256": "a" * 64,
             "node": "mock-node", "key_file": "unused-key", "run_id": "mock-run",
             "candidate_id": "mock-candidate"}
    monkeypatch.setattr(runner.shared, "read_json", lambda path: value)
    monkeypatch.setattr(runner.shared, "exact_file", lambda path, sha: path)
    monkeypatch.setattr(runner.sys, "argv", ["maintenance-runner", "--input", "mock-input"])
    calls = {"setup": [], "context": [], "context_write": [], "context_snapshot": [],
             "execution": [], "outcome": [], "evidence_binding": []}
    ordered = []

    def fail(where, configuration, scenario=None):
        calls[where].append((configuration, scenario))
        ordered.append((where, configuration, scenario))
        if stage == where and configuration == "no_memory" \
                and (scenario is None or scenario == "source_update"):
            raise RuntimeError("private exception text must not be retained")

    def setup(vault, configuration):
        fail("setup", configuration)
        return {"grant_id": "mock-grant", "vault_id": "mock-vault"}

    def context(vault, configuration, scenario):
        fail("context", configuration, scenario)
        task = public_task_projection(configuration, scenario)
        return {"capsule": {"capsule_id": "mock-capsule", "provider_capsule": {}},
                "provider_capsule": {}, "context_id": "mock-capsule",
                "task_input_sha256": task["input_sha256"], "verification": {"valid": True},
                "context_provenance": "source_free_synthetic_memory_compile"}

    original_json_file = runner._json_file

    def json_file(path, payload):
        if path.name == "context.json":
            fail("context_write", *calls["context"][-1])
        original_json_file(path, payload)

    def snapshot(vault, destination, *, expected_vault_id):
        configuration, scenario = calls["context"][-1]
        assert (destination.parent / "context.json").is_file()
        assert not any(entry == ("execution", configuration, scenario) for entry in ordered)
        fail("context_snapshot", configuration, scenario)
        destination.mkdir(mode=0o700)
        return {"schema_version": "deeplaw.maintenance-fixture-snapshot/v1",
                "vault_id": expected_vault_id, "legacy_revision": 1,
                "legacy_audit_head": "a" * 64, "autonomous_sequence": 2,
                "autonomous_audit_head": "b" * 64,
                "public_input_sha256": runner.FROZEN_INPUT_SHA256,
                "inventory": [], "inventory_sha256": runner.digest([])}

    def execute(root, *, configuration, scenario, **kwargs):
        fail("execution", configuration, scenario)
        root.mkdir(mode=0o700)
        for name, payload in (
            ("action-trace.json", canonical_json({"events": []}).encode() + b"\n"),
            ("provider-capsule.json", b"{}"), ("native-events.jsonl", b""),
        ):
            path = root / name
            if stage == "evidence_binding" and name == "action-trace.json" \
                    and (configuration, scenario) == ("no_memory", "source_update"):
                payload = b'{"events":[{}]}\n'
            path.write_bytes(payload)
            path.chmod(0o600)
        return {**_completed(), "trace": {"events": []}, "score": {"passed": True},
                "native_observations": [],
                "formal_admission": False, "prompt_dispatch_attempted": True,
                "prompt_dispatched": True, "model_execution_status": "observed"}

    def outcome(vault, grant_id, *, configuration, scenario, **kwargs):
        fail("outcome", configuration, scenario)
        return {"status": "recorded"}

    monkeypatch.setattr(runner, "prepare_context_vault", setup)
    monkeypatch.setattr(runner, "capture_context", context)
    monkeypatch.setattr(runner, "_json_file", json_file)
    monkeypatch.setattr(runner, "freeze_public_fixture_vault", snapshot, raising=False)
    monkeypatch.setattr(runner, "execute_case", execute)
    monkeypatch.setattr(runner, "record_host_outcome", outcome)
    original_evidence = runner._capture_evidence_files

    def evidence(root, result, *, configuration, scenario, **kwargs):
        identity = (configuration, scenario)
        calls["evidence_binding"].append(identity)
        ordered.append(("evidence_binding", *identity))
        original_evidence(root, result, configuration=configuration, scenario=scenario, **kwargs)

    monkeypatch.setattr(runner, "_capture_evidence_files", evidence)
    runner.main()
    report = json.loads((root / "report.json").read_text())
    results = report["results"]
    assert len(results) == 15
    assert [(item["configuration_id"], item["scenario_id"]) for item in results] == list(
        product(runner.CONFIGURATION_ORDER, runner.SCENARIO_ORDER)
    )
    assert len(list(root.glob("case-*.json"))) == 15
    failed = [item for item in results if item["failure"] is not None]
    assert len(failed) == (5 if stage == "setup" else 1)
    for item in failed:
        assert item["failure"] == (
            "EvidenceBindingRejected" if stage == "evidence_binding" else "RuntimeError"
        )
        assert item["failure_stage"] == ("context_snapshot" if stage == "context_write" else stage)
        assert item["status"] == (
            "not_executed" if stage in {"setup", "context", "context_write", "context_snapshot"}
            else "failed"
        )
        if stage not in {"outcome", "evidence_binding"}:
            assert item["model_task_executed"] is False
            assert item["score"] is None
        elif stage == "outcome":
            assert item["knowledge_outcome"]["commit_status"] == "unknown"
        else:
            assert item["knowledge_outcome"] == {"status": "recorded"}
            assert item["score"]["passed"] is True and item["model_task_executed"] is True
            assert item["evidence_files"]["action_trace"] is None
    assert all(item["formal_admission"] is False for item in results)
    assert "private exception text" not in json.dumps(report)
    assert len(calls["setup"]) == 3
    assert len(calls[stage]) == len(set(calls[stage]))  # No ambiguous mutation is replayed.
    for item in results:
        identity = (item["configuration_id"], item["scenario_id"])
        if item["failure_stage"] in {"setup", "context", "context_snapshot"}:
            assert item["context_snapshot"] is None
            assert identity not in calls["execution"] and identity not in calls["outcome"]
            receipt = item["evidence_files"]
            assert all(receipt[field] is None for field in (
                "action_trace", "provider_capsule", "native_events",
            ))
            assert all(gap["reason"] == "not_created_pre_dispatch" for gap in receipt["gaps"])
            continue
        evidence = item["context_snapshot"]
        assert evidence["run_id"] == item["run_id"]
        assert evidence["candidate_id"] == item["candidate_id"]
        assert evidence["configuration_id"] == identity[0]
        assert evidence["scenario_id"] == identity[1]
        assert evidence["vault_id"] == "mock-vault"
        task = public_task_projection(*identity)
        assert evidence["task_input_sha256"] == task["input_sha256"]
        assert evidence["task_binding"] == runner.build_task_context_binding(
            runner.digest(identity[0]), runner.digest(task["task_id"]),
        )
        assert evidence["owner_binding"] == runner.make_owner_binding(
            *identity, run_id=item["run_id"], candidate_id=item["candidate_id"],
            context_id=evidence["context_id"], capsule_digest=evidence["provider_capsule_sha256"],
        )
        assert evidence["binding_sha256"] == runner.digest(
            {key: value for key, value in evidence.items() if key != "binding_sha256"},
        )
        for field in ("context", "snapshot_manifest"):
            reference = evidence[field]
            path = root / reference["path"]
            assert not Path(reference["path"]).is_absolute()
            assert path.read_bytes() == canonical_json(json.loads(path.read_bytes())).encode()
            assert sha256_bytes(path.read_bytes()) == reference["sha256"]
            assert path.stat().st_size == reference["size"]
            assert path.stat().st_mode & 0o077 == 0
        assert ordered.index(("context_snapshot", *identity)) < ordered.index(
            ("execution", *identity),
        )
        if identity in calls["outcome"]:
            assert ordered.index(("execution", *identity)) < ordered.index(("outcome", *identity))
            assert ordered.index(("outcome", *identity)) < ordered.index(
                ("evidence_binding", *identity),
            )
    if stage == "evidence_binding":
        assert len(calls["execution"]) == len(calls["outcome"]) == 15


@pytest.mark.skipif(os.name != "posix", reason="fixture snapshot requires POSIX")
def test_context_snapshot_exports_exact_context_and_strictly_reopens(tmp_path):
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    vault = root / "source"
    setup = runner.prepare_context_vault(vault, "governed_maintenance")
    context = runner.capture_context(vault, "governed_maintenance", "source_update")
    before = {
        path.relative_to(vault).as_posix(): sha256_bytes(path.read_bytes())
        for path in vault.rglob("*") if path.is_file()
    }
    evidence = runner._persist_context_snapshot(
        root, vault, configuration="governed_maintenance", scenario="source_update",
        context=context, run_id="mock-run", candidate_id="mock-candidate",
        expected_vault_id=setup["vault_id"],
    )
    context_bytes = (root / evidence["context"]["path"]).read_bytes()
    manifest_bytes = (root / evidence["snapshot_manifest"]["path"]).read_bytes()
    assert context_bytes == canonical_json(context).encode()
    assert evidence["context"]["sha256"] == sha256_bytes(context_bytes)
    assert evidence["snapshot_manifest"]["sha256"] == sha256_bytes(manifest_bytes)
    manifest = json.loads(manifest_bytes)
    frozen = root / evidence["vault_path"]
    with KnowledgeVault(frozen, read_only=True) as legacy:
        assert verify_capsule(json.loads(context_bytes)["capsule"], vault=legacy)["valid"] is True
        assert legacy.audit_head == evidence["legacy_audit_head"]
    with AutonomousKnowledgeStore(frozen, read_only=True) as checked:
        assert checked.inspect()["verification"]["valid"] is True
        assert checked.audit_head == evidence["autonomous_audit_head"]
        assert checked.sequence == evidence["autonomous_sequence"]
        assert checked.vault_id == evidence["vault_id"] == setup["vault_id"]
    for item in manifest["inventory"]:
        path = frozen / item["path"]
        assert sha256_bytes(path.read_bytes()) == item["sha256"]
        assert path.stat().st_size == item["size"]
    assert {
        path.relative_to(vault).as_posix(): sha256_bytes(path.read_bytes())
        for path in vault.rglob("*") if path.is_file()
    } == before
    assert list((frozen / ".deeplaw/capabilities").iterdir()) == []
    assert str(root) not in canonical_json(evidence)
    assert not any(".token" in item["path"] for item in manifest["inventory"])


def _evidence_fixture(root: Path) -> tuple[dict, dict, dict[str, bytes]]:
    root.mkdir(mode=0o700)
    directory = root / "no_memory-source_update"
    directory.mkdir(mode=0o700)
    trace = {"events": []}
    provider = {"items": []}
    native = [{"event": "session.idle", "session_sha256": "a" * 64}]
    payloads = {
        "action-trace.json": b'{ "events" : [] }\n',
        "provider-capsule.json": b'{ "items" : [] }\n',
        "native-events.jsonl": canonical_json(native[0]).encode() + b"\n",
    }
    for name, raw in payloads.items():
        path = directory / name
        path.write_bytes(raw)
        path.chmod(0o600)
    result = {**_completed(), "trace": trace, "native_observations": native,
              "prompt_dispatched": True, "status": "failed"}
    return result, {"provider_capsule": provider}, payloads


@pytest.mark.skipif(os.name != "posix", reason="evidence binding requires POSIX")
def test_evidence_files_bind_original_bytes_without_reading_other_case_files(tmp_path):
    root = tmp_path / "run"
    result, context, payloads = _evidence_fixture(root)
    directory = root / "no_memory-source_update"
    (directory / "guard.json").symlink_to(tmp_path / "unavailable-guard")
    (directory / "workspace").symlink_to(tmp_path / "unavailable-workspace")
    before = {name: ((directory / name).read_bytes(), (directory / name).stat().st_mode)
              for name in payloads}
    runner._capture_evidence_files(
        root, result, configuration="no_memory", scenario="source_update", context=context,
    )
    receipt = result["evidence_files"]
    assert receipt["gaps"] == []
    assert receipt["caption"] == "Engineering evidence hashes; native authority is not established."
    for field, name in (("action_trace", "action-trace.json"),
                        ("provider_capsule", "provider-capsule.json"),
                        ("native_events", "native-events.jsonl")):
        assert receipt[field] == {
            "path": "no_memory-source_update/" + name,
            "sha256": sha256_bytes(payloads[name]), "size": len(payloads[name]),
        }
    assert result["failure"] is None and result["failure_stage"] is None
    assert "evidence_binding_failure" not in result
    assert "workspace" not in canonical_json(receipt) and str(root) not in canonical_json(receipt)
    assert {name: ((directory / name).read_bytes(), (directory / name).stat().st_mode)
            for name in payloads} == before


@pytest.mark.skipif(os.name != "posix", reason="evidence binding requires POSIX")
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "public_mode", "oversize",
                                  "changed_during_read", "replaced_during_read", "different_trace",
                                  "different_provider", "different_native", "duplicate_keys"])
def test_evidence_file_rejection_preserves_original_failure_and_files(
    tmp_path, monkeypatch, kind,
):
    root = tmp_path / "run"
    result, context, _ = _evidence_fixture(root)
    trace = root / "no_memory-source_update/action-trace.json"
    rejected_field = "action_trace"
    result.update({"failure": "OriginalHostFailure", "failure_stage": "observation"})
    if kind in {"symlink", "hardlink", "fifo"}:
        trace.unlink()
        other = root / "original-trace"
        other.write_bytes(b"{}")
        other.chmod(0o600)
        if kind == "symlink":
            trace.symlink_to(other)
        elif kind == "hardlink":
            os.link(other, trace)
        else:
            os.mkfifo(trace, 0o600)
    elif kind == "public_mode":
        trace.chmod(0o644)
    elif kind == "oversize":
        trace.write_bytes(b" " * (runner.MAX_TRACE_BYTES + 1))
    elif kind == "different_trace":
        trace.write_bytes(b'{"events":[{}]}')
    elif kind == "different_provider":
        (trace.parent / "provider-capsule.json").write_bytes(b'{"items":[{}]}')
        rejected_field = "provider_capsule"
    elif kind == "different_native":
        (trace.parent / "native-events.jsonl").write_bytes(b'{"event":"session.error"}\n')
        rejected_field = "native_events"
    elif kind == "duplicate_keys":
        trace.write_bytes(b'{"events":[{}],"events":[]}')
    else:
        original_read = runner.os.read
        changed = False

        def read(fd, size):
            nonlocal changed
            raw = original_read(fd, size)
            if not changed and raw.startswith(b'{ "events"'):
                changed = True
                if kind == "changed_during_read":
                    trace.write_bytes(b'{"events": []}\n')
                else:
                    trace.unlink()
                    trace.write_bytes(b'{"events": []}\n')
                    trace.chmod(0o600)
            return raw

        monkeypatch.setattr(runner.os, "read", read)
    runner._capture_evidence_files(
        root, result, configuration="no_memory", scenario="source_update", context=context,
    )
    assert result["evidence_files"][rejected_field] is None
    assert {"evidence": rejected_field, "reason": "rejected"} in result["evidence_files"]["gaps"]
    assert result["evidence_binding_failure"]["failure_stage"] == "evidence_binding"
    assert result["failure"] == "OriginalHostFailure" and result["failure_stage"] == "observation"
    assert trace.lstat() and result["status"] == "failed"


@pytest.mark.skipif(os.name != "posix", reason="evidence binding requires POSIX")
@pytest.mark.parametrize("observed", [False, True])
def test_missing_original_evidence_never_uses_memory_as_file_bytes(tmp_path, observed):
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    result = {**_completed(), "status": "failed", "model_task_executed": observed,
              "trace": {"events": []} if observed else None,
              "native_observations": [{"event": "session.idle"}] if observed else []}
    runner._capture_evidence_files(
        root, result, configuration="no_memory", scenario="source_update", context=None,
    )
    receipt = result["evidence_files"]
    assert all(receipt[field] is None for field in (
        "action_trace", "provider_capsule", "native_events",
    ))
    assert len(receipt["gaps"]) == 3
    assert all(item["reason"] == "missing" for item in receipt["gaps"])
    assert list(root.iterdir()) == []
    if observed:
        assert result["failure"] == "EvidenceBindingRejected"
        assert result["failure_stage"] == "evidence_binding"
    else:
        assert result["failure"] is None and "evidence_binding_failure" not in result


@pytest.mark.skipif(os.name != "posix", reason="evidence binding requires POSIX")
def test_pre_dispatch_failure_does_not_read_existing_case_path(tmp_path, monkeypatch):
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    path = root / "no_memory-source_update"
    path.symlink_to(tmp_path / "unavailable-case")
    result = runner._failed_slot(
        "no_memory", "source_update", run_id="mock-run", candidate_id="mock-candidate",
        stage="context_snapshot", failure="OriginalSnapshotFailure",
    )

    def unexpected_open(*args, **kwargs):
        raise AssertionError("pre-dispatch evidence path must not be read")

    monkeypatch.setattr(runner.os, "open", unexpected_open)
    runner._capture_evidence_files(
        root, result, configuration="no_memory", scenario="source_update",
        context=None, prepared=False,
    )
    assert all(item["reason"] == "not_created_pre_dispatch"
               for item in result["evidence_files"]["gaps"])
    assert "evidence_binding_failure" not in result
    assert result["status"] == "not_executed" and result["failure"] == "OriginalSnapshotFailure"
    assert path.is_symlink()


@pytest.mark.skipif(os.name != "posix", reason="evidence binding requires POSIX")
def test_evidence_binding_rejects_case_directory_escape(tmp_path):
    root = tmp_path / "run"
    result, context, _ = _evidence_fixture(root)
    directory = root / "no_memory-source_update"
    outside = tmp_path / "outside-case"
    directory.rename(outside)
    directory.symlink_to(outside)
    runner._capture_evidence_files(
        root, result, configuration="no_memory", scenario="source_update", context=context,
    )
    assert all(result["evidence_files"][field] is None for field in (
        "action_trace", "provider_capsule", "native_events",
    ))
    assert result["failure_stage"] == "evidence_binding"
    assert all(item["reason"] == "rejected" for item in result["evidence_files"]["gaps"])
    assert directory.is_symlink() and (outside / "action-trace.json").read_bytes()
