"""Real public fixture/trace bytes plus synthetic Host claims are engineering only."""

import os
import shutil
from copy import deepcopy

import pytest

from benchmarks.hosts import maintenance_run_evidence as consumer
from benchmarks.hosts import maintenance_task_mcp
from benchmarks.hosts import run_maintenance_host_tasks as runner
from benchmarks.hosts.maintenance_task_cases import MaintenanceTaskSession, score_host_trace
from deeplaw.util import canonical_json, sha256_bytes, strict_json_loads

pytestmark = pytest.mark.skipif(os.name != "posix", reason="run evidence requires POSIX")


def _write(path, value):
    path.write_bytes(canonical_json(value).encode())
    path.chmod(0o600)


@pytest.fixture(scope="module")
def public_run(tmp_path_factory):
    directory = tmp_path_factory.mktemp("public-run")
    root = directory / "run"
    root.mkdir(mode=0o700)
    sources = directory / "synthetic-fixtures"
    sources.mkdir(mode=0o700)
    _write(root / "frozen-input.json", {
        "input_sha256": runner.FROZEN_INPUT_SHA256,
        "configurations": runner.CONFIGURATION_ORDER, "scenarios": runner.SCENARIO_ORDER,
        "max_seconds_per_case": 180, "max_provider_requests_per_case": 6,
        "binary_sha256": "b" * 64, "candidate_id": "synthetic-candidate",
    })
    results = []
    for configuration in runner.CONFIGURATION_ORDER:
        vault = sources / configuration
        setup = runner.prepare_context_vault(vault, configuration)
        for scenario in runner.SCENARIO_ORDER:
            run_id = "synthetic-run-" + configuration + "-" + scenario
            context = runner.capture_context(vault, configuration, scenario)
            receipt = runner._persist_context_snapshot(
                root, vault, configuration=configuration, scenario=scenario,
                context=context, run_id=run_id, candidate_id="synthetic-candidate",
                expected_vault_id=setup["vault_id"],
            )
            case = root / (configuration + "-" + scenario)
            case.mkdir(mode=0o700)
            session = MaintenanceTaskSession(configuration, scenario)
            task = session.public_task
            if scenario == "unknown_action":
                kind = "record_unknown"
                parameters = {
                    "action_name": task["environment"]["scenario_facts"]["operation_name"],
                }
            elif scenario == "source_update":
                kind = "submit_resource_version"
                parameters = {"resource_id": task["environment"]["resource"]["resource_id"],
                              "version": "v2", "source_ref": "source:orchid-v2"}
                if configuration == "governed_maintenance":
                    parameters["experience_id"] = (
                        task["environment"]["experience"]["records"][0]["experience_id"]
                    )
            else:
                kind = "record_report_note"
                parameters = {"report_id": task["environment"]["report"]["report_id"],
                              "note": "Public synthetic incomplete task."}
            session.submit({
                "action_id": "public-action", "kind": kind,
                "observed_state_sha256": session.state_sha256, "parameters": parameters,
            })
            binding = receipt["owner_binding"]
            store = maintenance_task_mcp._TraceStore(case / "action-trace.json", binding, session)
            store.persist(session)
            trace = maintenance_task_mcp.load_persisted_trace(
                case / "action-trace.json", binding=binding,
            )
            score = score_host_trace(configuration, scenario, trace["events"])
            _write(case / "provider-capsule.json", context["provider_capsule"])
            session_sha = sha256_bytes(run_id.encode())
            native = [
                {"event": "message.updated", "session_sha256": session_sha, "provider": "deepseek",
                 "model": "deepseek-v4-flash", "finished": True, "cost": None,
                 "tokens": {"input": 1, "output": 1, "reasoning": 0, "cache_read": 0}},
                {"event": "session.idle", "session_sha256": session_sha},
            ]
            (case / "native-events.jsonl").write_bytes(
                b"".join(canonical_json(item).encode() + b"\n" for item in native)
            )
            (case / "native-events.jsonl").chmod(0o600)
            if scenario == "independent_support":
                outcome = {"status": "failed", "reason": "outcome_recording_failed",
                           "commit_status": "unknown"}
            else:
                # Only the existing public test-fixture owner entry creates/revokes its grant.
                outcome = runner.record_host_outcome(
                    vault, setup["grant_id"], configuration=configuration, scenario=scenario,
                    host_run_id=run_id, host_id="opencode", model_id="deepseek-v4-flash",
                    context=context, trace_payload=trace, score=score,
                    candidate_id="synthetic-candidate",
                )
            # These are synthetic declarations, never an actual Host/model/cleanup attestation.
            result = {
                "configuration_id": configuration, "scenario_id": scenario,
                "run_id": run_id, "candidate_id": "synthetic-candidate", "binding": binding,
                "trace": trace, "score": score, "native_observations": native, "host_exit_code": 0,
                "forced_kill": False, "guard": {"requests": [], "rejected": 0, "in_flight": 0,
                    "first_failure": None, "cleanup_confirmed": True, "cleanup_failure": None},
                "elapsed_ms": 1.0,
                "failure": "RuntimeError" if scenario == "independent_support" else None,
                "failure_stage": "outcome" if scenario == "independent_support" else None,
                "status": "succeeded" if score["passed"] else "failed",
                "formal_admission": False, "model_task_executed": True,
                "prompt_dispatch_attempted": True, "prompt_dispatched": True,
                "model_execution_status": "observed", "zero_model_mcp_status": None,
                "state_score_status": "observed",
                "isolation_evidence": "not_supplied_by_this_producer",
                "knowledge_outcome": outcome, "context_snapshot": receipt,
            }
            runner._capture_evidence_files(
                root, result, configuration=configuration, scenario=scenario, context=context,
            )
            assert result["evidence_files"]["gaps"] == []
            results.append(result)
            _write(root / f"case-{len(results):02}.json", result)
    _write(root / "report.json", {"results": results, "formal_admission": False,
                                  "fixed_input_sha256": runner.FROZEN_INPUT_SHA256})
    # Original runner-private areas are intentionally not enumerated or read by this consumer.
    (root / "governed_maintenance-vault").symlink_to(directory / "unavailable-private-vault")
    (root / "no_memory-source_update/workspace").symlink_to(directory / "unavailable-workspace")
    (root / "no_memory-source_update/guard.json").symlink_to(directory / "unavailable-guard")
    return root


