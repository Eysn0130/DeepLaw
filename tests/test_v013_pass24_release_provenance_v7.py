from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import benchmarks.release.release_provenance_v7 as provenance
from benchmarks.release.release_provenance_v7 import (
    ReleaseProvenanceV7Error,
    _canonical_digest,
    validate_release_provenance,
)

SHA = "a" * 64
COMMIT = "1" * 40
TREE = "2" * 40


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _threshold_sha256(gold: dict[str, Any]) -> str:
    return _digest(
        json.dumps(
            gold["thresholds"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )


def _write(path: Path, value: Any, *, newline: bool = True) -> bytes:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if newline:
        raw += b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _record(value: dict[str, Any], field: str = "record_sha256") -> dict[str, Any]:
    value[field] = _canonical_digest(value, excluded=field)
    return value


def _semantic_gold() -> dict[str, Any]:
    labels = [
        {"label_id": "include_answer", "description": "Expected bounded answer"},
        {"label_id": "exclude_unbound", "description": "Must remain excluded"},
    ]
    case_template = {
        "labels": ["include_answer", "exclude_unbound"],
        "expected": {"include": ["include_answer"], "exclude": ["exclude_unbound"]},
        "duties": ["exact_citation"],
        "hard_failures": ["false_authority"],
        "thresholds": {
            "minimum_case_pass_rate": 1.0,
            "minimum_duty_coverage": 1.0,
            "maximum_hard_failures": 0,
            "maximum_false_authority": 0,
        },
    }
    cases = []
    for suffix in ("cold", "resume", "compact"):
        case = dict(case_template)
        case["case_id"] = f"goldcase_{suffix}"
        case["labels"] = list(case_template["labels"])
        case["expected"] = dict(case_template["expected"])
        cases.append(case)
    return {
        "schema_version": "deeplaw.semantic-human-gold/v3",
        "status": "semantic_human_gold_frozen",
        "frozen_at": "2026-08-16T00:00:00Z",
        "gold_id": "semanticgold_0123456789abcdef01234567",
        "model_outputs_seen_before_freeze": False,
        "candidate_visible_when_frozen": False,
        "claim_eligible": False,
        "author": {"identity": "human-author:primary", "role": "human_author"},
        "human_approval": {
            "attestation_type": "external_human_attestation",
            "attestation_identity": "attestor:independent-reviewer",
            "attestation_digest": SHA,
            "approval_record": {
                "record_id": "approval:semantic-gold-v3",
                "record_sha256": SHA,
                "issuer": "issuer:human-review-board",
            },
            "approved_at": "2026-08-16T00:01:00Z",
            "decision": "approved",
        },
        "labels": labels,
        "cases": cases,
        "duties": [{"duty_id": "exact_citation", "description": "Exact evidence identity."}],
        "hard_failures": [{"code": "false_authority", "description": "No false authority."}],
        "thresholds": {
            "minimum_case_pass_rate": 1.0,
            "minimum_duty_coverage": 1.0,
            "maximum_hard_failures": 0,
            "maximum_false_authority": 0,
        },
    }


def _gate_result(
    candidate: dict[str, str],
    protocol_sha: str,
    classification: dict[str, Any],
) -> dict[str, Any]:
    validator_source = provenance._tracked_file_binding(provenance._GATE_VALIDATOR_SOURCE)
    validator_executable = provenance._tracked_file_binding(
        provenance._GATE_VALIDATOR_EXECUTABLE
    )
    result: dict[str, Any] = {
        "schema_version": "deeplaw.provenance-bound-gate-result/v3",
        "qualification_run_id": 130003,
        "gate_id": candidate["gate_id"],
        "category": "Core",
        "validator_id": provenance._GATE_VALIDATOR_ID,
        "validator_version": provenance._GATE_VALIDATOR_VERSION,
        "validator_source": validator_source,
        "validator_executable": validator_executable,
        "classification_binding": {
            "classification_id": classification["classification_id"],
            "classification_schema_version": classification["schema_version"],
            "classification_sha256": candidate["classification_sha256"],
        },
        "candidate_binding": {
            "candidate_commit": candidate["commit"],
            "candidate_tree": candidate["tree"],
            "candidate_wheel_sha256": candidate["wheel_sha256"],
            "candidate_sdist_sha256": candidate["sdist_sha256"],
        },
        "protocol_binding": {
            "protocol_id": "deeplaw-v013-source-candidate-qualification",
            "protocol_sha256": protocol_sha,
            "frozen": True,
        },
        "threshold_binding": {
            "threshold_id": provenance._SEMANTIC_GOLD_THRESHOLD_ID,
            "threshold_sha256": candidate["threshold_sha256"],
            "frozen": True,
        },
        "gold_binding": {
            "gold_sha256": candidate["semantic_gold_sha256"],
            "role": "qualification_gold",
            "source": "repository_external",
            "frozen": True,
        },
        "corpus": {
            "role": "qualification_holdout",
            "source": "repository_external",
            "sha256": candidate["holdout_sha256"],
            "frozen": True,
        },
        "status": "passed",
        "executions": [
            {
                "run_id": "run:bounded-context",
                "workflow_run_id": 130002,
                "input_refs": ["input:one"],
                "evidence_kind": "context_capsule_selection_usage",
            }
        ],
        "run_ids": ["run:bounded-context"],
        "metrics": [
            {
                "metric": "receipt_count",
                "observed": 1.0,
                "input_refs": ["input:one"],
            }
        ],
        "hard_failures": [
            {
                "failure_id": "input:one:synthetic",
                "count": 0,
                "maximum_allowed": 0,
                "input_refs": ["input:one"],
            }
        ],
        "inputs": [
            {
                "input_id": "input:one",
                "relative_path": "external-evidence/evidence/input.json",
                "byte_size": candidate["input_size"],
                "file_sha256": candidate["input_sha256"],
                "schema_version": "input.v1",
                "record_sha256": candidate["input_record_sha256"],
                "artifact_kind": "sanitized_supporting_receipt",
            }
        ],
    }
    result["result_sha256"] = _canonical_digest(result, excluded="result_sha256")
    return result


def _typed_junit_manifest(
    root: Path,
    candidate: dict[str, Any],
    *,
    holdout_sha256: str,
    runner: dict[str, str],
    scorer: dict[str, str],
) -> tuple[Path, dict[str, Any]]:
    typed_root = root / "typed/candidate-junit"
    typed_root.mkdir(parents=True)
    junit_path = typed_root / "candidate.xml"
    junit_raw = (
        b'<testsuites><testsuite><testcase classname="typed" name="pass"/>'
        b"</testsuite></testsuites>"
    )
    junit_path.write_bytes(junit_raw)
    source = {
        "relative_path": "candidate.xml",
        "byte_size": len(junit_raw),
        "sha256": _digest(junit_raw),
        "media_type": "application/xml",
    }
    envelope: dict[str, Any] = {
        "schema_version": "deeplaw.typed-qualification-evidence/v1",
        "kind": "candidate_full_junit",
        "candidate_binding": {
            key: candidate[key]
            for key in ("commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256")
        },
        "run_binding": {"run_id": "run:candidate-junit", "workflow_run_id": 130001},
        "corpus": {"sha256": holdout_sha256, "role": "candidate_full"},
        "runner": runner,
        "scorer": scorer,
        "payload": {"source": source},
        "record_sha256": "",
    }
    envelope["record_sha256"] = _canonical_digest(envelope, excluded="record_sha256")
    manifest_path = typed_root / "manifest.json"
    _write(manifest_path, envelope, newline=False)
    derived = {
        "schema_version": "deeplaw.typed-qualification-derived/v1",
        "kind": "candidate_full_junit",
        "status": "passed",
        "metrics": {
            "testcase_count": 1,
            "successful_testcase_count": 1,
            "identity_sha256": _digest(
                json.dumps(
                    ["typed::pass"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ),
        },
        "hard_failure_counts": {"junit_failure": 0, "junit_skip": 0},
        "evidence_record_sha256": envelope["record_sha256"],
    }
    return manifest_path, derived


def _seed(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "assets"
    root.mkdir()
    external_root = root / "external-evidence"
    external_root.mkdir()
    candidate_raw_root = tmp_path / "candidate-raw"
    candidate_raw_root.mkdir()
    protocol = {"schema_version": "deeplaw.v013-qualification-protocol/v1", "status": "frozen"}
    protocol_raw = _write(root / "benchmarks/v013/qualification-protocol-v1.json", protocol)
    protocol_sha = _digest(protocol_raw)

    wheel_raw = b"exact-wheel-bytes\x00"
    sdist_raw = b"exact-sdist-bytes\x00"
    (root / "dist").mkdir()
    (root / "dist/deeplaw-0.13.0-py3-none-any.whl").write_bytes(wheel_raw)
    (root / "dist/deeplaw-0.13.0.tar.gz").write_bytes(sdist_raw)
    (external_root / "retained").mkdir()
    (external_root / "retained/deeplaw-0.13.0-py3-none-any.whl").write_bytes(wheel_raw)
    (external_root / "retained/deeplaw-0.13.0.tar.gz").write_bytes(sdist_raw)
    retained_manifest_raw = _write(
        external_root / "retained/manifest.json",
        {"wheel": _digest(wheel_raw), "sdist": _digest(sdist_raw)},
    )

    semantic_gold_value = _semantic_gold()
    semantic_gold_raw = _write(external_root / "evidence/semantic-gold.json", semantic_gold_value)
    semantic_gold_sha = _digest(semantic_gold_raw)
    candidate_full_raw = b"candidate-full-test-corpus"
    holdout_raw = b"qualification-holdout"
    blind_raw = b"final-blind"
    scorer_raw = b"independent-scorer"
    runner_raw = b"isolated-runner"
    isolation_raw = b"compiler-scorer-isolation"
    for name, raw in {
        "holdout.txt": holdout_raw,
        "blind.txt": blind_raw,
        "candidate-full.txt": candidate_full_raw,
        "scorer.txt": scorer_raw,
        "runner.txt": runner_raw,
        "isolation.txt": isolation_raw,
    }.items():
        (external_root / "evidence" / name).parent.mkdir(parents=True, exist_ok=True)
        (external_root / "evidence" / name).write_bytes(raw)

    source_classification_path = (
        Path(__file__).resolve().parents[1] / "benchmarks/release/v013-gate-classification-v7.json"
    )
    classification = json.loads(source_classification_path.read_text(encoding="utf-8"))
    classification_path = tmp_path / "synthetic-v013-gate-classification-v7.json"
    _write(classification_path, classification)
    provenance._CURRENT_CLASSIFICATION_PATH = classification_path
    classification_raw = classification_path.read_bytes()
    classification_sha = _digest(classification_raw)

    input_receipt = _record(
        {"schema_version": "input.v1", "kind": "raw-receipt", "value": "observed"}
    )
    input_path = external_root / "evidence/input.json"
    input_raw = _write(input_path, input_receipt)

    descriptor_public_key = b"d" * 32
    descriptor = {
        "identity": "external-human-approver",
        "key_id": _digest(descriptor_public_key),
        "public_key_b64": base64.b64encode(descriptor_public_key).decode("ascii"),
    }
    descriptor_path = root / "trusted-human-approver.json"
    _write(descriptor_path, descriptor)

    active = {
        "schema_version": "deeplaw.v013-active-qualification/v1",
        "qualification_id": "deeplaw-v013-active-commercial-candidate",
        "status": "frozen_exact_candidate",
        "candidate_version": "0.13.0",
        "protocol_binding": {
            "protocol_id": "deeplaw-v013-source-candidate-qualification",
            "schema_version": "deeplaw.v013-qualification-protocol/v1",
            "relative_path": "benchmarks/v013/qualification-protocol-v1.json",
            "sha256": protocol_sha,
        },
        "candidate_binding": {
            "source_commit": COMMIT,
            "source_tree": TREE,
            "lock_sha256": SHA,
            "wheel_filename": "deeplaw-0.13.0-py3-none-any.whl",
            "wheel_sha256": _digest(wheel_raw),
            "sdist_filename": "deeplaw-0.13.0.tar.gz",
            "sdist_sha256": _digest(sdist_raw),
            "artifact_manifest_sha256": _digest(retained_manifest_raw),
            "source_date_epoch": 1_786_838_400,
        },
        "external_inputs": {
            "human_gold_manifest_sha256": semantic_gold_sha,
            "qualification_holdout_sha256": _digest(holdout_raw),
            "final_blind_holdout_sha256": _digest(blind_raw),
            "compiler_scorer_isolation_sha256": _digest(isolation_raw),
        },
        "host_constraints": {
            "codex": {
                "tool_version": "codex-cli 0.148.0-alpha.9",
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

    candidate = {
        "commit": COMMIT,
        "tree": TREE,
        "lock_sha256": SHA,
        "wheel_sha256": _digest(wheel_raw),
        "sdist_sha256": _digest(sdist_raw),
        "candidate_full_sha256": _digest(candidate_full_raw),
        "semantic_gold_sha256": semantic_gold_sha,
        "holdout_sha256": _digest(holdout_raw),
        "blind_sha256": _digest(blind_raw),
        "classification_sha256": classification_sha,
        "threshold_sha256": _threshold_sha256(semantic_gold_value),
        "input_sha256": _digest(input_raw),
        "input_size": len(input_raw),
        "input_record_sha256": input_receipt["record_sha256"],
        "gate_id": classification["categories"][0]["gate_ids"][0],
    }
    candidate_gold = {
        "schema_version": "deeplaw.candidate-gold-binding-receipt/v1",
        "status": "post_build_candidate_gold_bound",
        "bound_at": "2026-08-16T01:00:00Z",
        "semantic_gold": {
            "gold_id": "semanticgold_0123456789abcdef01234567",
            "schema_version": "deeplaw.semantic-human-gold/v3",
            "sha256": candidate["semantic_gold_sha256"],
        },
        "candidate": {k: candidate[k] for k in ("commit", "tree", "lock_sha256")},
        "artifacts": {
            "wheel": {
                "name": "deeplaw-0.13.0-py3-none-any.whl",
                "sha256": candidate["wheel_sha256"],
                "byte_size": len(wheel_raw),
            },
            "sdist": {
                "name": "deeplaw-0.13.0.tar.gz",
                "sha256": candidate["sdist_sha256"],
                "byte_size": len(sdist_raw),
            },
        },
        "holdout": {"role": "qualification_holdout", "sha256": candidate["holdout_sha256"]},
        "blind": {"role": "final_blind", "sha256": candidate["blind_sha256"]},
        "scorer": {"identity": "scorer:pass24-v1", "sha256": _digest(scorer_raw)},
        "runner": {"identity": "runner:pass24-v1", "sha256": _digest(runner_raw)},
    }
    candidate_gold_path = external_root / "receipts/candidate-gold.json"
    candidate_gold_raw = _write(candidate_gold_path, _record(candidate_gold))

    supply_paths: dict[str, tuple[str, bytes]] = {}
    for name in ("sbom.json", "openvex.json", "licenses.json", "provenance.json"):
        raw = _write(external_root / "supply" / name, {"name": name})
        supply_paths[name] = (f"external-evidence/supply/{name}", raw)
    pre_publish = {
        "schema_version": "deeplaw.pre-publish-artifact-gate/v1",
        "status": "pre_publish_passed",
        "created_at": "2026-08-16T02:00:00Z",
        "candidate": {k: candidate[k] for k in ("commit", "tree", "lock_sha256")},
        "builds": {
            "count": 2,
            "byte_identical": True,
            "first": {
                "build_id": "first",
                "wheel_sha256": candidate["wheel_sha256"],
                "sdist_sha256": candidate["sdist_sha256"],
                "receipt_sha256": SHA,
            },
            "second": {
                "build_id": "second",
                "wheel_sha256": candidate["wheel_sha256"],
                "sdist_sha256": candidate["sdist_sha256"],
                "receipt_sha256": "b" * 64,
            },
        },
        "retained_artifacts": {
            "manifest_sha256": _digest(retained_manifest_raw),
            "manifest_path": "external-evidence/retained/manifest.json",
            "wheel": {
                "name": "deeplaw-0.13.0-py3-none-any.whl",
                "sha256": candidate["wheel_sha256"],
                "byte_size": len(wheel_raw),
                "retained_path": "external-evidence/retained/deeplaw-0.13.0-py3-none-any.whl",
            },
            "sdist": {
                "name": "deeplaw-0.13.0.tar.gz",
                "sha256": candidate["sdist_sha256"],
                "byte_size": len(sdist_raw),
                "retained_path": "external-evidence/retained/deeplaw-0.13.0.tar.gz",
            },
        },
    }
    for key, (path, raw) in supply_paths.items():
        pre_publish[key.removesuffix(".json")] = {
            "format": key,
            "sha256": _digest(raw),
            "path": path,
            "verified": True,
        }
    pre_publish_path = root / "receipts/pre-publish.json"
    pre_publish_raw = _write(pre_publish_path, _record(pre_publish))

    candidate_identities = provenance._load_candidate_provenance_identities()
    candidate_runner_identity = candidate_identities["candidate_full_junit"]["runner"]
    candidate_scorer_identity = candidate_identities["candidate_full_junit"]["scorer"]
    typed_junit_path, typed_junit_derived = _typed_junit_manifest(
        external_root,
        candidate,
        holdout_sha256=candidate["candidate_full_sha256"],
        runner=candidate_runner_identity,
        scorer=candidate_scorer_identity,
    )
    typed_junit = json.loads(typed_junit_path.read_text(encoding="utf-8"))
    junit_raw = (external_root / "typed/candidate-junit/candidate.xml").read_bytes()
    candidate_raw_files = {
        "candidate-junit/candidate.xml": junit_raw,
        "verified-candidate-artifacts/deeplaw-0.13.0-py3-none-any.whl": wheel_raw,
        "verified-candidate-artifacts/deeplaw-0.13.0.tar.gz": sdist_raw,
    }
    candidate_inventory = {
        "schema_version": "deeplaw.candidate-full-inventory-receipt/v1",
        "record_kind": "candidate_full_raw_inventory",
        "run_id": 130001,
        "head_sha": COMMIT,
        "path_policy": "logical_relative_paths_only",
        "files": [],
    }
    for logical_path, raw in sorted(candidate_raw_files.items()):
        selected = candidate_raw_root / logical_path
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_bytes(raw)
        candidate_inventory["files"].append(
            {"logical_path": logical_path, "sha256": _digest(raw), "bytes": len(raw)}
        )
    candidate_inventory_raw = _write(
        candidate_raw_root / "candidate-full-inventory-receipt.json", candidate_inventory
    )
    embedded_inventory_path = external_root / "candidate/candidate-full-raw-inventory.json"
    embedded_inventory_path.parent.mkdir(parents=True, exist_ok=True)
    embedded_inventory_path.write_bytes(candidate_inventory_raw)
    candidate["candidate_full_inventory_sha256"] = _digest(candidate_inventory_raw)

    core_gate_ids = list(
        dict.fromkeys(
            next(
                category["gate_ids"]
                for category in classification["categories"]
                if category["category"] == "Core"
            )
        )
    )
    core_gate_ids.remove("migration_recovery")
    core_gate_ids.insert(0, "migration_recovery")
    gate_refs: list[dict[str, Any]] = []
    for gate_id in core_gate_ids:
        gate = _gate_result(
            {
                **candidate,
                "semantic_gold_sha256": semantic_gold_sha,
                "holdout_sha256": candidate["holdout_sha256"],
                "gate_id": gate_id,
            },
            protocol_sha,
            classification,
        )
        gate_path = root / f"evidence/gate-result-{gate_id}.json"
        gate_raw = _write(gate_path, gate)
        if gate_id == "migration_recovery":
            input_id = "input:typed-junit"
            typed_ref = {
                "input_id": input_id,
                "relative_path": typed_junit_path.relative_to(root).as_posix(),
                "byte_size": typed_junit_path.stat().st_size,
                "file_sha256": _digest(typed_junit_path.read_bytes()),
                "schema_version": "deeplaw.typed-qualification-evidence/v1",
                "record_sha256": typed_junit["record_sha256"],
                "artifact_kind": "typed-qualification-evidence",
                "evidence_kind": typed_junit["kind"],
                "derived_record_sha256": _digest(
                    json.dumps(
                        typed_junit_derived,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ),
            }
            gate["status"] = typed_junit_derived["status"]
            gate["corpus"] = {
                "role": "candidate_full",
                "source": "repository",
                "sha256": candidate["candidate_full_sha256"],
                "frozen": True,
            }
            gate["executions"][0]["run_id"] = "run:candidate-junit"
            gate["executions"][0]["workflow_run_id"] = 130001
            gate["executions"][0]["input_refs"] = [input_id]
            gate["executions"][0]["evidence_kind"] = "candidate_full_junit"
            gate["run_ids"] = ["run:candidate-junit"]
            gate["metrics"] = [
                {
                    "metric": f"{input_id}:{metric}",
                    "observed": observed,
                    "input_refs": [input_id],
                }
                for metric, observed in typed_junit_derived["metrics"].items()
            ]
            gate["hard_failures"] = [
                {
                    "failure_id": f"{input_id}:{failure_id}",
                    "count": count,
                    "maximum_allowed": 0,
                    "input_refs": [input_id],
                }
                for failure_id, count in typed_junit_derived["hard_failure_counts"].items()
            ]
            gate["inputs"] = [typed_ref]
            gate["result_sha256"] = _canonical_digest(gate, excluded="result_sha256")
            gate_raw = _write(gate_path, gate)
        gate_refs.append(
            {
                "gate_id": gate_id,
                "category": "Core",
                "result": {
                    "relative_path": gate_path.relative_to(root).as_posix(),
                    "byte_size": len(gate_raw),
                    "file_sha256": _digest(gate_raw),
                    "schema_version": "deeplaw.provenance-bound-gate-result/v3",
                    "record_sha256": gate["result_sha256"],
                    "artifact_kind": "provenance-bound-gate-result",
                },
            }
        )
    report = {
        "schema_version": "deeplaw.commercial-evidence-report/v4",
        "qualification_run_id": 130003,
        "report_kind": "v013_provenance_bound_gate_collection",
        "report_id": "report-pass24",
        "candidate_binding": {
            "candidate_commit": COMMIT,
            "candidate_tree": TREE,
            "candidate_wheel_sha256": candidate["wheel_sha256"],
            "candidate_sdist_sha256": candidate["sdist_sha256"],
        },
        "protocol_binding": {
            "protocol_id": "deeplaw-v013-source-candidate-qualification",
            "protocol_sha256": protocol_sha,
            "frozen": True,
        },
        "threshold_binding": {
            "threshold_id": provenance._SEMANTIC_GOLD_THRESHOLD_ID,
            "threshold_sha256": candidate["threshold_sha256"],
            "frozen": True,
        },
        "gold_binding": {
            "gold_sha256": semantic_gold_sha,
            "role": "qualification_gold",
            "source": "repository_external",
            "frozen": True,
        },
        "corpus": {
            "role": "qualification_holdout",
            "source": "repository_external",
            "sha256": candidate["holdout_sha256"],
            "frozen": True,
        },
        "classification_binding": {
            "classification_id": classification["classification_id"],
            "classification_schema_version": classification["schema_version"],
            "classification_sha256": classification_sha,
        },
        "gate_results": gate_refs,
    }
    report["report_sha256"] = _canonical_digest(report, excluded="report_sha256")
    report_path = root / "evidence/semantic-report.json"
    report_raw = _write(report_path, report)

    release = {
        "schema_version": "deeplaw.commercial-release-manifest/v7",
        "environment": {
            "platform_system": "Darwin",
            "platform_release": "test",
            "platform_version": "test",
            "machine": "arm64",
            "python_implementation": "CPython",
            "python_version": "3.13.0",
            "python_executable_name": "python",
            "uv_version": "0.8.0",
            "ci": True,
            "github_actions": True,
            "github_runner_os": "macos",
            "github_runner_arch": "arm64",
        },
        "release": {
            "repository": "Eysn0130/DeepLaw",
            "version": "0.13.0",
            "tag": "v0.13.0",
            "commit": COMMIT,
            "tree": TREE,
        },
        "run_ids": {
            "candidate_run_id": 130001,
            "evidence_run_id": 130002,
            "qualification_run_id": 130003,
        },
        "candidate_binding": {
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": SHA,
            "wheel_sha256": candidate["wheel_sha256"],
            "sdist_sha256": candidate["sdist_sha256"],
            "version": "0.13.0",
        },
        "artifact_binding": {
            "wheel": {
                "path": "dist/deeplaw-0.13.0-py3-none-any.whl",
                "sha256": candidate["wheel_sha256"],
                "byte_size": len(wheel_raw),
            },
            "sdist": {
                "path": "dist/deeplaw-0.13.0.tar.gz",
                "sha256": candidate["sdist_sha256"],
                "byte_size": len(sdist_raw),
            },
            "retained_manifest_sha256": _digest(retained_manifest_raw),
        },
        "external_bindings": {
            "semantic_gold_sha256": semantic_gold_sha,
            "holdout_sha256": candidate["holdout_sha256"],
            "blind_sha256": candidate["blind_sha256"],
            "scorer_sha256": _digest(scorer_raw),
            "runner_sha256": _digest(runner_raw),
            "isolation_sha256": _digest(isolation_raw),
        },
        "pre_publish_artifact_gate": {
            "path": "receipts/pre-publish.json",
            "receipt_sha256": _digest(pre_publish_raw),
            "status": "pre_publish_passed",
        },
        "semantic_evidence": {
            "report_path": "evidence/semantic-report.json",
            "report_sha256": _digest(report_raw),
            "record_sha256": report["report_sha256"],
            "status": "passed",
            "hard_zero": True,
            "core_gates_passed": True,
        },
        "release_ready": True,
        "public_release_verified": False,
        "post_public_verification": None,
        "commercial_release_eligible": True,
        "quality_protocol_eligible": True,
        "competitive_claim_eligible": False,
    }
    release_path = root / "release-manifest.json"
    _write(release_path, _record(release))

    bundle_files: list[dict[str, Any]] = []
    for path in sorted(external_root.rglob("*")):
        if not path.is_file() or path.name == "bundle-manifest.json":
            continue
        relative = path.relative_to(external_root).as_posix()
        if relative == "evidence/semantic-gold.json":
            kind = "human_gold_scorer"
        elif relative == "receipts/candidate-gold.json":
            kind = "post_build_gold_binding"
        elif relative == "candidate/candidate-full-raw-inventory.json":
            kind = "candidate_full_raw_inventory"
        elif relative == "typed/candidate-junit/manifest.json":
            kind = "candidate_full_junit"
        elif relative == "typed/candidate-junit/candidate.xml":
            kind = "typed_xml"
        elif relative == "retained/deeplaw-0.13.0-py3-none-any.whl":
            kind = "retained_wheel"
        elif relative == "retained/deeplaw-0.13.0.tar.gz":
            kind = "retained_sdist"
        elif relative.startswith("supply/"):
            kind = path.stem
        elif relative in {
            "evidence/holdout.txt",
            "evidence/blind.txt",
            "evidence/candidate-full.txt",
            "evidence/scorer.txt",
            "evidence/runner.txt",
            "evidence/isolation.txt",
        }:
            kind = "sanitized_text"
        else:
            kind = "sanitized_supporting_receipt"
        if kind == "retained_wheel":
            media_type = "application/zip"
        elif kind == "retained_sdist":
            media_type = "application/gzip"
        elif kind == "typed_xml":
            media_type = "application/xml"
        elif kind == "sanitized_text":
            media_type = "text/plain"
        else:
            media_type = "application/json"
        bundle_files.append(
            {
                "relative_path": relative,
                "byte_size": path.stat().st_size,
                "sha256": _digest(path.read_bytes()),
                "media_type": media_type,
                "evidence_kind": kind,
            }
        )
    bundle = {
        "schema_version": "deeplaw.external-qualification-bundle-manifest/v3",
        "candidate_run_id": 130001,
        "evidence_run_id": 130002,
        "candidate_binding": {
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": SHA,
            "wheel_sha256": candidate["wheel_sha256"],
            "sdist_sha256": candidate["sdist_sha256"],
        },
        "external_inputs": {
            "semantic_gold_sha256": semantic_gold_sha,
            "candidate_gold_binding_sha256": _digest(candidate_gold_raw),
            "qualification_holdout_sha256": candidate["holdout_sha256"],
            "final_blind_holdout_sha256": candidate["blind_sha256"],
            "runner_sha256": _digest(runner_raw),
            "scorer_sha256": _digest(scorer_raw),
            "compiler_scorer_isolation_sha256": _digest(isolation_raw),
        },
        "trusted_human_approver_descriptor_sha256": _digest(descriptor_path.read_bytes()),
        "candidate_full_raw_inventory_sha256": _digest(candidate_inventory_raw),
        "files": bundle_files,
    }
    bundle_path = external_root / "bundle-manifest.json"
    _write(bundle_path, _record(bundle))

    return {
        "root": root,
        "external_root": external_root,
        "release": release_path,
        "pre": pre_publish_path,
        "gold": candidate_gold_path,
        "bundle": bundle_path,
        "active": active_path,
        "classification": classification_path,
        "descriptor": descriptor_path,
        "candidate_raw_root": candidate_raw_root,
    }


def _refresh_bundle_refs(bundle_path: Path, root: Path, relative_paths: list[str]) -> None:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    by_path = {item["relative_path"]: item for item in bundle["files"]}
    bundle_root = bundle_path.parent
    for relative in relative_paths:
        path = root / relative
        local = path.relative_to(bundle_root).as_posix()
        by_path[local]["byte_size"] = path.stat().st_size
        by_path[local]["sha256"] = _digest(path.read_bytes())
    bundle["files"] = [by_path[item["relative_path"]] for item in bundle["files"]]
    _write(bundle_path, _record(bundle))


def _rewrite_first_typed_input(paths: dict[str, Path], mutate: Any) -> None:
    root = paths["root"]
    typed_path = root / "external-evidence/typed/candidate-junit/manifest.json"
    typed = json.loads(typed_path.read_text(encoding="utf-8"))
    mutate(typed)
    _write(typed_path, _record(typed))

    report_path = root / "evidence/semantic-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    gate_path = root / report["gate_results"][0]["result"]["relative_path"]
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    input_ref = gate["inputs"][0]
    input_ref["byte_size"] = typed_path.stat().st_size
    input_ref["file_sha256"] = _digest(typed_path.read_bytes())
    input_ref["record_sha256"] = typed["record_sha256"]
    _write(gate_path, _record(gate, field="result_sha256"))
    result_ref = report["gate_results"][0]["result"]
    result_ref["byte_size"] = gate_path.stat().st_size
    result_ref["file_sha256"] = _digest(gate_path.read_bytes())
    result_ref["record_sha256"] = gate["result_sha256"]
    _write(report_path, _record(report, field="report_sha256"))
    release = json.loads(paths["release"].read_text(encoding="utf-8"))
    release["semantic_evidence"]["report_sha256"] = _digest(report_path.read_bytes())
    release["semantic_evidence"]["record_sha256"] = report["report_sha256"]
    _write(paths["release"], _record(release))
    _refresh_bundle_refs(
        paths["bundle"],
        root,
        ["external-evidence/typed/candidate-junit/manifest.json"],
    )


def test_v7_candidate_identity_mapping_reopens_exact_checkout_bytes() -> None:
    identities = provenance._load_candidate_provenance_identities()
    assert (
        identities["candidate_full_junit"]["runner"]
        == identities["candidate_platform_receipt"]["runner"]
    )
    assert (
        identities["candidate_full_junit"]["scorer"]
        != identities["candidate_platform_receipt"]["scorer"]
    )
    assert identities["candidate_full_junit"]["runner"]["identity"].endswith(
        "/.github/workflows/candidate-full.yml"
    )
    assert identities["retained_supply_chain"]["runner"]["identity"].endswith(
        "/.github/workflows/candidate-full.yml/benchmarks/release/verify_reproducible_build.py"
    )
    assert identities["retained_supply_chain"]["runner"] != identities["candidate_full_junit"][
        "runner"
    ]
    assert "exact_wheel_execution" not in identities
    assert "exact_wheel_execution" not in provenance._CANDIDATE_WORKFLOW_KINDS
    assert "exact_wheel_execution" in provenance._EXTERNAL_WORKFLOW_KINDS
    assert provenance._GATE_EVIDENCE_KINDS["canonical_integrity"] == frozenset(
        {"exact_wheel_execution"}
    )
    assert provenance._GATE_EVIDENCE_KINDS["migration_recovery"] == frozenset(
        {"candidate_full_junit"}
    )


def test_v7_reopens_and_derives_the_transitive_chain(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    with pytest.raises(
        ReleaseProvenanceV7Error,
        match=r"Gate result schema|typed|no real executions",
    ):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


def test_v7_external_bundle_v3_closure_excludes_commercial_derivatives(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    boundary = provenance._validate_external_bundle_v3_boundary(
        bundle_root=paths["external_root"],
        active_qualification_path=paths["active"],
        trusted_human_approver_path=paths["descriptor"],
        candidate_run_id=130001,
        evidence_run_id=130002,
    )
    assert boundary["schema_version"] == "deeplaw.external-qualification-bundle-validation/v3"

    # Commercial-derived artifacts remain in the assets root but outside the external closure.
    _write(paths["root"] / "evidence/commercial-derived-only.json", {"status": "derived"})
    boundary_again = provenance._validate_external_bundle_v3_boundary(
        bundle_root=paths["external_root"],
        active_qualification_path=paths["active"],
        trusted_human_approver_path=paths["descriptor"],
        candidate_run_id=130001,
        evidence_run_id=130002,
    )
    assert boundary_again["bundle_manifest_sha256"] == boundary["bundle_manifest_sha256"]


def test_v7_rejects_orphan_inside_external_bundle_v3_closure(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    _write(paths["external_root"] / "orphan.json", {"orphan": True})
    with pytest.raises(ReleaseProvenanceV7Error, match="external qualification bundle v3 boundary"):
        provenance._validate_external_bundle_v3_boundary(
            bundle_root=paths["external_root"],
            active_qualification_path=paths["active"],
            trusted_human_approver_path=paths["descriptor"],
            candidate_run_id=130001,
            evidence_run_id=130002,
        )


def test_v7_reopens_threshold_bytes_instead_of_trusting_gate_self_hash(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    report_path = paths["root"] / "evidence/semantic-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["threshold_binding"]["threshold_sha256"] = _digest(b"caller-authored-threshold")
    _write(report_path, _record(report, field="report_sha256"))
    release = json.loads(paths["release"].read_text(encoding="utf-8"))
    release["semantic_evidence"]["report_sha256"] = _digest(report_path.read_bytes())
    release["semantic_evidence"]["record_sha256"] = report["report_sha256"]
    _write(paths["release"], _record(release))
    with pytest.raises(ReleaseProvenanceV7Error, match="threshold"):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


def test_v7_rejects_replaced_retained_bytes(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    (paths["root"] / "external-evidence/retained/deeplaw-0.13.0-py3-none-any.whl").write_bytes(
        b"replaced"
    )
    with pytest.raises(ReleaseProvenanceV7Error):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


def test_v7_rejects_duplicate_json_before_schema_validation(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    paths["release"].write_text('{"schema_version":"x","schema_version":"y"}\n', encoding="utf-8")
    with pytest.raises(ReleaseProvenanceV7Error, match="strict UTF-8 JSON"):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


def test_v7_rejects_post_public_receipt_at_authorization_boundary(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    release = json.loads(paths["release"].read_text(encoding="utf-8"))
    release["post_public_verification"] = {}
    _write(paths["release"], _record(release))
    with pytest.raises(ReleaseProvenanceV7Error):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


def test_v7_rejects_a_self_hashed_empty_passed_gate(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    report_path = paths["root"] / "evidence/semantic-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    gate_path = paths["root"] / report["gate_results"][0]["result"]["relative_path"]
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gate["executions"] = []
    gate["run_ids"] = []
    gate["metrics"] = []
    _write(gate_path, _record(gate, field="result_sha256"))

    result_ref = report["gate_results"][0]["result"]
    result_ref["byte_size"] = gate_path.stat().st_size
    result_ref["file_sha256"] = _digest(gate_path.read_bytes())
    result_ref["record_sha256"] = gate["result_sha256"]
    _write(report_path, _record(report, field="report_sha256"))

    release_path = paths["release"]
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["semantic_evidence"]["report_sha256"] = _digest(report_path.read_bytes())
    release["semantic_evidence"]["record_sha256"] = report["report_sha256"]
    _write(release_path, _record(release))
    with pytest.raises(
        ReleaseProvenanceV7Error,
        match=r"Gate result schema|typed|no real executions",
    ):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


def test_v7_rejects_a_self_hashed_gate_without_a_raw_receipt(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    input_path = paths["root"] / "external-evidence/evidence/input.json"
    input_path.unlink()
    bundle = json.loads(paths["bundle"].read_text(encoding="utf-8"))
    bundle["files"] = [
        item
        for item in bundle["files"]
        if item["relative_path"] != "evidence/input.json"
    ]
    _write(paths["bundle"], _record(bundle))
    with pytest.raises(ReleaseProvenanceV7Error, match=r"Gate result schema|typed"):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


def test_v7_rejects_candidate_manifest_bound_to_external_holdout(tmp_path: Path) -> None:
    paths = _seed(tmp_path)

    def bind_to_external_holdout(typed: dict[str, Any]) -> None:
        typed["corpus"] = {
            "sha256": "a" * 64,
            "role": "qualification_holdout",
        }

    _rewrite_first_typed_input(paths, bind_to_external_holdout)
    with pytest.raises(ReleaseProvenanceV7Error, match="corpus role"):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


@pytest.mark.parametrize("identity_field", ["runner", "scorer"])
def test_v7_rejects_candidate_manifest_bound_to_external_identity(
    tmp_path: Path,
    identity_field: str,
) -> None:
    paths = _seed(tmp_path)
    candidate_gold = json.loads(paths["gold"].read_text(encoding="utf-8"))

    def bind_to_external_identity(typed: dict[str, Any]) -> None:
        typed[identity_field] = candidate_gold[identity_field]

    _rewrite_first_typed_input(paths, bind_to_external_identity)
    with pytest.raises(ReleaseProvenanceV7Error, match=r"tracked runner|tracked scorer"):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


@pytest.mark.parametrize("identity_field", ["runner", "scorer"])
def test_v7_rejects_self_hashed_arbitrary_candidate_identity(
    tmp_path: Path,
    identity_field: str,
) -> None:
    paths = _seed(tmp_path)

    def bind_to_self_hashed_identity(typed: dict[str, Any]) -> None:
        raw = f"caller-authored-{identity_field}".encode()
        typed[identity_field] = {
            "identity": f"caller/{identity_field}",
            "sha256": _digest(raw),
        }

    _rewrite_first_typed_input(paths, bind_to_self_hashed_identity)
    with pytest.raises(ReleaseProvenanceV7Error, match=r"tracked runner|tracked scorer"):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


def test_v7_rejects_candidate_typed_source_missing_from_independent_raw_inventory(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    typed_path = paths["root"] / "external-evidence/typed/candidate-junit/manifest.json"
    source_path = paths["root"] / "external-evidence/typed/candidate-junit/candidate.xml"
    typed = json.loads(typed_path.read_text(encoding="utf-8"))
    replacement = (
        b"<testsuites> <testsuite><testcase classname=\"typed\" name=\"pass\"/>"
        b"</testsuite></testsuites>"
    )
    source_path.write_bytes(replacement)
    typed["payload"]["source"] = {
        "relative_path": "candidate.xml",
        "byte_size": len(replacement),
        "sha256": _digest(replacement),
        "media_type": "application/xml",
    }
    _write(typed_path, _record(typed), newline=False)
    report_path = paths["root"] / "evidence/semantic-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    gate_path = paths["root"] / report["gate_results"][0]["result"]["relative_path"]
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    input_ref = gate["inputs"][0]
    input_ref["byte_size"] = typed_path.stat().st_size
    input_ref["file_sha256"] = _digest(typed_path.read_bytes())
    input_ref["record_sha256"] = typed["record_sha256"]
    _write(gate_path, _record(gate, field="result_sha256"))
    result_ref = report["gate_results"][0]["result"]
    result_ref["byte_size"] = gate_path.stat().st_size
    result_ref["file_sha256"] = _digest(gate_path.read_bytes())
    result_ref["record_sha256"] = gate["result_sha256"]
    _write(report_path, _record(report, field="report_sha256"))
    release = json.loads(paths["release"].read_text(encoding="utf-8"))
    release["semantic_evidence"]["report_sha256"] = _digest(report_path.read_bytes())
    release["semantic_evidence"]["record_sha256"] = report["report_sha256"]
    _write(paths["release"], _record(release))
    _refresh_bundle_refs(
        paths["bundle"],
        paths["root"],
        [
            "external-evidence/typed/candidate-junit/candidate.xml",
            "external-evidence/typed/candidate-junit/manifest.json",
        ],
    )
    with pytest.raises(ReleaseProvenanceV7Error, match="not retained Candidate Full evidence"):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


def test_v7_rejects_embedded_inventory_different_from_independent_raw_artifact(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    bundle = json.loads(paths["bundle"].read_text(encoding="utf-8"))
    embedded_path = (
        paths["root"] / "external-evidence/candidate/candidate-full-raw-inventory.json"
    )
    embedded = json.loads(embedded_path.read_text(encoding="utf-8"))
    embedded["files"].append(
        {"logical_path": "caller-authored.bin", "sha256": "c" * 64, "bytes": 1}
    )
    embedded_raw = _write(embedded_path, embedded)
    for item in bundle["files"]:
        if item["relative_path"] == "candidate/candidate-full-raw-inventory.json":
            item["byte_size"] = len(embedded_raw)
            item["sha256"] = _digest(embedded_raw)
            break
    else:  # pragma: no cover - seed always contains the inventory reference
        raise AssertionError("embedded inventory reference missing")
    bundle["candidate_full_raw_inventory_sha256"] = _digest(embedded_raw)
    _write(paths["bundle"], _record(bundle))
    with pytest.raises(ReleaseProvenanceV7Error, match="raw inventory bytes"):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


def test_v7_rejects_a_report_missing_a_current_core_gate(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    report_path = paths["root"] / "evidence/semantic-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["gate_results"].pop()
    _write(report_path, _record(report, field="report_sha256"))

    release = json.loads(paths["release"].read_text(encoding="utf-8"))
    release["semantic_evidence"]["report_sha256"] = _digest(report_path.read_bytes())
    release["semantic_evidence"]["record_sha256"] = report["report_sha256"]
    _write(paths["release"], _record(release))
    with pytest.raises(ReleaseProvenanceV7Error):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )


def test_v7_rejects_historical_v3_classification_and_report(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    historical_classification = (
        Path(__file__).resolve().parents[1]
        / "benchmarks/release/v013-gate-classification-v3.json"
    )
    with pytest.raises(ReleaseProvenanceV7Error, match="canonical v7"):
        validate_release_provenance(
            paths["release"],
            classification_path=historical_classification,
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )

    report_path = paths["root"] / "evidence/semantic-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["schema_version"] = "deeplaw.commercial-evidence-report/v3"
    _write(report_path, _record(report, field="report_sha256"))
    release = json.loads(paths["release"].read_text(encoding="utf-8"))
    release["semantic_evidence"]["report_sha256"] = _digest(report_path.read_bytes())
    release["semantic_evidence"]["record_sha256"] = report["report_sha256"]
    _write(paths["release"], _record(release))
    with pytest.raises(ReleaseProvenanceV7Error, match="current v4"):
        validate_release_provenance(
            paths["release"],
            classification_path=paths["classification"],
            pre_publish_receipt_path=paths["pre"],
            candidate_gold_binding_path=paths["gold"],
            external_bundle_manifest_path=paths["bundle"],
            active_qualification_path=paths["active"],
            assets_root=paths["root"],
            candidate_raw_root=paths["candidate_raw_root"],
            trusted_human_approver_path=paths["descriptor"],
            expected_candidate_run_id=130001,
            expected_evidence_run_id=130002,
            expected_qualification_run_id=130003,
        )
