from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from benchmarks.evaluator import score_opencode_continuity_observation as evaluator

REPOSITORY = Path(__file__).resolve().parents[1]
EVIDENCE = (
    REPOSITORY
    / "benchmarks/hosts/evidence/pass11-opencode-continuity-2026-08-11"
)
OBSERVATION = EVIDENCE / "opencode-continuity-observation.json"
EVALUATION = EVIDENCE / "opencode-continuity-evaluation.json"
SCHEMA = REPOSITORY / "contracts/opencode-continuity-evaluation.v1.schema.json"
GOLD = REPOSITORY / "benchmarks/evaluator/continuity-qualification-gold-v1.json"


def test_failed_real_candidate_is_not_scored(tmp_path: Path) -> None:
    output = tmp_path / "evaluation.json"
    report = evaluator.evaluate(
        observation_path=OBSERVATION,
        gold_path=GOLD,
        output_path=output,
    )

    assert report["status"] == "failed"
    assert report["release_ready"] is False
    assert report["claim_eligible"] is False
    assert report["scoring_status"] == "not_scored"
    assert set(report["hard_failures"]) == {
        "candidate_run_failed",
        "host_output_missing",
        "provider_capsule_missing",
    }
    assert all(value is None for value in report["metrics"].values())
    assert report["duplicate_evidence"] is None
    assert report["redundancy"] is None
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_archived_opencode_evaluation_is_closed_and_exactly_bound() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    report = json.loads(EVALUATION.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)

    assert schema["additionalProperties"] is False
    assert report["status"] == "failed"
    assert report["scoring_status"] == "not_scored"
    assert report["observation"]["commit"] == (
        "ab5d43c14370e51fbcc5dcd996ad1c159b45d167"
    )
    assert report["observation"]["tree"] == (
        "aa0af3bbd4685b08cddc11490c3f14703f2f9152"
    )
    assert report["evaluator"]["gold_name"] == (
        "continuity-qualification-gold-v1.json"
    )
    assert report["evaluator"]["gold_sha256"] == hashlib.sha256(
        GOLD.read_bytes()
    ).hexdigest()
    assert report["evaluator"]["scorer_sha256"] == hashlib.sha256(
        Path(evaluator.__file__).read_bytes()
    ).hexdigest()


def test_archived_opencode_receipts_are_sanitized_and_fail_closed() -> None:
    observation = json.loads(OBSERVATION.read_text(encoding="utf-8"))
    run = observation["runs"][0]
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            OBSERVATION,
            EVALUATION,
            EVIDENCE / "opencode-run-1-events.sanitized.jsonl",
            EVIDENCE / "mcp-environment-receipt.json",
            EVIDENCE / "opencode.json",
        )
    )

    assert observation["binding"]["package_version"] == "0.12.0"
    assert observation["release_ready"] is False
    assert observation["claim_eligible"] is False
    assert run["status"] == "failed"
    assert run["actual_event_receipt"]["tool_calls"] == []
    assert run["provider_capsule"] is None
    assert run["host_output"] is None
    assert run["usage"]["status"] == "provider_reported"
    assert run["ledger_unchanged"] is True
    assert observation["security"]["host_state_removed"] is True
    assert observation["security"]["secret_leak"] is False
    assert observation["security"]["absolute_path_leak"] is False
    assert "/Users/" not in serialized
    assert "/tmp/" not in serialized
    assert "BEGIN PRIVATE KEY" not in serialized


def test_archived_opencode_manifest_binds_every_retained_artifact() -> None:
    manifest = json.loads((EVIDENCE / "SHA256SUMS.json").read_text(encoding="utf-8"))
    expected = {
        path.name: path
        for path in EVIDENCE.iterdir()
        if path.is_file() and path.name != "SHA256SUMS.json"
    }

    assert {item["name"] for item in manifest["artifacts"]} == set(expected)
    for item in manifest["artifacts"]:
        payload = expected[item["name"]].read_bytes()
        assert item["bytes"] == len(payload)
        assert item["sha256"] == hashlib.sha256(payload).hexdigest()
