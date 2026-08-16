from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from benchmarks.release.v013_gate_validator import (
    GateValidationError,
    record_sha256,
    validate_gate,
)

REPOSITORY = Path(__file__).resolve().parents[1]
CLASSIFICATION = REPOSITORY / "benchmarks/release/v013-gate-classification-v6.json"
EVIDENCE_RUN_ID = 220013


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def _frozen_active(path: Path) -> dict[str, Any]:
    digest = "d" * 64
    value = {
        "schema_version": "deeplaw.v013-active-qualification/v1",
        "qualification_id": "deeplaw-v013-active-commercial-candidate",
        "status": "frozen_exact_candidate",
        "candidate_version": "0.13.0",
        "protocol_binding": {
            "protocol_id": "deeplaw-v013-source-candidate-qualification",
            "schema_version": "deeplaw.v013-qualification-protocol/v1",
            "relative_path": "benchmarks/v013/qualification-protocol-v1.json",
            "sha256": "e" * 64,
        },
        "candidate_binding": {
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "lock_sha256": "c" * 64,
            "wheel_filename": "deeplaw-0.13.0-py3-none-any.whl",
            "wheel_sha256": "1" * 64,
            "sdist_filename": "deeplaw-0.13.0.tar.gz",
            "sdist_sha256": "2" * 64,
            "artifact_manifest_sha256": "3" * 64,
            "source_date_epoch": 1_786_838_400,
        },
        "external_inputs": {
            "human_gold_manifest_sha256": digest,
            "qualification_holdout_sha256": digest,
            "final_blind_holdout_sha256": digest,
            "compiler_scorer_isolation_sha256": digest,
        },
        "host_constraints": {
            "codex": {
                "tool_version": "0.147.0-alpha.1.2",
                "model_id": "gpt-5.6-luna",
                "reasoning_effort": "max",
            },
            "opencode": {
                "tool_version": "1.18.16",
                "model_id": "deepseek/deepseek-v4-flash",
                "reasoning_effort": None,
            },
        },
        "blocker": None,
        "release_ready": False,
        "claim_eligible": False,
    }
    _write(path, value)
    return value


def _raw(active: dict[str, Any], active_path: Path) -> dict[str, Any]:
    candidate = active["candidate_binding"]
    run_id = "run_shared_001"
    value: dict[str, Any] = {
        "schema_version": "deeplaw.v013-gate-raw-evidence/v1",
        "artifact_kind": "v013-reusable-raw-gate-execution",
        "candidate_version": "0.13.0",
        "candidate_binding": {
            "commit": candidate["source_commit"],
            "tree": candidate["source_tree"],
            "lock_sha256": candidate["lock_sha256"],
            "wheel_sha256": candidate["wheel_sha256"],
            "sdist_sha256": candidate["sdist_sha256"],
        },
        "protocol_binding": {
            "protocol_id": active["protocol_binding"]["protocol_id"],
            "protocol_sha256": active["protocol_binding"]["sha256"],
            "active_qualification_sha256": hashlib.sha256(
                active_path.read_bytes()
            ).hexdigest(),
        },
        "gold_binding": {
            "manifest_sha256": active["protocol_binding"]["sha256"],
            "role": "development",
            "source": "repository",
            "independent": False,
        },
        "corpus": {
            "sha256": hashlib.sha256(candidate["source_tree"].encode("ascii")).hexdigest(),
            "role": "development",
            "source": "repository",
            "read_only": True,
        },
        "isolation": {
            "manifest_sha256": active["protocol_binding"]["sha256"],
            "source_mount_read_only": True,
            "compiler_gold_visible": False,
            "compiler_scorer_visible": False,
            "scorer_process_separate": True,
            "repository_source_visible": False,
            "ambient_credentials_visible": False,
        },
        "gate_ids": ["canonical_integrity", "timeline"],
        "executions": [
            {
                "run_id": run_id,
                "argv": ["deeplaw", "qualification", "mechanical"],
                "exit_code": 0,
                "os_name": "linux",
                "python_version": "3.12.8",
                "tool_name": "deeplaw",
                "tool_version": "0.13.0",
                "model_id": None,
                "reasoning_effort": None,
                "dimensions": {"lane": "mechanical"},
                "provider_bytes": 0,
                "input_tokens": None,
                "output_tokens": None,
                "cache_tokens": None,
                "reasoning_tokens": None,
                "latency_ms": 1.0,
                "rss_peak_bytes": 1,
            }
        ],
        "metric_samples": [
            {
                "gate_id": "canonical_integrity",
                "run_id": run_id,
                "metric": "audit_integrity_pass_rate",
                "numerator": 1,
                "denominator": 1,
            },
            {
                "gate_id": "timeline",
                "run_id": run_id,
                "metric": "timeline_pass_rate",
                "numerator": 1,
                "denominator": 1,
            },
        ],
        "hard_failure_samples": [
            {
                "gate_id": "canonical_integrity",
                "run_id": run_id,
                "failure_id": "canonical_integrity_failure",
                "count": 0,
            },
            {
                "gate_id": "timeline",
                "run_id": run_id,
                "failure_id": "wrong_run_inclusion",
                "count": 0,
            },
            {
                "gate_id": "timeline",
                "run_id": run_id,
                "failure_id": "private_path_disclosure",
                "count": 0,
            },
        ],
        "redaction": {
            "secret_canary_count": 0,
            "private_path_count": 0,
            "raw_transcript_retained": False,
            "hidden_reasoning_retained": False,
            "authentication_material_retained": False,
        },
    }
    value["record_sha256"] = record_sha256(value)
    return value


