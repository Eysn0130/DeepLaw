from __future__ import annotations

import hashlib
import json
import os
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from benchmarks.hosts import host_preflight_receipt as host_preflight
from benchmarks.hosts import run_v013_host_task_qualification as host_task_runner
from benchmarks.hosts.run_v013_host_task_qualification import (
    HostTaskQualificationError,
    load_task_cases,
    task_case,
    validate_host_task_matrix,
)
from benchmarks.release.typed_qualification_evidence import (
    TypedQualificationEvidenceError,
    parse_typed_evidence,
)
from benchmarks.release.typed_qualification_evidence_v3_host_tasks import (
    HARD_FAILURE_IDS,
    TASK_DUTIES,
    TASK_OPERATIONS,
    TASK_WRONG_STATES,
    HostTaskEvidenceError,
    _host_identity_projection,
    parse_host_task_evidence,
)
from deeplaw.native_host import derive_native_host_receipt

COMMIT = "a" * 40
TREE = "b" * 40
LOCK = "c" * 64
WHEEL = "d" * 64
SDIST = "e" * 64
RUNNER = {"identity": "runner:v013-host-task", "sha256": "1" * 64}
SCORER = {"identity": "scorer:v013-derived", "sha256": "2" * 64}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _expected_sha(root: Path, host: str, task: str) -> str:
    return _sha((root / f"{host}/{task}/expected.json").read_bytes())


def _refresh_source_ref(manifest: Path, source_key: str, source_path: Path) -> None:
    envelope = json.loads(manifest.read_text())
    raw = source_path.read_bytes()
    reference = envelope["payload"][source_key]
    reference["byte_size"] = len(raw)
    reference["sha256"] = _sha(raw)
    envelope["record_sha256"] = _sha(
        _canonical({key: value for key, value in envelope.items() if key != "record_sha256"})
    )
    manifest.write_bytes(_canonical(envelope))


def _source(root: Path, relative: str, value: Any) -> dict[str, Any]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical(value)
    path.write_bytes(raw)
    return {
        "relative_path": relative,
        "byte_size": len(raw),
        "sha256": _sha(raw),
        "media_type": "application/json",
    }


def _candidate() -> dict[str, str]:
    return {
        "commit": COMMIT,
        "tree": TREE,
        "lock_sha256": LOCK,
        "wheel_sha256": WHEEL,
        "sdist_sha256": SDIST,
    }


def _host_identity(host: str, *, current: bool = False) -> dict[str, Any]:
    if host == "codex":
        if current:
            return {
                "binary_version": "codex-cli 0.149.0-alpha.4.3",
                "binary_sha256": "dd304ffe232fa9e782ed3e5358776d270e394c2fb85cab846f989823f0843313",
                "request_model": "gpt-5.6-luna",
                "reasoning_effort": "max",
                "auth_status_command": "codex login status",
                "auth_material_access": "forbidden",
            }
        return {
            "binary_version": "codex-cli 0.148.0-alpha.15",
            "binary_sha256": "7645c3caf5607e4528eb3a15b12496c284c2a918939aed34e863c760c1b421e7",
            "request_model": "gpt-5.6-luna",
            "reasoning": "max",
        }
    value = {
        "version": "1.18.16",
        "source_commit": "a3647eb025c7615159d417dcc49fc39fdaeba65b",
        "config_selector": "deepseek/deepseek-v4-flash",
        "expected_response_model_id": "deepseek-v4-flash",
        "executable_sha256": "a41776bf64c75786d6baf531b840ffb873c090d7c44793ae2dd4b1896de56a1f",
        "package_sha256": "d40af2479740f8ad3a32b700e9a907794ba4314c926d0e805c20fe39751d8722",
    }
    if current:
        value.update(
            runtime="host_bun_runtime_only",
            dotenv_policy="owner_only_external_strict_parser",
            secret_visibility="forbidden",
        )
    return value


def _event(host: str, event_type: str, index: int, *, current: bool = False) -> dict[str, Any]:
    session = "3" * 64
    value: dict[str, Any] = {
        "schema_version": (
            "deeplaw.native-host-event/v3" if current else "deeplaw.native-host-event/v2"
        ),
        "provenance_level": "native_plugin_hook",
        "host": host,
        "host_identity": _host_identity(host, current=current),
        "event_type": event_type,
        "event_sequence": {"index": index},
        "session_sha256": session,
        "parent_session_sha256": None,
        "observation": {"methods_observed": [event_type], "status": "received"},
        "route": {
            "status": "exact",
            "binding_sha256": "4" * 64,
            "task_handle_sha256": "5" * 64,
            "project_sha256": "6" * 64,
            "repository_sha256": "7" * 64,
            "worktree_sha256": "8" * 64,
        },
    }
    if current:
        value["execution_identity"] = {
            "selector_source_symlink": host == "opencode",
            "execution_target_regular": True,
            "execution_target_single_link": True,
        }
    if host == "opencode" and event_type == "fork":
        value["parent_session_sha256"] = session
    return value


def _events(host: str, *, current: bool = False) -> list[dict[str, Any]]:
    types = (
        ("SessionStart", "UserPromptSubmit", "PreCompact", "PostCompact", "SessionEnd")
        if host == "codex"
        else ("session", "chat.message", "fork", "compaction")
    )
    return [
        _event(host, event_type, index, current=current)
        for index, event_type in enumerate(types)
    ]


def _expected(
    root: Path,
    *,
    host: str,
    task: str,
    run_id: str,
    workflow: int,
) -> dict[str, Any]:
    rows = []
    if task == "continuity":
        cases = ("cold_start", "resume_fork", "compaction_forget")
    else:
        cases = TASK_DUTIES[task]
    for case_id in cases:
        rows.append(
            {
                "case_id": case_id,
                "required_duties": list(TASK_DUTIES[task]),
                "required_wrong_states": ["stale", "wrong_task_line", "wrong_worktree"]
                if task == "continuity"
                else ["wrong_state"],
                "required_operations": list(TASK_OPERATIONS[task]),
            }
        )
    expectations = []
    for duty in TASK_DUTIES[task]:
        expectations.append(
            {
                "duty": duty,
                "allowed_statuses": ["gap"]
                if task == "professional_evidence" and duty == "ocr_critical_token_gap"
                else ["observed"],
                "required_gap_code": "ocr_critical_token_gap"
                if task == "professional_evidence" and duty == "ocr_critical_token_gap"
                else None,
            }
        )
    return {
        "artifact_kind": "expected_task",
        "schema_version": "deeplaw.v013-host-task-evidence/v1",
        "run_id": run_id,
        "workflow_run_id": workflow,
        "task_case": task,
        "host": host,
        "required_duties": list(TASK_DUTIES[task]),
        "duty_expectations": expectations,
        "rows": rows,
        "hard_failure_ids": list(HARD_FAILURE_IDS),
    }


