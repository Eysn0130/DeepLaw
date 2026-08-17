from __future__ import annotations

import copy
import hashlib
from typing import Any

import pytest

from benchmarks.release.security_domain_receipt import (
    ROLE_LABELS,
    SecurityDomainReceiptError,
    process_receipt_set_sha256,
    record_sha256,
    security_domain_set_sha256,
    validate_security_domain_receipt,
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _receipt(role: str) -> dict[str, Any]:
    role_inputs = {
        "reference_freezer": {"reference-cases", "reviewer-inputs"},
        "candidate_host": {
            "verified-candidate-artifacts",
            "qualification-inputs",
            "final-blind-inputs",
        },
        "scorer_a": {"candidate-sanitized-output", "sealed-reference"},
        "scorer_b": {"candidate-sanitized-output", "sealed-reference"},
        "arbiter": {"scorer-a-output", "scorer-b-output"},
    }
    role_outputs = {
        "reference_freezer": "sealed-reference",
        "candidate_host": "candidate-sanitized-output",
        "scorer_a": "scorer-a-output",
        "scorer_b": "scorer-b-output",
        "arbiter": "arbiter-output",
    }
    forbidden = {
        "reference_freezer": {
            "candidate-sanitized-output",
            "scorer-a-output",
            "scorer-b-output",
            "arbiter-output",
        },
        "candidate_host": {
            "sealed-reference",
            "scorer-a-output",
            "scorer-b-output",
            "arbiter-output",
        },
        "scorer_a": {"scorer-b-output", "arbiter-output"},
        "scorer_b": {"scorer-a-output", "arbiter-output"},
        "arbiter": {"candidate-sanitized-output", "sealed-reference"},
    }

    def artifact(name: str) -> dict[str, str]:
        # The artifact digest is stable across producer and consumer domains.
        return {"name": name, "sha256": _sha(f"artifact:{name}")}

    ingress = [artifact(name) for name in sorted(role_inputs[role])]
    can_read = [row["sha256"] for row in ingress]
    canary_targets = [
        {"name": name, "sha256": _sha(f"negative-canary:{role}:{name}")}
        for name in sorted(forbidden[role])
    ]
    cannot_read = [item["sha256"] for item in canary_targets]
    process_receipts = [
        _sha(f"process-receipt:{role}:{index}")
        for index in range(2 if role == "candidate_host" else 1)
    ]
    value: dict[str, Any] = {
        "schema_version": "deeplaw.security-domain-receipt/v1",
        "profile": "machine_evaluated_no_human_attestation",
        "role": role,
        "domain_id": f"domain:{role}",
        "runner": {
            "ephemeral_runner_id": f"runner:{role}",
            "runner_label": ROLE_LABELS[role],
            "runner_attestation_sha256": _sha(f"attestation:{role}"),
        },
        "executable": {
            "executable_sha256": _sha(f"executable:{role}"),
            "process_tree_sha256": _sha(f"tree:{role}"),
        },
        "principal": {
            "uid": 1000,
            "principal_id": f"principal:{role}",
            "acl_sha256": _sha(f"acl:{role}"),
        },
        "mount": {
            "namespace_id": f"mount:{role}",
            "inventory_sha256": _sha(f"mount-inventory:{role}"),
            "read_only_input_sha256s": can_read,
        },
        "network": {
            "policy": "host_provider_allowlist"
            if role == "candidate_host"
            else "deny_all",
            "policy_sha256": _sha(f"network:{role}"),
        },
        "ipc": {
            "namespace_id": f"ipc:{role}",
            "policy": "artifact_pipe_only",
            "policy_sha256": _sha(f"ipc:{role}"),
        },
        "ingress": ingress,
        "egress": [artifact(role_outputs[role])],
        "visibility": {"can_read": can_read, "cannot_read": cannot_read},
        "negative_canary": {
            "attempts": len(canary_targets),
            "targets": canary_targets,
            "leaked_count": 0,
            "observation_sha256": _sha(f"canary:{role}"),
        },
        "secret_policy": "broker_only_exact_host"
        if role == "candidate_host"
        else "forbidden",
        "process_receipt_sha256": process_receipt_set_sha256(process_receipts),
        "process_receipt_sha256s": process_receipts,
        "observed_roots_sha256": _sha(f"observed-roots:{role}"),
        "attester_executable_sha256": _sha("frozen-security-domain-attester"),
        "observed": {
            "source": "os_runner_attestation",
            "command_id": f"observe:{role}",
            "attestation_sha256": _sha(f"os:{role}"),
        },
    }
    value["record_sha256"] = record_sha256(value)
    return value


def test_valid_sanitized_receipt_is_self_bound_and_set_is_canonical() -> None:
    roles = ["reference_freezer", "candidate_host", "scorer_a", "scorer_b", "arbiter"]
    receipts = [_receipt(role) for role in roles]
    assert all(validate_security_domain_receipt(item) == item for item in receipts)
    assert security_domain_set_sha256(receipts) == security_domain_set_sha256(
        list(reversed(receipts))
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"separate_processes": True}),
        lambda value: value["negative_canary"].update({"leaked_count": 1}),
        lambda value: value["negative_canary"].update({"targets": []}),
        lambda value: value["mount"].update({"namespace_id": "/private/shared"}),
        lambda value: value["mount"].update({"read_only_input_sha256s": []}),
        lambda value: value["observed"].update({"attestation_sha256": "not-a-hash"}),
        lambda value: value["observed"].update({"command_id": "observe /srv/secret"}),
        lambda value: value["executable"].update({"executable_sha256": "f" * 64}),
        lambda value: value.update({"attester_executable_sha256": "0" * 64}),
    ],
)
def test_receipt_rejects_caller_claims_or_unverifiable_os_observation(mutate: Any) -> None:
    value = _receipt("scorer_a")
    mutated = copy.deepcopy(value)
    mutate(mutated)
    if mutated.get("record_sha256") == record_sha256(mutated):
        mutated["record_sha256"] = "0" * 64
    with pytest.raises(SecurityDomainReceiptError):
        validate_security_domain_receipt(mutated)


def test_receipt_rejects_duplicate_domain_identity() -> None:
    first = _receipt("scorer_a")
    second = _receipt("scorer_b")
    second["domain_id"] = first["domain_id"]
    second["record_sha256"] = record_sha256(second)
    with pytest.raises(SecurityDomainReceiptError, match="not distinct"):
        security_domain_set_sha256([first, second])


def test_receipt_rejects_assembly_role_from_mandatory_set() -> None:
    value = _receipt("arbiter")
    value["role"] = "assembly"
    value["record_sha256"] = record_sha256(value)
    with pytest.raises(SecurityDomainReceiptError, match="role is invalid"):
        validate_security_domain_receipt(value)