def _codex_raw(active: dict[str, Any], active_path: Path) -> dict[str, Any]:
    value = _raw(active, active_path)
    digest = active["external_inputs"]["qualification_holdout_sha256"]
    value["gold_binding"] = {
        "manifest_sha256": active["external_inputs"]["human_gold_manifest_sha256"],
        "role": "qualification_holdout",
        "source": "repository_external",
        "independent": True,
    }
    value["corpus"] = {
        "sha256": digest,
        "role": "qualification_holdout",
        "source": "repository_external",
        "read_only": True,
    }
    value["isolation"]["manifest_sha256"] = active["external_inputs"][
        "compiler_scorer_isolation_sha256"
    ]
    value["gate_ids"] = ["codex"]
    execution = {
        "run_id": "codex_case_a",
        "argv": ["codex", "app-server", "--stdio"],
        "exit_code": 0,
        "os_name": "macos",
        "python_version": "3.11.15",
        "tool_name": "codex",
        "tool_version": "0.147.0-alpha.1.2",
        "model_id": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "dimensions": {
            "host": "codex",
            "model": "gpt-5.6-luna",
            "task_case": "A",
        },
        "provider_bytes": 1,
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_tokens": 0,
        "reasoning_tokens": 1,
        "latency_ms": 1.0,
        "rss_peak_bytes": 1,
    }
    value["executions"] = []
    for suffix, task_case in (("a", "A"), ("b", "B"), ("c", "C")):
        item = deepcopy(execution)
        item["run_id"] = f"codex_case_{suffix}"
        item["dimensions"]["task_case"] = task_case
        value["executions"].append(item)
    value["metric_samples"] = [
        {
            "gate_id": "codex",
            "run_id": item["run_id"],
            "metric": "model_task_acceptance_rate",
            "numerator": 1,
            "denominator": 1,
        }
        for item in value["executions"]
    ]
    value["hard_failure_samples"] = [
        {
            "gate_id": "codex",
            "run_id": "codex_case_a",
            "failure_id": failure_id,
            "count": 0,
        }
        for failure_id in ("model_substitution", "secret_exposure", "wrong_tool_or_parameter")
    ]
    value["record_sha256"] = record_sha256(value)
    return value