def _task_result(
    *, host: str, task: str, run_id: str, workflow: int, provider_bytes: int = 100
) -> dict[str, Any]:
    selected = [{"kind": "task_binding", "identity_sha256": "9" * 64}]
    duties = []
    for duty in TASK_DUTIES[task]:
        duties.append(
            {
                "duty": duty,
                "status": "gap"
                if task == "professional_evidence" and duty == "ocr_critical_token_gap"
                else "observed",
                "gap_code": "ocr_critical_token_gap"
                if task == "professional_evidence" and duty == "ocr_critical_token_gap"
                else None,
            }
        )
    if task == "continuity":
        seams = ["knowledge_support", "native_capsule"]
        steps = [
            {"step": step, "observed": True, "gap_code": None}
            for step in (
                "new",
                "resume",
                "fork",
                "compaction",
                "stale",
                "wrong_task_line",
                "forget",
                "resume_after_forget",
            )
        ]
        authorized = {
            "observed": True,
            "operation": "owner_forget",
            "owner_authorized": True,
            "receipt_sha256": "a" * 64,
        }
        write_performed = True
        ledger_after = "b" * 64
    elif task == "living_wiki":
        seams = ["wiki_read", "source_read", "query_context"]
        steps = []
        authorized = {
            "observed": False,
            "operation": None,
            "owner_authorized": False,
            "receipt_sha256": None,
        }
        write_performed = False
        ledger_after = "a" * 64
    else:
        seams = ["source_read", "fragment_read", "wiki_read", "query_context"]
        steps = []
        authorized = {
            "observed": False,
            "operation": None,
            "owner_authorized": False,
            "receipt_sha256": None,
        }
        write_performed = False
        ledger_after = "a" * 64
    return {
        "artifact_kind": "task_result",
        "schema_version": "deeplaw.v013-host-task-evidence/v1",
        "run_id": run_id,
        "workflow_run_id": workflow,
        "task_case": task,
        "host": host,
        "first_correct_action": {
            "observed": True,
            "event_index": 1,
            "seam": seams[0],
        },
        "decision_preservation": {"observed": True, "identity_sha256": _sha(_canonical(selected))},
        "wrong_state_admission": [
            {"state": state, "admitted": False} for state in TASK_WRONG_STATES[task]
        ],
        "duties": duties,
        "provider": {
            "capsule_sha256": "c" * 64,
            "provider_bytes": provider_bytes,
            "input_tokens": 1,
            "output_tokens": 2,
            "cache_tokens": 3,
            "reasoning_tokens": 4,
        },
        "selected_identities": selected,
        "duplicate_distractor": [
            {"state": "duplicate", "admitted": False},
            {"state": "distractor", "admitted": False},
        ],
        "no_hidden_mutation": {
            "hidden_mutation": False,
            "write_performed": write_performed,
            "ledger_before_sha256": "a" * 64,
            "ledger_after_sha256": ledger_after,
            "authorized_mutation": authorized,
            "process_receipt_observed": True,
        },
        "query_trace": {"in_capsule": False, "sha256": "d" * 64, "entry_count": 2},
        "ledger": {"in_capsule": False, "sha256": "e" * 64, "entry_count": 2},
        "lifecycle_steps": steps,
        "observed_public_seams": seams,
        "claim_eligible": False,
    }


