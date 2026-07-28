from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, Literal, cast

from .knowledge_identity import make_collection_id, make_knowledge_key
from .knowledge_models import (
    ASSET_KINDS,
    MEMORY_TIERS,
    SENSITIVITY_LEVELS,
    AssetKind,
    MemoryTier,
    Sensitivity,
    canonical_timestamp,
    utc_now,
)
from .knowledge_store import KnowledgeVault, knowledge_source_key
from .util import (
    canonical_json,
    has_instruction_risk,
    sha256_bytes,
    sha256_file,
    stable_id,
    strict_json_loads,
)

INBOX_ARTIFACT_SCHEMA = "deeplaw.knowledge-inbox-artifact/v1"
InboxArtifactType = Literal["proposal", "feedback", "run", "eval"]

_EXTENSIONS = {
    "proposal": ".dlproposal",
    "feedback": ".dlfeedback",
    "run": ".dlrun",
    "eval": ".dleval",
}
_ALLOWED_SIGNALS = frozenset(
    {
        "user_confirmed",
        "repeated_success",
        "failure_fixed",
        "decision_landed",
        "tool_result_validated",
        "superseded_rule",
        "repeated_gap",
    }
)
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
_MAX_PAYLOAD_BYTES = 1024 * 1024


def _inbox_directory(
    vault: KnowledgeVault,
    state: str = "pending",
    *,
    create: bool = True,
) -> Path:
    if state not in {"pending", "processed", "rejected"}:
        raise ValueError("unsupported inbox state")
    root = vault.root / "inbox"
    if root.is_symlink():
        raise RuntimeError("knowledge inbox must not be a symbolic link")
    if create:
        root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
    path = root / state
    if path.is_symlink():
        raise RuntimeError("knowledge inbox state directory must not be a symbolic link")
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    return path


