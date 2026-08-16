from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import benchmarks.release.release_provenance_v8 as provenance
from benchmarks.release.release_provenance_v8 import (
    ReleaseProvenanceV8Error,
    _canonical_digest,
)

SHA = "a" * 64
OTHER_SHA = "b" * 64
COMMIT = "1" * 40
TREE = "2" * 40


def test_v8_classification_is_exactly_machine_only_and_fourteen_core_gates() -> None:
    value, raw, core_ids = provenance._load_classification(
        provenance._CURRENT_CLASSIFICATION_PATH
    )
    assert value["profile"] == "machine_evaluated_no_human_attestation"
    assert value["assembly_policy"]["assembly_enabled"] is False
    assert len(core_ids) == 14
    assert "human_gold_isolation" not in core_ids
    assert "machine_reference_isolation" in core_ids
    assert hashlib.sha256(raw).hexdigest() == hashlib.sha256(
        provenance._CURRENT_CLASSIFICATION_PATH.read_bytes()
    ).hexdigest()
    assert provenance._expected_gate_corpus_roles("machine_reference_isolation") == [
        "qualification_holdout",
        "final_blind",
    ]
    assert provenance._expected_gate_corpus_roles("codex") == ["qualification_holdout"]
    assert provenance._expected_gate_corpus_roles("migration_recovery") == ["candidate_full"]


def test_machine_external_binding_keeps_panel_arbiter_and_isolation_distinct() -> None:
    panel = {
        "scorer_a": {
            "role": "independent_scorer_a",
            "identity": "independent-scorer-a:test",
            "sha256": SHA,
        },
        "scorer_b": {
            "role": "independent_scorer_b",
            "identity": "independent-scorer-b:test",
            "sha256": OTHER_SHA,
        },
        "panel_sha256": "c" * 64,
        "distinct_scorers": True,
    }
    binding: dict[str, Any] = {
        "semantic_reference": {"sha256": "d" * 64},
        "agent_roster": {"sha256": "e" * 64},
        "agent_consensus": {"sha256": "f" * 64},
        "agent_isolation": {"sha256": "0" * 64},
        "holdout": {"sha256": "1" * 64},
        "blind": {"sha256": "2" * 64},
        "scorer_panel": panel,
        "arbiter": {
            "role": "deterministic_arbiter",
            "identity": "deterministic-arbiter:test",
            "sha256": "3" * 64,
        },
        "runner": {"identity": "runner:test", "sha256": "4" * 64},
    }
    expected = provenance._machine_external_from_binding(
        binding,
        binding_sha256="5" * 64,
        compiler_scorer_isolation_sha256="6" * 64,
    )
    assert expected["scorer_panel_sha256"] == panel["panel_sha256"]
    assert expected["arbiter_sha256"] == binding["arbiter"]["sha256"]
    assert expected["compiler_scorer_isolation_sha256"] == "6" * 64
    assert expected["compiler_scorer_isolation_sha256"] != expected["arbiter_sha256"]