def _source_evidence(
    active: dict[str, Any],
    active_path: Path,
    source_root: Path,
    *,
    gate_id: str = "timeline",
) -> dict[str, Any]:
    legacy = _codex_raw(active, active_path) if gate_id == "codex" else _raw(active, active_path)
    source_by_gate = {
        "timeline": "timeline_receipt",
        "codex": "host_receipt",
    }
    artifact_by_gate = {
        "timeline": "timeline-report",
        "codex": "codex-host-result",
    }
    executions = []
    for index, execution in enumerate(legacy["executions"]):
        if gate_id == "timeline":
            facts: dict[str, Any] = {
                "stable_route_bound": True,
                "expected_identity_count": 4,
                "observed_identity_count": 4,
                "wrong_run_inclusion": 0,
                "private_path_matches": 0,
                "content_field_count": 0,
                "bounded": True,
                "gap_on_diverged": True,
                "gap_on_forgotten": True,
            }
        else:
            role = "qualification_holdout" if index < 2 else "final_blind"
            facts = {
                "binary_sha256": "9" * 64,
                "native_receipt_sha256": "8" * 64,
                "response_model_id": "gpt-5.6-luna",
                "corpus_role": role,
                "corpus_sha256": active["external_inputs"][
                    "qualification_holdout_sha256"
                    if role == "qualification_holdout"
                    else "final_blind_holdout_sha256"
                ],
                "first_correct_action": True,
                "decision_preservation": True,
                "wrong_state_admission": 0,
                "stale_state_rejected": True,
                "wrong_version_rejected": True,
                "provider_bytes": 100,
                "provider_hard_limit_bytes": 65_536,
                "secret_matches": 0,
                "wrong_tool_or_parameter": 0,
                "actual_provider_tokens": 100,
                "ledger_write_boundary_valid": True,
            }
        observation: dict[str, Any] = {
            "schema_version": "deeplaw.v013-source-observation/v1",
            "evidence_source": source_by_gate[gate_id],
            "gate_id": gate_id,
            "run_id": execution["run_id"],
            "candidate_binding": legacy["candidate_binding"],
            "facts": facts,
        }
        observation["record_sha256"] = record_sha256(observation)
        relative = f"sources/{gate_id}-{execution['run_id']}.json"
        source_path = source_root / relative
        source_path.parent.mkdir(parents=True, exist_ok=True)
        _write(source_path, observation)
        executions.append(
            {
                "run_id": execution["run_id"],
                "argv": execution["argv"],
                "os_name": execution["os_name"],
                "python_version": execution["python_version"],
                "tool_name": execution["tool_name"],
                "tool_version": execution["tool_version"],
                "model_id": execution["model_id"],
                "reasoning_effort": execution["reasoning_effort"],
                "dimensions": execution["dimensions"],
                "source": {
                    "relative_path": relative,
                    "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                    "format": "source_observation_v1",
                },
            }
        )
    value = {
        "schema_version": "deeplaw.v013-gate-source-evidence/v1",
        "artifact_kind": artifact_by_gate[gate_id],
        "evidence_source": source_by_gate[gate_id],
        "gate_id": gate_id,
        "candidate_version": legacy["candidate_version"],
        "candidate_binding": legacy["candidate_binding"],
        "protocol_binding": legacy["protocol_binding"],
        "gold_binding": legacy["gold_binding"],
        "corpus": legacy["corpus"],
        "isolation": legacy["isolation"],
        "workflow_provenance": {
            "repository": "Eysn0130/DeepLaw",
            "workflow_name": "External Qualification Evidence",
            "workflow_path": ".github/workflows/external-qualification-evidence.yml",
            "workflow_run_id": EVIDENCE_RUN_ID,
            "head_sha": legacy["candidate_binding"]["commit"],
            "event": "workflow_dispatch",
            "runner_environment": "self-hosted-macos-qualification",
        },
        "executions": executions,
    }
    value["record_sha256"] = record_sha256(value)
    return value