def _artifact_digest(value: dict[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "artifact_sha256"}
    return sha256_bytes(canonical_json(unsigned).encode("utf-8"))


def _validate_payload(artifact_type: InboxArtifactType, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("inbox artifact payload must be an object")
    try:
        payload_size = len(canonical_json(payload).encode("utf-8"))
    except (TypeError, ValueError) as error:
        raise ValueError("inbox artifact payload is not canonical JSON") from error
    if payload_size > _MAX_PAYLOAD_BYTES:
        raise ValueError("inbox artifact payload exceeds 1 MiB")
    if artifact_type == "proposal":
        required = {"kind", "memory_tier", "title", "statement"}
        optional = {
            "semantic_key",
            "tags",
            "expires_at",
            "supersedes_asset_id",
            "origin_uri",
            "candidate_type",
            "capsule_id",
            "run_id",
            "tool_result_sha256",
        }
        if not required.issubset(payload) or not set(payload).issubset(required | optional):
            raise ValueError("proposal inbox payload does not match its closed contract")
        if payload["kind"] not in ASSET_KINDS or payload["memory_tier"] not in MEMORY_TIERS:
            raise ValueError("proposal inbox kind or memory tier is invalid")
        for field, maximum in (("title", 500), ("statement", 20_000)):
            value = payload[field]
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
                raise ValueError(f"proposal inbox {field} is invalid")
        tags = payload.get("tags", [])
        if (
            not isinstance(tags, list)
            or len(tags) > 64
            or any(not isinstance(tag, str) or not 1 <= len(tag.strip()) <= 200 for tag in tags)
        ):
            raise ValueError("proposal inbox tags are invalid")
    elif artifact_type == "feedback":
        if not {"run_id", "labels", "observation"}.issubset(payload):
            raise ValueError("feedback inbox payload is incomplete")
    elif artifact_type == "run":
        if not {"capsule_id", "capsule_digest", "status", "host"}.issubset(payload):
            raise ValueError("run inbox payload is incomplete")
    elif artifact_type == "eval":
        if not {"case_id", "query", "expected"}.issubset(payload):
            raise ValueError("evaluation inbox payload is incomplete")
    return payload


def submit_inbox_artifact(
    vault: KnowledgeVault,
    *,
    artifact_type: InboxArtifactType,
    payload: dict[str, Any],
    producer_name: str,
    producer_version: str,
    priority_signals: tuple[str, ...] = (),
    sensitivity: Sensitivity = "private",
    confirm_no_case_data: bool,
) -> dict[str, Any]:
    if not confirm_no_case_data:
        raise ValueError("inbox submission requires confirmation that it contains no case data")
    if artifact_type not in _EXTENSIONS:
        raise ValueError("unsupported inbox artifact type")
    if sensitivity not in SENSITIVITY_LEVELS:
        raise ValueError("unsupported inbox sensitivity")
    if (
        not isinstance(producer_name, str)
        or not 1 <= len(producer_name.strip()) <= 200
        or not isinstance(producer_version, str)
        or not 1 <= len(producer_version.strip()) <= 200
    ):
        raise ValueError("inbox producer identity is invalid")
    if (
        len(priority_signals) > 16
        or len(set(priority_signals)) != len(priority_signals)
        or any(signal not in _ALLOWED_SIGNALS for signal in priority_signals)
    ):
        raise ValueError("inbox priority signals are invalid")
    checked_payload = _validate_payload(artifact_type, payload)
    payload_sha256 = sha256_bytes(canonical_json(checked_payload).encode("utf-8"))
    created_at = utc_now()
    artifact_id = stable_id(
        "inbox",
        vault.vault_id,
        artifact_type,
        payload_sha256,
        created_at,
        secrets.token_hex(16),
    )
    artifact = {
        "schema_version": INBOX_ARTIFACT_SCHEMA,
        "artifact_id": artifact_id,
        "vault_id": vault.vault_id,
        "artifact_type": artifact_type,
        "created_at": created_at,
        "producer": {
            "name": producer_name.strip(),
            "version": producer_version.strip(),
        },
        "priority_signals": list(priority_signals),
        "review_priority": len(priority_signals),
        "sensitivity": sensitivity,
        "payload": checked_payload,
        "payload_sha256": payload_sha256,
        "authority": "isolated-proposal-only",
        "canonical_write_performed": False,
    }
    artifact["artifact_sha256"] = _artifact_digest(artifact)
    destination = _inbox_directory(vault) / f"{artifact_id}{_EXTENSIONS[artifact_type]}"
    if destination.exists() or destination.is_symlink():
        raise RuntimeError("inbox artifact destination already exists or is unsafe")
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((canonical_json(artifact) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {**artifact, "state": "pending"}


def _resolve_artifact(vault: KnowledgeVault, artifact_id: str) -> tuple[Path, str]:
    if not isinstance(artifact_id, str) or not artifact_id.startswith("inbox_"):
        raise ValueError("inbox artifact ID is invalid")
    matches: list[tuple[Path, str]] = []
    for state in ("pending", "processed", "rejected"):
        directory = _inbox_directory(vault, state, create=False)
        if not directory.is_dir():
            continue
        for extension in _EXTENSIONS.values():
            path = directory / f"{artifact_id}{extension}"
            if path.is_symlink():
                raise RuntimeError("inbox artifact must not be a symbolic link")
            if path.is_file():
                matches.append((path, state))
    if len(matches) != 1:
        raise KeyError(f"inbox artifact is unavailable or ambiguous: {artifact_id}")
    return matches[0]


def verify_inbox_artifact(
    vault: KnowledgeVault,
    artifact_id: str,
) -> dict[str, Any]:
    try:
        path, state = _resolve_artifact(vault, artifact_id)
        if not 1 <= path.stat().st_size <= _MAX_ARTIFACT_BYTES:
            raise ValueError("artifact file size is invalid")
        artifact = strict_json_loads(path.read_bytes())
        expected_fields = {
            "schema_version",
            "artifact_id",
            "vault_id",
            "artifact_type",
            "created_at",
            "producer",
            "priority_signals",
            "review_priority",
            "sensitivity",
            "payload",
            "payload_sha256",
            "authority",
            "canonical_write_performed",
            "artifact_sha256",
        }
        if not isinstance(artifact, dict) or set(artifact) != expected_fields:
            raise ValueError("artifact envelope is invalid")
        artifact_type = cast(InboxArtifactType, artifact["artifact_type"])
        canonical_timestamp(artifact["created_at"], field="inbox created_at")
        if (
            artifact["schema_version"] != INBOX_ARTIFACT_SCHEMA
            or artifact["artifact_id"] != artifact_id
            or artifact["vault_id"] != vault.vault_id
            or artifact_type not in _EXTENSIONS
            or path.suffix != _EXTENSIONS[artifact_type]
            or artifact["authority"] != "isolated-proposal-only"
            or artifact["canonical_write_performed"] is not False
            or artifact["sensitivity"] not in SENSITIVITY_LEVELS
            or artifact["artifact_sha256"] != _artifact_digest(artifact)
        ):
            raise ValueError("artifact identity or digest is invalid")
        checked_payload = _validate_payload(artifact_type, artifact["payload"])
        if artifact["payload_sha256"] != sha256_bytes(
            canonical_json(checked_payload).encode("utf-8")
        ):
            raise ValueError("artifact payload digest is invalid")
        signals = artifact["priority_signals"]
        if (
            not isinstance(signals, list)
            or len(signals) != len(set(signals))
            or any(signal not in _ALLOWED_SIGNALS for signal in signals)
            or artifact["review_priority"] != len(signals)
        ):
            raise ValueError("artifact priority is invalid")
        valid = True
        reason = None
    except (OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        artifact = None
        state = None
        valid = False
        reason = str(error)
    return {
        "schema_version": "deeplaw.knowledge-inbox-verification/v1",
        "artifact_id": artifact_id,
        "state": state,
        "valid": valid,
        "reason": reason,
        "artifact": artifact,
    }


def list_inbox_artifacts(
    vault: KnowledgeVault,
    *,
    state: str = "pending",
    artifact_type: InboxArtifactType | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not 1 <= limit <= 1_000:
        raise ValueError("inbox limit must be between 1 and 1000")
    directory = _inbox_directory(vault, state, create=False)
    if not directory.is_dir():
        return {
            "schema_version": "deeplaw.knowledge-inbox-list/v1",
            "vault_id": vault.vault_id,
            "state": state,
            "artifacts": [],
            "invalid_artifact_count": 0,
            "truncated": False,
        }
    paths = sorted(path for path in directory.iterdir() if path.is_file())
    values: list[dict[str, Any]] = []
    invalid_count = 0
    for path in paths:
        artifact_id = path.stem
        verification = verify_inbox_artifact(vault, artifact_id)
        artifact = verification["artifact"]
        if not verification["valid"]:
            invalid_count += 1
            continue
        if artifact_type is not None and artifact["artifact_type"] != artifact_type:
            continue
        values.append(
            {
                "artifact_id": artifact_id,
                "artifact_type": artifact["artifact_type"],
                "created_at": artifact["created_at"],
                "producer": artifact["producer"],
                "review_priority": artifact["review_priority"],
                "priority_signals": artifact["priority_signals"],
                "sensitivity": artifact["sensitivity"],
            }
        )
        if len(values) >= limit:
            break
    return {
        "schema_version": "deeplaw.knowledge-inbox-list/v1",
        "vault_id": vault.vault_id,
        "state": state,
        "artifacts": values,
        "invalid_artifact_count": invalid_count,
        "truncated": len(values) == limit,
    }


def promote_inbox_proposal(
    vault: KnowledgeVault,
    *,
    artifact_id: str,
    confirm_reviewed: bool,
) -> dict[str, Any]:
    if not confirm_reviewed:
        raise ValueError("inbox promotion requires explicit operator review")
    verification = verify_inbox_artifact(vault, artifact_id)
    artifact = verification["artifact"]
    if not verification["valid"] or artifact is None:
        raise ValueError("inbox artifact verification failed")
    if verification["state"] != "pending" or artifact["artifact_type"] != "proposal":
        raise ValueError("only pending proposal artifacts can be promoted")
    source_path, _ = _resolve_artifact(vault, artifact_id)
    payload = artifact["payload"]
    title = payload["title"].strip()
    statement = payload["statement"].strip()
    fragment_text = f"{title}\n{statement}"
    instruction_risk = has_instruction_risk(fragment_text)
    collection_name = "proposal-inbox"
    collection_id = make_collection_id(vault_id=vault.vault_id, name=collection_name)
    logical_path = f"artifacts/{artifact_id}.dlproposal"
    source_key = knowledge_source_key(
        vault_id=vault.vault_id,
        source_kind="conversation",
        source_path=source_path,
        origin_uri=None,
        collection_id=collection_id,
        logical_path=logical_path,
    )
    logical_node_key = f"inbox:{artifact_id}:payload"
    supersedes_asset_id = payload.get("supersedes_asset_id")
    knowledge_key: str | None = None
    if supersedes_asset_id is not None:
        predecessor = vault.connection.execute(
            """
            SELECT knowledge_revisions_v2.knowledge_key
            FROM asset_revision_bindings_v2
            JOIN knowledge_revisions_v2 USING(asset_revision_id)
            WHERE asset_revision_bindings_v2.legacy_asset_id = ?
            """,
            (supersedes_asset_id,),
        ).fetchone()
        if predecessor is None:
            raise ValueError(
                "an Inbox supersession target must have a source-bound Identity v2 revision"
            )
        knowledge_key = predecessor["knowledge_key"]
    if knowledge_key is None:
        knowledge_key = make_knowledge_key(
            vault_id=vault.vault_id,
            source_key=source_key,
            logical_node_key=logical_node_key,
            proposal_role=f"inbox:{payload['kind']}",
        )
    content_sha256 = sha256_file(source_path)
    fragment_sha256 = sha256_bytes(fragment_text.encode("utf-8"))
    compiler = {
        "schema_version": "deeplaw.knowledge-compiler/v1",
        "source_key": source_key,
        "collection_id": collection_id,
        "collection_name": collection_name,
        "logical_path": logical_path,
        "format": "DLPROPOSAL",
        "source_sha256": content_sha256,
        "extractor": "deeplaw-inbox-envelope",
        "extractor_version": "1",
        "source_adapter": "deeplaw-inbox-json",
        "source_adapter_version": "1",
        "source_ir_schema": "deeplaw.source-ir/v1",
        "configuration": ["closed-envelope", "json-pointer-source-span"],
        "block_count": 1,
        "page_count": None,
        "character_count": len(fragment_text),
        "section_count": 1,
        "compiled_fragment_sha256": fragment_sha256,
        "instruction_risk": instruction_risk,
        "typed_extraction": "inbox-proposal-v1",
        "reference_proposals": False,
        "typed_extractor": None,
        "policy": "Agent artifacts remain untrusted, source-bound review candidates",
    }
    compiled = vault.add_compiled_source(
        source_path=source_path,
        source_key=source_key,
        expected_byte_size=source_path.stat().st_size,
        expected_content_sha256=content_sha256,
        source_kind="conversation",
        title=f"Inbox proposal {artifact_id}",
        origin_uri=None,
        media_type="application/vnd.deeplaw.proposal+json",
        trust="untrusted",
        sensitivity=cast(Sensitivity, artifact["sensitivity"]),
        instruction_risk=instruction_risk,
        warnings=("Agent-generated proposal artifact requires explicit human review",),
        compiler=compiler,
        fragments=(
            {
                "text": fragment_text,
                "locator": "json:/payload",
                "instruction_risk": instruction_risk,
                "logical_node_key": logical_node_key,
                "logical_node_keys": (logical_node_key,),
                "source_span": {
                    "json_pointer": "/payload",
                    "fields": ["title", "statement"],
                },
            },
        ),
        asset_specs=(
            {
                "kind": cast(AssetKind, payload["kind"]),
                "memory_tier": cast(MemoryTier, payload["memory_tier"]),
                "title": title,
                "statement": statement,
                "knowledge_key": knowledge_key,
                "proposal_role": f"inbox:{payload['kind']}",
                "logical_node_keys": (logical_node_key,),
                "source_ref_indexes": (0,),
                "applicability": {
                    "episode_type": "agent_proposal",
                    "artifact_id": artifact_id,
                    "capsule_id": payload.get("capsule_id"),
                    "run_id": payload.get("run_id"),
                },
                "observed_at": artifact["created_at"],
                "expires_at": payload.get("expires_at"),
                "project_scope": collection_id,
                "repository_scope": None,
                "branch_scope": None,
                "version_scope": None,
                "environment_scope": None,
                "supersedes_asset_id": supersedes_asset_id,
                "trust": "untrusted",
                "quarantined": True,
                "tags": ("proposal-inbox", *tuple(payload.get("tags", ()))),
                "warnings": (
                    "Agent-generated content cannot inherit source trust or approval",
                ),
                "origin_uri": (
                    payload.get("origin_uri")
                    or f"deeplaw-inbox://{vault.vault_id}/{artifact_id}"
                ),
            },
        ),
        source_ir_nodes=(
            {
                "logical_node_key": logical_node_key,
                "parent_logical_node_key": None,
                "ordinal": 1,
                "node_type": "agent_proposal",
                "title": title,
                "text": fragment_text,
                "locator": "json:/payload",
                "source_span": {
                    "json_pointer": "/payload",
                    "fields": ["title", "statement"],
                },
                "content_sha256": fragment_sha256,
                "quality_flags": ["agent_generated", "review_required"],
                "instruction_risk": instruction_risk,
                "fragment_id": None,
            },
        ),
    )
    if len(compiled["asset_ids"]) != 1:
        raise RuntimeError("Inbox proposal compilation did not produce exactly one proposal")
    asset = vault.get_asset(compiled["asset_ids"][0], include_inactive=True)
    destination = _inbox_directory(vault, "processed") / source_path.name
    if destination.exists() or destination.is_symlink():
        raise RuntimeError("processed inbox destination is unsafe")
    os.replace(source_path, destination)
    return {
        "schema_version": "deeplaw.knowledge-inbox-promotion/v1",
        "artifact_id": artifact_id,
        "asset_id": asset.asset_id,
        "asset_status": asset.status,
        "source_id": compiled["source"]["source_id"],
        "source_revision_id": compiled["source"]["source_revision_id"],
        "knowledge_key": knowledge_key,
        "state": "processed",
        "authority": "proposal-only",
        "active": False,
    }


def reject_inbox_artifact(
    vault: KnowledgeVault,
    *,
    artifact_id: str,
    confirm_reviewed: bool,
) -> dict[str, Any]:
    if not confirm_reviewed:
        raise ValueError("inbox rejection requires explicit operator review")
    verification = verify_inbox_artifact(vault, artifact_id)
    if not verification["valid"] or verification["state"] != "pending":
        raise ValueError("only a verified pending inbox artifact can be rejected")
    source_path, _ = _resolve_artifact(vault, artifact_id)
    destination = _inbox_directory(vault, "rejected") / source_path.name
    if destination.exists() or destination.is_symlink():
        raise RuntimeError("rejected inbox destination is unsafe")
    os.replace(source_path, destination)
    return {
        "schema_version": "deeplaw.knowledge-inbox-rejection/v1",
        "artifact_id": artifact_id,
        "state": "rejected",
        "canonical_write_performed": False,
    }
