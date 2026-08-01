from __future__ import annotations

from typing import Any

from .util import canonical_json, sha256_bytes, stable_id


def build_authoritative_pack_descriptor(
    *,
    domain: str,
    authority_namespace: str,
    issuer_id: str,
    not_public_authority: bool,
    trust_root_id: str,
    catalog_id: str,
    catalog_sha256: str,
    signature_verified: bool,
    sequence: int,
    revocations_sha256: str,
    release_id: str,
    release_sha256: str,
    previous_release_id: str | None,
    active: bool,
    document_count: int,
    source_count: int,
    segment_count: int,
    inventory_sha256: str,
    store_identity: str,
    physical_partition: str,
) -> dict[str, Any]:
    """Describe shared pack invariants without introducing a second storage engine."""
    if sequence < 1 or min(document_count, source_count, segment_count) < 1:
        raise ValueError("Authoritative Pack sequence and inventory counts must be positive")
    activation_sha256 = sha256_bytes(
        canonical_json(
            {
                "pack_release_id": release_id,
                "catalog_id": catalog_id,
                "sequence": sequence,
                "active": active,
            }
        ).encode("utf-8")
    )
    value = {
        "schema_version": "deeplaw.authoritative-pack-core/v1",
        "pack": {
            "pack_id": stable_id(
                "authpack", authority_namespace, issuer_id, trust_root_id
            ),
            "domain": domain,
            "authority_namespace": authority_namespace,
            "issuer_id": issuer_id,
            "not_public_authority": not_public_authority,
        },
        "trust": {
            "trust_root_id": trust_root_id,
            "catalog_id": catalog_id,
            "catalog_sha256": catalog_sha256,
            "signature_verified": signature_verified,
            "sequence": sequence,
            "revocations_sha256": revocations_sha256,
        },
        "release": {
            "release_id": release_id,
            "release_sha256": release_sha256,
            "previous_release_id": previous_release_id,
            "immutable": True,
            "active": active,
            "activation_sha256": activation_sha256,
        },
        "inventory": {
            "document_count": document_count,
            "source_count": source_count,
            "segment_count": segment_count,
            "locator_schema": "deeplaw.authoritative-locator/v1",
            "extraction_provenance_schema": "deeplaw.document-ir-extraction/v1",
            "capability_schema": "deeplaw.evidence-capability-record/v1",
            "inventory_sha256": inventory_sha256,
        },
        "storage": {
            "store_identity": store_identity,
            "physical_partition": physical_partition,
            "read_only": True,
        },
        "historical_pinning": "supported",
        "public_mcp_leaf": False,
    }
    value["core_sha256"] = sha256_bytes(canonical_json(value).encode("utf-8"))
    return value


def verify_authoritative_pack_descriptor(value: dict[str, Any]) -> dict[str, Any]:
    body = dict(value)
    digest = body.pop("core_sha256", None)
    valid = digest == sha256_bytes(canonical_json(body).encode("utf-8"))
    reason = "verified" if valid else "core_digest_mismatch"
    if valid and value["release"]["active"] and not value["trust"]["signature_verified"]:
        valid = False
        reason = "active_release_requires_verified_catalog_signature"
    return {
        "schema_version": "deeplaw.authoritative-pack-core-verification/v1",
        "pack_id": value["pack"]["pack_id"],
        "release_id": value["release"]["release_id"],
        "valid": valid,
        "reason": reason,
    }