def test_machine_binding_path_cannot_be_used_as_human_gold_input(tmp_path: Path) -> None:
    legacy = {
        "schema_version": "deeplaw.candidate-gold-binding-receipt/v1",
        "status": "post_build_candidate_gold_bound",
        "human_authenticity": "human_verified",
    }
    path = tmp_path / "candidate-gold.json"
    path.write_text(
        json.dumps(legacy, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(ReleaseProvenanceV8Error):
        provenance._load_json(
            path,
            label="post-build machine reference binding",
            schema="candidate-gold-binding-receipt.v2.schema.json",
        )


def test_parser_rejects_trusted_human_approver_option() -> None:
    with pytest.raises(SystemExit):
        provenance._parser().parse_args(["--trusted-human-approver", "approver.json"])


def test_record_digest_is_canonical_and_excludes_only_record_field() -> None:
    value = {"b": 2, "a": "x", "record_sha256": ""}
    digest = _canonical_digest(value, excluded="record_sha256")
    assert digest == hashlib.sha256(b'{"a":"x","b":2}').hexdigest()


def test_active_requires_frozen_exact_candidate_and_complete_external_bindings() -> None:
    candidate = {
        "commit": COMMIT,
        "tree": TREE,
        "lock_sha256": SHA,
        "wheel_sha256": "c" * 64,
        "sdist_sha256": "d" * 64,
    }
    external = {
        "semantic_reference_sha256": "e" * 64,
        "candidate_binding_sha256": "f" * 64,
        "qualification_holdout_sha256": "1" * 64,
        "final_blind_holdout_sha256": "2" * 64,
        "agent_roster_sha256": "3" * 64,
        "agent_consensus_sha256": "4" * 64,
        "agent_isolation_sha256": "5" * 64,
        "runner_sha256": "6" * 64,
        "scorer_panel_sha256": "7" * 64,
        "arbiter_sha256": "8" * 64,
        "compiler_scorer_isolation_sha256": "9" * 64,
    }
    panel = {
        "scorer_a": {"identity": "scorer:a", "sha256": "a" * 64},
        "scorer_b": {"identity": "scorer:b", "sha256": "b" * 64},
    }
    review_panel = hashlib.sha256(
        json.dumps(
            {
                "agent_roster_sha256": external["agent_roster_sha256"],
                "agent_consensus_sha256": external["agent_consensus_sha256"],
                "agent_isolation_sha256": external["agent_isolation_sha256"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    active = {
        "profile": "machine_evaluated_no_human_attestation",
        "status": "frozen_exact_candidate_machine_evaluation_pending",
        "candidate_version": "0.13.0",
        "blocker": None,
        "release_ready": False,
        "claim_eligible": False,
        "machine_qualification_claim_eligible": False,
        "competitive_claim_eligible": False,
        "candidate_binding": {
            "source_commit": candidate["commit"],
            "source_tree": candidate["tree"],
            "lock_sha256": candidate["lock_sha256"],
            "wheel_sha256": candidate["wheel_sha256"],
            "sdist_sha256": candidate["sdist_sha256"],
        },
        "protocol_binding": {
            "schema_version": "deeplaw.v013-qualification-protocol/v2",
            "relative_path": "benchmarks/v013/qualification-protocol-v2.json",
            "sha256": "0" * 64,
        },
        "external_inputs": {
            "semantic_machine_proposal_sha256": external["semantic_reference_sha256"],
            "qualification_holdout_sha256": external["qualification_holdout_sha256"],
            "final_blind_holdout_sha256": external["final_blind_holdout_sha256"],
            "agent_review_panel_sha256": review_panel,
            "runner_sha256": external["runner_sha256"],
            "scorer_a_sha256": panel["scorer_a"]["sha256"],
            "scorer_b_sha256": panel["scorer_b"]["sha256"],
            "arbitration_sha256": external["arbiter_sha256"],
            "isolation_sha256": external["compiler_scorer_isolation_sha256"],
        },
    }
    provenance._validate_active(
        active,
        candidate=candidate,
        protocol={"protocol_sha256": "0" * 64},
        external=external,
        binding={"scorer_panel": panel},
    )
    pending = dict(active)
    pending["status"] = "machine_evaluation_pending"
    with pytest.raises(ReleaseProvenanceV8Error, match="frozen exact"):
        provenance._validate_active(
            pending,
            candidate=candidate,
            protocol={"protocol_sha256": "0" * 64},
            external=external,
            binding={"scorer_panel": panel},
        )
    incomplete = dict(active)
    incomplete["external_inputs"] = dict(active["external_inputs"])
    incomplete["external_inputs"]["runner_sha256"] = None
    with pytest.raises(ReleaseProvenanceV8Error, match="incomplete or differ"):
        provenance._validate_active(
            incomplete,
            candidate=candidate,
            protocol={"protocol_sha256": "0" * 64},
            external=external,
            binding={"scorer_panel": panel},
        )
