from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from benchmarks.release.platform_gate import load_platform_manifest
from benchmarks.release.v013_commercial_release import assemble_manifest
from benchmarks.release.v013_gate_collection import (
    GateCollectionError,
    build_collection,
    validate_collection,
)
from benchmarks.release.v013_gate_validator import (
    GateValidationError,
    record_sha256,
    validate_gate,
)

REPOSITORY = Path(__file__).resolve().parents[1]
CLASSIFICATION_PATH = (
    REPOSITORY / "benchmarks/release/v013-gate-classification-v6.json"
)
PLATFORM_MANIFEST_PATH = (
    REPOSITORY / "benchmarks/release/platform-core-test-manifest-v2.json"
)
EVIDENCE_RUN_ID = 220013

_CI_CASES = {
    "canonical_integrity": [
        (
            "tests.test_knowledge_assets",
            "test_audit_chain_detects_database_tampering",
        ),
        (
            "tests.test_autonomous_knowledge",
            "test_doctor_includes_autonomous_canonical_integrity",
        ),
    ],
    "migration_recovery": [
        (
            "tests.test_knowledge_control",
            "test_interrupted_migration_rolls_back_and_retains_a_verified_backup",
        ),
        (
            "tests.test_v013_pass22_continuity_closure",
            "test_partial_checkpoint_recovers_after_process_exit_and_restart",
        ),
    ],
    "secret_host_isolation": [
        (
            "tests.test_v013_host_environment_isolation",
            "test_fake_mcp_child_cannot_see_ambient_or_provider_secret",
        ),
        (
            "tests.test_v013_pass21_task_routing_closure",
            "test_secret_looking_untracked_file_fails_closed_without_content_read",
        ),
    ],
}


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def _asset(root: Path, relative: str, payload: bytes) -> dict[str, Any]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_size": len(payload),
    }


def _json_asset(root: Path, relative: str, value: dict[str, Any]) -> dict[str, Any]:
    _write(root / relative, value)
    payload = (root / relative).read_bytes()
    return {
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_size": len(payload),
    }


def _candidate(active: dict[str, Any]) -> dict[str, str]:
    value = active["candidate_binding"]
    return {
        "commit": value["source_commit"],
        "tree": value["source_tree"],
        "lock_sha256": value["lock_sha256"],
        "wheel_sha256": value["wheel_sha256"],
        "sdist_sha256": value["sdist_sha256"],
    }


def _binding_fields(
    active: dict[str, Any], active_path: Path, *, role: str
) -> dict[str, Any]:
    if role == "development":
        gold = {
            "manifest_sha256": active["protocol_binding"]["sha256"],
            "role": role,
            "source": "repository",
            "independent": False,
        }
        corpus = {
            "sha256": hashlib.sha256(
                active["candidate_binding"]["source_tree"].encode("ascii")
            ).hexdigest(),
            "role": role,
            "source": "repository",
            "read_only": True,
        }
        isolation_sha256 = active["protocol_binding"]["sha256"]
    else:
        gold = {
            "manifest_sha256": active["external_inputs"][
                "human_gold_manifest_sha256"
            ],
            "role": role,
            "source": "repository_external",
            "independent": True,
        }
        corpus = {
            "sha256": active["external_inputs"][
                "qualification_holdout_sha256"
            ],
            "role": role,
            "source": "repository_external",
            "read_only": True,
        }
        isolation_sha256 = active["external_inputs"][
            "compiler_scorer_isolation_sha256"
        ]
    return {
        "candidate_version": active["candidate_version"],
        "candidate_binding": _candidate(active),
        "protocol_binding": {
            "protocol_id": active["protocol_binding"]["protocol_id"],
            "protocol_sha256": active["protocol_binding"]["sha256"],
            "active_qualification_sha256": hashlib.sha256(
                active_path.read_bytes()
            ).hexdigest(),
        },
        "gold_binding": gold,
        "corpus": corpus,
        "isolation": {
            "manifest_sha256": isolation_sha256,
            "source_mount_read_only": True,
            "compiler_gold_visible": False,
            "compiler_scorer_visible": False,
            "scorer_process_separate": True,
            "repository_source_visible": False,
            "ambient_credentials_visible": False,
        },
    }


