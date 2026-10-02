"""Read-only reopening of one fixed public maintenance context snapshot.

The receipt and file hashes bind engineering evidence only. They cannot supply
native Host execution or isolation authority. Callers must serialize writes to
the retained snapshot during validation.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from benchmarks.hosts.maintenance_fixture_snapshot import validate_public_fixture_snapshot
from benchmarks.hosts.maintenance_host_context import digest
from benchmarks.hosts.maintenance_task_cases import FROZEN_INPUT_SHA256, public_task_projection
from benchmarks.hosts.maintenance_task_mcp import make_owner_binding, provider_capsule_digest
from deeplaw.context_compiler import verify_capsule
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import (
    assert_provider_output_safe,
    canonical_json,
    sha256_bytes,
    strict_json_loads,
)

_MAX_CONTEXT_BYTES = 384 * 1024
_MAX_MANIFEST_BYTES = 65_536
_MAX_VAULT_BYTES = 16 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY_FIELDS = {
    "vault_id", "legacy_revision", "legacy_audit_head", "autonomous_sequence",
    "autonomous_audit_head", "inventory_sha256",
}
_RECEIPT_FIELDS = _IDENTITY_FIELDS | {
    "schema_version", "capture_phase", "configuration_id", "scenario_id", "run_id",
    "candidate_id", "public_input_sha256", "task_input_sha256", "task_binding",
    "context_id", "provider_capsule_sha256", "owner_binding", "context",
    "snapshot_manifest", "vault_path", "binding_sha256",
}


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _directory(root: Path, relative: str) -> tuple[int, list[tuple[Path, tuple]]]:
    """Open all parents without following links; constrain the retained subtree."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(root.anchor, flags)
    current = Path(root.anchor)
    parents = []
    try:
        parts = root.parts[1:] + (() if relative == "." else PurePosixPath(relative).parts)
        for index, part in enumerate(parts):
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            current = current / part
            checked = os.fstat(fd)
            if index >= len(root.parts) - 2:
                if checked.st_mode & 0o077 or checked.st_uid != os.geteuid():
                    raise ValueError("unsafe snapshot directory")
                parents.append((current, _identity(checked)))
        return fd, parents
    except Exception:
        os.close(fd)
        raise


def _read(root: Path, relative: str, maximum: int) -> tuple[bytes, tuple]:
    path = PurePosixPath(relative)
    parent_fd, parents = _directory(root, path.parent.as_posix())
    fd = None
    try:
        before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_mode & 0o077
                or opened.st_uid != os.geteuid() or opened.st_nlink != 1
                or not 0 <= opened.st_size <= maximum or _identity(before) != _identity(opened)):
            raise ValueError("unsafe snapshot file")
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (len(raw) != opened.st_size or _identity(after) != _identity(opened)
                or _identity(current) != _identity(opened)
                or any(_identity(parent.lstat()) != identity for parent, identity in parents)):
            raise ValueError("snapshot file changed")
        return raw, _identity(opened)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def _reference(value: Any, expected_path: str, maximum: int) -> None:
    if (type(value) is not dict or set(value) != {"path", "sha256", "size"}
            or value["path"] != expected_path or type(value["path"]) is not str
            or type(value["sha256"]) is not str or not _SHA256.fullmatch(value["sha256"])
            or type(value["size"]) is not int or not 0 < value["size"] <= maximum):
        raise ValueError("snapshot file reference differs")


def _slot_closed(root: Path, relative: str) -> None:
    fd, parents = _directory(root, relative)
    try:
        names = set()
        with os.scandir(fd) as entries:
            for entry in entries:
                if len(names) >= 3:
                    raise ValueError("snapshot slot exceeds its bound")
                names.add(entry.name)
        if names != {"context.json", "snapshot-manifest.json", "vault"} \
                or any(_identity(parent.lstat()) != identity for parent, identity in parents):
            raise ValueError("snapshot slot closure differs")
    finally:
        os.close(fd)


