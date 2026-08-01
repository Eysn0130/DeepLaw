from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.legal.generate_ocr_mutations import generate
from benchmarks.legal.review_held_out import confirm_candidate, validate_candidate
from benchmarks.legal.run_authoritative_evidence_gate import run
from benchmarks.legal.score_legal_quality import score
from benchmarks.release.evidence import repository_binding, write_report
from deeplaw.authoritative_pack import verify_authoritative_pack_descriptor

REPOSITORY = Path(__file__).resolve().parents[1]
CANDIDATE = REPOSITORY / "benchmarks/legal/held-out-candidate-v1.json"


def _candidate() -> dict:
    return json.loads(CANDIDATE.read_text(encoding="utf-8"))


def test_legal_held_out_candidate_is_complete_but_not_expert_confirmed() -> None:
    value = _candidate()
    digest = validate_candidate(value)
    assert value["status"] == "expert_review_pending"
    assert value["review"] is None
    assert len(value["cases"]) == 16
    assert len(value["split"]["held_out_case_ids"]) == 8
    assert len(digest) == 64


def test_expert_confirmation_requires_frozen_exact_evidence() -> None:
    with pytest.raises(ValueError, match="exact release, segment and hashes"):
        confirm_candidate(
            _candidate(),
            reviewer_id="expert:test",
            reviewer_role="legal_expert",
            reason="Reviewed the proposed labels against the selected corpus.",
            reviewed_at="2026-08-01T02:03:04Z",
        )


def test_legal_scorer_refuses_pending_candidate() -> None:
    with pytest.raises(ValueError, match="expert-confirmed"):
        score(gold=_candidate(), run={})


def test_ocr_mutations_are_deterministic_and_cannot_claim_review() -> None:
    first = generate("合成金额壹万元，未完成。")
    second = generate("合成金额壹万元，未完成。")
    assert first == second
    assert first["source_free_synthetic"] is True
    assert len(first["cases"]) >= 3
    assert {case["expected_capability"] for case in first["cases"]} == {"extraction:ocr_unreviewed"}
    assert {case["expected_answerability"] for case in first["cases"]} == {
        "duty_evidence_uncertain"
    }


def test_new_legal_quality_contracts_are_valid_draft_2020_12() -> None:
    for name in (
        "citation-audit.v1.schema.json",
        "legal-held-out-gold.v1.schema.json",
        "legal-quality-run.v1.schema.json",
        "legal-quality-report.v1.schema.json",
        "authoritative-pack-core.v1.schema.json",
        "evidence-capability-record.v1.schema.json",
        "release-capability-migration.v1.schema.json",
        "release-capability-rollback.v1.schema.json",
        "authoritative-evidence-quality.v1.schema.json",
    ):
        Draft202012Validator.check_schema(
            json.loads((REPOSITORY / "contracts" / name).read_text(encoding="utf-8"))
        )


def test_authoritative_evidence_gate_is_executable_and_does_not_claim_expert_gold(
    tmp_path: Path,
) -> None:
    binding = repository_binding(REPOSITORY)
    evaluation = tmp_path / "evaluation-report.json"
    evaluation.write_text(
        json.dumps(
            {
                "schema_version": "deeplaw.evaluation-report/v1",
                "candidate": {
                    "commit": binding["commit"],
                    "tree": binding["tree"],
                    "version": binding["package_version"],
                },
                "scoring": {"quality_gate_passed": True},
                "hard_failures": [],
            }
        ),
        encoding="utf-8",
    )
    report = run(REPOSITORY, evaluation, require_clean=False)
    output = tmp_path / "authoritative-evidence-quality.json"
    write_report(output, report)
    value = json.loads(output.read_text(encoding="utf-8"))
    schema = json.loads(
        (REPOSITORY / "contracts/authoritative-evidence-quality.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(value)

    assert value["passed"] is True
    assert value["expert_gold"] == {
        "status": "expert_review_pending",
        "expert_quality_claimed": False,
    }
    assert not any(value["security_failures"].values())


def test_internal_nonlegal_pack_core_is_verified_without_a_public_mcp_leaf() -> None:
    fixture = json.loads(
        (REPOSITORY / "benchmarks/legal/synthetic-policy-pack-core-v1.json").read_text(
            encoding="utf-8"
        )
    )
    schema = json.loads(
        (REPOSITORY / "contracts/authoritative-pack-core.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(fixture)
    assert verify_authoritative_pack_descriptor(fixture)["valid"] is True
    assert fixture["pack"]["not_public_authority"] is True
    assert fixture["public_mcp_leaf"] is False
    law_input = json.loads(
        (REPOSITORY / "contracts/law-support.input.v4.schema.json").read_text(encoding="utf-8")
    )
    assert "authoritative_support" not in json.dumps(law_input)

    tampered = json.loads(json.dumps(fixture))
    tampered["trust"]["sequence"] = 2
    assert verify_authoritative_pack_descriptor(tampered) == {
        "schema_version": "deeplaw.authoritative-pack-core-verification/v1",
        "pack_id": fixture["pack"]["pack_id"],
        "release_id": fixture["release"]["release_id"],
        "valid": False,
        "reason": "core_digest_mismatch",
    }