def _source_evidence(
    definition: dict[str, Any], active: dict[str, Any], active_path: Path, root: Path
) -> dict[str, Any]:
    gate_id = definition["gate_id"]
    source_by_gate = {
        "canonical_integrity": "ci_junit",
        "migration_recovery": "ci_junit",
        "secret_host_isolation": "ci_junit",
        "bounded_context": "host_receipt",
        "legal_evidence": "legal_exact_source",
        "source_citation_locator": "legal_exact_source",
        "scale_performance": "scale_report",
        "supported_platforms": "platform",
        "reproducible_supply_chain": "reproducible_artifact",
        "human_gold_isolation": "human_gold_scorer",
        "codex": "host_receipt",
        "opencode": "host_receipt",
        "timeline": "timeline_receipt",
    }
    role = definition["required_corpus_roles"][0]
    platforms = definition["required_execution_platforms"]
    count = max(definition["minimum_distinct_run_count"], len(platforms))
    executions = []
    for index in range(count):
        platform = platforms[index % len(platforms)]
        host = definition["constraints"]["host"]
        model = definition["constraints"]["model_id"]
        tool_name = host or "deeplaw"
        tool_version = definition["constraints"]["tool_version"] or "0.13.0"
        argv = definition["constraints"]["argv_prefix"] or [
            "deeplaw",
            "qualification",
            gate_id,
        ]
        dimensions = {"lane": gate_id}
        if host is not None:
            dimensions.update(
                {
                    "host": host,
                    "model": str(model),
                    "task_case": f"case_{index + 1}",
                }
            )
        run_id = f"{gate_id}_run_{index + 1}"
        if source_by_gate[gate_id] in {"ci_junit", "platform"}:
            relative = f"evidence/source-{gate_id}-{index + 1}.xml"
            source_path = root / relative
            junit_root = ET.Element("testsuites")
            if gate_id == "supported_platforms":
                manifest = load_platform_manifest(PLATFORM_MANIFEST_PATH)
                cases = list(manifest["inventories"]["common"]["cases"])
                if platform["platform"] == "windows":
                    cases.extend(
                        manifest["inventories"]["windows"]["additional_cases"]
                    )
                identities = [
                    (case["junit"]["classname"], case["junit"]["name"])
                    for case in cases
                ]
            else:
                identities = _CI_CASES[gate_id]
            suite = ET.SubElement(
                junit_root,
                "testsuite",
                name=gate_id,
                tests=str(len(identities)),
                failures="0",
                errors="0",
                skipped="0",
            )
            for classname, name in identities:
                ET.SubElement(suite, "testcase", classname=classname, name=name)
            ET.ElementTree(junit_root).write(
                source_path,
                encoding="utf-8",
                xml_declaration=True,
            )
            source_format = "junit_xml"
        else:
            if gate_id == "bounded_context":
                facts: dict[str, Any] = {
                    "provider_bytes": 100,
                    "provider_hard_limit_bytes": 65_536,
                    "payload_admitted": True,
                    "secret_matches": 0,
                    "private_path_matches": 0,
                }
            elif gate_id == "legal_evidence":
                facts = {
                    "source_count": 28,
                    "exact_source_count": 28,
                    "false_authority_count": 0,
                    "wrong_version_primary_count": 0,
                    "invalid_quote_count": 0,
                    "invalid_locator_count": 0,
                    "protected_source_mutation_count": 0,
                    "cross_boundary_disclosure_count": 0,
                    "secret_matches": 0,
                }
            elif gate_id == "source_citation_locator":
                facts = {
                    "citation_count": 28,
                    "invalid_source_count": 0,
                    "invalid_quote_count": 0,
                    "invalid_locator_count": 0,
                }
            elif gate_id == "scale_performance":
                facts = {
                    "scale_1k_public_completed": True,
                    "scale_10k_public_completed": True,
                    "scale_100k_public_completed": True,
                    "private_bulk_api_used": False,
                    "second_store_used": False,
                }
            elif gate_id == "reproducible_supply_chain":
                facts = {
                    "first_wheel_sha256": active["candidate_binding"]["wheel_sha256"],
                    "second_wheel_sha256": active["candidate_binding"]["wheel_sha256"],
                    "candidate_wheel_sha256": active["candidate_binding"]["wheel_sha256"],
                    "first_sdist_sha256": active["candidate_binding"]["sdist_sha256"],
                    "second_sdist_sha256": active["candidate_binding"]["sdist_sha256"],
                    "candidate_sdist_sha256": active["candidate_binding"]["sdist_sha256"],
                    "sbom_verified": True,
                    "openvex_verified": True,
                    "licenses_verified": True,
                    "provenance_verified": True,
                    "signature_verified": True,
                    "public_redownload_verified": True,
                    "secret_matches": 0,
                    "private_path_matches": 0,
                }
            elif gate_id == "human_gold_isolation":
                facts = {
                    "source_mount_read_only": True,
                    "compiler_gold_visible": False,
                    "compiler_scorer_visible": False,
                    "scorer_process_separate": True,
                    "repository_source_visible": False,
                    "ambient_credentials_visible": False,
                    "evaluator_output_mutations": 0,
                    "blind_contaminations": 0,
                }
            elif gate_id in {"codex", "opencode"}:
                corpus_role = (
                    "qualification_holdout" if index < 2 else "final_blind"
                )
                corpus_key = (
                    "qualification_holdout_sha256"
                    if corpus_role == "qualification_holdout"
                    else "final_blind_holdout_sha256"
                )
                facts = {
                    "binary_sha256": "9" * 64,
                    "native_receipt_sha256": "8" * 64,
                    "response_model_id": str(model),
                    "corpus_role": corpus_role,
                    "corpus_sha256": active["external_inputs"][corpus_key],
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
            elif gate_id == "timeline":
                facts = {
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
                raise AssertionError(f"missing source fixture for {gate_id}")
            observation: dict[str, Any] = {
                "schema_version": "deeplaw.v013-source-observation/v1",
                "evidence_source": source_by_gate[gate_id],
                "gate_id": gate_id,
                "run_id": run_id,
                "candidate_binding": _candidate(active),
                "facts": facts,
            }
            observation["record_sha256"] = record_sha256(observation)
            relative = f"evidence/source-{gate_id}-{index + 1}.json"
            source_path = root / relative
            _write(source_path, observation)
            source_format = "source_observation_v1"
        executions.append(
            {
                "run_id": run_id,
                "argv": argv,
                "os_name": platform["platform"],
                "python_version": platform["python_version"],
                "tool_name": tool_name,
                "tool_version": tool_version,
                "model_id": model,
                "reasoning_effort": (
                    active["host_constraints"][host]["reasoning_effort"]
                    if host is not None
                    else None
                ),
                "dimensions": dimensions,
                "source": {
                    "relative_path": relative,
                    "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                    "format": source_format,
                },
            }
        )
    value: dict[str, Any] = {
        "schema_version": "deeplaw.v013-gate-source-evidence/v1",
        "artifact_kind": definition["artifact_kinds"][0],
        "evidence_source": source_by_gate[gate_id],
        "gate_id": gate_id,
        **_binding_fields(active, active_path, role=role),
        "workflow_provenance": {
            "repository": "Eysn0130/DeepLaw",
            "workflow_name": "External Qualification Evidence",
            "workflow_path": ".github/workflows/external-qualification-evidence.yml",
            "workflow_run_id": EVIDENCE_RUN_ID,
            "head_sha": active["candidate_binding"]["source_commit"],
            "event": "workflow_dispatch",
            "runner_environment": "self-hosted-macos-qualification",
        },
        "executions": executions,
    }
    value["record_sha256"] = record_sha256(value)
    return value


def _selective_forget(active: dict[str, Any], active_path: Path) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": "deeplaw.selective-forget-qualification/v1",
        "artifact_kind": "selective-forget-raw-receipt",
        **{
            key: item
            for key, item in _binding_fields(
                active, active_path, role="development"
            ).items()
            if key in {"candidate_version", "candidate_binding", "protocol_binding"}
        },
        "run_id": "selective_forget_run_1",
        "checkpoint": {
            "status": "checkpointed",
            "knowledge_id": "knowledge_target",
            "revision_id": "revision_checkpoint",
            "run_id": "selective_forget_run_1",
            "write_performed": True,
        },
        "forget": {
            "status": "forgotten",
            "knowledge_id": "knowledge_target",
            "expected_revision_id": "revision_checkpoint",
            "tombstone_revision_id": "revision_tombstone",
            "write_performed": True,
        },
        "post_forget_resume": {
            "status": "gap",
            "selected_knowledge_ids": [],
            "gap_codes": ["forgotten"],
            "provider_bytes": 1,
            "absolute_path_count": 0,
            "secret_count": 0,
            "write_performed": False,
        },
        "control_resume": {
            "status": "admitted",
            "selected_knowledge_ids": ["knowledge_control"],
            "gap_codes": [],
            "provider_bytes": 1,
            "absolute_path_count": 0,
            "secret_count": 0,
            "write_performed": False,
        },
        "ledger": {
            "head_before_checkpoint": None,
            "head_after_checkpoint": "4" * 64,
            "head_after_forget": "5" * 64,
            "head_after_reads": "5" * 64,
            "write_event_types": [
                "knowledge_run_recorded",
                "knowledge_revision_committed",
                "knowledge_revision_committed",
            ],
        },
    }
    receipt["record_sha256"] = record_sha256(receipt)
    envelope: dict[str, Any] = {
        "schema_version": "deeplaw.v013-selective-forget-evidence/v1",
        "workflow_provenance": {
            "repository": "Eysn0130/DeepLaw",
            "workflow_name": "External Qualification Evidence",
            "workflow_path": ".github/workflows/external-qualification-evidence.yml",
            "workflow_run_id": EVIDENCE_RUN_ID,
            "head_sha": active["candidate_binding"]["source_commit"],
            "event": "workflow_dispatch",
            "runner_environment": "self-hosted-macos-qualification",
        },
        "receipt": receipt,
    }
    envelope["record_sha256"] = record_sha256(envelope)
    return envelope


def _qualification_assets(tmp_path: Path) -> tuple[Path, Path, dict[str, Any], list[Path]]:
    root = tmp_path / "assets"
    root.mkdir()
    wheel = _asset(root, "dist/deeplaw-0.13.0-py3-none-any.whl", b"wheel")
    sdist = _asset(root, "dist/deeplaw-0.13.0.tar.gz", b"sdist")
    human = _asset(root, "evidence/human-gold.json", b"external-gold")
    isolation = _asset(root, "evidence/isolation.json", b"isolation")
    thresholds = _asset(root, "evidence/thresholds.json", b"thresholds")
    active: dict[str, Any] = {
        "schema_version": "deeplaw.v013-active-qualification/v1",
        "qualification_id": "deeplaw-v013-active-commercial-candidate",
        "status": "frozen_exact_candidate",
        "candidate_version": "0.13.0",
        "protocol_binding": {
            "protocol_id": "deeplaw-v013-source-candidate-qualification",
            "schema_version": "deeplaw.v013-qualification-protocol/v1",
            "relative_path": "benchmarks/v013/qualification-protocol-v1.json",
            "sha256": thresholds["sha256"],
        },
        "candidate_binding": {
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "lock_sha256": "c" * 64,
            "wheel_filename": wheel["path"].rsplit("/", 1)[-1],
            "wheel_sha256": wheel["sha256"],
            "sdist_filename": sdist["path"].rsplit("/", 1)[-1],
            "sdist_sha256": sdist["sha256"],
            "artifact_manifest_sha256": "3" * 64,
            "source_date_epoch": 1_786_838_400,
        },
        "external_inputs": {
            "human_gold_manifest_sha256": human["sha256"],
            "qualification_holdout_sha256": human["sha256"],
            "final_blind_holdout_sha256": human["sha256"],
            "compiler_scorer_isolation_sha256": isolation["sha256"],
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
    active_path = root / "evidence/active.json"
    _write(active_path, active)
    classification = json.loads(CLASSIFICATION_PATH.read_text(encoding="utf-8"))
    results: list[Path] = []
    for definition in classification["gates"]:
        if definition["category"] != "Core":
            continue
        gate_id = definition["gate_id"]
        raw = (
            _selective_forget(active, active_path)
            if gate_id == "selective_forget"
            else _source_evidence(definition, active, active_path, root)
        )
        raw_path = root / f"evidence/raw-{gate_id}.json"
        _write(raw_path, raw)
        result = validate_gate(
            gate_id,
            [raw_path],
            root=root,
            active_path=active_path,
            classification_path=CLASSIFICATION_PATH,
            expected_evidence_run_id=EVIDENCE_RUN_ID,
        )
        assert result["status"] == "passed", (gate_id, result["failures"])
        result_path = root / f"evidence/result-{gate_id}.json"
        _write(result_path, result)
        results.append(result_path)
    return root, active_path, active, results


def _inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "byte_size": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def test_complete_v6_collection_assembles_commercial_manifest(tmp_path: Path) -> None:
    root, active_path, active, results = _qualification_assets(tmp_path)
    collection = build_collection(
        results,
        root=root,
        report_id="pass22-complete-v6",
        active_path=active_path,
        classification_path=CLASSIFICATION_PATH,
        expected_evidence_run_id=EVIDENCE_RUN_ID,
    )
    collection_path = root / "evidence/gate-collection.json"
    _write(collection_path, collection)
    validated = validate_collection(
        collection_path,
        root=root,
        active_path=active_path,
        classification_path=CLASSIFICATION_PATH,
        expected_evidence_run_id=EVIDENCE_RUN_ID,
    )
    assert validated["release_ready"] is True
    assert validated["claim_eligible"] is True
    assert len(
        [status for status in validated["gate_statuses"].values() if status == "passed"]
    ) == 14

    _asset(root, "evidence/prd.md", b"prd")
    _asset(root, "evidence/traceability.md", b"traceability")
    _asset(root, "evidence/classification.json", CLASSIFICATION_PATH.read_bytes())
    artifacts = _inventory(root)
    by_path = {item["path"]: item for item in artifacts}
    template = {
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
            "commit": active["candidate_binding"]["source_commit"],
            "tree": active["candidate_binding"]["source_tree"],
        },
        "bindings": {
            "prd_path": "evidence/prd.md",
            "prd_sha256": by_path["evidence/prd.md"]["sha256"],
            "traceability_path": "evidence/traceability.md",
            "traceability_sha256": by_path["evidence/traceability.md"]["sha256"],
            "qualification_protocol_path": "evidence/active.json",
            "qualification_protocol_sha256": by_path["evidence/active.json"]["sha256"],
            "thresholds_path": "evidence/thresholds.json",
            "thresholds_sha256": by_path["evidence/thresholds.json"]["sha256"],
            "human_gold_manifest_path": "evidence/human-gold.json",
            "human_gold_manifest_sha256": by_path["evidence/human-gold.json"]["sha256"],
            "compiler_evaluator_isolation_path": "evidence/isolation.json",
            "compiler_evaluator_isolation_sha256": by_path["evidence/isolation.json"][
                "sha256"
            ],
            "gate_classification_path": "evidence/classification.json",
            "gate_classification_sha256": by_path["evidence/classification.json"][
                "sha256"
            ],
            "candidate_commit": active["candidate_binding"]["source_commit"],
            "candidate_tree": active["candidate_binding"]["source_tree"],
            "candidate_wheel_sha256": active["candidate_binding"]["wheel_sha256"],
            "candidate_sdist_sha256": active["candidate_binding"]["sdist_sha256"],
            "candidate_version": "0.13.0",
        },
        "artifacts": artifacts,
    }
    manifest = assemble_manifest(
        template,
        semantic_report_path="evidence/gate-collection.json",
        assets_root=root,
        expected_evidence_run_id=EVIDENCE_RUN_ID,
    )
    assert manifest["commercial_release_eligible"] is True
    assert manifest["quality_protocol_eligible"] is True
    assert manifest["competitive_claim_eligible"] is False


def test_v6_collection_rejects_missing_core(tmp_path: Path) -> None:
    root, active_path, _active, results = _qualification_assets(tmp_path)
    with pytest.raises(GateCollectionError, match="exactly every Core Gate"):
        build_collection(
            results[:-1],
            root=root,
            report_id="pass22-missing-core",
            active_path=active_path,
            classification_path=CLASSIFICATION_PATH,
            expected_evidence_run_id=EVIDENCE_RUN_ID,
        )


def test_v6_validator_rejects_noncanonical_classification(tmp_path: Path) -> None:
    root, active_path, _active, _results = _qualification_assets(tmp_path)
    classification = json.loads(CLASSIFICATION_PATH.read_text(encoding="utf-8"))
    classification["gates"][0]["validator_version"] = "1.0.1"
    altered = root / "evidence/altered-classification.json"
    _write(altered, classification)
    with pytest.raises(GateValidationError, match="differs from canonical v6 bytes"):
        validate_gate(
            "timeline",
            [root / "evidence/raw-timeline.json"],
            root=root,
            active_path=active_path,
            classification_path=altered,
            expected_evidence_run_id=EVIDENCE_RUN_ID,
        )


def test_v6_collection_derives_hard_failure_from_mismatched_observation(
    tmp_path: Path,
) -> None:
    root, active_path, _active, results = _qualification_assets(tmp_path)
    raw_path = root / "evidence/raw-canonical_integrity.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    source_path = root / raw["executions"][0]["source"]["relative_path"]
    junit_root = ET.Element("testsuites")
    suite = ET.SubElement(junit_root, "testsuite", tests="2")
    for index, (classname, name) in enumerate(_CI_CASES["canonical_integrity"]):
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if index == 0:
            ET.SubElement(case, "failure").text = "mismatch"
    ET.ElementTree(junit_root).write(source_path, encoding="utf-8")
    raw["executions"][0]["source"]["sha256"] = hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    raw["record_sha256"] = record_sha256(raw)
    _write(raw_path, raw)
    failed = validate_gate(
        "canonical_integrity",
        [raw_path],
        root=root,
        active_path=active_path,
        classification_path=CLASSIFICATION_PATH,
        expected_evidence_run_id=EVIDENCE_RUN_ID,
    )
    assert failed["status"] == "failed"
    assert failed["hard_failures"] == [
        {
            "failure_id": "canonical_integrity_failure",
            "count": 1,
            "maximum_allowed": 0,
        }
    ]
    result_path = root / "evidence/result-canonical_integrity.json"
    _write(result_path, failed)
    collection = build_collection(
        results,
        root=root,
        report_id="pass22-hard-failure",
        active_path=active_path,
        classification_path=CLASSIFICATION_PATH,
        expected_evidence_run_id=EVIDENCE_RUN_ID,
    )
    validation = validate_collection(
        collection,
        root=root,
        active_path=active_path,
        classification_path=CLASSIFICATION_PATH,
        expected_evidence_run_id=EVIDENCE_RUN_ID,
    )
    assert validation["release_ready"] is False
    assert validation["claim_eligible"] is False


def test_unrelated_passing_junit_cannot_validate_core_gate(tmp_path: Path) -> None:
    root, active_path, _active, _results = _qualification_assets(tmp_path)
    raw_path = root / "evidence/raw-canonical_integrity.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    source_path = root / raw["executions"][0]["source"]["relative_path"]
    source_path.write_text(
        '<testsuite name="unrelated"><testcase classname="tests.test_unrelated" '
        'name="test_passes"/></testsuite>\n',
        encoding="utf-8",
    )
    raw["executions"][0]["source"]["sha256"] = hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    raw["record_sha256"] = record_sha256(raw)
    _write(raw_path, raw)

    with pytest.raises(GateValidationError, match="Gate-specific public seam inventory"):
        validate_gate(
            "canonical_integrity",
            [raw_path],
            root=root,
            active_path=active_path,
            classification_path=CLASSIFICATION_PATH,
            expected_evidence_run_id=EVIDENCE_RUN_ID,
        )


def test_reproducible_gate_derives_candidate_artifact_mismatch(tmp_path: Path) -> None:
    root, active_path, _active, _results = _qualification_assets(tmp_path)
    raw_path = root / "evidence/raw-reproducible_supply_chain.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    source_path = root / raw["executions"][0]["source"]["relative_path"]
    observation = json.loads(source_path.read_text(encoding="utf-8"))
    observation["facts"]["second_wheel_sha256"] = "0" * 64
    observation["record_sha256"] = record_sha256(observation)
    _write(source_path, observation)
    raw["executions"][0]["source"]["sha256"] = hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    raw["record_sha256"] = record_sha256(raw)
    _write(raw_path, raw)

    result = validate_gate(
        "reproducible_supply_chain",
        [raw_path],
        root=root,
        active_path=active_path,
        classification_path=CLASSIFICATION_PATH,
        expected_evidence_run_id=EVIDENCE_RUN_ID,
    )
    assert result["status"] == "failed"
    assert result["hard_failures"][0] == {
        "failure_id": "artifact_hash_mismatch",
        "count": 1,
        "maximum_allowed": 0,
    }


def test_v6_collection_reopens_retained_source_artifact_bytes(tmp_path: Path) -> None:
    root, active_path, _active, results = _qualification_assets(tmp_path)
    collection = build_collection(
        results,
        root=root,
        report_id="pass22-artifact-binding",
        active_path=active_path,
        classification_path=CLASSIFICATION_PATH,
        expected_evidence_run_id=EVIDENCE_RUN_ID,
    )
    raw_path = root / "evidence/raw-timeline.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    source_path = root / raw["executions"][0]["source"]["relative_path"]
    source_path.write_bytes(source_path.read_bytes() + b" ")
    with pytest.raises(GateCollectionError, match="did not reproduce"):
        validate_collection(
            collection,
            root=root,
            active_path=active_path,
            classification_path=CLASSIFICATION_PATH,
            expected_evidence_run_id=EVIDENCE_RUN_ID,
        )