def _manifest(
    tmp_path: Path,
    *,
    host: str,
    task: str,
    provider_bytes: int = 100,
    current: bool = False,
) -> Path:
    run_id = f"v013:{host}:{task}"
    workflow = 13
    events = _events(host, current=current)
    receipts = [derive_native_host_receipt(event) for event in events]
    expected = _expected(tmp_path, host=host, task=task, run_id=run_id, workflow=workflow)
    expected_ref = _source(tmp_path, f"{host}/{task}/expected.json", expected)
    model = "gpt-5.6-luna" if host == "codex" else "deepseek-v4-flash"
    event_meta = {
        "artifact_kind": "event_sequence",
        "schema_version": "deeplaw.v013-host-task-evidence/v1",
        "run_id": run_id,
        "workflow_run_id": workflow,
        "task_case": task,
        "host": host,
        "actual_response_model_id": model,
    }
    event_ref = _source(tmp_path, f"{host}/{task}/events.json", {**event_meta, "events": events})
    lifecycle_ref = _source(
        tmp_path,
        f"{host}/{task}/lifecycle.json",
        {
            **{key: event_meta[key] for key in event_meta},
            "artifact_kind": "lifecycle_sequence",
            "receipts": receipts,
        },
    )
    identity_hash = _sha(
        _canonical(
            _host_identity_projection(_host_identity(host, current=current), host=host)
        )
    )
    usage = {
        "artifact_kind": "usage_receipt",
        "schema_version": "deeplaw.v013-host-task-evidence/v1",
        "run_id": run_id,
        "workflow_run_id": workflow,
        "task_case": task,
        "host": host,
        "actual_response_model_id": model,
        "rows": [
            {
                "run_id": run_id,
                "workflow_run_id": workflow,
                "task_case": task,
                "host": host,
                "actual_response_model_id": model,
                "host_identity_sha256": identity_hash,
                "candidate_commit": COMMIT,
                "candidate_tree": TREE,
                "corpus_sha256": expected_ref["sha256"],
                "runner_identity": RUNNER["identity"],
                "runner_sha256": RUNNER["sha256"],
                "input_tokens": 1,
                "output_tokens": 2,
                "cache_tokens": 3,
                "reasoning_tokens": 4,
                "provider_bytes": provider_bytes,
                "provider_sha256": "f" * 64,
                "latency_ms": 1.0,
                "rss_peak_bytes": 1,
            }
        ],
    }
    usage_ref = _source(tmp_path, f"{host}/{task}/usage.json", usage)
    result = _task_result(
        host=host, task=task, run_id=run_id, workflow=workflow, provider_bytes=provider_bytes
    )
    result_ref = _source(tmp_path, f"{host}/{task}/result.json", result)
    authorized = result["no_hidden_mutation"]["authorized_mutation"]
    isolation = {
        "artifact_kind": "isolation_receipt",
        "schema_version": "deeplaw.v013-host-task-evidence/v1",
        "candidate_binding": _candidate(),
        "run_binding": {"run_id": run_id, "workflow_run_id": workflow},
        "corpus": {"sha256": expected_ref["sha256"], "role": "host_qualification"},
        "runner": RUNNER,
        "scorer": SCORER,
        "host": host,
        "task_case": task,
        "secret_boundary": {
            "parent_secret_present": True,
            "child_secret_present": False,
            "auth_read": False,
            "transcript_read": False,
            "prompt_read": False,
            "reasoning_read": False,
            "secret_read": False,
        },
        "process_boundary": {
            "native_receipt_observed": True,
            "host_process_separated": True,
            "mcp_process_separated": True,
        },
        "write_observation": {
            "hidden_mutation": False,
            "write_performed": result["no_hidden_mutation"]["write_performed"],
            "authorized_mutation": authorized,
            "audit_head_before_sha256": "a" * 64,
            "audit_head_after_sha256": "b" * 64 if authorized["observed"] else "a" * 64,
        },
        "claim_eligible": False,
    }
    isolation_ref = _source(tmp_path, f"{host}/{task}/isolation.json", isolation)
    envelope: dict[str, Any] = {
        "schema_version": "deeplaw.typed-qualification-evidence/v3",
        "profile": "kernel_release_core",
        "reference_provenance": "deterministic_expected_evidence",
        "human_authenticity": "not_claimed",
        "kind": "host_event_sequence",
        "candidate_binding": _candidate(),
        "run_binding": {"run_id": run_id, "workflow_run_id": workflow},
        "corpus": {"sha256": expected_ref["sha256"], "role": "host_qualification"},
        "runner": RUNNER,
        "scorer": SCORER,
        "payload": {
            "event_source": event_ref,
            "lifecycle_source": lifecycle_ref,
            "usage_source": usage_ref,
            "expected_source": expected_ref,
            "continuity_source": result_ref,
            "isolation_source": isolation_ref,
        },
        "record_sha256": "",
    }
    envelope["record_sha256"] = _sha(
        _canonical({key: value for key, value in envelope.items() if key != "record_sha256"})
    )
    manifest = tmp_path / f"{host}-{task}.json"
    manifest.write_bytes(_canonical(envelope))
    return manifest


@pytest.mark.parametrize("host", ["codex", "opencode"])
@pytest.mark.parametrize("task", ["continuity", "living_wiki", "professional_evidence"])
def test_v013_host_task_manifest_derives_host_task_metrics(
    tmp_path: Path, host: str, task: str
) -> None:
    manifest = _manifest(tmp_path, host=host, task=task)
    result = parse_typed_evidence(
        manifest, root=tmp_path, expected_corpus_sha256=_expected_sha(tmp_path, host, task)
    )
    assert result["kind"] == "host_event_sequence"
    assert result["status"] == "passed"
    assert result["metrics"]["host"] == host
    assert result["metrics"]["task_case"] == task
    assert result["metrics"]["query_trace_in_capsule"] is False
    assert result["metrics"]["ledger_in_capsule"] is False
    assert result["hard_failure_counts"]["required_duty_gap"] == 0


@pytest.mark.parametrize(
    ("source_key", "source_json", "message"),
    [
        (
            "lifecycle_source",
            b'{"nested":{"duplicate":1,"duplicate":2}}',
            "duplicate JSON keys",
        ),
        (
            "usage_source",
            b'{"nested":{"value":NaN}}',
            "non-finite JSON value",
        ),
        (
            "expected_source",
            b'{"nested":{"value":Infinity}}',
            "non-finite JSON value",
        ),
        (
            "continuity_source",
            b'{"nested":{"duplicate":1,"duplicate":2}}',
            "duplicate JSON keys",
        ),
        (
            "isolation_source",
            b'{"nested":{"value":-Infinity}}',
            "non-finite JSON value",
        ),
    ],
)
def test_v013_host_task_nested_sources_reject_non_strict_json(
    tmp_path: Path,
    source_key: str,
    source_json: bytes,
    message: str,
) -> None:
    manifest = _manifest(tmp_path, host="codex", task="continuity")
    envelope = json.loads(manifest.read_text())
    source_path = tmp_path / envelope["payload"][source_key]["relative_path"]
    source_path.write_bytes(source_json)
    _refresh_source_ref(manifest, source_key, source_path)
    envelope = json.loads(manifest.read_text())
    with pytest.raises(HostTaskEvidenceError, match=message):
        parse_host_task_evidence(
            envelope,
            root=tmp_path,
            record_sha256=envelope["record_sha256"],
            expected_corpus_sha256=_expected_sha(tmp_path, "codex", "continuity"),
        )


def test_v013_host_task_manifest_accepts_current_native_v3_codex_identity(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path, host="codex", task="continuity", current=True)
    result = parse_typed_evidence(
        manifest,
        root=tmp_path,
        expected_corpus_sha256=_expected_sha(tmp_path, "codex", "continuity"),
    )
    assert result["status"] == "passed"
    assert result["metrics"]["host_identity_sha256"] == _sha(
        _canonical(_host_identity("codex", current=True))
    )


