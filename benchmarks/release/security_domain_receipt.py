"""Validated, sanitized receipts for external-qualification security domains.

The receipt is deliberately an observation record rather than a claim record.  It
contains hashes and logical artifact identities obtained from the runner/OS
attestation path; a caller-authored ``separate_processes`` flag is not accepted
as evidence of isolation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
ABSOLUTE_PATH_RE = re.compile(
    r"(?:(?<![A-Za-z0-9/])/(?!/)(?:[^/\s\"']+(?:/[^/\s\"']*)*)?)|"
    r"(?:^|[\s\"'])[A-Za-z]:[\\/]",
    re.IGNORECASE,
)
SECRET_RE = re.compile(
    r"(?ix)(?:api[_-]?key|access[_-]?token|authorization|bearer|password|passwd|"
    r"private[_-]?key|secret|token)\s*[:=]\s*\S+|"
    r"-----begin[^\n]*(?:private|rsa|openssh)[^\n]*-----|"
    r"(?:ghp_|github_pat_|glpat-|xox[baprs]-|sk-[A-Za-z0-9])"
)

SCHEMA_VERSION = "deeplaw.security-domain-receipt/v1"
PROFILE = "machine_evaluated_no_human_attestation"
ROLES = frozenset(
    {
        "reference_freezer",
        "candidate_host",
        "scorer_a",
        "scorer_b",
        "arbiter",
    }
)
ROLE_LABELS = {
    "reference_freezer": "deeplaw-qualification-reference",
    "candidate_host": "deeplaw-qualification-candidate",
    "scorer_a": "deeplaw-qualification-scorer-a",
    "scorer_b": "deeplaw-qualification-scorer-b",
    "arbiter": "deeplaw-qualification-arbiter",
}


class SecurityDomainReceiptError(ValueError):
    """Raised when a receipt is not an OS-observed, sanitized v1 record."""


def _fail(message: str) -> None:
    raise SecurityDomainReceiptError(message)


def canonical_json_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        return encoded.encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise SecurityDomainReceiptError("security receipt is not canonical JSON") from error


def record_sha256(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _sha(value: Any, label: str) -> None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        _fail(f"{label} must be a SHA-256 digest")


def _identifier(value: Any, label: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        _fail(f"{label} is invalid")


def _safe_name(value: Any, label: str) -> None:
    if not isinstance(value, str) or not SAFE_NAME_RE.fullmatch(value):
        _fail(f"{label} is invalid")
    if any(part in {"", ".", ".."} for part in value.split("/")) or "\\" in value:
        _fail(f"{label} is invalid")
    if value in {".env", "auth", "auth.json", "transcript", "reasoning", "raw-events"}:
        _fail(f"{label} names forbidden material")


def _scan_strings(value: Any) -> None:
    if isinstance(value, str):
        if ABSOLUTE_PATH_RE.search(value) or SECRET_RE.search(value):
            _fail("security receipt contains a private path or Secret")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("security receipt key is not a string")
            if key.lower() in {
                "secret",
                "secrets",
                "credential",
                "credentials",
                "dotenv",
                "auth",
                "token",
                "password",
                "transcript",
                "reasoning",
                "raw_log",
            }:
                _fail("security receipt contains a forbidden field")
            _scan_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for item in value:
            _scan_strings(item)
    elif value is not None and not isinstance(value, (bool, int, float)):
        _fail("security receipt contains an unsupported value")


def _artifact_list(value: Any, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 256:
        _fail(f"{label} must be a bounded list")
    result: list[dict[str, str]] = []
    names: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"name", "sha256"}:
            _fail(f"{label} contains an open artifact row")
        _safe_name(item.get("name"), f"{label} artifact name")
        _sha(item.get("sha256"), f"{label} artifact hash")
        name = str(item["name"])
        if name in names:
            _fail(f"{label} contains a duplicate artifact")
        names.add(name)
        result.append({"name": name, "sha256": str(item["sha256"])})
    return result


def _hash_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 256:
        _fail(f"{label} must be a bounded digest list")
    result: list[str] = []
    for item in value:
        _sha(item, label)
        if item in result:
            _fail(f"{label} contains a duplicate digest")
        result.append(str(item))
    return result


def process_receipt_set_sha256(digests: Sequence[str]) -> str:
    """Bind the complete, order-independent process-receipt digest set."""

    values = _hash_list(list(digests), "process receipt hashes")
    if not values:
        _fail("process receipt hashes must not be empty")
    return hashlib.sha256(canonical_json_bytes(sorted(values))).hexdigest()


def validate_security_domain_receipt(
    value: Mapping[str, Any],
    *,
    expected_role: str | None = None,
) -> dict[str, Any]:
    """Validate one sanitized receipt and return its canonical value.

    ``observed.source`` and ``observed.attestation_sha256`` are mandatory.  The
    latter binds the receipt to an OS/runner observation; policy booleans alone
    are intentionally not part of the contract.
    """

    if not isinstance(value, Mapping):
        _fail("security domain receipt must be an object")
    _scan_strings(value)
    required = {
        "schema_version",
        "profile",
        "role",
        "domain_id",
        "runner",
        "executable",
        "principal",
        "mount",
        "network",
        "ipc",
        "ingress",
        "egress",
        "visibility",
        "negative_canary",
        "secret_policy",
        "process_receipt_sha256",
        "process_receipt_sha256s",
        "observed_roots_sha256",
        "attester_executable_sha256",
        "observed",
        "record_sha256",
    }
    if set(value) != required:
        _fail("security domain receipt fields are not closed")
    if value.get("schema_version") != SCHEMA_VERSION or value.get("profile") != PROFILE:
        _fail("security domain receipt version or profile differs")
    role = value.get("role")
    if role not in ROLES or (expected_role is not None and role != expected_role):
        _fail("security domain receipt role is invalid")
    _identifier(value.get("domain_id"), "domain_id")

    runner = value["runner"]
    if not isinstance(runner, Mapping) or set(runner) != {
        "ephemeral_runner_id",
        "runner_label",
        "runner_attestation_sha256",
    }:
        _fail("runner observation is not closed")
    _identifier(runner.get("ephemeral_runner_id"), "ephemeral runner identity")
    if runner.get("runner_label") != ROLE_LABELS[role]:
        _fail("runner label is not role-specific")
    _sha(runner.get("runner_attestation_sha256"), "runner attestation")

    executable = value["executable"]
    if not isinstance(executable, Mapping) or set(executable) != {
        "executable_sha256",
        "process_tree_sha256",
    }:
        _fail("executable observation is not closed")
    _sha(executable.get("executable_sha256"), "executable hash")
    _sha(executable.get("process_tree_sha256"), "process tree hash")

    principal = value["principal"]
    if not isinstance(principal, Mapping) or set(principal) != {
        "uid",
        "principal_id",
        "acl_sha256",
    }:
        _fail("principal observation is not closed")
    if isinstance(principal.get("uid"), bool) or not isinstance(principal.get("uid"), int):
        _fail("principal uid is invalid")
    _identifier(principal.get("principal_id"), "principal identity")
    _sha(principal.get("acl_sha256"), "ACL observation")

    mount = value["mount"]
    if not isinstance(mount, Mapping) or set(mount) != {
        "namespace_id",
        "inventory_sha256",
        "read_only_input_sha256s",
    }:
        _fail("mount observation is not closed")
    _identifier(mount.get("namespace_id"), "mount namespace")
    _sha(mount.get("inventory_sha256"), "mount inventory")
    read_only_inputs = _hash_list(
        mount.get("read_only_input_sha256s"), "read-only input hashes"
    )

    network = value["network"]
    if not isinstance(network, Mapping) or set(network) != {"policy", "policy_sha256"}:
        _fail("network observation is not closed")
    if network.get("policy") not in {"deny_all", "host_provider_allowlist"}:
        _fail("network policy is invalid")
    _sha(network.get("policy_sha256"), "network policy")

    ipc = value["ipc"]
    if not isinstance(ipc, Mapping) or set(ipc) != {"namespace_id", "policy", "policy_sha256"}:
        _fail("IPC observation is not closed")
    _identifier(ipc.get("namespace_id"), "IPC namespace")
    if ipc.get("policy") not in {"deny_shared", "artifact_pipe_only"}:
        _fail("IPC policy is invalid")
    _sha(ipc.get("policy_sha256"), "IPC policy")

    ingress = _artifact_list(value["ingress"], "artifact ingress")
    _artifact_list(value["egress"], "artifact egress")
    ingress_digests = {row["sha256"] for row in ingress}
    if set(read_only_inputs) != ingress_digests:
        _fail("read-only input hashes must equal artifact ingress")
    visibility = value["visibility"]
    if not isinstance(visibility, Mapping) or set(visibility) != {"can_read", "cannot_read"}:
        _fail("artifact visibility is not closed")
    can_read = _hash_list(visibility.get("can_read"), "visible artifact hashes")
    cannot_read = _hash_list(visibility.get("cannot_read"), "hidden artifact hashes")
    if set(can_read) & set(cannot_read):
        _fail("artifact visibility overlaps")
    if set(can_read) != ingress_digests:
        _fail("visible artifact hashes must equal artifact ingress")

    negative = value["negative_canary"]
    if not isinstance(negative, Mapping) or set(negative) != {
        "attempts",
        "targets",
        "leaked_count",
        "observation_sha256",
    }:
        _fail("negative-canary observation is not closed")
    targets = _artifact_list(negative.get("targets"), "negative-canary targets")
    if (
        isinstance(negative.get("attempts"), bool)
        or not isinstance(negative.get("attempts"), int)
        or negative["attempts"] != len(targets)
        or not targets
        or isinstance(negative.get("leaked_count"), bool)
        or not isinstance(negative.get("leaked_count"), int)
        or negative["leaked_count"] != 0
    ):
        _fail("negative-canary result is not zero")
    if set(cannot_read) != {item["sha256"] for item in targets}:
        _fail("hidden artifact hashes must equal negative-canary targets")
    _sha(negative.get("observation_sha256"), "negative-canary observation")

    if value.get("secret_policy") not in {"forbidden", "broker_only_exact_host"}:
        _fail("Secret policy is invalid")
    process_receipts = _hash_list(
        value.get("process_receipt_sha256s"), "process receipt hashes"
    )
    expected_process_receipt = process_receipt_set_sha256(process_receipts)
    if value.get("process_receipt_sha256") != expected_process_receipt:
        _fail("process receipt aggregate hash differs")
    _sha(value.get("observed_roots_sha256"), "observed roots")
    _sha(value.get("attester_executable_sha256"), "attester executable")
    observed = value["observed"]
    if not isinstance(observed, Mapping) or set(observed) != {
        "source",
        "command_id",
        "attestation_sha256",
    }:
        _fail("OS observation binding is not closed")
    if observed.get("source") != "os_runner_attestation":
        _fail("receipt is not bound to OS runner observation")
    _identifier(observed.get("command_id"), "observation command")
    _sha(observed.get("attestation_sha256"), "OS attestation")
    if value.get("record_sha256") != record_sha256(value):
        _fail("security domain receipt self-hash differs")
    return dict(value)


def security_domain_set_sha256(receipts: Sequence[Mapping[str, Any]]) -> str:
    """Return the canonical digest of role-sorted receipt records."""

    if not receipts:
        _fail("security domain receipt set is empty")
    validated = [validate_security_domain_receipt(item) for item in receipts]
    roles = [str(item["role"]) for item in validated]
    if len(set(roles)) != len(roles):
        _fail("security domain receipt roles are duplicated")
    for label, values in (
        ("domain", [item["domain_id"] for item in validated]),
        ("runner", [item["runner"]["ephemeral_runner_id"] for item in validated]),
        ("mount namespace", [item["mount"]["namespace_id"] for item in validated]),
        ("IPC namespace", [item["ipc"]["namespace_id"] for item in validated]),
        ("principal", [item["principal"]["principal_id"] for item in validated]),
    ):
        if len(values) != len(set(values)):
            _fail(f"security domain {label} identities are not distinct")
    ordered = sorted(validated, key=lambda item: str(item["role"]))
    return hashlib.sha256(canonical_json_bytes(ordered)).hexdigest()
