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


def test_one_raw_execution_is_reusable_by_independent_validators(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    raw_path = tmp_path / "raw.json"
    _write(raw_path, _raw(active, active_path))

    canonical = validate_gate(
        "canonical_integrity",
        [raw_path],
        root=tmp_path,
        active_path=active_path,
        classification_path=CLASSIFICATION,
    )
    timeline = validate_gate(
        "timeline",
        [raw_path],
        root=tmp_path,
        active_path=active_path,
        classification_path=CLASSIFICATION,
    )

    assert canonical["status"] == timeline["status"] == "passed"
    assert canonical["raw_inputs"] == timeline["raw_inputs"]
    assert canonical["gate_id"] != timeline["gate_id"]


def test_validator_rejects_wrong_active_hash_even_with_resealed_raw(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    raw = _raw(active, active_path)
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
        )


def test_validator_rejects_resealed_unbound_development_gold(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    raw = _raw(active, active_path)
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
        )


def test_codex_reasoning_effort_is_bound_to_active_candidate(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    raw = _codex_raw(active, active_path)
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
    )
    assert result["status"] == "failed"
    assert "reasoning_effort_mismatch" in result["failures"]


def test_codex_frozen_tool_version_must_match_gate_classification(tmp_path: Path) -> None:
    active_path = tmp_path / "active.json"
    active = _frozen_active(active_path)
    active["host_constraints"]["codex"]["tool_version"] = "0.148.0-alpha.9"
    _write(active_path, active)
    raw_path = tmp_path / "raw.json"
    _write(raw_path, _codex_raw(active, active_path))

    with pytest.raises(GateValidationError, match="Host constraint differs"):
        validate_gate(
            "codex",
            [raw_path],
            root=tmp_path,
            active_path=active_path,
            classification_path=CLASSIFICATION,
        )