def test_v013_host_task_schema_and_frozen_catalog_are_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "contracts/v013-host-task-evidence.v1.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(schema)
    catalog = load_task_cases()
    assert [row["task_case"] for row in catalog["task_cases"]] == list(TASK_DUTIES)
    assert task_case("codex", "continuity")["status"] == "not_executed"

    identity_input = {
        "schema_version": "deeplaw.host-exact-identity/v1",
        "hosts": {
            host: _host_identity(host, current=True)
            for host in ("codex", "opencode")
        },
    }
    identity_path = tmp_path / "host-exact-identity.json"
    identity_path.write_bytes(_canonical(identity_input))
    identity_path.chmod(0o600)
    builder = getattr(host_task_runner, "build_external_collector_handoff", None)
    assert callable(builder)
    handoff = builder(host_identity_input=identity_path)
    assert handoff["status"] == "not_executed"
    assert handoff["executed"] is False
    assert handoff["claim_eligible"] is False
    assert handoff["release_ready"] is False
    assert handoff["task_catalog_descriptor"]["role"] == "host_task_catalog"
    assert handoff["task_catalog_descriptor"]["sha256"]
    assert [
        (slot["host"], slot["task_case"])
        for slot in handoff["slots"]
    ] == [
        (host, task)
        for host in ("codex", "opencode")
        for task in ("continuity", "living_wiki", "professional_evidence")
    ]
    for slot in handoff["slots"]:
        assert slot["status"] == "not_executed"
        assert slot["executed"] is False
        assert slot["typed_source_slots"] == [
            "event_source",
            "lifecycle_source",
            "usage_source",
            "expected_source",
            "continuity_source",
            "isolation_source",
        ]
        assert slot["control_receipt_slots"] == [
            "host_preflight_receipt",
            "host_process_receipt",
        ]
        assert slot["seed_descriptor"]["sha256"]
        assert slot["driver_descriptor"]["sha256"]
        assert slot["seed_descriptor"]["role"] == "task_domain_seed"
        assert slot["driver_descriptor"]["role"] in {
            "codex_app_server_native_hook",
            "opencode_exact_project_plugin_native_hook",
        }
    handoff_schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "contracts/v013-host-task-handoff.v1.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(handoff_schema)
    Draft202012Validator(handoff_schema).validate(handoff)
    validator = getattr(host_task_runner, "validate_external_collector_handoff", None)
    assert callable(validator)
    assert validator(handoff, host_identity_input=identity_path) == handoff
    tampered = json.loads(json.dumps(handoff))
    tampered["slots"][0]["status"] = "passed"
    with pytest.raises(HostTaskQualificationError):
        validator(tampered, host_identity_input=identity_path)
    duplicate = tmp_path / "duplicate-handoff.json"
    raw_handoff = _canonical(handoff)
    duplicate.write_bytes(raw_handoff[:-1] + b',"status":"not_executed"}')
    with pytest.raises(HostTaskQualificationError):
        validator(duplicate, host_identity_input=identity_path)
    tampered = json.loads(json.dumps(handoff))
    tampered["slots"][0]["seed_descriptor"]["sha256"] = "0" * 64
    tampered["record_sha256"] = _sha(
        _canonical({key: value for key, value in tampered.items() if key != "record_sha256"})
    )
    with pytest.raises(HostTaskQualificationError):
        validator(tampered, host_identity_input=identity_path)
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/kernel-qualification-evidence.yml"
    ).read_text(encoding="utf-8")
    preflight = workflow.index("Run Codex owner-external zero-model preflight")
    collector = workflow.index(
        "Execute Codex x3, OpenCode x3, and deterministic Kernel evidence"
    )
    assert preflight < collector
    assert "--codex-zero-model-preflight" in workflow
    assert "--candidate-binding-input" in workflow
    assert "--expected-codex-broker-sha256" in workflow
    assert 'codex_preflight="${RUNNER_TEMP}/codex-zero-model-preflight-v4.json"' in workflow
    assert '"stdin/close"' in workflow
    assert '"deeplaw.codex-owner-external-broker-control/v4"' in workflow
    assert 'provider_guard.get("provider_id")' in workflow
    assert '"turn_start_count": 1' in workflow
    assert '"provider_request_count": 0' in workflow
    assert '"model_invocation_count": 0' in workflow
    assert '"sampling_count": 0' in workflow
    assert '"accepted_connection_count": 0' in workflow
    assert '"request_count": 0' in workflow
    assert 'hook.get("status") != "stopped"' in workflow
    assert 'hook.get("stop_boundary") != "before_run_sampling_request"' in workflow
    assert 'type(result.get(field)) is not int' in workflow
    assert 'hook_response.get("continue") is not False' in workflow
    assert 'result.get("formal_admission") is not False' in workflow
    repository = tmp_path / "repository"
    repository.mkdir()
    lock_raw = b"exact construction lock\n"
    (repository / "uv.lock").write_bytes(lock_raw)
    external_root = tmp_path / "candidate-external"
    external_root.mkdir()
    wheel_raw = b"exact wheel bytes"
    sdist_raw = b"exact sdist bytes"
    lock_sha256 = _sha(lock_raw)
    wheel_sha256 = _sha(wheel_raw)
    sdist_sha256 = _sha(sdist_raw)
    active = {
        "schema_version": "deeplaw.v013-active-qualification/v3",
        "status": "frozen_exact_candidate_machine_evaluation_pending",
        "candidate_version": "0.13.0",
        "construction_package_version": "0.12.0",
        "release_target": "0.13.0",
        "candidate_binding": {
            "source_commit": COMMIT,
            "source_tree": TREE,
            "lock_sha256": lock_sha256,
            "wheel_sha256": wheel_sha256,
            "sdist_sha256": sdist_sha256,
            "package_version": "0.13.0",
            "wheel_filename": "deeplaw-0.13.0-py3-none-any.whl",
            "sdist_filename": "deeplaw-0.13.0.tar.gz",
        },
    }
    selected = external_root / "frozen-active-qualification.json"
    selected.write_bytes(_canonical(active))
    (external_root / active["candidate_binding"]["wheel_filename"]).write_bytes(
        wheel_raw
    )
    (external_root / active["candidate_binding"]["sdist_filename"]).write_bytes(
        sdist_raw
    )
    assert repository not in selected.resolve(strict=True).parents

    monkeypatch.setattr(
        host_task_runner,
        "repository_binding",
        lambda _repository: {"commit": COMMIT, "tree": TREE},
    )
    assert host_task_runner.load_exact_candidate_binding(
        selected,
        repository=repository,
    ) == {
        "commit": COMMIT,
        "tree": TREE,
        "lock_sha256": lock_sha256,
        "wheel_sha256": wheel_sha256,
        "sdist_sha256": sdist_sha256,
    }

    active["candidate_binding"]["package_version"] = "0.12.0"
    selected.write_bytes(_canonical(active))
    with pytest.raises(HostTaskQualificationError, match="package version differs"):
        host_task_runner.load_exact_candidate_binding(
            selected,
            repository=repository,
        )
    active["candidate_binding"]["package_version"] = "0.13.0"
    selected.write_bytes(_canonical(active))

    other_wheel = external_root / "other.whl"
    other_wheel.write_bytes(b"other")
    with pytest.raises(HostTaskQualificationError, match="wheel path differs"):
        host_task_runner.load_exact_candidate_binding(
            selected,
            candidate_wheel=other_wheel,
            repository=repository,
        )

    active["candidate_binding"]["wheel_sha256"] = "f" * 64
    selected.write_bytes(_canonical(active))
    with pytest.raises(HostTaskQualificationError, match="exact-byte binding differs"):
        host_task_runner.load_exact_candidate_binding(
            selected,
            repository=repository,
        )
    active["candidate_binding"]["wheel_sha256"] = wheel_sha256
    selected.write_bytes(_canonical(active))

    hard_link = external_root / "frozen-active-hard-link.json"
    os.link(selected, hard_link)
    try:
        with pytest.raises(HostTaskQualificationError, match="single-link"):
            host_task_runner.load_exact_candidate_binding(
                selected,
                repository=repository,
            )
    finally:
        hard_link.unlink()

    wheel_path = external_root / active["candidate_binding"]["wheel_filename"]
    wheel_hard_link = external_root / "candidate-wheel-hard-link.whl"
    os.link(wheel_path, wheel_hard_link)
    try:
        with pytest.raises(HostTaskQualificationError, match=r"wheel.*single-link"):
            host_task_runner.load_exact_candidate_binding(
                selected,
                repository=repository,
            )
    finally:
        wheel_hard_link.unlink()

    sdist_path = external_root / active["candidate_binding"]["sdist_filename"]
    sdist_target = external_root / "candidate-sdist-target.tar.gz"
    os.replace(sdist_path, sdist_target)
    try:
        sdist_path.symlink_to(sdist_target.name)
        with pytest.raises(
            HostTaskQualificationError,
            match=r"sdist.*regular non-symlink",
        ):
            host_task_runner.load_exact_candidate_binding(
                selected,
                repository=repository,
            )
    finally:
        if sdist_path.is_symlink():
            sdist_path.unlink()
        os.replace(sdist_target, sdist_path)

    linked_root = tmp_path / "candidate-linked-parent"
    try:
        linked_root.symlink_to(external_root, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    with pytest.raises(HostTaskQualificationError, match="symlink"):
        host_task_runner.load_exact_candidate_binding(
            linked_root / selected.name,
            repository=repository,
        )

    host_root = tmp_path / "host-external"
    host_root.mkdir()
    binary = host_root / "codex"
    binary.write_bytes(b"codex fixture")
    binary.chmod(0o700)
    identity = {"hosts": {"codex": _host_identity("codex", current=True)}}
    expected_sha256 = _sha(binary.read_bytes())
    identity["hosts"]["codex"]["binary_sha256"] = expected_sha256
    assert host_task_runner._validate_codex_binary_static(
        binary,
        identity=identity,
        repository=repository,
    ) == {
        "version": identity["hosts"]["codex"]["binary_version"],
        "sha256": expected_sha256,
    }
    assert host_task_runner._windows_acl_hardening_verified(
        {
            "platform": "nt",
            "applied": True,
            "verification": {"permissions_verified": True},
        }
    )
    for invalid_acl_report in (
        None,
        {},
        {"platform": "posix", "applied": True, "verification": {"permissions_verified": True}},
        {"platform": "nt", "applied": False, "verification": {"permissions_verified": True}},
        {"platform": "nt", "applied": True, "verification": {"permissions_verified": False}},
    ):
        assert not host_task_runner._windows_acl_hardening_verified(invalid_acl_report)

    original_lstat = Path.lstat
    original_fstat = os.fstat
    path_stat = original_lstat(binary)
    path_snapshot = type(
        "PathStat",
        (),
        {
            "st_dev": path_stat.st_dev,
            "st_ino": path_stat.st_ino,
            "st_size": path_stat.st_size,
            "st_mode": path_stat.st_mode,
            "st_uid": path_stat.st_uid,
            "st_nlink": path_stat.st_nlink,
            "st_mtime_ns": 100,
            "st_ctime_ns": 200,
        },
    )()
    fd_snapshot = type(
        "FdStat",
        (),
        {
            "st_dev": path_stat.st_dev,
            "st_ino": path_stat.st_ino,
            "st_size": path_stat.st_size,
            "st_mode": path_stat.st_mode ^ 0o040,
            "st_uid": path_stat.st_uid + 1000,
            "st_nlink": path_stat.st_nlink,
            "st_mtime_ns": 300,
            "st_ctime_ns": 400,
        },
    )()

    def windows_lstat(path: Path) -> os.stat_result:
        if Path(path) == binary:
            return path_snapshot  # type: ignore[return-value]
        return original_lstat(path)

    def windows_fstat(descriptor: int) -> os.stat_result:
        if descriptor >= 0:
            return fd_snapshot  # type: ignore[return-value]
        return original_fstat(descriptor)

    with monkeypatch.context() as windows:
        windows.setattr(Path, "lstat", windows_lstat)
        windows.setattr(os, "fstat", windows_fstat)
        digest, retained = host_task_runner._read_stable_regular_file(
            binary,
            repository=repository,
            label="candidate sdist",
            require_external=True,
            retain_bytes=True,
        )
    assert digest == expected_sha256
    assert retained == b"codex fixture"

    linked_host_root = tmp_path / "host-linked-parent"
    linked_host_root.symlink_to(host_root, target_is_directory=True)
    with pytest.raises(HostTaskQualificationError, match=r"parent.*symlink"):
        host_task_runner._validate_codex_binary_static(
            linked_host_root / binary.name,
            identity=identity,
            repository=repository,
        )

    stable_signature = host_task_runner._stable_stat_signature
    signature_calls = 0

    def drifting_signature(details: os.stat_result) -> tuple[Any, ...]:
        nonlocal signature_calls
        signature_calls += 1
        signature = stable_signature(details)
        return (*signature, "drift") if signature_calls == 3 else signature

    monkeypatch.setattr(
        host_task_runner,
        "_stable_stat_signature",
        drifting_signature,
    )
    with pytest.raises(HostTaskQualificationError, match="changed while it was read"):
        host_task_runner._validate_codex_binary_static(
            binary,
            identity=identity,
            repository=repository,
        )
    monkeypatch.setattr(
        host_task_runner,
        "_stable_stat_signature",
        stable_signature,
    )

    broker = host_root / "owner-broker"
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
        assert system_root
        original_native = Path(system_root) / "System32" / "whoami.exe"
        replacement_native = Path(system_root) / "System32" / "hostname.exe"
        assert original_native.is_file()
        assert replacement_native.is_file()
        original_broker_raw = original_native.read_bytes()
        replacement_broker_raw = replacement_native.read_bytes()
        expected_completed = subprocess.run(
            [str(original_native)],
            capture_output=True,
            check=False,
            timeout=5,
            env=host_preflight._host_version_probe_environment(),
        )
        assert expected_completed.returncode == 0
        assert expected_completed.stdout or expected_completed.stderr
    else:
        original_broker_raw = b"#!/bin/sh\nprintf 'verified-original\\n'\n"
        replacement_broker_raw = b"#!/bin/sh\nprintf 'replaced-path\\n'\n"
        expected_completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"verified-original\n",
            stderr=b"",
        )
    broker.write_bytes(original_broker_raw)
    broker.chmod(0o700)
    replacement = host_root / "owner-broker-replacement"
    replacement.write_bytes(replacement_broker_raw)
    replacement.chmod(0o700)
    with host_task_runner._stage_exact_broker_executable(
        broker,
        repository=repository,
        host_binary=binary,
        expected_sha256=_sha(original_broker_raw),
    ) as staged_broker:
        assert staged_broker.suffix == (".exe" if os.name == "nt" else "")
        os.replace(replacement, broker)
        completed = subprocess.run(
            [str(staged_broker)],
            capture_output=True,
            check=False,
            timeout=5,
            env=host_preflight._host_version_probe_environment(),
        )
        assert completed.returncode == expected_completed.returncode
        assert completed.stdout == expected_completed.stdout
        assert completed.stderr == expected_completed.stderr
        assert completed.returncode == 0
        assert completed.stdout or completed.stderr
        if os.name != "nt":
            assert b"replaced-path" not in completed.stdout

    _assert_codex_zero_model_runner_serializes_stop_before_sampling(
        monkeypatch,
        tmp_path,
    )


