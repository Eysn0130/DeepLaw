from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
CONTRACTS = REPOSITORY / "contracts"
V2_PROTOCOL_SCHEMA = CONTRACTS / "v013-qualification-protocol.v2.schema.json"
V2_ACTIVE_SCHEMA = CONTRACTS / "v013-active-qualification.v2.schema.json"
V2_PROTOCOL = REPOSITORY / "benchmarks/v013/qualification-protocol-v2.json"
V2_PROTOCOL_HASH = REPOSITORY / "benchmarks/v013/qualification-protocol-v2.sha256"
V2_ACTIVE = REPOSITORY / "benchmarks/v013/active-qualification-v2.json"
V1_PROTOCOL = REPOSITORY / "benchmarks/v013/qualification-protocol-v1.json"
V1_ACTIVE = REPOSITORY / "benchmarks/v013/active-qualification-v1.json"

LOCK_SHA256 = "e2cacd96e66132fcb28f1b9bf4746709ad2696159ffb8498ddf0769c213a7082"
CODEX_SHA256 = "7645c3caf5607e4528eb3a15b12496c284c2a918939aed34e863c760c1b421e7"
OPENCODE_EXECUTABLE_SHA256 = (
    "a41776bf64c75786d6baf531b840ffb873c090d7c44793ae2dd4b1896de56a1f"
)
OPENCODE_PACKAGE_SHA256 = (
    "d40af2479740f8ad3a32b700e9a907794ba4314c926d0e805c20fe39751d8722"
)
OPENCODE_COMMIT = "a3647eb025c7615159d417dcc49fc39fdaeba65b"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(path: Path) -> jsonschema.Draft202012Validator:
    schema = _read_json(path)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )


def _assert_valid(schema_path: Path, value: dict[str, Any]) -> None:
    _validator(schema_path).validate(value)


def test_machine_protocol_and_active_binding_validate() -> None:
    protocol_schema = _read_json(V2_PROTOCOL_SCHEMA)
    active_schema = _read_json(V2_ACTIVE_SCHEMA)
    jsonschema.Draft202012Validator.check_schema(protocol_schema)
    jsonschema.Draft202012Validator.check_schema(active_schema)
    protocol = _read_json(V2_PROTOCOL)
    active = _read_json(V2_ACTIVE)
    _assert_valid(V2_PROTOCOL_SCHEMA, protocol)
    _assert_valid(V2_ACTIVE_SCHEMA, active)
    assert protocol["profile"] == "machine_evaluated_no_human_attestation"
    assert active["profile"] == protocol["profile"]
    assert active["candidate_version"] in {"0.12.0", "0.13.0"}
    assert active["release_ready"] is False
    assert active["claim_eligible"] is False
    assert active["machine_qualification_claim_eligible"] is False


def test_protocol_sidecar_hash_binds_exact_json_bytes() -> None:
    sidecar = V2_PROTOCOL_HASH.read_text(encoding="utf-8")
    match = re.fullmatch(r"([0-9a-f]{64})  qualification-protocol-v2\.json\n", sidecar)
    assert match is not None
    observed = hashlib.sha256(V2_PROTOCOL.read_bytes()).hexdigest()
    assert match.group(1) == observed, (match.group(1), observed)
    active = _read_json(V2_ACTIVE)
    assert active["protocol_binding"]["sha256"] == observed


def test_machine_roles_are_closed_and_independent() -> None:
    protocol = _read_json(V2_PROTOCOL)
    roles = protocol["roles"]
    members = roles["members"]
    ids = [item["role_id"] for item in members]
    assert roles["minimum_independent_reviewers"] == 3
    assert roles["maximum_role_count"] == 13
    assert len(members) == 13
    assert len(ids) == len(set(ids))
    reviewer_ids = {
        "semantic-reviewer-a",
        "semantic-reviewer-b",
        "semantic-reviewer-c",
    }
    assert reviewer_ids <= set(ids)
    by_id = {item["role_id"]: item for item in members}
    for role_id in reviewer_ids:
        role = by_id[role_id]
        assert role["separate_process"] is True
        assert role["candidate_visibility"] == "forbidden"
        assert role["reference_label_visibility"] == "forbidden"
        assert role["peer_visibility"] == "forbidden"
        assert role["secret_visibility"] == "forbidden"
    runner = by_id["exact-candidate-runner"]
    assert runner["candidate_visibility"] == "exact_artifact_only"
    assert runner["reference_label_visibility"] == "forbidden"
    scorer_ids = {"independent-scorer-a", "independent-scorer-b"}
    for role_id in scorer_ids:
        scorer = by_id[role_id]
        assert scorer["candidate_visibility"] == "forbidden"
        assert scorer["reference_label_visibility"] == "evaluator_only"
        assert scorer["peer_visibility"] == "forbidden"


