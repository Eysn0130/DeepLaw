"""Closed-contract and semantic negative tests for the v0.13 release evidence seam."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from benchmarks.release import release_policy
from benchmarks.release.semantic_evidence import (
    CLASSIFICATION_PATH,
    REPORT_SCHEMA_PATH,
    SemanticEvidenceError,
    artifact_sha256,
    report_sha256,
    validate_release_manifest_semantics,
    validate_report,
    validate_report_file,
)
from benchmarks.release.v013_commercial_release import (
    V013CommercialReleaseError,
    assemble_manifest,
)

REPOSITORY = Path(__file__).resolve().parents[1]
CLASSIFICATION = json.loads(CLASSIFICATION_PATH.read_text(encoding="utf-8"))

COMMIT = "a" * 40
TREE = "b" * 40
WHEEL = "c" * 64
SDIST = "d" * 64
PROTOCOL = "e" * 64
THRESHOLD = "f" * 64
GOLD = "1" * 64
CORPUS_SHA = "2" * 64
CORE_GATES = [
    "canonical_integrity",
    "migration_recovery",
    "secret_host_isolation",
    "bounded_context",
    "legal_evidence",
    "source_citation_locator",
    "scale_performance",
    "supported_platforms",
    "reproducible_supply_chain",
    "human_gold_isolation",
    "codex",
    "selective_forget",
]


def _bindings() -> dict[str, str]:
    return {
        "candidate_commit": COMMIT,
        "candidate_tree": TREE,
        "candidate_wheel_sha256": WHEEL,
        "candidate_sdist_sha256": SDIST,
        "protocol_sha256": PROTOCOL,
        "threshold_sha256": THRESHOLD,
        "gold_sha256": GOLD,
    }


def _corpus(role: str = "final_blind") -> dict[str, Any]:
    if role == "development":
        return {"role": role, "source": "repository", "sha256": CORPUS_SHA, "frozen": True}
    return {
        "role": role,
        "source": "repository_external",
        "sha256": CORPUS_SHA,
        "frozen": True,
    }


def _observation(gate_id: str, *, corpus: dict[str, Any] | None = None) -> dict[str, Any]:
    run_count = 9 if gate_id == "supported_platforms" else 3 if gate_id == "codex" else 1
    definition = next(item for item in CLASSIFICATION["gates"] if item["gate_id"] == gate_id)
    return {
        "schema_version": "deeplaw.commercial-gate-observation/v1",
        "gate_id": gate_id,
        "bindings": _bindings(),
        "command": {
            "argv": ["uv", "run", "qualification-gate", gate_id],
            "exit_code": 0,
            "run_count": run_count,
        },
        "environment": {
            "os_name": "Darwin",
            "os_version": "test-os-1",
            "python_version": "3.13.5",
            "tool_name": "codex" if gate_id == "codex" else "deeplaw-gate",
            "tool_version": "1.0.0",
            "model_id": "gpt-5.6-luna" if gate_id == "codex" else None,
        },
        "thresholds": [
            {
                "metric": threshold["metric"],
                "observed": (
                    threshold["minimum"]
                    if threshold["minimum"] is not None
                    else threshold["maximum"]
                ),
                "minimum": threshold["minimum"],
                "maximum": threshold["maximum"],
            }
            for threshold in definition["thresholds"]
        ],
        "hard_failures": [
            {"failure_id": failure_id, "count": 0, "maximum_allowed": 0}
            for failure_id in definition["hard_zero_ids"]
        ],
        "failure_inventory": [],
        "corpus": corpus or _corpus(),
        "redaction": {
            "secret_canary_count": 0,
            "private_path_count": 0,
            "output_redacted": True,
        },
    }


def _report(*, role: str = "final_blind", declared_core: list[str] | None = None) -> dict[str, Any]:
    corpus = _corpus(role)
    declared_core = declared_core or CORE_GATES
    artifacts: list[dict[str, Any]] = []
    gates: list[dict[str, Any]] = []
    category_by_gate = {
        gate["gate_id"]: gate["category"] for gate in CLASSIFICATION["gates"]
    }
    for gate_id in declared_core:
        observation = _observation(gate_id, corpus=corpus)
        artifact = {
            "artifact_id": f"{gate_id}-artifact",
            "gate_id": gate_id,
            "content": observation,
        }
        artifact["artifact_sha256"] = artifact_sha256(artifact)
        artifacts.append(artifact)
        gates.append(
            {
                "gate_id": gate_id,
                "category": category_by_gate[gate_id],
                "artifact_id": artifact["artifact_id"],
            }
        )
    report: dict[str, Any] = {
        "schema_version": "deeplaw.commercial-evidence-report/v1",
        "report_kind": "v013_commercial_gate_collection",
        "report_id": "v013-semantic-test",
        "candidate_binding": {
            "candidate_commit": COMMIT,
            "candidate_tree": TREE,
            "candidate_wheel_sha256": WHEEL,
            "candidate_sdist_sha256": SDIST,
        },
        "protocol_binding": {
            "protocol_id": "v013-protocol-v1",
            "protocol_sha256": PROTOCOL,
            "frozen": True,
        },
        "threshold_binding": {
            "threshold_id": "v013-thresholds-v1",
            "threshold_sha256": THRESHOLD,
            "frozen": True,
        },
        "gold_binding": {
            "gold_sha256": GOLD,
            "role": "final_blind_gold" if role == "final_blind" else "development_gold",
            "source": "repository_external" if role != "development" else "repository",
            "frozen": True,
        },
        "corpus": corpus,
        "gates": gates,
        "artifacts": artifacts,
    }
    _refresh(report)
    return report


def _refresh(report: dict[str, Any]) -> None:
    for artifact in report["artifacts"]:
        artifact["artifact_sha256"] = artifact_sha256(artifact)
    report["report_sha256"] = report_sha256(report)


def _expected() -> dict[str, str]:
    return {
        "candidate_commit": COMMIT,
        "candidate_tree": TREE,
        "wheel_sha256": WHEEL,
        "sdist_sha256": SDIST,
        "protocol_sha256": PROTOCOL,
        "threshold_sha256": THRESHOLD,
        "gold_sha256": GOLD,
        "protocol_id": "v013-protocol-v1",
        "threshold_id": "v013-thresholds-v1",
        "corpus_role": "final_blind",
    }


def _validate(report: dict[str, Any]) -> dict[str, Any]:
    return validate_report(report, _expected())


def test_contracts_and_classification_fixture_are_closed() -> None:
    report_schema = json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    classification_schema = json.loads(
        (REPOSITORY / "contracts/v013-release-gate-classification.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(report_schema)
    Draft202012Validator.check_schema(classification_schema)
    Draft202012Validator(classification_schema).validate(CLASSIFICATION)
    assert {item["category"] for item in CLASSIFICATION["categories"]} == {
        "Core",
        "Capability",
        "Competitive Claim",
    }
    assert {
        item["gate_id"] for item in CLASSIFICATION["gates"] if item["category"] == "Core"
    } == set(CORE_GATES)
    assert set(CORE_GATES) == release_policy.V013_CORE_GATE_IDS
    assert {
        item["gate_id"]
        for item in CLASSIFICATION["gates"]
        if item["category"] == "Capability"
    } == release_policy.V013_CAPABILITY_GATE_IDS
    assert {
        item["gate_id"]
        for item in CLASSIFICATION["gates"]
        if item["category"] == "Competitive Claim"
    } == release_policy.V013_COMPETITIVE_GATE_IDS


def test_legacy_self_report_cannot_pass_and_deferred_capabilities_are_not_claimed() -> None:
    result = _validate(_report())
    assert result["status"] == "failed"
    assert result["release_ready"] is False
    assert result["claim_eligible"] is False
    assert result["hard_zero"] is False
    assert result["gate_statuses"]["codex"] == "failed"
    assert "legacy_self_report_not_provenance_bound" in result["computed"]["codex"][
        "issues"
    ]
    assert result["gate_statuses"]["timeline"] == "not_claimed"
    assert result["gate_statuses"]["semantic_restore"] == "not_claimed"
    assert result["gate_statuses"]["claude"] == "not_claimed"
    assert result["gate_statuses"]["opencode"] == "not_claimed"
    assert all(result["gate_statuses"][gate] != "not_claimed" for gate in CORE_GATES)


def test_validator_reads_actual_artifact_content_not_a_hash_only_text() -> None:
    report = _report()
    report["artifacts"][0]["content"] = "arbitrary text with a correct enclosing hash"
    report["artifacts"][0]["artifact_sha256"] = artifact_sha256(report["artifacts"][0])
    _refresh(report)
    with pytest.raises(SemanticEvidenceError, match="schema violation"):
        _validate(report)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("candidate_commit", "9" * 40),
        ("candidate_tree", "8" * 40),
        ("candidate_wheel_sha256", "7" * 64),
        ("candidate_sdist_sha256", "6" * 64),
    ],
)
def test_candidate_binding_mismatch_is_rejected(field: str, replacement: str) -> None:
    report = _report()
    report["candidate_binding"][field] = replacement
    _refresh(report)
    with pytest.raises(SemanticEvidenceError, match="binding mismatch"):
        _validate(report)


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("protocol_binding", "protocol_sha256", "9" * 64),
        ("threshold_binding", "threshold_sha256", "8" * 64),
        ("gold_binding", "gold_sha256", "7" * 64),
    ],
)
def test_protocol_threshold_and_gold_binding_mismatch_is_rejected(
    section: str, field: str, replacement: str
) -> None:
    report = _report()
    report[section][field] = replacement
    _refresh(report)
    with pytest.raises(SemanticEvidenceError, match="binding mismatch"):
        _validate(report)


def test_run_count_below_frozen_minimum_fails() -> None:
    report = _report()
    codex = next(item for item in report["artifacts"] if item["gate_id"] == "codex")
    codex["content"]["command"]["run_count"] = 2
    _refresh(report)
    result = _validate(report)
    assert result["status"] == "failed"
    assert result["gate_statuses"]["codex"] == "failed"
    assert result["claim_eligible"] is False


def test_nonzero_hard_zero_fails() -> None:
    report = _report()
    report["artifacts"][1]["content"]["hard_failures"][0]["count"] = 1
    _refresh(report)
    result = _validate(report)
    assert result["status"] == "failed"
    assert result["hard_zero"] is False
    assert result["gate_statuses"]["migration_recovery"] == "failed"


def test_threshold_failure_and_failure_inventory_are_semantic_failures() -> None:
    report = _report()
    observation = report["artifacts"][2]["content"]
    observation["thresholds"][0]["observed"] = 0.4
    observation["failure_inventory"] = [
        {"failure_id": "invalid_locator", "count": 1, "severity": "hard"}
    ]
    _refresh(report)
    result = _validate(report)
    assert result["gate_statuses"]["secret_host_isolation"] == "failed"
    assert any(
        "threshold_below_minimum" in item
        for item in result["computed"]["secret_host_isolation"]["issues"]
    )
    assert any(
        "failure_inventory_nonempty" in item
        for item in result["computed"]["secret_host_isolation"]["issues"]
    )


def test_report_cannot_weaken_frozen_threshold_or_omit_hard_zero_counter() -> None:
    weakened = _report()
    weakened["artifacts"][0]["content"]["thresholds"][0]["minimum"] = 0
    _refresh(weakened)
    with pytest.raises(SemanticEvidenceError, match="threshold bounds differ"):
        _validate(weakened)

    omitted = _report()
    omitted["artifacts"][4]["content"]["hard_failures"].pop()
    _refresh(omitted)
    with pytest.raises(SemanticEvidenceError, match="hard-zero inventory differs"):
        _validate(omitted)


def test_development_corpus_cannot_masquerade_as_final_blind() -> None:
    report = _report(role="final_blind")
    report["corpus"]["source"] = "repository"
    for artifact in report["artifacts"]:
        artifact["content"]["corpus"]["source"] = "repository"
    _refresh(report)
    with pytest.raises(SemanticEvidenceError, match="frozen and external"):
        _validate(report)


def test_development_role_is_not_release_evidence() -> None:
    report = _report(role="development")
    result = validate_report(report)
    assert result["status"] == "failed"
    assert result["release_ready"] is False
    assert result["claim_eligible"] is False


def test_secret_canary_is_rejected_even_when_counter_and_hash_are_consistent() -> None:
    report = _report()
    report["report_id"] = "DEEPLAW_TEST_AMBIENT_SECRET"
    _refresh(report)
    with pytest.raises(SemanticEvidenceError, match="secret canary"):
        _validate(report)


def test_private_absolute_path_is_rejected_even_when_hash_is_consistent() -> None:
    report = _report()
    observation = report["artifacts"][0]["content"]
    observation["applicability"] = "not_applicable"
    observation["not_applicable_reason"] = "/Users/private/evidence/gold.json"
    _refresh(report)
    with pytest.raises(SemanticEvidenceError, match="private absolute path"):
        _validate(report)


def test_artifact_self_digest_is_independent_of_report_digest() -> None:
    report = _report()
    report["artifacts"][0]["content"]["command"]["exit_code"] = 1
    _refresh(report)
    report["artifacts"][0]["content"]["command"]["exit_code"] = 0
    report["report_sha256"] = report_sha256(report)
    with pytest.raises(SemanticEvidenceError, match=r"artifact .* self digest"):
        _validate(report)


def test_path_validator_reads_the_json_bytes_and_keeps_status_deterministic(tmp_path: Path) -> None:
    report = _report()
    report_path = tmp_path / "commercial-evidence.json"
    report_path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    result = validate_report_file(report_path, expected=_expected())
    assert result["status"] == "failed"
    assert result["release_ready"] is False


def _write_asset(root: Path, logical_path: str, content: bytes) -> dict[str, Any]:
    path = root / logical_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "path": logical_path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "byte_size": len(content),
    }


def _release_inputs(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    root = tmp_path / "assets"
    root.mkdir()
    records = [
        _write_asset(root, "dist/deeplaw-0.13.0-py3-none-any.whl", b"wheel"),
        _write_asset(root, "dist/deeplaw-0.13.0.tar.gz", b"sdist"),
        _write_asset(root, "evidence/prd.md", b"prd"),
        _write_asset(root, "evidence/traceability.md", b"trace"),
        _write_asset(root, "evidence/protocol.json", b"protocol"),
        _write_asset(root, "evidence/thresholds.json", b"thresholds"),
        _write_asset(root, "evidence/human-gold.json", b"external-gold"),
        _write_asset(root, "evidence/isolation.json", b"isolation"),
        _write_asset(root, "evidence/classification.json", CLASSIFICATION_PATH.read_bytes()),
    ]
    by_path = {item["path"]: item for item in records}
    report = _report()
    report["candidate_binding"].update(
        {
            "candidate_wheel_sha256": by_path[
                "dist/deeplaw-0.13.0-py3-none-any.whl"
            ]["sha256"],
            "candidate_sdist_sha256": by_path["dist/deeplaw-0.13.0.tar.gz"]["sha256"],
        }
    )
    report["protocol_binding"]["protocol_sha256"] = by_path["evidence/protocol.json"][
        "sha256"
    ]
    report["threshold_binding"]["threshold_sha256"] = by_path[
        "evidence/thresholds.json"
    ]["sha256"]
    report["gold_binding"]["gold_sha256"] = by_path["evidence/human-gold.json"]["sha256"]
    for artifact in report["artifacts"]:
        artifact["content"]["bindings"].update(
            {
                "candidate_wheel_sha256": report["candidate_binding"][
                    "candidate_wheel_sha256"
                ],
                "candidate_sdist_sha256": report["candidate_binding"][
                    "candidate_sdist_sha256"
                ],
                "protocol_sha256": report["protocol_binding"]["protocol_sha256"],
                "threshold_sha256": report["threshold_binding"]["threshold_sha256"],
                "gold_sha256": report["gold_binding"]["gold_sha256"],
            }
        )
    _refresh(report)
    report_bytes = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    records.append(_write_asset(root, "evidence/semantic-report.json", report_bytes))
    by_path = {item["path"]: item for item in records}
    template: dict[str, Any] = {
        "schema_version": "deeplaw.commercial-release-manifest/v6",
        "environment": {
            "platform_system": "Darwin",
            "platform_release": "test",
            "platform_version": "test",
            "machine": "arm64",
            "python_implementation": "CPython",
            "python_version": "3.13.5",
            "python_executable_name": "python",
            "uv_version": "0.8.0",
            "ci": True,
            "github_actions": True,
            "github_runner_os": "macOS",
            "github_runner_arch": "ARM64",
        },
        "release": {
            "repository": "Eysn0130/DeepLaw",
            "version": "0.13.0",
            "tag": "v0.13.0",
            "commit": COMMIT,
            "tree": TREE,
        },
        "bindings": {
            "prd_path": "evidence/prd.md",
            "prd_sha256": by_path["evidence/prd.md"]["sha256"],
            "traceability_path": "evidence/traceability.md",
            "traceability_sha256": by_path["evidence/traceability.md"]["sha256"],
            "qualification_protocol_path": "evidence/protocol.json",
            "qualification_protocol_sha256": by_path["evidence/protocol.json"]["sha256"],
            "thresholds_path": "evidence/thresholds.json",
            "thresholds_sha256": by_path["evidence/thresholds.json"]["sha256"],
            "human_gold_manifest_path": "evidence/human-gold.json",
            "human_gold_manifest_sha256": by_path["evidence/human-gold.json"]["sha256"],
            "compiler_evaluator_isolation_path": "evidence/isolation.json",
            "compiler_evaluator_isolation_sha256": by_path["evidence/isolation.json"][
                "sha256"
            ],
            "gate_classification_path": "evidence/classification.json",
            "gate_classification_sha256": by_path["evidence/classification.json"]["sha256"],
            "candidate_commit": COMMIT,
            "candidate_tree": TREE,
            "candidate_wheel_sha256": by_path[
                "dist/deeplaw-0.13.0-py3-none-any.whl"
            ]["sha256"],
            "candidate_sdist_sha256": by_path["dist/deeplaw-0.13.0.tar.gz"]["sha256"],
            "candidate_version": "0.13.0",
        },
        "artifacts": records,
    }
    return template, report, root


def _manifest_record_sha256(manifest: dict[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "record_sha256"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_v013_assembler_rejects_legacy_self_report_bytes(tmp_path: Path) -> None:
    template, _report_value, root = _release_inputs(tmp_path)
    with pytest.raises(V013CommercialReleaseError, match="self-reported observations"):
        assemble_manifest(
            template,
            semantic_report_path="evidence/semantic-report.json",
            assets_root=root,
        )


def test_v013_provenance_assembler_remains_disabled_until_core_validators_exist(
    tmp_path: Path,
) -> None:
    template, _report_value, root = _release_inputs(tmp_path)
    report_path = root / "evidence/semantic-report.json"
    report_path.write_text(
        json.dumps({"schema_version": "deeplaw.commercial-evidence-report/v2"}),
        encoding="utf-8",
    )
    classification_path = root / "evidence/classification.json"
    classification_path.write_bytes(
        (REPOSITORY / "benchmarks/release/v013-gate-classification-v2.json").read_bytes()
    )
    for logical_path, path in (
        ("evidence/semantic-report.json", report_path),
        ("evidence/classification.json", classification_path),
    ):
        record = next(item for item in template["artifacts"] if item["path"] == logical_path)
        record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        record["byte_size"] = path.stat().st_size
    template["bindings"]["gate_classification_sha256"] = hashlib.sha256(
        classification_path.read_bytes()
    ).hexdigest()

    with pytest.raises(V013CommercialReleaseError, match="assembly remains disabled"):
        assemble_manifest(
            template,
            semantic_report_path="evidence/semantic-report.json",
            assets_root=root,
        )


def test_v013_assembler_rejects_caller_supplied_pass_decisions(tmp_path: Path) -> None:
    template, _report_value, root = _release_inputs(tmp_path)
    template["commercial_release_eligible"] = True
    with pytest.raises(V013CommercialReleaseError, match="must not supply"):
        assemble_manifest(
            template,
            semantic_report_path="evidence/semantic-report.json",
            assets_root=root,
        )


def test_manifest_pass_claim_is_rejected_when_actual_report_fails(tmp_path: Path) -> None:
    template, report, root = _release_inputs(tmp_path)
    report["artifacts"][0]["content"]["command"]["exit_code"] = 1
    _refresh(report)
    report_path = root / "evidence/semantic-report.json"
    report_path.write_text(
        json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    report_record = next(
        item for item in template["artifacts"] if item["path"] == "evidence/semantic-report.json"
    )
    report_record["sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    report_record["byte_size"] = report_path.stat().st_size
    with pytest.raises(V013CommercialReleaseError, match="self-reported observations"):
        assemble_manifest(
            template,
            semantic_report_path="evidence/semantic-report.json",
            assets_root=root,
        )


def test_publish_validator_rejects_forged_pass_receipt_for_legacy_report(
    tmp_path: Path,
) -> None:
    manifest, report, root = _release_inputs(tmp_path)
    category_by_gate = {
        item["gate_id"]: item["category"] for item in CLASSIFICATION["gates"]
    }
    statuses = [
        {
            "gate_id": gate_id,
            "category": category_by_gate[gate_id],
            "status": "passed",
        }
        for gate_id in CORE_GATES
    ]
    statuses.extend(
        {
            "gate_id": item["gate_id"],
            "category": item["category"],
            "status": "not_claimed",
        }
        for item in CLASSIFICATION["gates"]
        if item["category"] != "Core"
    )
    report_path = root / "evidence/semantic-report.json"
    manifest["semantic_evidence"] = {
        "report_path": "evidence/semantic-report.json",
        "report_artifact_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "report_record_sha256": report["report_sha256"],
        "report_kind": report["report_kind"],
        "status": "passed",
        "hard_zero": True,
        "release_ready": True,
        "claim_eligible": True,
        "competitive_claim_eligible": False,
        "gate_statuses": sorted(statuses, key=lambda item: item["gate_id"]),
    }
    manifest["commercial_release_eligible"] = True
    manifest["quality_protocol_eligible"] = True
    manifest["competitive_claim_eligible"] = False
    manifest["record_sha256"] = _manifest_record_sha256(manifest)

    with pytest.raises(SemanticEvidenceError, match="receipt differs"):
        validate_release_manifest_semantics(manifest, assets_root=root)


def test_assembler_rejects_hash_correct_inventory_for_different_wheel_bytes(
    tmp_path: Path,
) -> None:
    template, _report_value, root = _release_inputs(tmp_path)
    (root / "dist/deeplaw-0.13.0-py3-none-any.whl").write_bytes(b"different-wheel")
    with pytest.raises(V013CommercialReleaseError, match="actual bytes"):
        assemble_manifest(
            template,
            semantic_report_path="evidence/semantic-report.json",
            assets_root=root,
        )


def test_self_consistent_arbitrary_protocol_gold_and_distribution_bytes_cannot_qualify(
    tmp_path: Path,
) -> None:
    """Inventory self-consistency must not turn arbitrary bytes into release evidence."""

    # Reuse the existing shape-only fixture: its wheel, sdist, protocol, and
    # external-Gold files are arbitrary bytes whose inventory hashes agree.
    template, _report_value, root = _release_inputs(tmp_path)
    with pytest.raises(V013CommercialReleaseError, match="self-reported observations"):
        assemble_manifest(
            template,
            semantic_report_path="evidence/semantic-report.json",
            assets_root=root,
        )


def test_missing_core_is_not_claimed_and_is_not_a_pass() -> None:
    report = _report(declared_core=CORE_GATES[1:])
    result = _validate(report)
    assert result["gate_statuses"]["canonical_integrity"] == "not_executed"
    assert result["gate_statuses"]["canonical_integrity"] != "not_claimed"
    assert result["status"] == "failed"


def test_caller_supplied_passed_flag_is_rejected_by_closed_report() -> None:
    report = _report()
    report["passed"] = True
    _refresh(report)
    with pytest.raises(SemanticEvidenceError, match="schema violation"):
        _validate(report)


def test_report_kind_and_exact_environment_are_closed() -> None:
    report = _report()
    report["report_kind"] = "arbitrary_report"
    _refresh(report)
    with pytest.raises(SemanticEvidenceError, match="schema violation"):
        _validate(report)

    report = _report()
    report["artifacts"][0]["content"]["environment"]["host_secret"] = "redacted"
    _refresh(report)
    with pytest.raises(SemanticEvidenceError, match="schema violation"):
        _validate(report)


def test_model_required_gate_fails_without_exact_model_id() -> None:
    report = _report()
    codex = next(item for item in report["artifacts"] if item["gate_id"] == "codex")
    codex["content"]["environment"]["model_id"] = None
    _refresh(report)
    result = _validate(report)
    assert result["gate_statuses"]["codex"] == "failed"
    assert "exact_model_identity_missing" in result["computed"]["codex"]["issues"]


def test_fake_model_and_runner_cannot_make_a_closed_report_release_ready() -> None:
    """A self-consistent report must not certify a made-up model or runner."""

    report = _report()
    codex = next(item for item in report["artifacts"] if item["gate_id"] == "codex")
    assert codex["content"]["gate_id"] == "codex"
    codex["content"]["command"]["argv"] = [
        "definitely-not-a-real-runner",
        "claimed-codex-run",
    ]
    codex["content"]["environment"]["model_id"] = "made-up-model"
    _refresh(report)

    result = _validate(report)
    assert (result["release_ready"], result["claim_eligible"]) == (False, False), (
        "reproduced A: closed self-report with made-up model/argv was accepted as release "
        f"evidence ({result!r})"
    )


def test_one_observation_cannot_self_report_three_runs_and_clean_metrics() -> None:
    """run_count/threshold/hard-zero/redaction fields need independent raw evidence."""

    report = _report()
    codex_artifacts = [item for item in report["artifacts"] if item["gate_id"] == "codex"]
    assert len(codex_artifacts) == 1
    observation = codex_artifacts[0]["content"]
    observation["command"]["run_count"] = 3
    # This one Codex observation has no three distinct run IDs, host reports,
    # or scorer rows.  Thresholds, hard-zero counters, and redaction remain
    # self-reported fields in the same single artifact.
    assert "run_id" not in observation
    assert "run_ids" not in observation
    assert "raw_run_ids" not in observation
    assert "host_reports" not in observation
    assert "raw_runs" not in observation
    assert "scorer_rows" not in observation
    assert all(item["count"] == 0 for item in observation["hard_failures"])
    assert observation["redaction"] == {
        "secret_canary_count": 0,
        "private_path_count": 0,
        "output_redacted": True,
    }
    _refresh(report)

    result = _validate(report)
    assert result["release_ready"] is False, (
        "reproduced C: one observation self-reported run_count=3, passing thresholds, "
        f"zero hard failures, and clean redaction but remained release-ready ({result!r})"
    )


def test_input_bound_rejects_overlarge_arbitrary_text() -> None:
    report = _report()
    report["report_id"] = "x" * 8_193
    with pytest.raises(SemanticEvidenceError, match="bounded input size"):
        _validate(report)


def test_classification_fixture_keeps_explicit_capability_not_claimed_gates() -> None:
    by_id = {item["gate_id"]: item for item in CLASSIFICATION["gates"]}
    assert by_id["timeline"]["category"] == "Capability"
    assert by_id["semantic_restore"]["category"] == "Capability"
    assert by_id["claude"]["category"] == "Capability"
    assert by_id["opencode"]["category"] == "Capability"
    assert {by_id[gate]["category"] for gate in CORE_GATES} == {"Core"}