def test_v013_construction_kit_manifest_is_closed_and_exactly_bound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    lock_sha256 = _sha((repository / "uv.lock").read_bytes())
    protocol_sha256 = _sha(
        (repository / "benchmarks/v013/qualification-protocol-v3.json").read_bytes()
    )
    active_schema_sha256 = _sha(
        (repository / "contracts/v013-active-qualification.v3.schema.json").read_bytes()
    )
    monkeypatch.setattr(
        host_task_runner,
        "repository_binding",
        lambda _repository: {
            "commit": COMMIT,
            "tree": TREE,
            "worktree_clean": True,
            "package_version": "0.12.0",
        },
    )
    external_root = tmp_path / "construction-kit"
    external_root.mkdir()
    wheel_name = "deeplaw-0.12.0-py3-none-any.whl"
    sdist_name = "deeplaw-0.12.0.tar.gz"
    wheel_raw = b"construction wheel bytes"
    sdist_raw = b"construction sdist bytes"
    (external_root / wheel_name).write_bytes(wheel_raw)
    (external_root / sdist_name).write_bytes(sdist_raw)
    manifest = {
        "schema_version": "deeplaw.v013-external-kit-manifest/v2",
        "evidence_class": "control_manifest_only",
        "status": "construction_zero_model_preflight_ready",
        "formal_admission": False,
        "construction": {
            "commit": COMMIT,
            "tree": TREE,
            "package_version": "0.12.0",
            "release_target": "0.13.0",
            "uv_lock_sha256": lock_sha256,
        },
        "artifacts": {
            "wheel": {
                "filename": wheel_name,
                "sha256": _sha(wheel_raw),
            },
            "sdist": {
                "filename": sdist_name,
                "sha256": _sha(sdist_raw),
            },
        },
        "protocol_binding": {
            "qualification_protocol_sha256": protocol_sha256,
            "active_qualification_schema_sha256": active_schema_sha256,
            "codex_control_schema_version": (
                "deeplaw.codex-owner-external-broker-control/v4"
            ),
        },
        "qualification_state": {
            "formal_n6": "not_executed",
            "human_gold": "not_executed",
            "release_ready": False,
        },
        "manifest_sha256_scope": (
            "utf8_json_sort_keys_compact_without_manifest_sha256_no_trailing_newline"
        ),
    }
    manifest["manifest_sha256"] = _sha(
        _canonical(manifest)
    )
    selected = external_root / "construction-kit-manifest.json"

    def write(value: dict[str, Any]) -> None:
        selected.write_bytes(_canonical(value))

    write(manifest)
    schema = json.loads(
        (repository / "contracts/v013-external-kit-manifest.v2.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest)
    expected = {
        "commit": COMMIT,
        "tree": TREE,
        "lock_sha256": lock_sha256,
        "wheel_sha256": _sha(wheel_raw),
        "sdist_sha256": _sha(sdist_raw),
    }
    assert host_task_runner.load_construction_candidate_binding(
        selected,
        repository=repository,
    ) == expected
    assert host_task_runner.load_zero_model_candidate_binding(
        selected,
        candidate_wheel=external_root / wheel_name,
        repository=repository,
    ) == expected
    with pytest.raises(HostTaskQualificationError, match="not frozen active v3"):
        host_task_runner.load_exact_candidate_binding(
            selected,
            repository=repository,
        )

    def clone() -> dict[str, Any]:
        return json.loads(json.dumps(manifest))

    def assert_rejected(candidate: dict[str, Any], message: str) -> None:
        candidate["manifest_sha256"] = _sha(
            _canonical(
                {
                    key: item
                    for key, item in candidate.items()
                    if key != "manifest_sha256"
                }
            )
        )
        write(candidate)
        with pytest.raises(HostTaskQualificationError, match=message):
            host_task_runner.load_zero_model_candidate_binding(
                selected,
                repository=repository,
            )

    candidate = clone()
    candidate["manifest_sha256"] = "f" * 64
    write(candidate)
    with pytest.raises(HostTaskQualificationError, match="self-hash differs"):
        host_task_runner.load_zero_model_candidate_binding(
            selected,
            repository=repository,
        )

    candidate = clone()
    candidate["status"] = "construction_zero_model_preflight_blocked"
    assert_rejected(candidate, "status is invalid")

    candidate = clone()
    candidate["formal_admission"] = True
    assert_rejected(candidate, "status is invalid")

    candidate = clone()
    candidate["protocol_binding"]["qualification_protocol_sha256"] = "f" * 64
    assert_rejected(candidate, "current binding differs")

    candidate = clone()
    candidate["artifacts"]["wheel"]["sha256"] = "f" * 64
    assert_rejected(candidate, "artifact bytes differ")

    candidate = clone()
    candidate["artifacts"]["wheel"]["filename"] = "deeplaw-0.13.0-py3-none-any.whl"
    assert_rejected(candidate, "filename version differs")

    candidate = clone()
    candidate["construction"]["package_version"] = "0.13.0"
    assert_rejected(candidate, "package or release target differs")

    candidate = clone()
    candidate["qualification_state"]["formal_n6"] = "passed"
    assert_rejected(candidate, "qualification state is invalid")

    candidate = clone()
    candidate["extra"] = True
    assert_rejected(candidate, "not closed v2")

    candidate = clone()
    candidate["artifacts"]["wheel"]["extra"] = True
    assert_rejected(candidate, "artifact binding is not closed")

    candidate = clone()
    candidate["manifest_sha256_scope"] = "with_newline"
    assert_rejected(candidate, "status is invalid")


def test_v013_host_task_catalog_rejects_binary_drift(tmp_path: Path) -> None:
    catalog = load_task_cases()
    catalog["host_constraints"]["opencode"]["binary_sha256"] = "moving"
    selected = tmp_path / "cases.json"
    selected.write_bytes(_canonical(catalog))
    with pytest.raises(HostTaskQualificationError, match="strict schema validation"):
        load_task_cases(selected)


def test_v013_host_task_missing_public_seam_derives_failure(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, host="codex", task="professional_evidence")
    result_path = tmp_path / "codex/professional_evidence/result.json"
    result = json.loads(result_path.read_text())
    result["observed_public_seams"].remove("fragment_read")
    result_path.write_bytes(_canonical(result))
    _refresh_source_ref(manifest, "continuity_source", result_path)
    result = parse_typed_evidence(
        manifest,
        root=tmp_path,
        expected_corpus_sha256=_expected_sha(tmp_path, "codex", "professional_evidence"),
    )
    assert result["status"] == "failed"
    assert result["hard_failure_counts"]["missing_required_operation"] > 0


def test_v013_host_task_ocr_gap_is_an_accepted_expected_disposition(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, host="codex", task="professional_evidence")
    result = parse_typed_evidence(
        manifest,
        root=tmp_path,
        expected_corpus_sha256=_expected_sha(tmp_path, "codex", "professional_evidence"),
    )
    assert result["metrics"]["required_duty_rate"] == 1.0


@pytest.mark.parametrize("field", ["secret", "prompt", "transcript", "query_trace", "ledger"])
def test_v013_host_task_sensitive_projection_fails_closed(tmp_path: Path, field: str) -> None:
    manifest = _manifest(tmp_path, host="codex", task="living_wiki")
    result_path = tmp_path / "codex/living_wiki/result.json"
    result = json.loads(result_path.read_text())
    if field in {"query_trace", "ledger"}:
        result[field]["raw"] = "forbidden"
    else:
        result[field] = "forbidden"
    result_path.write_bytes(_canonical(result))
    _refresh_source_ref(manifest, "continuity_source", result_path)
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            manifest,
            root=tmp_path,
            expected_corpus_sha256=_expected_sha(tmp_path, "codex", "living_wiki"),
        )


def test_v013_host_task_provider_overflow_and_missing_usage_fail_closed(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, host="codex", task="continuity", provider_bytes=65_537)
    result = parse_typed_evidence(
        manifest,
        root=tmp_path,
        expected_corpus_sha256=_expected_sha(tmp_path, "codex", "continuity"),
    )
    assert result["status"] == "failed"
    assert result["hard_failure_counts"]["provider_bytes_overflow"] > 0
    missing = _manifest(tmp_path / "missing", host="codex", task="continuity")
    source = json.loads(missing.read_text())
    source["payload"]["usage_source"]["relative_path"] = "does-not-exist.json"
    source["payload"]["usage_source"]["byte_size"] = 1
    source["payload"]["usage_source"]["sha256"] = "0" * 64
    source["record_sha256"] = _sha(
        _canonical({key: value for key, value in source.items() if key != "record_sha256"})
    )
    missing.write_bytes(_canonical(source))
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            missing,
            root=missing.parent,
            expected_corpus_sha256=_expected_sha(missing.parent, "codex", "continuity"),
        )