def test_isolation_and_scoring_are_fail_closed() -> None:
    protocol = _read_json(V2_PROTOCOL)
    isolation = protocol["isolation_contract"]
    assert all(
        isolation[field] == "forbidden"
        for field in (
            "reviewer_peer_visibility",
            "reviewer_candidate_visibility",
            "reviewer_runner_visibility",
            "reviewer_scorer_visibility",
            "runner_reference_labels_visibility",
            "runner_scorer_visibility",
            "scorer_peer_visibility",
            "scorer_candidate_source_visibility",
            "scorer_runner_process_visibility",
            "shared_filesystem",
            "shared_ipc",
            "shared_transcript",
            "secret_visibility",
        )
    )
    assert isolation["input_mounts_read_only"] is True
    scoring = protocol["scoring_contract"]
    assert scoring["independent_scorer_count"] == 2
    assert scoring["arbitration"] == "deterministic_exact_replay"
    assert scoring["disagreement_action"] == "failed"
    assert scoring["missing_case_action"] == "failed"
    assert scoring["duplicate_case_action"] == "failed"
    assert scoring["hard_failure_maximum"] == 0
    assert scoring["false_authority_maximum"] == 0
    failure = protocol["failure_policy"]
    assert failure["reviewer_disagreement"] == "failed"
    assert failure["scorer_disagreement"] == "failed"
    assert failure["reference_or_blind_contamination"] == (
        "failed_replace_with_new_unseen_blind"
    )
    assert failure["repair_after_final_blind_failure"] == "new_unseen_blind_required"
    assert failure["placeholder_evidence"] == "reject"


def test_blind_layers_and_human_status_are_explicit() -> None:
    protocol = _read_json(V2_PROTOCOL)
    layers = protocol["data_layers"]
    assert layers["development"]["status"] == "development_only"
    assert layers["qualification_holdout"]["status"] == "not_bound"
    assert layers["qualification_holdout"]["reference_visibility"] == "evaluator_only"
    assert layers["final_blind"]["status"] == "not_bound"
    assert layers["final_blind"]["reference_visibility"] == "evaluator_only"
    assert layers["final_blind"]["failure_policy"] == (
        "failure_then_repair_requires_new_unseen_holdout"
    )
    human_review = protocol["human_review"]
    assert human_review["mode"] == "optional_non_gating"
    assert human_review["status"] == "not_executed"
    assert human_review["authenticity"] == "not_claimed"
    assert human_review["attestation"] == "not_used_for_machine_profile"


def test_exact_host_pins_are_frozen() -> None:
    protocol = _read_json(V2_PROTOCOL)
    host = protocol["host_constraints"]
    codex = host["codex"]
    assert codex == {
        "binary_version": "codex-cli 0.148.0-alpha.15",
        "binary_sha256": CODEX_SHA256,
        "request_model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "auth_status_command": "codex login status",
        "auth_material_access": "forbidden",
    }
    opencode = host["opencode"]
    assert opencode["version"] == "1.18.16"
    assert opencode["source_commit"] == OPENCODE_COMMIT
    assert opencode["executable_sha256"] == OPENCODE_EXECUTABLE_SHA256
    assert opencode["package_sha256"] == OPENCODE_PACKAGE_SHA256
    assert opencode["config_selector"] == "deepseek/deepseek-v4-flash"
    assert opencode["expected_response_model_id"] == "deepseek-v4-flash"
    assert opencode["runtime"] == "host_bun_runtime_only"
    assert opencode["dotenv_policy"] == "owner_only_external_strict_parser"


def test_active_v2_is_release_closed_and_stage_consistent() -> None:
    active = _read_json(V2_ACTIVE)
    candidate = active["candidate_binding"]
    version = active["candidate_version"]
    assert version == "0.12.0"
    assert candidate["package_version"] == version
    for field in (
        "source_commit",
        "source_tree",
        "wheel_filename",
        "wheel_sha256",
        "sdist_filename",
        "sdist_sha256",
        "artifact_manifest_sha256",
    ):
        assert candidate[field] is None
    assert candidate["lock_sha256"] == LOCK_SHA256
    assert active["status"] == "machine_evaluation_pending"
    assert all(value is None for value in active["external_inputs"].values())
    assert active["blocker"] == "machine_evaluation_not_executed"
    assert active["owner_tag_release_confirmation"] == "required_at_release_decision"


def test_profile_cannot_be_relabelled_as_human_or_passed() -> None:
    protocol = _read_json(V2_PROTOCOL)
    human = copy.deepcopy(protocol)
    human["profile"] = "human_gold"
    with pytest.raises(jsonschema.ValidationError):
        _assert_valid(V2_PROTOCOL_SCHEMA, human)

    enabled = copy.deepcopy(protocol)
    enabled["claim_policy"]["machine_qualification_claim_eligible"] = True
    with pytest.raises(jsonschema.ValidationError):
        _assert_valid(V2_PROTOCOL_SCHEMA, enabled)


def test_historical_v1_contracts_remain_historical() -> None:
    assert _read_json(V1_PROTOCOL)["schema_version"] == (
        "deeplaw.v013-qualification-protocol/v1"
    )
    assert _read_json(V1_ACTIVE)["schema_version"] == "deeplaw.v013-active-qualification/v1"