def _validate(root, **kwargs):
    return consumer.validate_run_evidence(root, **{
        "run_id": "synthetic-run", "candidate_id": "synthetic-candidate",
        "binary_sha256": "b" * 64, **kwargs,
    })


def _state(root):
    return {path.relative_to(root).as_posix(): (
        path.lstat().st_mode,
        sha256_bytes(path.read_bytes()) if path.is_file() and not path.is_symlink() else None,
    ) for path in (root, *root.rglob("*"))}


def _copy(public_run, tmp_path):
    root = tmp_path / "run"
    shutil.copytree(public_run, root, symlinks=True)
    return root


def _case(root, index=0):
    return strict_json_loads((root / f"case-{index + 1:02}.json").read_bytes())


def _replace_case(root, value, index=0):
    report = strict_json_loads((root / "report.json").read_bytes())
    report["results"][index] = value
    _write(root / f"case-{index + 1:02}.json", value)
    _write(root / "report.json", report)


def test_public_run_recomputes_honest_failure_and_unknown_without_native_authority(public_run):
    before = _state(public_run)
    result = _validate(public_run)
    assert result["formal_admission"] is False
    assert len(result["slots"]) == 15
    assert sum(slot["score"]["passed"] for slot in result["slots"]) == 3
    unknown = [slot for slot in result["slots"] if slot["scenario_id"] == "unknown_action"]
    assert len(unknown) == 3 and all(slot["score"]["safe_termination"] for slot in unknown)
    assert all(slot["score"]["passed"] is False for slot in unknown)
    assert sum(slot["producer_status"] == "failed" for slot in result["slots"]) == 12
    assert "run_record" in _case(public_run)["knowledge_outcome"]
    assert len(_case(public_run, 10)["knowledge_outcome"]["feedback_records"]) == 1
    assert "Actual native admission remains missing." in result["caption"]
    assert _state(public_run) == before
    paths = [reference["path"] for reference in result["files"]]
    assert paths == sorted(set(paths))
    assert not any(
        "workspace" in path or "guard.json" in path or "-vault/" in path for path in paths
    )
    for reference in result["files"]:
        raw = (public_run / reference["path"]).read_bytes()
        assert reference["size"] == len(raw) and reference["sha256"] == sha256_bytes(raw)