def test_self_hashed_generic_raw_cannot_produce_a_core_pass(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    raw_path = tmp_path / "raw.json"
    _write(raw_path, _raw(active, active_path))

    with pytest.raises(GateValidationError, match="development diagnostic only"):
        validate_gate(
            "timeline",
            [raw_path],
            root=tmp_path,
            active_path=active_path,
            classification_path=CLASSIFICATION,
        )


def test_source_specific_timeline_derives_pass_from_retained_receipt(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    raw_path = tmp_path / "timeline.json"
    _write(raw_path, _source_evidence(active, active_path, tmp_path))

    result = validate_gate(
        "timeline",
        [raw_path],
        root=tmp_path,
        active_path=active_path,
        classification_path=CLASSIFICATION,
        expected_evidence_run_id=EVIDENCE_RUN_ID,
    )
    assert result["status"] == "passed"
    assert result["metrics"][0]["observed"] == 1


def test_self_hashed_source_json_without_verified_workflow_run_cannot_pass_core(
    tmp_path: Path,
) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    raw_path = tmp_path / "timeline.json"
    _write(raw_path, _source_evidence(active, active_path, tmp_path))

    with pytest.raises(GateValidationError, match="verified workflow run identity"):
        validate_gate(
            "timeline",
            [raw_path],
            root=tmp_path,
            active_path=active_path,
            classification_path=CLASSIFICATION,
        )

    with pytest.raises(GateValidationError, match="differs from the verified run"):
        validate_gate(
            "timeline",
            [raw_path],
            root=tmp_path,
            active_path=active_path,
            classification_path=CLASSIFICATION,
            expected_evidence_run_id=EVIDENCE_RUN_ID + 1,
        )


def test_source_observation_rejects_caller_authored_pass_field(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    raw = _source_evidence(active, active_path, tmp_path)
    source = tmp_path / raw["executions"][0]["source"]["relative_path"]
    observation = json.loads(source.read_text(encoding="utf-8"))
    observation["passed"] = True
    observation["record_sha256"] = record_sha256(observation)
    _write(source, observation)
    raw["executions"][0]["source"]["sha256"] = hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    raw["record_sha256"] = record_sha256(raw)
    raw_path = tmp_path / "timeline.json"
    _write(raw_path, raw)

    with pytest.raises(GateValidationError, match="source observation schema violation"):
        validate_gate(
            "timeline",
            [raw_path],
            root=tmp_path,
            active_path=active_path,
            classification_path=CLASSIFICATION,
            expected_evidence_run_id=EVIDENCE_RUN_ID,
        )


def test_host_gate_requires_two_holdout_and_one_final_blind(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    raw = _source_evidence(active, active_path, tmp_path, gate_id="codex")
    final_source = tmp_path / raw["executions"][2]["source"]["relative_path"]
    observation = json.loads(final_source.read_text(encoding="utf-8"))
    observation["facts"]["corpus_role"] = "qualification_holdout"
    observation["facts"]["corpus_sha256"] = active["external_inputs"][
        "qualification_holdout_sha256"
    ]
    observation["record_sha256"] = record_sha256(observation)
    _write(final_source, observation)
    raw["executions"][2]["source"]["sha256"] = hashlib.sha256(
        final_source.read_bytes()
    ).hexdigest()
    raw["record_sha256"] = record_sha256(raw)
    raw_path = tmp_path / "codex.json"
    _write(raw_path, raw)

    result = validate_gate(
        "codex",
        [raw_path],
        root=tmp_path,
        active_path=active_path,
        classification_path=CLASSIFICATION,
        expected_evidence_run_id=EVIDENCE_RUN_ID,
    )
    assert result["status"] == "failed"
    assert "host_holdout_blind_coverage_missing" in result["failures"]


def test_validator_rejects_wrong_active_hash_even_with_resealed_raw(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    raw = _source_evidence(active, active_path, tmp_path)
    raw["protocol_binding"]["active_qualification_sha256"] = "0" * 64
    raw["record_sha256"] = record_sha256(raw)
    raw_path = tmp_path / "raw.json"
    _write(raw_path, raw)

    with pytest.raises(GateValidationError, match="protocol differs"):
        validate_gate(
            "timeline",
            [raw_path],
            root=tmp_path,
            active_path=active_path,
            classification_path=CLASSIFICATION,
            expected_evidence_run_id=EVIDENCE_RUN_ID,
        )


def test_validator_rejects_unfrozen_construction_candidate(tmp_path: Path) -> None:
    active_path = REPOSITORY / "benchmarks/v013/active-qualification-v1.json"
    raw_path = tmp_path / "unused.json"
    raw_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(GateValidationError, match="not frozen"):
        validate_gate(
            "timeline",
            [raw_path],
            root=tmp_path,
            active_path=active_path,
            classification_path=CLASSIFICATION,
            expected_evidence_run_id=EVIDENCE_RUN_ID,
        )


def test_validator_rejects_resealed_unbound_development_gold(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    raw = _source_evidence(active, active_path, tmp_path)
    raw["gold_binding"]["manifest_sha256"] = "0" * 64
    raw["record_sha256"] = record_sha256(raw)
    raw_path = tmp_path / "raw.json"
    _write(raw_path, raw)

    with pytest.raises(GateValidationError, match="active source binding"):
        validate_gate(
            "timeline",
            [raw_path],
            root=tmp_path,
            active_path=active_path,
            classification_path=CLASSIFICATION,
            expected_evidence_run_id=EVIDENCE_RUN_ID,
        )


def test_codex_reasoning_effort_is_bound_to_active_candidate(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    raw = _source_evidence(active, active_path, tmp_path, gate_id="codex")
    raw["executions"][0]["reasoning_effort"] = "high"
    raw["record_sha256"] = record_sha256(raw)
    raw_path = tmp_path / "raw.json"
    _write(raw_path, raw)

    result = validate_gate(
        "codex",
        [raw_path],
        root=tmp_path,
        active_path=active_path,
        classification_path=CLASSIFICATION,
        expected_evidence_run_id=EVIDENCE_RUN_ID,
    )
    assert result["status"] == "failed"
    assert "reasoning_effort_mismatch" in result["failures"]


def test_codex_frozen_tool_version_must_match_gate_classification(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    active["host_constraints"]["codex"]["tool_version"] = "0.148.0-alpha.9"
    _write(active_path, active)
    raw_path = tmp_path / "raw.json"
    _write(raw_path, _source_evidence(active, active_path, tmp_path, gate_id="codex"))

    with pytest.raises(GateValidationError, match="Host constraint differs"):
        validate_gate(
            "codex",
            [raw_path],
            root=tmp_path,
            active_path=active_path,
            classification_path=CLASSIFICATION,
            expected_evidence_run_id=EVIDENCE_RUN_ID,
        )