def test_v013_host_task_usage_metadata_and_host_pin_fail_closed(tmp_path: Path) -> None:
    usage_manifest = _manifest(tmp_path / "usage", host="codex", task="living_wiki")
    usage_path = usage_manifest.parent / "codex/living_wiki/usage.json"
    usage = json.loads(usage_path.read_text())
    usage["actual_response_model_id"] = "deepseek-v4-flash"
    usage_path.write_bytes(_canonical(usage))
    _refresh_source_ref(usage_manifest, "usage_source", usage_path)
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            usage_manifest,
            root=usage_manifest.parent,
            expected_corpus_sha256=_expected_sha(
                usage_manifest.parent, "codex", "living_wiki"
            ),
        )

    pin_manifest = _manifest(tmp_path / "pin", host="opencode", task="living_wiki")
    event_path = pin_manifest.parent / "opencode/living_wiki/events.json"
    events = json.loads(event_path.read_text())
    events["events"][0]["host_identity"]["executable_sha256"] = "9" * 64
    event_path.write_bytes(_canonical(events))
    _refresh_source_ref(pin_manifest, "event_source", event_path)
    result = parse_typed_evidence(
        pin_manifest,
        root=pin_manifest.parent,
        expected_corpus_sha256=_expected_sha(pin_manifest.parent, "opencode", "living_wiki"),
    )
    assert result["status"] == "failed"
    assert result["hard_failure_counts"]["native_host_pin_mismatch"] > 0