@pytest.mark.parametrize("field,value", [
    ("run_id", "other-run"), ("candidate_id", "other-candidate"), ("binary_sha256", "0" * 64),
])
def test_run_evidence_rejects_expected_identity_drift(public_run, field, value):
    with pytest.raises(ValueError):
        _validate(public_run, **{field: value})


@pytest.mark.parametrize("tamper", [
    "reorder", "duplicate_slot", "extra_slot", "missing_slot", "report_extra", "case_mismatch",
    "case_extra", "not_executed", "model_false", "gaps", "binding_failure", "score", "trace_swap",
    "provider_drift", "native_drift", "reference_extra", "missing_trace", "duplicate_json",
    "nonfinite_json", "frozen_budget", "trace_tool_error", "trace_invalid_chain",
    "context_cross_binding", "context_drift", "snapshot_extra", "native_projection",
    "native_model", "native_extra", "native_duplicate_json", "provider_projection",
    "outcome_extra", "frozen_oversize",
])
def test_original_run_evidence_rejects_incomplete_or_drifted_bytes(public_run, tmp_path, tamper):
    root = _copy(public_run, tmp_path)
    report = strict_json_loads((root / "report.json").read_bytes())
    item = deepcopy(report["results"][0])
    if tamper in {"reorder", "duplicate_slot", "extra_slot", "missing_slot", "report_extra"}:
        if tamper == "reorder":
            report["results"][0], report["results"][1] = report["results"][1], report["results"][0]
        elif tamper == "duplicate_slot":
            report["results"][1] = deepcopy(report["results"][0])
        elif tamper == "extra_slot":
            report["results"].append(deepcopy(report["results"][0]))
        elif tamper == "missing_slot":
            report["results"].pop()
        else:
            report["extra"] = "synthetic-marker"
        _write(root / "report.json", report)
    elif tamper == "case_mismatch":
        item["elapsed_ms"] = 2.0
        _write(root / "case-01.json", item)
    elif tamper in {"duplicate_json", "nonfinite_json"}:
        path = root / "report.json"
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + (b',"formal_admission":false}' if tamper == "duplicate_json"
                                    else b',"extra":1e999}'))
    elif tamper == "frozen_oversize":
        (root / "frozen-input.json").write_bytes(b" " * 65_537)
    elif tamper == "frozen_budget":
        value = strict_json_loads((root / "frozen-input.json").read_bytes())
        value["max_provider_requests_per_case"] = 7
        _write(root / "frozen-input.json", value)
    else:
        if tamper == "case_extra":
            item["private_key"] = "synthetic-marker"
        elif tamper == "not_executed":
            item["status"] = "not_executed"
        elif tamper == "model_false":
            item["model_task_executed"] = False
        elif tamper == "gaps":
            item["evidence_files"]["gaps"] = [{"evidence": "action_trace", "reason": "missing"}]
        elif tamper == "binding_failure":
            item["evidence_binding_failure"] = {"failure_stage": "evidence_binding"}
        elif tamper == "score":
            item["score"]["passed"] = not item["score"]["passed"]
        elif tamper == "reference_extra":
            item["evidence_files"]["action_trace"]["extra"] = "synthetic-marker"
        elif tamper == "context_cross_binding":
            item["context_snapshot"] = deepcopy(report["results"][1]["context_snapshot"])
        elif tamper == "context_drift":
            path = root / item["context_snapshot"]["context"]["path"]
            path.write_bytes(path.read_bytes() + b" ")
        elif tamper == "snapshot_extra":
            path = root / item["context_snapshot"]["vault_path"] / "objects/extra-synthetic"
            path.parent.mkdir(mode=0o700, exist_ok=True)
            path.write_bytes(b"Public unreferenced synthetic object.")
            path.chmod(0o600)
        elif tamper == "outcome_extra":
            item["knowledge_outcome"]["private_key"] = "synthetic-marker"
        elif tamper == "native_projection":
            item["native_observations"][0]["tokens"]["output"] = 2
        else:
            field = "provider_capsule" if tamper.startswith("provider_") else (
                "native_events" if tamper.startswith("native_") else "action_trace"
            )
            reference = item["evidence_files"][field]
            path = root / reference["path"]
            if tamper == "missing_trace":
                path.unlink()
            elif tamper == "trace_swap":
                other = report["results"][1]["evidence_files"]["action_trace"]["path"]
                path.write_bytes((root / other).read_bytes())
                reference.update({"sha256": sha256_bytes(path.read_bytes()),
                                  "size": path.stat().st_size})
                item["trace"] = strict_json_loads(path.read_bytes())
            elif tamper in {"native_model", "native_extra", "native_duplicate_json"}:
                value = deepcopy(item["native_observations"])
                if tamper == "native_model":
                    value[0]["model"] = "synthetic-other-model"
                elif tamper == "native_extra":
                    value[0]["extra"] = "synthetic-marker"
                raw = b"".join(canonical_json(row).encode() + b"\n" for row in value)
                if tamper == "native_duplicate_json":
                    line, remainder = raw.split(b"\n", 1)
                    raw = line[:-1] + b',"finished":true}\n' + remainder
                path.write_bytes(raw)
                reference.update({"sha256": sha256_bytes(raw), "size": len(raw)})
                item["native_observations"] = value
            elif tamper == "provider_projection":
                value = strict_json_loads(path.read_bytes())
                value["synthetic_marker"] = "Public marker."
                _write(path, value)
                reference.update({"sha256": sha256_bytes(path.read_bytes()),
                                  "size": path.stat().st_size})
            elif tamper in {"trace_tool_error", "trace_invalid_chain"}:
                value = strict_json_loads(path.read_bytes())
                if tamper == "trace_tool_error":
                    value["tool_errors"] = [{"attempt_ordinal": 1, "code": "invalid_action",
                                            "request_sha256": "0" * 64}]
                else:
                    value["events"][0]["event_sha256"] = "0" * 64
                _write(path, value)
                raw = path.read_bytes()
                reference.update({"sha256": sha256_bytes(raw), "size": len(raw)})
                item["trace"] = value
            else:
                path.write_bytes(path.read_bytes() + b" ")
        _replace_case(root, item)
    before = _state(root)
    with pytest.raises(ValueError) as error:
        _validate(root)
    assert str(root) not in str(error.value) and "synthetic-marker" not in str(error.value)
    assert _state(root) == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "unsafe_mode"])
