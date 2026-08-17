from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from benchmarks.release.assemble_commercial_qualification_v8 import (
    _GATE_EVIDENCE_KINDS,
    CommercialQualificationAssemblerError,
    _execution,
    _gate_result,
    _load_active,
    _parser,
    _safe_relative,
    _TypedRecord,
)
from benchmarks.release.qualification_evidence_core import require_exact_protocol_gate_ids

SHA = "a" * 64
COMMIT = "1" * 40
TREE = "2" * 40
REPOSITORY = Path(__file__).resolve().parents[1]


def _record(tmp_path: Path, *, status: str = "passed") -> _TypedRecord:
    manifest = {
        "schema_version": "deeplaw.typed-qualification-evidence/v2",
        "profile": "machine_evaluated_no_human_attestation",
        "reference_provenance": "agent_consensus",
        "human_authenticity": "not_claimed",
        "kind": "exact_wheel_execution",
        "candidate_binding": {
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": SHA,
            "wheel_sha256": SHA,
            "sdist_sha256": SHA,
        },
        "run_binding": {"run_id": "run:one", "workflow_run_id": 1},
        "corpus": {"role": "candidate_full", "sha256": SHA},
        "runner": {"identity": "runner:one", "sha256": SHA},
        "scorer_panel": {
            "scorer_a": {
                "role": "independent_scorer_a",
                "identity": "scorer:a",
                "sha256": SHA,
            },
            "scorer_b": {
                "role": "independent_scorer_b",
                "identity": "scorer:b",
                "sha256": SHA,
            },
            "panel_sha256": SHA,
            "distinct_scorers": True,
        },
        "arbiter": {
            "role": "deterministic_arbiter",
            "identity": "arbiter:one",
            "sha256": SHA,
        },
        "payload": {},
        "record_sha256": SHA,
    }
    path = tmp_path / "manifest.json"
    path.write_bytes(b"{}")
    derived = {
        "schema_version": "deeplaw.typed-qualification-derived/v2",
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


def _reference_binding() -> dict[str, object]:
    return {
        "semantic_reference_sha256": SHA,
        "agent_roster_sha256": SHA,
        "agent_consensus_sha256": SHA,
        "agent_isolation_sha256": SHA,
        "frozen": True,
    }


def test_cli_parser_requires_all_machine_bindings() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args([])


def test_cli_parser_does_not_accept_human_approver_argument() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(["--trusted-human-approver", "outside.json"])


def test_load_active_accepts_current_machine_only_profile() -> None:
    active, raw = _load_active(REPOSITORY / "benchmarks/v013/active-qualification-v2.json")

    assert raw
    assert active["profile"] == "machine_evaluated_no_human_attestation"
    assert active["human_review"]["authenticity"] == "not_claimed"


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
                "classification_id": "deeplaw-v013-commercial-gates-v8",
                "classification_schema_version": "deeplaw.v013-release-gate-classification/v8",
                "classification_sha256": SHA,
            },
            protocol_binding={
                "protocol_id": "protocol:v2",
                "protocol_sha256": SHA,
                "frozen": True,
            },
            threshold_binding={
                "threshold_id": "machine-reference-thresholds",
                "threshold_sha256": SHA,
                "frozen": True,
            },
            reference_binding=_reference_binding(),
            corpora=[
                {
                    "role": "qualification_holdout",
                    "source": "repository_external",
                    "sha256": SHA,
                    "frozen": True,
                }
            ],
            validator_source={
                "relative_path": "benchmarks/release/typed_qualification_evidence.py",
                "byte_size": 1,
                "file_sha256": SHA,
            },
            validator_executable={
                "relative_path": "benchmarks/release/assemble_commercial_qualification_v8.py",
                "byte_size": 1,
                "file_sha256": SHA,
            },
        )


def test_v8_mapping_has_fourteen_core_machine_gates() -> None:
    assert len(_GATE_EVIDENCE_KINDS) == 14
    assert "machine_reference_isolation" in _GATE_EVIDENCE_KINDS
    assert "human_gold_isolation" not in _GATE_EVIDENCE_KINDS
    assert _GATE_EVIDENCE_KINDS["machine_reference_isolation"] == {
        "machine_reference_scorer"
    }


def test_protocol_gate_inventory_must_equal_classification() -> None:
    protocol = {"gates": [{"gate_id": gate_id} for gate_id in _GATE_EVIDENCE_KINDS]}
    require_exact_protocol_gate_ids(
        protocol,
        expected_gate_ids=list(_GATE_EVIDENCE_KINDS),
        error_type=CommercialQualificationAssemblerError,
    )
    protocol["gates"][-1]["gate_id"] = "classification_only_gate"
    with pytest.raises(
        CommercialQualificationAssemblerError,
        match="differs from classification",
    ):
        require_exact_protocol_gate_ids(
            protocol,
            expected_gate_ids=list(_GATE_EVIDENCE_KINDS),
            error_type=CommercialQualificationAssemblerError,
        )


def test_execution_identity_is_path_free() -> None:
    result = _execution(
        {"run_binding": {"run_id": "run:host", "workflow_run_id": 9}},
        input_id="input:host:1",
        kind="host_event_sequence",
        derived={"metrics": {"expected_response_model_id": "gpt-5.6-luna"}},
    )
    serialized = repr(result)
    assert "/Users/" not in serialized
    assert "\\" not in serialized
    assert result["run_id"] == "run:host"


def test_digest_helpers_are_sha256_sized() -> None:
    assert len(hashlib.sha256(b"typed-v2").hexdigest()) == 64
