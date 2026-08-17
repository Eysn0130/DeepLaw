from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.release.security_domain_receipt import (
    ROLE_LABELS,
    process_receipt_set_sha256,
    record_sha256,
    security_domain_set_sha256,
    validate_security_domain_receipt,
)
from benchmarks.release.typed_qualification_evidence import (
    TypedQualificationEvidenceError,
    parse_typed_evidence,
)

COMMIT = "1" * 40
TREE = "2" * 40
LOCK = "3" * 64
WHEEL = "4" * 64
SDIST = "5" * 64
HOLDOUT = "6" * 64
BLIND = "7" * 64
SCORER_A_SHA = "8" * 64
SCORER_B_SHA = "9" * 64
RUNNER_SHA = "a" * 64
ARBITER_SHA = "b" * 64
RUBRIC_SHA = "c" * 64
SOURCE_CORPUS_SHA = "d" * 64
SECURITY_ROLES = (
    "reference_freezer",
    "candidate_host",
    "scorer_a",
    "scorer_b",
    "arbiter",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    value["record_sha256"] = _sha(_canonical(value))
    return value


def _write(root: Path, relative: str, value: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical(value)
    path.write_bytes(raw)
    return (
        {
            "relative_path": relative,
            "byte_size": len(raw),
            "sha256": _sha(raw),
            "media_type": "application/json",
        },
        raw,
    )


def _receipt(envelope: dict[str, Any], scorer: dict[str, str]) -> dict[str, Any]:
    return {
        "candidate": envelope["candidate_binding"],
        "run": envelope["run_binding"],
        "corpus": envelope["corpus"],
        "runner": envelope["runner"],
        "scorer": {"identity": scorer["identity"], "sha256": scorer["sha256"]},
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
        return {"name": name, "sha256": _sha(f"artifact:{name}".encode())}

    ingress = [artifact(name) for name in sorted(ingress_names)]
    egress = [artifact(egress_name)]
    canary_targets = [
        {
            "name": name,
            "sha256": _sha(f"negative-canary:{role}:{name}".encode()),
        }
        for name in sorted(forbidden_names)
    ]
    process_receipts = process_receipt_sha256s or [
        _sha(f"process-receipt:{role}".encode())
    ]
    value: dict[str, Any] = {
        "schema_version": "deeplaw.security-domain-receipt/v1",
        "profile": "machine_evaluated_no_human_attestation",
        "role": role,
        "domain_id": f"domain:{role}",
        "runner": {
            "ephemeral_runner_id": f"runner:{role}",
            "runner_label": ROLE_LABELS[role],
            "runner_attestation_sha256": _sha(f"runner-attestation:{role}".encode()),
        },
        "executable": {
            "executable_sha256": executable_sha256
            or _sha(f"executable:{role}".encode()),
            "process_tree_sha256": _sha(f"process-tree:{role}".encode()),
        },
        "principal": {
            "uid": 1000 + len(role),
            "principal_id": f"principal:{role}",
            "acl_sha256": _sha(f"acl:{role}".encode()),
        },
        "mount": {
            "namespace_id": f"mount:{role}",
            "inventory_sha256": _sha(f"mount:{role}".encode()),
            "read_only_input_sha256s": [item["sha256"] for item in ingress],
        },
        "network": {
            "policy": network_policy,
            "policy_sha256": _sha(f"network:{role}".encode()),
        },
        "ipc": {
            "namespace_id": f"ipc:{role}",
            "policy": "artifact_pipe_only",
            "policy_sha256": _sha(f"ipc:{role}".encode()),
        },
        "ingress": ingress,
        "egress": egress,
        "visibility": {
            "can_read": [item["sha256"] for item in ingress],
            "cannot_read": [item["sha256"] for item in canary_targets],
        },
        "negative_canary": {
            "attempts": len(canary_targets),
            "targets": canary_targets,
            "leaked_count": 0,
            "observation_sha256": _sha(f"canary:{role}:zero".encode()),
        },
        "secret_policy": "broker_only_exact_host" if role == "candidate_host" else "forbidden",
        "process_receipt_sha256": process_receipt_set_sha256(process_receipts),
        "process_receipt_sha256s": process_receipts,
        "observed_roots_sha256": _sha(f"observed-roots:{role}".encode()),
        "attester_executable_sha256": _sha(b"frozen-security-domain-attester"),
        "observed": {
            "source": "os_runner_attestation",
            "command_id": f"observe:{role}",
            "attestation_sha256": _sha(f"os-observation:{role}".encode()),
        },
    }
    value["record_sha256"] = record_sha256(value)
    return value


def _fixture(root: Path) -> tuple[Path, dict[str, Any]]:
    reviewers = [
        {
            "agent_id": f"agent:reviewer-{index}",
            "role": f"semantic-reviewer-{index}",
            "model_id": f"model:reviewer-{index}",
            "implementation_sha256": f"{index}" * 64,
            "prompt_sha256": f"{index + 3}" * 64,
            "process_identity_sha256": f"{index + 6}" * 64,
            "output_sha256": f"{index + 9:x}" * 64,
            "conclusions_hidden_from_peers": True,
            "separate_process": True,
        }
        for index in (1, 2, 3)
    ]
    reference_id = "semanticref_0123456789abcdef01234567"
    profile = "machine_evaluated_no_human_attestation"
    roster = _seal(
        {
            "schema_version": "deeplaw.agent-review-roster/v1",
            "profile": profile,
            "reference_id": reference_id,
            "reviewers": reviewers,
        }
    )
    roster_ref, roster_raw = _write(root, "reference/roster.json", roster)
    consensus = _seal(
        {
            "schema_version": "deeplaw.agent-review-consensus/v1",
            "profile": profile,
            "reference_id": reference_id,
            "roster_sha256": _sha(roster_raw),
            "rubric_sha256": RUBRIC_SHA,
            "source_corpus_sha256": SOURCE_CORPUS_SHA,
            "reviewer_output_sha256s": [row["output_sha256"] for row in reviewers],
            "unanimous": True,
            "disagreements": [],
        }
    )
    consensus_ref, consensus_raw = _write(root, "reference/consensus.json", consensus)
    isolation = _seal(
        {
            "schema_version": "deeplaw.agent-review-isolation/v1",
            "profile": profile,
            "reference_id": reference_id,
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
    )
    isolation_ref, isolation_raw = _write(root, "reference/isolation.json", isolation)
    thresholds = {
        "minimum_case_pass_rate": 1.0,
        "minimum_duty_coverage": 1.0,
        "maximum_hard_failures": 0,
        "maximum_false_authority": 0,
    }
    cases = [
        {
            "case_id": f"goldcase_{name}",
            "labels": ["bounded_answer"],
            "expected": {"include": ["bounded_answer"], "exclude": []},
            "duties": ["exact_citation"],
            "hard_failures": ["false_authority"],
            "thresholds": thresholds,
        }
        for name in ("cold", "resume", "compact")
    ]
    reference = _seal(
        {
            "schema_version": "deeplaw.semantic-machine-reference/v1",
            "status": "semantic_machine_reference_frozen",
            "profile": profile,
            "reference_provenance": "agent_consensus",
            "human_authenticity": "not_claimed",
            "frozen_at": "2026-08-17T00:00:00Z",
            "reference_id": reference_id,
            "model_outputs_seen_before_freeze": True,
            "candidate_visible_when_frozen": False,
            "human_claim_eligible": False,
            "competitive_claim_eligible": False,
            "agent_review": {
                "reviewers": reviewers,
                "roster_sha256": _sha(roster_raw),
                "consensus_sha256": _sha(consensus_raw),
                "isolation_sha256": _sha(isolation_raw),
                "rubric_sha256": RUBRIC_SHA,
                "source_corpus_sha256": SOURCE_CORPUS_SHA,
                "minimum_distinct_agents": 3,
                "unanimity_required": True,
            },
            "labels": [
                {
                    "label_id": "bounded_answer",
                    "description": "The bounded expected result is present.",
                }
            ],
            "cases": cases,
            "duties": [
                {
                    "duty_id": "exact_citation",
                    "description": "The result retains exact evidence identity.",
                }
            ],
            "hard_failures": [
                {
                    "code": "false_authority",
                    "description": "Derived text is promoted to source authority.",
                }
            ],
            "thresholds": thresholds,
        }
    )
    reference_ref, reference_raw = _write(root, "reference/semantic.json", reference)

    scorer_a = {
        "role": "independent_scorer_a",
        "identity": "independent-scorer-a:machine-v1",
        "sha256": SCORER_A_SHA,
    }
    scorer_b = {
        "role": "independent_scorer_b",
        "identity": "independent-scorer-b:machine-v1",
        "sha256": SCORER_B_SHA,
    }
    panel = {
        "scorer_a": scorer_a,
        "scorer_b": scorer_b,
        "panel_sha256": _sha(_canonical({"scorer_a": scorer_a, "scorer_b": scorer_b})),
        "distinct_scorers": True,
    }
    arbiter = {
        "role": "deterministic_arbiter",
        "identity": "deterministic-arbiter:machine-v1",
        "sha256": ARBITER_SHA,
    }
    runner = {"identity": "runner:isolated-v1", "sha256": RUNNER_SHA}
    binding = _seal(
        {
            "schema_version": "deeplaw.candidate-gold-binding-receipt/v2",
            "status": "post_build_machine_reference_bound",
            "profile": profile,
            "reference_provenance": "agent_consensus",
            "human_authenticity": "not_claimed",
            "bound_at": "2026-08-17T01:00:00Z",
            "semantic_reference": {
                "reference_id": reference_id,
                "schema_version": "deeplaw.semantic-machine-reference/v1",
                "sha256": _sha(reference_raw),
            },
            "agent_roster": {"sha256": _sha(roster_raw)},
            "agent_consensus": {"sha256": _sha(consensus_raw)},
            "agent_isolation": {"sha256": _sha(isolation_raw)},
            "candidate": {"commit": COMMIT, "tree": TREE, "lock_sha256": LOCK},
            "artifacts": {
                "wheel": {
                    "name": "deeplaw-0.13.0-py3-none-any.whl",
                    "sha256": WHEEL,
                    "byte_size": 100,
                },
                "sdist": {
                    "name": "deeplaw-0.13.0.tar.gz",
                    "sha256": SDIST,
                    "byte_size": 120,
                },
            },
            "holdout": {"role": "qualification_holdout", "sha256": HOLDOUT},
            "blind": {"role": "final_blind", "sha256": BLIND},
            "scorer_panel": panel,
            "arbiter": arbiter,
            "runner": runner,
        }
    )
    binding_ref, _ = _write(root, "reference/binding.json", binding)
    process_receipts: dict[str, list[str]] = {}
    process_receipt_sources: list[dict[str, Any]] = []
    for role in SECURITY_ROLES:
        process_receipts[role] = []
        for index in range(2 if role == "candidate_host" else 1):
            source_ref, raw = _write(
                root,
                f"process/{role}-{index + 1}.json",
                {
                    "schema_version": "deeplaw.sanitized-process-observation/v1",
                    "role": role,
                    "ordinal": index + 1,
                },
            )
            process_receipts[role].append(_sha(raw))
            process_receipt_sources.append(source_ref)

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
            executable_sha256=RUNNER_SHA,
            process_receipt_sha256s=process_receipts["candidate_host"],
        ),
        "scorer_a": _security_receipt(
            "scorer_a",
            {"candidate-sanitized-output", "sealed-reference"},
            "scorer-a-output",
            forbidden_names={"scorer-b-output", "arbiter-output"},
            executable_sha256=SCORER_A_SHA,
            process_receipt_sha256s=process_receipts["scorer_a"],
        ),
        "scorer_b": _security_receipt(
            "scorer_b",
            {"candidate-sanitized-output", "sealed-reference"},
            "scorer-b-output",
            forbidden_names={"scorer-a-output", "arbiter-output"},
            executable_sha256=SCORER_B_SHA,
            process_receipt_sha256s=process_receipts["scorer_b"],
        ),
        "arbiter": _security_receipt(
            "arbiter",
            {"scorer-a-output", "scorer-b-output"},
            "arbiter-output",
            forbidden_names={"candidate-sanitized-output", "sealed-reference"},
            executable_sha256=ARBITER_SHA,
            process_receipt_sha256s=process_receipts["arbiter"],
        ),
    }
    security_domain_receipt_sources: list[dict[str, Any]] = []
    for role in SECURITY_ROLES:
        relative = f"security/{role}.json"
        source_ref, _ = _write(root, relative, security_receipts[role])
        security_domain_receipt_sources.append(source_ref)
        validate_security_domain_receipt(security_receipts[role], expected_role=role)
    assert security_domain_set_sha256(list(security_receipts.values()))
    envelope: dict[str, Any] = {
        "schema_version": "deeplaw.typed-qualification-evidence/v2",
        "profile": profile,
        "reference_provenance": "agent_consensus",
        "human_authenticity": "not_claimed",
        "kind": "machine_reference_scorer",
        "candidate_binding": {
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": LOCK,
            "wheel_sha256": WHEEL,
            "sdist_sha256": SDIST,
        },
        "run_binding": {"run_id": "machine-run-1", "workflow_run_id": 17},
        "corpus": {"role": "qualification_holdout", "sha256": HOLDOUT},
        "runner": runner,
        "scorer": {"identity": arbiter["identity"], "sha256": arbiter["sha256"]},
        "scorer_panel": panel,
        "arbiter": arbiter,
        "payload": {},
    }
    candidate_output_rows = [
        {
            "case_id": case["case_id"],
            "observed": case["expected"],
            "duties": case["duties"],
            "hard_failures": [],
            "false_authority": False,
        }
        for case in cases
    ]
    candidate_output = _seal(
        {
            "schema_version": "deeplaw.machine-candidate-output/v1",
            "profile": profile,
            "candidate": envelope["candidate_binding"],
            "run": envelope["run_binding"],
            "corpus": envelope["corpus"],
            "runner": envelope["runner"],
            "rows": candidate_output_rows,
        }
    )
    candidate_output_ref, candidate_output_raw = _write(
        root,
        "candidate/raw-output.json",
        candidate_output,
    )
    candidate_execution = _seal(
        {
            "schema_version": "deeplaw.machine-candidate-execution/v1",
            "profile": profile,
            "candidate": envelope["candidate_binding"],
            "run": envelope["run_binding"],
            "corpus": envelope["corpus"],
            "runner": envelope["runner"],
            "executable_sha256": envelope["runner"]["sha256"],
            "process": {
                "pid": 101,
                "parent_pid": 100,
                "process_tree_sha256": "e" * 64,
                "environment_key_allowlist": ["PATH"],
                "read_only_input_sha256s": [WHEEL, HOLDOUT],
                "started_at": "2026-08-17T02:00:00Z",
                "finished_at": "2026-08-17T02:01:00Z",
                "exit_code": 0,
            },
            "output_sha256": _sha(candidate_output_raw),
        }
    )
    candidate_execution_ref, _ = _write(
        root,
        "candidate/execution.json",
        candidate_execution,
    )
    rows_a = []
    rows_b = []
    for case, output_row in zip(cases, candidate_output_rows, strict=True):
        common = {
            "case_id": case["case_id"],
            "expected": case["expected"],
            "observed": case["expected"],
            "duties": case["duties"],
            "hard_failures": [],
            "runner_process_id": "runner:process",
            "false_authority": False,
            "candidate_output_row_sha256": _sha(_canonical(output_row)),
        }
        rows_a.append({**common, "scorer_process_id": "scorer-a:process"})
        rows_b.append({**common, "scorer_process_id": "scorer-b:process"})
    scorer_a_ref, _ = _write(
        root,
        "scorer/a-rows.json",
        {"receipt": _receipt(envelope, scorer_a), "rows": rows_a},
    )
    scorer_b_ref, _ = _write(
        root,
        "scorer/b-rows.json",
        {"receipt": _receipt(envelope, scorer_b), "rows": rows_b},
    )
    arbiter_rows = [
        {
            "case_id": row_a["case_id"],
            "expected": row_a["expected"],
            "observed": row_a["observed"],
            "duties": row_a["duties"],
            "hard_failures": row_a["hard_failures"],
            "false_authority": row_a["false_authority"],
            "scorer_a_row_sha256": _sha(_canonical(row_a)),
            "scorer_b_row_sha256": _sha(_canonical(row_b)),
            "arbiter_process_id": "arbiter:process",
            "agreement": True,
        }
        for row_a, row_b in zip(rows_a, rows_b, strict=True)
    ]
    arbiter_ref, _ = _write(
        root,
        "scorer/arbiter-rows.json",
        {"receipt": _receipt(envelope, envelope["scorer"]), "rows": arbiter_rows},
    )
    envelope["payload"] = {
        "candidate_execution_source": candidate_execution_ref,
        "candidate_output_source": candidate_output_ref,
        "semantic_reference_source": reference_ref,
        "candidate_binding_source": binding_ref,
        "agent_roster_source": roster_ref,
        "agent_consensus_source": consensus_ref,
        "agent_isolation_source": isolation_ref,
        "security_domain_receipt_sources": security_domain_receipt_sources,
        "process_receipt_sources": process_receipt_sources,
        "scorer_a_rows_source": scorer_a_ref,
        "scorer_b_rows_source": scorer_b_ref,
        "arbiter_consensus_rows_source": arbiter_ref,
        "process_identity": {
            "scorer_a_process_id": "scorer-a:process",
            "scorer_b_process_id": "scorer-b:process",
            "runner_process_id": "runner:process",
            "arbiter_process_id": "arbiter:process",
            "scorer_a_identity_sha256": SCORER_A_SHA,
            "scorer_b_identity_sha256": SCORER_B_SHA,
            "runner_identity_sha256": RUNNER_SHA,
            "arbiter_identity_sha256": ARBITER_SHA,
            "scorer_processes_distinct": True,
            "arbiter_process_distinct": True,
            "separate_processes": True,
        },
    }
    _seal(envelope)
    manifest, _ = _write(root, "manifest.json", envelope)
    return root / manifest["relative_path"], envelope


def _rewrite_manifest(path: Path, envelope: dict[str, Any]) -> None:
    envelope.pop("record_sha256", None)
    _seal(envelope)
    path.write_bytes(_canonical(envelope))


def test_machine_reference_recomputes_pass_without_human_attestation(tmp_path: Path) -> None:
    manifest, _ = _fixture(tmp_path)

    result = parse_typed_evidence(
        manifest,
        root=tmp_path,
        expected_candidate={
            "commit": COMMIT,
            "tree": TREE,
            "lock_sha256": LOCK,
            "wheel_sha256": WHEEL,
            "sdist_sha256": SDIST,
        },
        expected_workflow_run_id=17,
        expected_corpus_sha256=HOLDOUT,
    )

    assert result["schema_version"] == "deeplaw.typed-qualification-derived/v2"
    assert result["status"] == "passed"
    assert result["metrics"]["case_pass_rate"] == 1.0
    assert result["metrics"]["human_attested"] is False
    assert result["metrics"]["human_authenticity"] == "not_claimed"
    assert len(result["metrics"]["security_domains_sha256"]) == 64


def test_machine_reference_rejects_a_candidate_domain_without_broker_boundary(
    tmp_path: Path,
) -> None:
    manifest, envelope = _fixture(tmp_path)
    source = next(
        item
        for item in envelope["payload"]["security_domain_receipt_sources"]
        if item["relative_path"] == "security/candidate_host.json"
    )
    receipt_path = tmp_path / source["relative_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["network"]["policy"] = "deny_all"
    receipt["record_sha256"] = record_sha256(receipt)
    raw = _canonical(receipt)
    receipt_path.write_bytes(raw)
    source.update({"byte_size": len(raw), "sha256": _sha(raw)})
    _rewrite_manifest(manifest, envelope)

    with pytest.raises(TypedQualificationEvidenceError, match="broker-only"):
        parse_typed_evidence(manifest, root=tmp_path)


def test_machine_reference_cannot_pass_without_retained_candidate_output_and_execution(
    tmp_path: Path,
) -> None:
    manifest, envelope = _fixture(tmp_path)
    envelope["payload"].pop("candidate_output_source")
    _rewrite_manifest(manifest, envelope)

    with pytest.raises(
        TypedQualificationEvidenceError,
        match=r"candidate (?:output|execution)",
    ):
        parse_typed_evidence(
            manifest,
            root=tmp_path,
            expected_candidate={
                "commit": COMMIT,
                "tree": TREE,
                "lock_sha256": LOCK,
                "wheel_sha256": WHEEL,
                "sdist_sha256": SDIST,
            },
            expected_workflow_run_id=17,
            expected_corpus_sha256=HOLDOUT,
        )


def test_machine_reference_cannot_pass_without_retained_process_receipt_bytes(
    tmp_path: Path,
) -> None:
    manifest, envelope = _fixture(tmp_path)
    envelope["payload"]["process_receipt_sources"].pop()
    _rewrite_manifest(manifest, envelope)

    with pytest.raises(
        TypedQualificationEvidenceError,
        match="process receipt",
    ):
        parse_typed_evidence(manifest, root=tmp_path)


def test_replacing_candidate_output_invalidates_old_scorer_receipts(tmp_path: Path) -> None:
    manifest, envelope = _fixture(tmp_path)
    output_path = tmp_path / envelope["payload"]["candidate_output_source"]["relative_path"]
    output = json.loads(output_path.read_text(encoding="utf-8"))
    output["rows"][0]["observed"] = {"include": [], "exclude": ["bounded_answer"]}
    output.pop("record_sha256")
    _seal(output)
    output_path.write_bytes(_canonical(output))
    output_raw = output_path.read_bytes()
    envelope["payload"]["candidate_output_source"].update(
        {"byte_size": len(output_raw), "sha256": _sha(output_raw)}
    )
    execution_path = tmp_path / envelope["payload"]["candidate_execution_source"][
        "relative_path"
    ]
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    execution["output_sha256"] = _sha(output_raw)
    execution.pop("record_sha256")
    _seal(execution)
    execution_path.write_bytes(_canonical(execution))
    execution_raw = execution_path.read_bytes()
    envelope["payload"]["candidate_execution_source"].update(
        {"byte_size": len(execution_raw), "sha256": _sha(execution_raw)}
    )
    _rewrite_manifest(manifest, envelope)

    with pytest.raises(TypedQualificationEvidenceError, match="candidate output"):
        parse_typed_evidence(
            manifest,
            root=tmp_path,
            expected_candidate={
                "commit": COMMIT,
                "tree": TREE,
                "lock_sha256": LOCK,
                "wheel_sha256": WHEEL,
                "sdist_sha256": SDIST,
            },
            expected_workflow_run_id=17,
            expected_corpus_sha256=HOLDOUT,
        )


def test_caller_authored_pass_is_rejected_even_with_fresh_record_hash(tmp_path: Path) -> None:
    manifest, envelope = _fixture(tmp_path)
    envelope["payload"]["passed"] = True
    _rewrite_manifest(manifest, envelope)

    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(manifest, root=tmp_path)


def test_scorer_disagreement_is_rejected(tmp_path: Path) -> None:
    manifest, envelope = _fixture(tmp_path)
    scorer_path = tmp_path / envelope["payload"]["scorer_b_rows_source"]["relative_path"]
    scorer = json.loads(scorer_path.read_text(encoding="utf-8"))
    scorer["rows"][0]["observed"] = {"include": ["different"], "exclude": []}
    scorer_path.write_bytes(_canonical(scorer))
    envelope["payload"]["scorer_b_rows_source"]["byte_size"] = scorer_path.stat().st_size
    envelope["payload"]["scorer_b_rows_source"]["sha256"] = _sha(scorer_path.read_bytes())
    _rewrite_manifest(manifest, envelope)

    with pytest.raises(TypedQualificationEvidenceError, match="exact agreement"):
        parse_typed_evidence(manifest, root=tmp_path)


def test_panel_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    manifest, envelope = _fixture(tmp_path)
    envelope["scorer_panel"]["panel_sha256"] = "f" * 64
    binding_path = tmp_path / envelope["payload"]["candidate_binding_source"]["relative_path"]
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binding["scorer_panel"]["panel_sha256"] = "f" * 64
    binding.pop("record_sha256")
    _seal(binding)
    binding_path.write_bytes(_canonical(binding))
    envelope["payload"]["candidate_binding_source"]["byte_size"] = binding_path.stat().st_size
    envelope["payload"]["candidate_binding_source"]["sha256"] = _sha(
        binding_path.read_bytes()
    )
    _rewrite_manifest(manifest, envelope)

    with pytest.raises(TypedQualificationEvidenceError, match="panel identity"):
        parse_typed_evidence(manifest, root=tmp_path)


def test_process_identity_collision_is_rejected(tmp_path: Path) -> None:
    manifest, envelope = _fixture(tmp_path)
    envelope["payload"]["process_identity"]["arbiter_process_id"] = "runner:process"
    _rewrite_manifest(manifest, envelope)

    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(manifest, root=tmp_path)


def test_orphan_receipt_is_rejected(tmp_path: Path) -> None:
    manifest, _ = _fixture(tmp_path)
    (tmp_path / "orphan.json").write_text("{}", encoding="utf-8")

    with pytest.raises(TypedQualificationEvidenceError, match="unreferenced"):
        parse_typed_evidence(manifest, root=tmp_path)


def test_human_authenticity_claim_is_rejected(tmp_path: Path) -> None:
    manifest, envelope = _fixture(tmp_path)
    envelope["human_authenticity"] = "human_approved"
    _rewrite_manifest(manifest, envelope)

    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(manifest, root=tmp_path)


def test_reference_candidate_binding_mismatch_is_rejected(tmp_path: Path) -> None:
    manifest, envelope = _fixture(tmp_path)
    binding_path = tmp_path / envelope["payload"]["candidate_binding_source"]["relative_path"]
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binding["candidate"]["tree"] = "e" * 40
    binding.pop("record_sha256")
    _seal(binding)
    binding_path.write_bytes(_canonical(binding))
    envelope["payload"]["candidate_binding_source"]["byte_size"] = binding_path.stat().st_size
    envelope["payload"]["candidate_binding_source"]["sha256"] = _sha(
        binding_path.read_bytes()
    )
    _rewrite_manifest(manifest, envelope)

    with pytest.raises(TypedQualificationEvidenceError, match="binding mismatch"):
        parse_typed_evidence(manifest, root=tmp_path)


def test_fixture_mutation_does_not_reuse_original_envelope(tmp_path: Path) -> None:
    manifest, envelope = _fixture(tmp_path)
    original = copy.deepcopy(envelope)
    envelope["run_binding"]["run_id"] = "machine-run-2"

    assert json.loads(manifest.read_text(encoding="utf-8")) == original