def test_run_evidence_rejects_unsafe_raw_files_without_removing_them(public_run, tmp_path, kind):
    root = _copy(public_run, tmp_path)
    item = _case(root)
    path = root / item["evidence_files"]["action_trace"]["path"]
    if kind == "unsafe_mode":
        path.chmod(0o644)
    else:
        saved = root / "saved-trace"
        path.rename(saved)
        if kind == "symlink":
            path.symlink_to(saved)
        elif kind == "hardlink":
            os.link(saved, path)
        else:
            os.mkfifo(path, 0o600)
    with pytest.raises(ValueError):
        _validate(root)
    assert path.lstat()


def test_run_evidence_rejects_replacement_between_reads(public_run, tmp_path, monkeypatch):
    root = _copy(public_run, tmp_path)
    original = consumer._read
    replaced = False

    def read(run_root, relative, maximum):
        nonlocal replaced
        result = original(run_root, relative, maximum)
        if relative == "frozen-input.json" and not replaced:
            replaced = True
            path = root / relative
            replacement = root / "synthetic-replacement"
            replacement.write_bytes(result[0])
            replacement.chmod(0o600)
            replacement.replace(path)
        return result

    monkeypatch.setattr(consumer, "_read", read)
    with pytest.raises(ValueError):
        _validate(root)
    assert replaced and (root / "frozen-input.json").is_file()


def test_run_evidence_rejects_claimed_success_for_unknown(public_run, tmp_path):
    root = _copy(public_run, tmp_path)
    index = runner.SCENARIO_ORDER.index("unknown_action")
    item = _case(root, index)
    assert item["score"]["unknown_outcome"] is True
    item["status"] = "succeeded"
    _replace_case(root, item, index)
    before = _state(root)
    with pytest.raises(ValueError):
        _validate(root)
    assert _state(root) == before
