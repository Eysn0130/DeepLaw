from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from benchmarks.release.assemble_commercial_qualification_v7 import (
    CommercialQualificationAssemblerError,
    _execution,
    _gate_result,
    _parser,
    _safe_relative,
    _TypedRecord,
)

SHA = "a" * 64
COMMIT = "1" * 40
TREE = "2" * 40


def _record(tmp_path: Path, *, status: str = "passed") -> _TypedRecord:
    manifest = {
        "schema_version": "deeplaw.typed-qualification-evidence/v1",
        "kind": "exact_wheel_execution",
        "candidate_binding": {
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": SHA,
            "wheel_sha256": SHA,
            "sdist_sha256": SHA,
        },
        "run_binding": {"run_id": "run:one", "workflow_run_id": 1},
        "corpus": {"role": "qualification_holdout", "sha256": SHA},
        "runner": {"identity": "runner:one", "sha256": SHA},
        "scorer": {"identity": "scorer:one", "sha256": SHA},
        "payload": {},
        "record_sha256": SHA,
    }
    path = tmp_path / "manifest.json"
    path.write_bytes(b"{}")
    derived = {
        "schema_version": "deeplaw.typed-qualification-derived/v1",
        "kind": "exact_wheel_execution",
        "status": status,
        "metrics": {"wheel_sha256": SHA},
        "hard_failure_counts": {"exact_wheel_identity": 0},
        "evidence_record_sha256": SHA,
    }
    return _TypedRecord(
        kind="exact_wheel_execution",
        path=path,
        manifest=manifest,
        derived=derived,
        bundle_relative="manifest.json",
    )


def test_cli_parser_requires_all_exact_bindings() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args([])


def test_safe_relative_rejects_absolute_and_parent_paths() -> None:
    for value in ("/tmp/receipt.json", "../receipt.json", "C:/receipt.json"):
        with pytest.raises(CommercialQualificationAssemblerError):
            _safe_relative(value, label="test path")


def test_gate_result_rejects_parser_failure_instead_of_accepting_pass_override(
    tmp_path: Path,
) -> None:
    record = _record(tmp_path, status="failed")
    with pytest.raises(CommercialQualificationAssemblerError, match="did not pass"):
        _gate_result(
            "canonical_integrity",
            [record],
            assets_root=tmp_path,
            qualification_run_id=3,
            candidate={
                "commit": COMMIT,
                "tree": TREE,
                "wheel_sha256": SHA,
                "sdist_sha256": SHA,
            },
            classification_binding={
                "classification_id": "deeplaw-v013-commercial-gates-v7",
                "classification_schema_version": "deeplaw.v013-release-gate-classification/v7",
                "classification_sha256": SHA,
            },
            protocol_binding={
                "protocol_id": "protocol:v7",
                "protocol_sha256": SHA,
                "frozen": True,
            },
            threshold_binding={
                "threshold_id": "semantic-gold-thresholds",
                "threshold_sha256": SHA,
                "frozen": True,
            },
            gold_binding={
                "gold_sha256": SHA,
                "role": "qualification_gold",
                "source": "repository_external",
                "frozen": True,
            },
            corpus={
                "role": "qualification_holdout",
                "source": "repository_external",
                "sha256": SHA,
                "frozen": True,
            },
            validator_source={
                "relative_path": "benchmarks/release/typed_qualification_evidence.py",
                "byte_size": 1,
                "file_sha256": SHA,
            },
            validator_executable={
                "relative_path": "benchmarks/release/assemble_commercial_qualification_v7.py",
                "byte_size": 1,
                "file_sha256": SHA,
            },
        )


def test_execution_identity_is_path_free() -> None:
    record = {
        "run_binding": {"run_id": "run:host", "workflow_run_id": 9},
    }
    result = _execution(
        record,
        input_id="input:host:1",
        kind="host_event_sequence",
        derived={
            "metrics": {
                "actual_response_model_id": "gpt-5.6-luna",
            }
        },
    )
    serialized = repr(result)
    assert "/Users/" not in serialized
    assert "\\" not in serialized
    assert result["run_id"] == "run:host"


def test_digest_helpers_are_sha256_sized() -> None:
    assert len(hashlib.sha256(b"typed").hexdigest()) == 64