def test_v013_host_task_authorized_forget_is_not_hidden_mutation(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, host="codex", task="continuity")
    result = parse_typed_evidence(
        manifest,
        root=tmp_path,
        expected_corpus_sha256=_expected_sha(tmp_path, "codex", "continuity"),
    )
    assert result["metrics"]["no_hidden_mutation"] is True
    result_path = tmp_path / "codex/continuity/result.json"
    value = json.loads(result_path.read_text())
    value["no_hidden_mutation"]["write_performed"] = True
    value["no_hidden_mutation"]["authorized_mutation"]["observed"] = False
    value["no_hidden_mutation"]["authorized_mutation"]["operation"] = None
    value["no_hidden_mutation"]["authorized_mutation"]["owner_authorized"] = False
    value["no_hidden_mutation"]["authorized_mutation"]["receipt_sha256"] = None
    result_path.write_bytes(_canonical(value))
    _refresh_source_ref(manifest, "continuity_source", result_path)
    result = parse_typed_evidence(
        manifest,
        root=tmp_path,
        expected_corpus_sha256=_expected_sha(tmp_path, "codex", "continuity"),
    )
    assert result["status"] == "failed"
    assert result["hard_failure_counts"]["hidden_mutation"] > 0


def test_v013_host_task_matrix_requires_exact_six() -> None:
    with pytest.raises(HostTaskQualificationError):
        validate_host_task_matrix([])
    rows = [
        {"kind": "host_event_sequence", "metrics": {"host": host, "task_case": task}}
        for host in ("codex", "opencode")
        for task in ("continuity", "living_wiki", "professional_evidence")
    ]
    assert validate_host_task_matrix(rows)["result_count"] == 6
    with pytest.raises(HostTaskQualificationError):
        validate_host_task_matrix(rows[:-1])


