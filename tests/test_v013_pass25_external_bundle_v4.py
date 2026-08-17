from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.release.external_qualification_bundle_v4 import (
    ExternalQualificationBundleV4Error,
    _panel_digest,
    _record_sha256,
    validate_external_bundle,
)
from benchmarks.release.security_domain_receipt import (
    ROLE_LABELS,
    process_receipt_set_sha256,
    record_sha256,
    security_domain_set_sha256,
)

COMMIT = "1" * 40
TREE = "2" * 40
LOCK = "3" * 64
HOLDOUT = "4" * 64
BLIND = "5" * 64
SCORER_A = "6" * 64
SCORER_B = "7" * 64
ARBITER = "8" * 64
RUNNER = "9" * 64
REFERENCE_ID = "semanticref_0123456789abcdef01234567"
REPOSITORY = Path(__file__).resolve().parents[1]
EXACT_WHEEL_RUNNER = hashlib.sha256(
    (REPOSITORY / "benchmarks/release/exact_wheel_runner.py").read_bytes()
).hexdigest()


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    ) + b"\n"


def _write_json(root: Path, relative: str, value: Any) -> bytes:
    raw = _json_bytes(value)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _write_bytes(root: Path, relative: str, raw: bytes) -> bytes:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _source(root: Path, relative: str, media_type: str = "application/json") -> dict[str, Any]:
    raw = (root / relative).read_bytes()
    return {
        "relative_path": relative,
        "byte_size": len(raw),
        "sha256": _digest(raw),
        "media_type": media_type,
    }


def _security_receipt(
    role: str,
    ingress_names: set[str],
    egress_name: str,
    *,
    forbidden_names: set[str],
    network_policy: str = "deny_all",
    executable_sha256: str | None = None,
    process_receipt_sha256s: list[str] | None = None,
) -> dict[str, Any]:
    def artifact(name: str) -> dict[str, str]:
        # Artifact identity is global across the handoff.  A role-specific
        # digest would allow a producer/consumer mismatch to pass unnoticed.
        return {"name": name, "sha256": _digest(f"artifact:{name}".encode())}

    ingress = [artifact(name) for name in sorted(ingress_names)]
    egress = [artifact(egress_name)]
    can_read = [item["sha256"] for item in ingress]
    canary_targets = [
        {"name": name, "sha256": _digest(f"negative-canary:{role}:{name}".encode())}
        for name in sorted(forbidden_names)
    ]
    cannot_read = [item["sha256"] for item in canary_targets]
    process_receipts = process_receipt_sha256s or [
        _digest(f"process-receipt:{role}".encode())
    ]
    value: dict[str, Any] = {
        "schema_version": "deeplaw.security-domain-receipt/v1",
        "profile": "machine_evaluated_no_human_attestation",
        "role": role,
        "domain_id": f"domain:{role}",
        "runner": {
            "ephemeral_runner_id": f"runner:{role}",
            "runner_label": ROLE_LABELS[role],
            "runner_attestation_sha256": _digest(f"runner-attestation:{role}".encode()),
        },
        "executable": {
            "executable_sha256": executable_sha256
            or _digest(f"executable:{role}".encode()),
            "process_tree_sha256": _digest(f"process-tree:{role}".encode()),
        },
        "principal": {
            "uid": 1000 + len(role),
            "principal_id": f"principal:{role}",
            "acl_sha256": _digest(f"acl:{role}".encode()),
        },
        "mount": {
            "namespace_id": f"mount:{role}",
            "inventory_sha256": _digest(f"mount:{role}".encode()),
            "read_only_input_sha256s": can_read,
        },
        "network": {
            "policy": network_policy,
            "policy_sha256": _digest(f"network:{role}".encode()),
        },
        "ipc": {
            "namespace_id": f"ipc:{role}",
            "policy": "artifact_pipe_only",
            "policy_sha256": _digest(f"ipc:{role}".encode()),
        },
        "ingress": ingress,
        "egress": egress,
        "visibility": {"can_read": can_read, "cannot_read": cannot_read},
        "negative_canary": {
            "attempts": len(canary_targets),
            "targets": canary_targets,
            "leaked_count": 0,
            "observation_sha256": _digest(f"canary:{role}:zero".encode()),
        },
        "secret_policy": "broker_only_exact_host" if role == "candidate_host" else "forbidden",
        "process_receipt_sha256": process_receipt_set_sha256(process_receipts),
        "process_receipt_sha256s": process_receipts,
        "observed_roots_sha256": _digest(f"observed-roots:{role}".encode()),
        "attester_executable_sha256": _digest(b"frozen-security-domain-attester"),
        "observed": {
            "source": "os_runner_attestation",
            "command_id": f"observe:{role}",
            "attestation_sha256": _digest(f"os-observation:{role}".encode()),
        },
    }
    value["record_sha256"] = record_sha256(value)
    return value