def _validate(
    root: Path, receipt: dict, *, configuration: str, scenario: str,
    run_id: str, candidate_id: str,
) -> dict[str, Any]:
    if os.name != "posix" or not isinstance(root, Path):
        raise ValueError("unsupported snapshot filesystem")
    root = root.absolute()
    if ".." in root.parts or len(root.parts) > 128:
        raise ValueError("unsafe snapshot root")
    root_stat = root.lstat()
    if (not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_mode & 0o077
            or root_stat.st_uid != os.geteuid()):
        raise ValueError("unsafe snapshot root")
    task = public_task_projection(configuration, scenario)
    if type(receipt) is not dict or set(receipt) != _RECEIPT_FIELDS:
        raise ValueError("snapshot receipt fields differ")
    expected = {
        "schema_version": "deeplaw.maintenance-context-snapshot/v1",
        "capture_phase": "pre_dispatch_pre_outcome", "configuration_id": configuration,
        "scenario_id": scenario, "run_id": run_id, "candidate_id": candidate_id,
        "public_input_sha256": FROZEN_INPUT_SHA256, "task_input_sha256": task["input_sha256"],
    }
    if any(type(receipt[field]) is not str or receipt[field] != value
           for field, value in expected.items()):
        raise ValueError("snapshot public identity differs")
    for field in ("provider_capsule_sha256", "legacy_audit_head", "autonomous_audit_head",
                  "inventory_sha256", "binding_sha256"):
        if type(receipt[field]) is not str or not _SHA256.fullmatch(receipt[field]):
            raise ValueError("snapshot digest differs")
    for field, prefix in (("vault_id", "vault"), ("context_id", "capsule")):
        if (type(receipt[field]) is not str
                or not re.fullmatch(prefix + r"_[0-9a-f]{24}", receipt[field])):
            raise ValueError("snapshot object identity differs")
    if any(type(receipt[field]) is not int or not 0 <= receipt[field] <= 1024
           for field in ("legacy_revision", "autonomous_sequence")):
        raise ValueError("snapshot sequence differs")
    task_binding = build_task_context_binding(digest(configuration), digest(task["task_id"]))
    owner_binding = make_owner_binding(
        configuration, scenario, run_id=run_id, candidate_id=candidate_id,
        context_id=receipt["context_id"], capsule_digest=receipt["provider_capsule_sha256"],
    )
    if (type(receipt["task_binding"]) is not dict or receipt["task_binding"] != task_binding
            or type(receipt["owner_binding"]) is not dict
            or receipt["owner_binding"] != owner_binding):
        raise ValueError("snapshot task or owner binding differs")
    slot = "context-snapshots/" + configuration + "-" + scenario
    if type(receipt["vault_path"]) is not str or receipt["vault_path"] != slot + "/vault":
        raise ValueError("snapshot Vault path differs")
    _reference(receipt["context"], slot + "/context.json", _MAX_CONTEXT_BYTES)
    _reference(receipt["snapshot_manifest"], slot + "/snapshot-manifest.json", _MAX_MANIFEST_BYTES)
    if receipt["binding_sha256"] != digest({
        key: value for key, value in receipt.items() if key != "binding_sha256"
    }):
        raise ValueError("snapshot receipt digest differs")
    _slot_closed(root, slot)
    retained = {}
    for field, maximum in (
        ("context", _MAX_CONTEXT_BYTES), ("snapshot_manifest", _MAX_MANIFEST_BYTES),
    ):
        reference = receipt[field]
        raw, identity = _read(root, reference["path"], maximum)
        if sha256_bytes(raw) != reference["sha256"] or len(raw) != reference["size"]:
            raise ValueError("snapshot original bytes differ")
        retained[reference["path"]] = (raw, identity, maximum)
    context = strict_json_loads(retained[receipt["context"]["path"]][0])
    manifest = strict_json_loads(retained[receipt["snapshot_manifest"]["path"]][0])
    if (type(context) is not dict or set(context) != {
        "capsule", "provider_capsule", "verification", "context_id", "task_input_sha256",
        "context_provenance",
    } or any(type(context[field]) is not dict
             for field in ("capsule", "provider_capsule", "verification"))
            or context["context_provenance"] != "source_free_synthetic_memory_compile"
            or context["context_id"] != receipt["context_id"]
            or context["task_input_sha256"] != task["input_sha256"]):
        raise ValueError("snapshot context fields differ")
    capsule, provider = context["capsule"], context["provider_capsule"]
    plan = capsule.get("query_plan")
    query = (task["task"] + " " + task["goal"]).strip()
    if (type(plan) is not dict or capsule.get("schema_version") != "deeplaw.knowledge-capsule/v4"
            or capsule.get("capsule_id") != receipt["context_id"]
            or capsule.get("vault_id") != receipt["vault_id"]
            or capsule.get("task") != task["task"] or capsule.get("goal") != task["goal"]
            or capsule.get("task_binding") is not None or plan.get("task_binding") is not None
            or capsule.get("as_of") is not None or plan.get("as_of") is not None
            or capsule.get("audit_head") != receipt["autonomous_audit_head"]
            or plan.get("input_audit_head") != receipt["autonomous_audit_head"]
            or plan.get("input_legacy_audit_head") != receipt["legacy_audit_head"]
            or plan.get("purpose") != "answer" or plan.get("policy_id") != "compiled-first-v1"
            or plan.get("scope") != "project" or plan.get("max_sensitivity") != "public"
            or plan.get("query_target", {}).get("text") != query
            or plan.get("query_sha256") != sha256_bytes(query.encode())
            or plan.get("budget") != {"items": 8, "characters": 8000, "tokens": 6000,
                                      "sources": 12, "provider_characters": 65536}
            or plan.get("retrieval_controls", {}).get("graph_hops") != 1
            or plan.get("retrieval_controls", {}).get("retrieval_mode") != "hybrid"
            or canonical_json(provider) != canonical_json(capsule.get("provider_capsule"))
            or provider_capsule_digest(provider) != receipt["provider_capsule_sha256"]):
        raise ValueError("snapshot Capsule public binding differs")
    assert_provider_output_safe(provider, interface="maintenance_context_snapshot")
    vault = root / receipt["vault_path"]
    actual_manifest = validate_public_fixture_snapshot(
        vault, manifest, expected_vault_id=receipt["vault_id"],
    )
    if any(actual_manifest[field] != receipt[field] for field in _IDENTITY_FIELDS):
        raise ValueError("snapshot actual Vault identity differs")
    for item in actual_manifest["inventory"]:
        relative = receipt["vault_path"] + "/" + item["path"]
        raw, identity = _read(root, relative, _MAX_VAULT_BYTES)
        if len(raw) != item["size"] or sha256_bytes(raw) != item["sha256"]:
            raise ValueError("snapshot registered file differs")
        retained[relative] = (raw, identity, _MAX_VAULT_BYTES)
    with KnowledgeVault(vault, read_only=True) as store:
        verification = verify_capsule(capsule, vault=store)
    if (verification.get("valid") is not True
            or canonical_json(context["verification"]) != canonical_json(verification)):
        raise ValueError("snapshot Capsule does not currently verify")
    if validate_public_fixture_snapshot(
        vault, manifest, expected_vault_id=receipt["vault_id"],
    ) != actual_manifest:
        raise ValueError("snapshot changed during verification")
    _slot_closed(root, slot)
    for relative, (before, identity, maximum) in retained.items():
        if _read(root, relative, maximum) != (before, identity):
            raise ValueError("snapshot bytes changed during verification")
    return {
        "context": context, "snapshot_manifest": actual_manifest, "verification": verification,
        "vault_path": receipt["vault_path"], "files": [
            {"path": relative, "sha256": sha256_bytes(raw), "size": len(raw)}
            for relative, (raw, _, _) in sorted(retained.items())
        ], "formal_admission": False,
        "caption": ("Read-only public context snapshot validation; "
                    "native authority is not established."),
    }


def validate_context_snapshot(
    root: Path, receipt: dict, *, configuration: str, scenario: str,
    run_id: str, candidate_id: str,
) -> dict[str, Any]:
    """Reopen exact public context and Vault bytes without writes or reselection.

    Return the original validated context, reconstructed snapshot manifest,
    current verification and used root-relative file inventory. Errors are
    bounded and never include retained payloads or local paths.
    """
    try:
        return _validate(root, receipt, configuration=configuration, scenario=scenario,
                         run_id=run_id, candidate_id=candidate_id)
    except Exception:
        raise ValueError("maintenance context snapshot validation failed") from None