def _assert_codex_zero_model_runner_serializes_stop_before_sampling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate = {
        "commit": COMMIT,
        "tree": TREE,
        "lock_sha256": LOCK,
        "wheel_sha256": WHEEL,
        "sdist_sha256": SDIST,
    }
    monkeypatch.setattr(
        host_task_runner,
        "load_zero_model_candidate_binding",
        lambda *_args, **_kwargs: candidate,
    )
    monkeypatch.setattr(
        host_task_runner,
        "_load_external_identity",
        lambda *_args, **_kwargs: {
            "source_sha256": "4" * 64,
            "hosts": {"codex": {"host": "codex"}},
        },
    )
    monkeypatch.setattr(
        host_task_runner,
        "_validate_codex_binary_static",
        lambda *_args, **_kwargs: {"version": "codex-canary", "sha256": "5" * 64},
    )
    monkeypatch.setattr(
        host_task_runner,
        "host_identity_sha256",
        lambda _value: "6" * 64,
    )
    monkeypatch.setattr(
        host_task_runner,
        "_stage_exact_broker_executable",
        lambda *_args, **_kwargs: nullcontext(tmp_path / "staged-broker"),
    )
    session_start_hook = {
        "event_name": "SessionStart",
        "status": "stopped",
        "owner": "broker",
        "handler_type": "command",
        "execution_mode": "sync",
        "response": {"continue": False},
        "stop_boundary": "before_run_sampling_request",
        "event_sha256": "7" * 64,
    }

    def observed_preflight(
        _broker: Path,
        *,
        request: dict[str, Any],
        timeout_seconds: float = 60.0,
        seen_nonce_sha256s: set[str],
    ) -> dict[str, Any]:
        assert timeout_seconds == 60.0
        assert seen_nonce_sha256s == set()
        assert request["allowed_sequence"] == [
            "initialize",
            "initialized",
            "thread/start",
            "turn/start",
            "SessionStart",
            "stdin/close",
        ]
        return {
            "schema_version": "deeplaw.codex-owner-external-broker-control/v4",
            "host_process_receipt": {"record_sha256": "8" * 64},
            "observed_sequence": request["allowed_sequence"],
            "fresh_ephemeral_thread": True,
            "turn_start_count": 1,
            "session_start_hook": session_start_hook,
            "provider_guard": {
                "owner": "broker",
                "transport": "loopback_http",
                "provider_id": "deeplaw_zero_model_preflight",
                "requires_openai_auth": False,
                "supports_websockets": False,
            },
            "accepted_connection_count": 0,
            "request_count": 0,
            "model_inventory_count": 0,
            "model_invocation_count": 0,
            "provider_request_count": 0,
            "sampling_count": 0,
        }

    monkeypatch.setattr(
        host_task_runner,
        "consume_codex_zero_model_preflight",
        observed_preflight,
    )
    result = host_task_runner.run_codex_owner_external_zero_model_preflight(
        candidate_binding_input=tmp_path / "candidate.json",
        host_identity_input=tmp_path / "host-identity.json",
        codex_binary=tmp_path / "codex",
        codex_broker=tmp_path / "broker",
        expected_broker_sha256="1" * 64,
        task_case="continuity",
        run_id="codex-zero-model-canary",
        evidence_run_id=1,
        qualification_run_id=1,
        repository=tmp_path / "repository",
    )
    serialized = json.loads(json.dumps(result, sort_keys=True))
    assert serialized["formal_admission"] is False
    assert serialized["evidence_class"] == "zero_model_preflight_only"
    assert serialized["control_schema_version"] == (
        "deeplaw.codex-owner-external-broker-control/v4"
    )
    assert serialized["turn_start_count"] == 1
    assert serialized["provider_guard"] == {
        "owner": "broker",
        "transport": "loopback_http",
        "provider_id": "deeplaw_zero_model_preflight",
        "requires_openai_auth": False,
        "supports_websockets": False,
    }
    assert serialized["accepted_connection_count"] == 0
    assert serialized["request_count"] == 0
    assert serialized["provider_request_count"] == 0
    assert serialized["model_invocation_count"] == 0
    assert serialized["sampling_count"] == 0
    assert serialized["session_start_hook"] == session_start_hook