def _record(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result["record_sha256"] = _record_sha256(result)
    return result


def _semantic_reference(
    reviewers: list[dict[str, Any]], *, source_corpus_sha256: str
) -> dict[str, Any]:
    cases = []
    for suffix in ("cold", "resume", "compact"):
        cases.append(
            {
                "case_id": f"goldcase_{suffix}",
                "labels": ["include_answer"],
                "expected": {"include": ["include_answer"], "exclude": []},
                "duties": ["exact_citation"],
                "hard_failures": ["false_authority"],
                "thresholds": {
                    "minimum_case_pass_rate": 1.0,
                    "minimum_duty_coverage": 1.0,
                    "maximum_hard_failures": 0,
                    "maximum_false_authority": 0,
                },
            }
        )
    return _record(
        {
            "schema_version": "deeplaw.semantic-machine-reference/v1",
            "status": "semantic_machine_reference_frozen",
            "profile": "machine_evaluated_no_human_attestation",
            "reference_provenance": "agent_consensus",
            "human_authenticity": "not_claimed",
            "frozen_at": "2026-08-17T00:00:00Z",
            "reference_id": REFERENCE_ID,
            "model_outputs_seen_before_freeze": True,
            "candidate_visible_when_frozen": False,
            "human_claim_eligible": False,
            "competitive_claim_eligible": False,
            "agent_review": {
                "reviewers": reviewers,
                "roster_sha256": SCORER_A,
                "consensus_sha256": SCORER_B,
                "isolation_sha256": ARBITER,
                "rubric_sha256": RUNNER,
                "source_corpus_sha256": source_corpus_sha256,
                "minimum_distinct_agents": 3,
                "unanimity_required": True,
            },
            "labels": [{"label_id": "include_answer", "description": "Bounded answer"}],
            "cases": cases,
            "duties": [{"duty_id": "exact_citation", "description": "Retain evidence identity"}],
            "hard_failures": [
                {"code": "false_authority", "description": "Derived text is not source authority"}
            ],
            "thresholds": {
                "minimum_case_pass_rate": 1.0,
                "minimum_duty_coverage": 1.0,
                "maximum_hard_failures": 0,
                "maximum_false_authority": 0,
            },
        }
    )


def _seed(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "bundle"
    root.mkdir(parents=True)
    holdout_raw = _write_json(
        root,
        "corpus/qualification-holdout.json",
        {"corpus_id": "qualification-holdout", "cases": ["cold", "resume", "compact"]},
    )
    blind_raw = _write_json(
        root,
        "corpus/final-blind.json",
        {"corpus_id": "final-blind", "cases": ["blind-cold"]},
    )
    holdout_sha = _digest(holdout_raw)
    blind_sha = _digest(blind_raw)
    reviewers: list[dict[str, Any]] = []
    for index in range(3):
        process_raw = _write_json(
            root,
            f"reference/reviewer-{index + 1}-process.json",
            {"agent_id": f"agent:reviewer-{index + 1}", "process_id": f"process-{index + 1}"},
        )
        output_raw = _write_json(
            root,
            f"reference/reviewer-{index + 1}-output.json",
            _record(
                {
                    "schema_version": "deeplaw.machine-reviewer-output/v1",
                    "profile": "machine_evaluated_no_human_attestation",
                    "human_authenticity": "not_claimed",
                    "reference_id": REFERENCE_ID,
                    "agent_id": f"agent:reviewer-{index + 1}",
                    "model_id": f"model:reviewer-{index + 1}",
                    "rubric_sha256": RUNNER,
                    "source_corpus_sha256": holdout_sha,
                    "process_identity_sha256": _digest(process_raw),
                    "decision": "approved",
                    "disagreements": [],
                }
            ),
        )
        reviewers.append(
            {
                "agent_id": f"agent:reviewer-{index + 1}",
                "role": f"role-{index + 1}",
                "model_id": f"model:reviewer-{index + 1}",
                "implementation_sha256": SCORER_A,
                "prompt_sha256": SCORER_B,
                "process_identity_sha256": _digest(process_raw),
                "output_sha256": _digest(output_raw),
                "conclusions_hidden_from_peers": True,
                "separate_process": True,
            }
        )
    semantic = _semantic_reference(reviewers, source_corpus_sha256=holdout_sha)
    reviewers = semantic["agent_review"]["reviewers"]
    roster_raw = _write_json(
        root,
        "reference/roster.json",
        _record(
            {
                "schema_version": "deeplaw.agent-review-roster/v1",
                "profile": "machine_evaluated_no_human_attestation",
                "reference_id": semantic["reference_id"],
                "reviewers": reviewers,
            }
        ),
    )
    consensus_raw = _write_json(
        root,
        "reference/consensus.json",
        _record(
            {
                "schema_version": "deeplaw.agent-review-consensus/v1",
                "profile": "machine_evaluated_no_human_attestation",
                "reference_id": semantic["reference_id"],
                "roster_sha256": _digest(roster_raw),
                "rubric_sha256": RUNNER,
                "source_corpus_sha256": holdout_sha,
                "reviewer_output_sha256s": [row["output_sha256"] for row in reviewers],
                "unanimous": True,
                "disagreements": [],
            }
        ),
    )
    isolation_raw = _write_json(
        root,
        "reference/isolation.json",
        _record(
            {
                "schema_version": "deeplaw.agent-review-isolation/v1",
                "profile": "machine_evaluated_no_human_attestation",
                "reference_id": semantic["reference_id"],
                "reviewer_processes_distinct": True,
                "reviewer_outputs_hidden": True,
                "candidate_hidden": True,
                "runner_reference_labels_hidden": True,
                "scorers_mutually_hidden": True,
                "scorer_runner_isolated": True,
                "arbiter_deterministic": True,
                "compiler_reference_access": False,
                "evaluator_output_mutation": False,
                "blind_contamination": False,
                "violations": [],
            }
        ),
    )
    semantic["agent_review"].update(
        {
            "roster_sha256": _digest(roster_raw),
            "consensus_sha256": _digest(consensus_raw),
            "isolation_sha256": _digest(isolation_raw),
        }
    )
    semantic.pop("record_sha256")
    semantic = _record(semantic)
    semantic_raw = _write_json(root, "reference/semantic.json", semantic)
    wheel_raw = _write_bytes(root, "artifacts/deeplaw-wheel.whl", b"PK\x03\x04machine-wheel")
    sdist_raw = _write_bytes(root, "artifacts/deeplaw-sdist.tar.gz", b"\x1f\x8bmachine-sdist")
    wheel_sha = _digest(wheel_raw)
    sdist_sha = _digest(sdist_raw)
    panel = {
        "scorer_a": {
            "role": "independent_scorer_a",
            "identity": "independent-scorer-a:default",
            "sha256": SCORER_A,
        },
        "scorer_b": {
            "role": "independent_scorer_b",
            "identity": "independent-scorer-b:default",
            "sha256": SCORER_B,
        },
        "distinct_scorers": True,
    }
    panel["panel_sha256"] = _panel_digest(panel)
    external = {
        "semantic_reference_sha256": _digest(semantic_raw),
        "candidate_binding_sha256": "0" * 64,
        "qualification_holdout_sha256": holdout_sha,
        "final_blind_holdout_sha256": blind_sha,
        "agent_roster_sha256": _digest(roster_raw),
        "agent_consensus_sha256": _digest(consensus_raw),
        "agent_isolation_sha256": _digest(isolation_raw),
        "runner_sha256": RUNNER,
        "scorer_panel_sha256": panel["panel_sha256"],
        "arbiter_sha256": ARBITER,
        "compiler_scorer_isolation_sha256": _digest(b"frozen-security-domain-attester"),
    }
    binding = _record(
        {
            "schema_version": "deeplaw.candidate-gold-binding-receipt/v2",
            "status": "post_build_machine_reference_bound",
            "profile": "machine_evaluated_no_human_attestation",
            "reference_provenance": "agent_consensus",
            "human_authenticity": "not_claimed",
            "bound_at": "2026-08-17T01:00:00Z",
            "semantic_reference": {
                "reference_id": semantic["reference_id"],
                "schema_version": "deeplaw.semantic-machine-reference/v1",
                "sha256": _digest(semantic_raw),
            },
            "agent_roster": {"sha256": _digest(roster_raw)},
            "agent_consensus": {"sha256": _digest(consensus_raw)},
            "agent_isolation": {"sha256": _digest(isolation_raw)},
            "candidate": {"commit": COMMIT, "tree": TREE, "lock_sha256": LOCK},
            "artifacts": {
                "wheel": {
                    "name": "deeplaw-wheel.whl",
                    "sha256": wheel_sha,
                    "byte_size": len(wheel_raw),
                },
                "sdist": {
                    "name": "deeplaw-sdist.tar.gz",
                    "sha256": sdist_sha,
                    "byte_size": len(sdist_raw),
                },
            },
            "holdout": {"role": "qualification_holdout", "sha256": holdout_sha},
            "blind": {"role": "final_blind", "sha256": blind_sha},
            "scorer_panel": panel,
            "arbiter": {
                "role": "deterministic_arbiter",
                "identity": "deterministic-arbiter:default",
                "sha256": ARBITER,
            },
            "runner": {"identity": "runner:isolated", "sha256": RUNNER},
        }
    )
    binding_raw = _write_json(root, "reference/candidate-binding.json", binding)
    external["candidate_binding_sha256"] = _digest(binding_raw)

    process_receipts: dict[str, list[str]] = {}
    for role in (
        "reference_freezer",
        "candidate_host",
        "scorer_a",
        "scorer_b",
        "arbiter",
    ):
        process_receipts[role] = []
        for index in range(2 if role == "candidate_host" else 1):
            raw = _write_json(
                root,
                f"process/{role}-{index + 1}.json",
                {
                    "schema_version": "deeplaw.sanitized-process-observation/v1",
                    "role": role,
                    "ordinal": index + 1,
                },
            )
            process_receipts[role].append(_digest(raw))

    security_receipts = {
        "reference_freezer": _security_receipt(
            "reference_freezer",
            {"reference-cases", "reviewer-inputs"},
            "sealed-reference",
            forbidden_names={
                "candidate-sanitized-output",
                "scorer-a-output",
                "scorer-b-output",
                "arbiter-output",
            },
            process_receipt_sha256s=process_receipts["reference_freezer"],
        ),
        "candidate_host": _security_receipt(
            "candidate_host",
            {"verified-candidate-artifacts", "qualification-inputs", "final-blind-inputs"},
            "candidate-sanitized-output",
            forbidden_names={
                "sealed-reference",
                "scorer-a-output",
                "scorer-b-output",
                "arbiter-output",
            },
            network_policy="host_provider_allowlist",
            executable_sha256=RUNNER,
            process_receipt_sha256s=process_receipts["candidate_host"],
        ),
        "scorer_a": _security_receipt(
            "scorer_a",
            {"candidate-sanitized-output", "sealed-reference"},
            "scorer-a-output",
            forbidden_names={"scorer-b-output", "arbiter-output"},
            executable_sha256=SCORER_A,
            process_receipt_sha256s=process_receipts["scorer_a"],
        ),
        "scorer_b": _security_receipt(
            "scorer_b",
            {"candidate-sanitized-output", "sealed-reference"},
            "scorer-b-output",
            forbidden_names={"scorer-a-output", "arbiter-output"},
            executable_sha256=SCORER_B,
            process_receipt_sha256s=process_receipts["scorer_b"],
        ),
        "arbiter": _security_receipt(
            "arbiter",
            {"scorer-a-output", "scorer-b-output"},
            "arbiter-output",
            forbidden_names={"candidate-sanitized-output", "sealed-reference"},
            executable_sha256=ARBITER,
            process_receipt_sha256s=process_receipts["arbiter"],
        ),
    }
    security_receipt_sources: list[dict[str, Any]] = []
    for role, receipt in security_receipts.items():
        relative = f"security/{role}.json"
        _write_json(root, relative, receipt)
        security_receipt_sources.append(_source(root, relative))
    external["security_domains_sha256"] = security_domain_set_sha256(
        list(security_receipts.values())
    )
    external["security_domain_executable_sha256"] = {
        role: receipt["executable"]["executable_sha256"]
        for role, receipt in security_receipts.items()
    }
    external["security_domain_process_tree_sha256"] = {
        role: receipt["executable"]["process_tree_sha256"]
        for role, receipt in security_receipts.items()
    }
    external["security_domain_process_receipt_sha256"] = {
        role: receipt["process_receipt_sha256"]
        for role, receipt in security_receipts.items()
    }
    external["security_domain_observed_roots_sha256"] = {
        role: receipt["observed_roots_sha256"]
        for role, receipt in security_receipts.items()
    }

    def source(relative: str, media: str = "application/json") -> dict[str, Any]:
        return _source(root, relative, media)

    def typed_base(
        kind: str, *, run_id: str, workflow: int, corpus: dict[str, Any]
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": "deeplaw.typed-qualification-evidence/v2",
            "profile": "machine_evaluated_no_human_attestation",
            "reference_provenance": "agent_consensus",
            "human_authenticity": "not_claimed",
            "kind": kind,
            "candidate_binding": {
                "commit": COMMIT,
                "tree": TREE,
                "lock_sha256": LOCK,
                "wheel_sha256": wheel_sha,
                "sdist_sha256": sdist_sha,
            },
            "run_binding": {"run_id": run_id, "workflow_run_id": workflow},
            "corpus": corpus,
            "runner": {"identity": "runner:isolated", "sha256": RUNNER},
            "scorer": (
                {
                    "identity": "deterministic-arbiter:default",
                    "sha256": ARBITER,
                }
                if kind == "machine_reference_scorer"
                else {"identity": "typed-parser:v2", "sha256": "f" * 64}
            ),
            "payload": {},
        }
        if kind == "machine_reference_scorer":
            value["scorer_panel"] = panel
            value["arbiter"] = {
                "role": "deterministic_arbiter",
                "identity": "deterministic-arbiter:default",
                "sha256": ARBITER,
            }
        return value

    # The raw sources are deliberately retained as ordinary sanitized JSON/XML
    # files; only their typed envelopes can satisfy the required inventory.
    _write_bytes(root, "candidate/junit.xml", b"<testsuite tests=\"1\"><testcase/></testsuite>\n")
    _write_json(root, "candidate/platform.json", {"platform": "candidate-full"})
    for index in range(6):
        _write_json(root, f"host/source-{index}.json", {"event": f"event-{index}"})
    _write_json(root, "qualification/wheel-execution.json", {"module": "deeplaw", "isolated": True})
    for relative in (
        "legal/catalog.json",
        "legal/expected.json",
        "legal/observed.json",
        "wiki/expected.json",
        "wiki/observed.json",
        "context/expected.json",
        "context/capsule.json",
        "context/trace.json",
        "context/ledger.json",
        "context/usage.json",
        "scale/expected.json",
        "scale/observed.json",
    ):
        _write_json(root, relative, {"kind": relative})
    for relative in (
        "legal/original.json",
        "supply/build.json",
        "supply/retained.json",
        "supply/prepublish.json",
        "supply/sbom.json",
        "supply/openvex.json",
        "supply/licenses.json",
        "supply/provenance.json",
    ):
        _write_json(root, relative, {"kind": relative})

    def machine_payload(tag: str, typed: dict[str, Any]) -> dict[str, Any]:
        a_path = f"rows/{tag}-a.json"
        b_path = f"rows/{tag}-b.json"
        arb_path = f"rows/{tag}-arbiter.json"
        output_path = f"candidate/{tag}-output.json"
        execution_path = f"candidate/{tag}-execution.json"
        output_raw = _write_json(
            root,
            output_path,
            _record(
                {
                    "schema_version": "deeplaw.machine-candidate-output/v1",
                    "profile": "machine_evaluated_no_human_attestation",
                    "candidate": typed["candidate_binding"],
                    "run": typed["run_binding"],
                    "corpus": typed["corpus"],
                    "runner": typed["runner"],
                    "rows": [
                        {
                            "case_id": f"goldcase_{suffix}",
                            "observed": {"include": ["include_answer"], "exclude": []},
                            "duties": ["exact_citation"],
                            "hard_failures": [],
                            "false_authority": False,
                        }
                        for suffix in ("cold", "resume", "compact")
                    ],
                }
            ),
        )
        _write_json(
            root,
            execution_path,
            _record(
                {
                    "schema_version": "deeplaw.machine-candidate-execution/v1",
                    "profile": "machine_evaluated_no_human_attestation",
                    "candidate": typed["candidate_binding"],
                    "run": typed["run_binding"],
                    "corpus": typed["corpus"],
                    "runner": typed["runner"],
                    "executable_sha256": typed["runner"]["sha256"],
                    "process": {
                        "pid": 101,
                        "parent_pid": 100,
                        "process_tree_sha256": "e" * 64,
                        "environment_key_allowlist": ["PATH"],
                        "read_only_input_sha256s": [
                            typed["candidate_binding"]["wheel_sha256"],
                            typed["corpus"]["sha256"],
                        ],
                        "started_at": "2026-08-17T02:00:00Z",
                        "finished_at": "2026-08-17T02:01:00Z",
                        "exit_code": 0,
                    },
                    "output_sha256": _digest(output_raw),
                }
            ),
        )
        _write_json(
            root,
            a_path,
            {"observations": [{"case_id": "goldcase_cold", "label": "include_answer"}]},
        )
        _write_json(
            root,
            b_path,
            {
                "observations": [
                    {"case_id": "goldcase_cold", "label": "include_answer", "independent": True}
                ]
            },
        )
        _write_json(
            root,
            arb_path,
            {"adjudications": [{"case_id": "goldcase_cold", "agreement": True}]},
        )
        return {
            "candidate_output_source": source(output_path),
            "candidate_execution_source": source(execution_path),
            "semantic_reference_source": source("reference/semantic.json"),
            "candidate_binding_source": source("reference/candidate-binding.json"),
            "agent_roster_source": source("reference/roster.json"),
            "agent_consensus_source": source("reference/consensus.json"),
            "agent_isolation_source": source("reference/isolation.json"),
            "security_domain_receipt_sources": security_receipt_sources,
            "scorer_a_rows_source": source(a_path),
            "scorer_b_rows_source": source(b_path),
            "arbiter_consensus_rows_source": source(arb_path),
            "process_identity": {
                "scorer_a_process_id": f"scorer-a:{tag}",
                "scorer_b_process_id": f"scorer-b:{tag}",
                "runner_process_id": f"runner:{tag}",
                "arbiter_process_id": f"arbiter:{tag}",
                "scorer_a_identity_sha256": SCORER_A,
                "scorer_b_identity_sha256": SCORER_B,
                "runner_identity_sha256": RUNNER,
                "arbiter_identity_sha256": ARBITER,
                "scorer_processes_distinct": True,
                "arbiter_process_distinct": True,
                "separate_processes": True,
            },
        }

    machine_files: list[str] = []
    for tag, role in (("holdout", "qualification_holdout"), ("blind", "final_blind")):
        typed = typed_base(
            "machine_reference_scorer",
            run_id=f"evidence-{tag}-202",
            workflow=202,
            corpus={
                "sha256": holdout_sha if role == "qualification_holdout" else blind_sha,
                "role": role,
            },
        )
        typed["payload"] = machine_payload(tag, typed)
        relative = f"typed/machine-{tag}.json"
        _write_json(root, relative, _record(typed))
        machine_files.append(relative)

    typed_files: list[tuple[str, dict[str, Any], str]] = []
    candidate_full = typed_base(
        "candidate_full_junit",
        run_id="candidate-run-101",
        workflow=101,
        corpus={"sha256": holdout_sha, "role": "candidate_full"},
    )
    candidate_full["payload"] = {"source": source("candidate/junit.xml", "application/xml")}
    typed_files.append(("typed/candidate-junit.json", candidate_full, "candidate_full_junit"))
    platform_receipt = typed_base(
        "candidate_platform_receipt",
        run_id="candidate-run-101",
        workflow=101,
        corpus={"sha256": holdout_sha, "role": "candidate_full"},
    )
    platform_receipt["payload"] = {
        "source": source("candidate/platform.json"),
        "platform_manifest_source": source("candidate/platform.json"),
        "junit_sources": [
            {
                "platform": platform,
                "python_version": python_version,
                "source": source("candidate/junit.xml", "application/xml"),
            }
            for platform in ("ubuntu", "macos", "windows")
            for python_version in ("3.11", "3.12", "3.13")
        ],
    }
    typed_files.append(
        ("typed/platform.json", platform_receipt, "candidate_platform_receipt")
    )
    for index in range(6):
        event = typed_base(
            "host_event_sequence",
            run_id="evidence-run-202",
            workflow=202,
            corpus={"sha256": holdout_sha, "role": "qualification_holdout"},
        )
        refs = {
            name: source(f"host/source-{index}.json")
            for name in (
                "event",
                "lifecycle",
                "usage",
                "expected",
                "continuity",
                "isolation",
            )
        }
        event["payload"] = {f"{name}_source": value for name, value in refs.items()}
        typed_files.append((f"typed/host-{index}.json", event, "host_event_sequence"))
    exact = typed_base(
        "exact_wheel_execution",
        run_id="evidence-run-202",
        workflow=202,
        corpus={"sha256": holdout_sha, "role": "candidate_full"},
    )
    exact["runner"] = {
        "identity": "exact-wheel-runner:v2",
        "sha256": EXACT_WHEEL_RUNNER,
    }
    exact["payload"] = {"source": source("qualification/wheel-execution.json")}
    typed_files.append(("typed/exact-wheel.json", exact, "exact_wheel_execution"))
    legal = typed_base(
        "legal_rows",
        run_id="evidence-run-202",
        workflow=202,
        corpus={"sha256": holdout_sha, "role": "qualification_holdout"},
    )
    legal["payload"] = {
        "source_catalog_source": source("legal/catalog.json"),
        "original_source_refs": [
            {
                "source_id": "source:law",
                "version_id": "version:1",
                "source": source("legal/original.json"),
            }
            for _ in range(28)
        ],
        "expected_source": source("legal/expected.json"),
        "observed_source": source("legal/observed.json"),
    }
    typed_files.append(("typed/legal.json", legal, "legal_rows"))
    wiki = typed_base(
        "wiki_journey_rows",
        run_id="evidence-run-202",
        workflow=202,
        corpus={"sha256": holdout_sha, "role": "qualification_holdout"},
    )
    wiki["payload"] = {
        "expected_source": source("wiki/expected.json"),
        "observed_source": source("wiki/observed.json"),
    }
    typed_files.append(("typed/wiki.json", wiki, "wiki_journey_rows"))
    context = typed_base(
        "context_capsule_selection_usage",
        run_id="evidence-run-202",
        workflow=202,
        corpus={"sha256": holdout_sha, "role": "qualification_holdout"},
    )
    context["payload"] = {
        "expected_source": source("context/expected.json"),
        "provider_capsule_source": source("context/capsule.json"),
        "query_trace_source": source("context/trace.json"),
        "ledger_source": source("context/ledger.json"),
        "usage_source": source("context/usage.json"),
    }
    typed_files.append(("typed/context.json", context, "context_capsule_selection_usage"))
    scale = typed_base(
        "scale_report",
        run_id="evidence-run-202",
        workflow=202,
        corpus={"sha256": holdout_sha, "role": "qualification_holdout"},
    )
    scale["payload"] = {
        "expected_source": source("scale/expected.json"),
        "observed_source": source("scale/observed.json"),
    }
    typed_files.append(("typed/scale.json", scale, "scale_report"))
    supply = typed_base(
        "retained_supply_chain",
        run_id="candidate-run-101",
        workflow=101,
        corpus={"sha256": holdout_sha, "role": "candidate_full"},
    )
    supply["payload"] = {
        "candidate_build_source": source("supply/build.json"),
        "retained_candidate_source": source("supply/retained.json"),
        "pre_publish_receipt_source": source("supply/prepublish.json"),
        "wheel_source": source("artifacts/deeplaw-wheel.whl", "application/octet-stream"),
        "sdist_source": source("artifacts/deeplaw-sdist.tar.gz", "application/octet-stream"),
        "sbom_source": source("supply/sbom.json"),
        "openvex_source": source("supply/openvex.json"),
        "licenses_source": source("supply/licenses.json"),
        "provenance_source": source("supply/provenance.json"),
    }
    typed_files.append(("typed/supply.json", supply, "retained_supply_chain"))
    for relative, value, _kind in typed_files:
        _write_json(root, relative, _record(value))

    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "bundle-manifest.json":
            continue
        raw = path.read_bytes()
        if relative.endswith(".xml"):
            media = "application/xml"
            kind = "typed_xml"
        elif relative in {
            "artifacts/deeplaw-wheel.whl",
            "artifacts/deeplaw-sdist.tar.gz",
        }:
            media = "application/octet-stream"
            kind = "retained_wheel" if relative.endswith(".whl") else "retained_sdist"
        elif relative == "reference/semantic.json":
            media = "application/json"
            kind = "semantic_machine_reference"
        elif relative == "reference/candidate-binding.json":
            media = "application/json"
            kind = "post_build_machine_reference_binding"
        elif relative == "reference/roster.json":
            media = "application/json"
            kind = "agent_roster"
        elif relative == "reference/consensus.json":
            media = "application/json"
            kind = "agent_consensus"
        elif relative == "reference/isolation.json":
            media = "application/json"
            kind = "agent_isolation"
        elif relative.startswith("security/") and relative.endswith(".json"):
            media = "application/json"
            kind = "security_domain_receipt"
        elif relative.startswith("reference/reviewer-") and relative.endswith(
            "-output.json"
        ):
            media = "application/json"
            kind = "machine_reviewer_output"
        elif relative == "candidate/candidate-full-raw-inventory.json":
            media = "application/json"
            kind = "candidate_full_raw_inventory"
        elif relative.startswith("typed/machine-"):
            media = "application/json"
            kind = "machine_reference_scorer"
        elif relative.startswith("candidate/") and relative.endswith("-output.json"):
            media = "application/json"
            kind = "machine_candidate_output"
        elif relative.startswith("candidate/") and relative.endswith("-execution.json"):
            media = "application/json"
            kind = "machine_candidate_execution"
        elif relative.startswith("typed/"):
            media = "application/json"
            kind = next(kind for path_value, _value, kind in typed_files if path_value == relative)
        else:
            media = "application/json"
            kind = "sanitized_supporting_receipt"
        files.append(
            {
                "relative_path": relative,
                "byte_size": len(raw),
                "sha256": _digest(raw),
                "media_type": media,
                "evidence_kind": kind,
            }
        )

    candidate_raw_rows = [
        {
            "logical_path": item["relative_path"],
            "sha256": item["sha256"],
            "bytes": item["byte_size"],
        }
        for item in files
        if item["evidence_kind"]
        in {
            "candidate_full_junit",
            "candidate_platform_receipt",
            "retained_supply_chain",
            "retained_wheel",
            "retained_sdist",
        }
    ]
    inventory = {
        "schema_version": "deeplaw.candidate-full-inventory-receipt/v1",
        "record_kind": "candidate_full_raw_inventory",
        "run_id": 101,
        "head_sha": COMMIT,
        "path_policy": "logical_relative_paths_only",
        "files": candidate_raw_rows,
    }
    inventory_raw = _write_json(root, "candidate/candidate-full-raw-inventory.json", inventory)
    # The inventory is the only file intentionally not listed in its own rows.
    files.append(
        {
            "relative_path": "candidate/candidate-full-raw-inventory.json",
            "byte_size": len(inventory_raw),
            "sha256": _digest(inventory_raw),
            "media_type": "application/json",
            "evidence_kind": "candidate_full_raw_inventory",
        }
    )
    exact_path = root / "typed/exact-wheel.json"
    exact_typed = json.loads(exact_path.read_text(encoding="utf-8"))
    exact_typed["corpus"]["sha256"] = _digest(inventory_raw)
    exact_raw = _json_bytes(_record(exact_typed))
    exact_path.write_bytes(exact_raw)
    for item in files:
        if item["relative_path"] == "typed/exact-wheel.json":
            item.update({"byte_size": len(exact_raw), "sha256": _digest(exact_raw)})
            break
    manifest = {
        "schema_version": "deeplaw.external-qualification-bundle-manifest/v4",
        "profile": "machine_evaluated_no_human_attestation",
        "reference_provenance": "agent_consensus",
        "human_authenticity": "not_claimed",
        "candidate_run_id": 101,
        "evidence_run_id": 202,
        "candidate_binding": {
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": LOCK,
            "wheel_sha256": wheel_sha,
            "sdist_sha256": sdist_sha,
        },
        "external_inputs": external,
        "candidate_full_raw_inventory_sha256": _digest(inventory_raw),
        "files": files,
    }
    _write_json(root, "bundle-manifest.json", _record(manifest))
    return {"root": root, "manifest": root / "bundle-manifest.json"}


def _validate(paths: dict[str, Any]) -> dict[str, Any]:
    return validate_external_bundle(
        paths["root"],
        expected_candidate_run_id=101,
        expected_evidence_run_id=202,
    )


def _refresh_security_receipt(
    paths: dict[str, Any], role: str, receipt: dict[str, Any]
) -> None:
    path = paths["root"] / f"security/{role}.json"
    raw = _json_bytes(receipt)
    path.write_bytes(raw)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if item["relative_path"] == f"security/{role}.json":
            item.update({"byte_size": len(raw), "sha256": _digest(raw)})
            break
    paths["manifest"].write_bytes(_json_bytes(_record(manifest)))


def _refresh_reference_chain(paths: dict[str, Any], *, reviewer_index: int) -> None:
    root = paths["root"]
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    output_relative = f"reference/reviewer-{reviewer_index}-output.json"
    output_raw = (root / output_relative).read_bytes()

    semantic_path = root / "reference/semantic.json"
    semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
    semantic["agent_review"]["reviewers"][reviewer_index - 1]["output_sha256"] = _digest(
        output_raw
    )
    reviewers = semantic["agent_review"]["reviewers"]

    roster_path = root / "reference/roster.json"
    roster = json.loads(roster_path.read_text(encoding="utf-8"))
    roster["reviewers"] = reviewers
    roster_raw = _json_bytes(_record(roster))
    roster_path.write_bytes(roster_raw)

    consensus_path = root / "reference/consensus.json"
    consensus = json.loads(consensus_path.read_text(encoding="utf-8"))
    consensus["roster_sha256"] = _digest(roster_raw)
    consensus["reviewer_output_sha256s"] = [row["output_sha256"] for row in reviewers]
    consensus_raw = _json_bytes(_record(consensus))
    consensus_path.write_bytes(consensus_raw)

    semantic["agent_review"]["roster_sha256"] = _digest(roster_raw)
    semantic["agent_review"]["consensus_sha256"] = _digest(consensus_raw)
    semantic_raw = _json_bytes(_record(semantic))
    semantic_path.write_bytes(semantic_raw)

    binding_path = root / "reference/candidate-binding.json"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binding["semantic_reference"]["sha256"] = _digest(semantic_raw)
    binding["agent_roster"]["sha256"] = _digest(roster_raw)
    binding["agent_consensus"]["sha256"] = _digest(consensus_raw)
    binding_raw = _json_bytes(_record(binding))
    binding_path.write_bytes(binding_raw)

    replacements = {
        output_relative: output_raw,
        "reference/roster.json": roster_raw,
        "reference/consensus.json": consensus_raw,
        "reference/semantic.json": semantic_raw,
        "reference/candidate-binding.json": binding_raw,
    }
    for relative in ("typed/machine-holdout.json", "typed/machine-blind.json"):
        typed_path = root / relative
        typed = json.loads(typed_path.read_text(encoding="utf-8"))
        typed["payload"].update(
            {
                "semantic_reference_source": _source(root, "reference/semantic.json"),
                "candidate_binding_source": _source(
                    root, "reference/candidate-binding.json"
                ),
                "agent_roster_source": _source(root, "reference/roster.json"),
                "agent_consensus_source": _source(root, "reference/consensus.json"),
            }
        )
        typed_raw = _json_bytes(_record(typed))
        typed_path.write_bytes(typed_raw)
        replacements[relative] = typed_raw
    for item in manifest["files"]:
        raw = replacements.get(item["relative_path"])
        if raw is not None:
            item.update({"byte_size": len(raw), "sha256": _digest(raw)})
    manifest["external_inputs"].update(
        {
            "semantic_reference_sha256": _digest(semantic_raw),
            "candidate_binding_sha256": _digest(binding_raw),
            "agent_roster_sha256": _digest(roster_raw),
            "agent_consensus_sha256": _digest(consensus_raw),
        }
    )
    paths["manifest"].write_bytes(_json_bytes(_record(manifest)))


def test_v4_accepts_machine_only_bundle_and_returns_no_claim_flags(tmp_path: Path) -> None:
    result = _validate(_seed(tmp_path))
    assert result["schema_version"].endswith("/v4")
    assert result["validation_level"] == "structural_preflight"
    assert result["typed_derivation_performed"] is False
    assert result["qualification_passed"] is False
    assert result["machine_reference_scorer_count"] == 2
    assert result["machine_reference_roles"] == ["qualification_holdout", "final_blind"]
    assert result["human_authenticity"] == "not_claimed"
    assert "release_ready" not in result
    assert "claim_eligible" not in result


def test_v4_rejects_schema_invalid_reviewer_output_even_with_fresh_hash_chain(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    _write_json(
        paths["root"],
        "reference/reviewer-1-output.json",
        {
            "agent_id": "agent:reviewer-1",
            "decision": "approved",
            "unexpected": True,
        },
    )
    _refresh_reference_chain(paths, reviewer_index=1)

    with pytest.raises(ExternalQualificationBundleV4Error, match="reviewer output"):
        _validate(paths)


def test_v4_rejects_human_gold_even_when_self_hashed(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    root = paths["root"]
    human_path = root / "typed/human.json"
    human_path.write_bytes((root / "typed/machine-holdout.json").read_bytes())
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "relative_path": "typed/human.json",
            "byte_size": human_path.stat().st_size,
            "sha256": _digest(human_path.read_bytes()),
            "media_type": "application/json",
            "evidence_kind": "human_gold_scorer",
        }
    )
    paths["manifest"].write_bytes(_json_bytes(_record(manifest)))
    with pytest.raises(
        ExternalQualificationBundleV4Error,
        match=r"schema validation|human Gold",
    ):
        _validate(paths)


def test_v4_rejects_caller_authored_pass_without_real_receipt(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    root = paths["root"]
    rows = root / "rows/holdout-a.json"
    rows.write_bytes(_json_bytes({"passed": True}))
    typed_path = root / "typed/machine-holdout.json"
    typed = json.loads(typed_path.read_text(encoding="utf-8"))
    typed["payload"]["scorer_a_rows_source"].update(
        {"byte_size": rows.stat().st_size, "sha256": _digest(rows.read_bytes())}
    )
    typed_path.write_bytes(_json_bytes(_record(typed)))
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if item["relative_path"] == "rows/holdout-a.json":
            item.update({"byte_size": rows.stat().st_size, "sha256": _digest(rows.read_bytes())})
        if item["relative_path"] == "typed/machine-holdout.json":
            item.update(
                {
                    "byte_size": typed_path.stat().st_size,
                    "sha256": _digest(typed_path.read_bytes()),
                }
            )
    paths["manifest"].write_bytes(_json_bytes(_record(manifest)))
    with pytest.raises(ExternalQualificationBundleV4Error, match="caller-authored"):
        _validate(paths)


def test_v4_rejects_machine_manifest_without_bound_arbiter_scorer(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    typed_path = paths["root"] / "typed/machine-holdout.json"
    typed = json.loads(typed_path.read_text(encoding="utf-8"))
    typed.pop("scorer")
    typed_path.write_bytes(_json_bytes(_record(typed)))
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if item["relative_path"] == "typed/machine-holdout.json":
            item.update(
                {
                    "byte_size": typed_path.stat().st_size,
                    "sha256": _digest(typed_path.read_bytes()),
                }
            )
    paths["manifest"].write_bytes(_json_bytes(_record(manifest)))

    with pytest.raises(ExternalQualificationBundleV4Error, match="schema validation"):
        _validate(paths)


def test_v4_rejects_orphan_and_symlink(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    (paths["root"] / "orphan.json").write_bytes(b"{}")
    with pytest.raises(
        ExternalQualificationBundleV4Error,
        match=r"orphan|unreferenced",
    ):
        _validate(paths)
    paths = _seed(tmp_path / "symlink")
    link = paths["root"] / "rows/link.json"
    try:
        link.symlink_to(paths["root"] / "rows/holdout-a.json")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ExternalQualificationBundleV4Error, match="symbolic link"):
        _validate(paths)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b'{"x":Infinity}\n'])
def test_v4_rejects_duplicate_or_nonfinite_manifest(tmp_path: Path, raw: bytes) -> None:
    paths = _seed(tmp_path)
    paths["manifest"].write_bytes(raw)
    with pytest.raises(
        ExternalQualificationBundleV4Error,
        match=r"strict JSON|non-finite|duplicate",
    ):
        _validate(paths)


def test_v4_rejects_wrong_candidate_binding_even_with_valid_record(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["candidate_binding"]["commit"] = "a" * 40
    paths["manifest"].write_bytes(_json_bytes(_record(manifest)))
    with pytest.raises(ExternalQualificationBundleV4Error):
        _validate(paths)


def test_v4_rejects_exact_wheel_bound_to_external_or_caller_runner(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    typed_path = paths["root"] / "typed/exact-wheel.json"
    typed = json.loads(typed_path.read_text(encoding="utf-8"))
    typed["runner"] = {"identity": "runner:isolated", "sha256": RUNNER}
    typed_path.write_bytes(_json_bytes(_record(typed)))
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if item["relative_path"] == "typed/exact-wheel.json":
            item.update(
                {
                    "byte_size": typed_path.stat().st_size,
                    "sha256": _digest(typed_path.read_bytes()),
                }
            )
    paths["manifest"].write_bytes(_json_bytes(_record(manifest)))

    with pytest.raises(
        ExternalQualificationBundleV4Error,
        match="exact-wheel typed evidence runner identity differs",
    ):
        _validate(paths)


def test_v4_requires_distinct_candidate_and_evidence_run_ids(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    with pytest.raises(ExternalQualificationBundleV4Error, match="distinct"):
        validate_external_bundle(
            paths["root"],
            expected_candidate_run_id=101,
            expected_evidence_run_id=101,
        )


def test_v4_rejects_non_candidate_full_inventory_schema(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    inventory_path = paths["root"] / "candidate/candidate-full-raw-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory["schema_version"] = "deeplaw.candidate-full-raw-inventory/v1"
    inventory_path.write_bytes(_json_bytes(inventory))
    inventory_raw = inventory_path.read_bytes()
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["candidate_full_raw_inventory_sha256"] = _digest(inventory_raw)
    for item in manifest["files"]:
        if item["relative_path"] == "candidate/candidate-full-raw-inventory.json":
            item.update(
                {
                    "byte_size": len(inventory_raw),
                    "sha256": _digest(inventory_raw),
                }
            )
    paths["manifest"].write_bytes(_json_bytes(_record(manifest)))

    with pytest.raises(ExternalQualificationBundleV4Error, match="inventory kind"):
        _validate(paths)


@pytest.mark.parametrize(
    ("relative", "message"),
    [
        ("reference/reviewer-1-output.json", "reviewer output"),
        ("corpus/final-blind.json", "final blind holdout"),
    ],
)
def test_v4_requires_retained_reviewer_and_corpus_bytes(
    tmp_path: Path,
    relative: str,
    message: str,
) -> None:
    paths = _seed(tmp_path)
    selected = paths["root"] / relative
    selected.unlink()
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["files"] = [
        item for item in manifest["files"] if item["relative_path"] != relative
    ]
    paths["manifest"].write_bytes(_json_bytes(_record(manifest)))

    with pytest.raises(ExternalQualificationBundleV4Error, match=message):
        _validate(paths)


def test_v4_rejects_security_domain_executable_hash_drift(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    receipt_path = paths["root"] / "security/scorer_a.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["executable"]["executable_sha256"] = "f" * 64
    receipt["record_sha256"] = record_sha256(receipt)
    receipt_raw = _json_bytes(receipt)
    receipt_path.write_bytes(receipt_raw)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if item["relative_path"] == "security/scorer_a.json":
            item.update({"byte_size": len(receipt_raw), "sha256": _digest(receipt_raw)})
    paths["manifest"].write_bytes(_json_bytes(_record(manifest)))
    with pytest.raises(ExternalQualificationBundleV4Error, match="executable hash drifted"):
        _validate(paths)


def test_v4_rejects_security_domain_attester_hash_drift(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    receipt_path = paths["root"] / "security/scorer_a.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["attester_executable_sha256"] = "f" * 64
    receipt["record_sha256"] = record_sha256(receipt)
    _refresh_security_receipt(paths, "scorer_a", receipt)
    with pytest.raises(
        ExternalQualificationBundleV4Error,
        match="attester executable hash drifted",
    ):
        _validate(paths)


def test_v4_rejects_security_domain_read_only_ingress_drift(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    receipt_path = paths["root"] / "security/scorer_a.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["mount"]["read_only_input_sha256s"] = []
    receipt["record_sha256"] = record_sha256(receipt)
    _refresh_security_receipt(paths, "scorer_a", receipt)
    with pytest.raises(
        ExternalQualificationBundleV4Error,
        match="read-only input hashes must equal artifact ingress",
    ):
        _validate(paths)


def test_v4_rejects_security_domain_producer_consumer_hash_drift(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    receipt_path = paths["root"] / "security/scorer_a.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["egress"][0]["sha256"] = "f" * 64
    receipt["record_sha256"] = record_sha256(receipt)
    _refresh_security_receipt(paths, "scorer_a", receipt)
    with pytest.raises(
        ExternalQualificationBundleV4Error,
        match="producer/consumer artifact hash differs",
    ):
        _validate(paths)


def test_v4_rejects_arbitrary_or_missing_prohibited_visibility_hash(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    receipt_path = paths["root"] / "security/scorer_a.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["visibility"]["cannot_read"] = ["f" * 64]
    receipt["record_sha256"] = record_sha256(receipt)
    _refresh_security_receipt(paths, "scorer_a", receipt)
    with pytest.raises(
        ExternalQualificationBundleV4Error,
        match="negative-canary targets",
    ):
        _validate(paths)


def test_v4_rejects_wrong_named_negative_canary_target(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    receipt_path = paths["root"] / "security/scorer_a.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["negative_canary"]["targets"][0]["name"] = "sealed-reference"
    receipt["record_sha256"] = record_sha256(receipt)
    _refresh_security_receipt(paths, "scorer_a", receipt)
    with pytest.raises(
        ExternalQualificationBundleV4Error,
        match="prohibited artifact visibility is incomplete",
    ):
        _validate(paths)


def test_v4_requires_provider_allowlist_for_candidate_domain(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    receipt_path = paths["root"] / "security/candidate_host.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["network"]["policy"] = "deny_all"
    receipt["record_sha256"] = record_sha256(receipt)
    _refresh_security_receipt(paths, "candidate_host", receipt)
    with pytest.raises(
        ExternalQualificationBundleV4Error,
        match="network policy is not provider allowlisted",
    ):
        _validate(paths)
