from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from .util import canonical_json, sha256_bytes

IntegrityCapability = Literal["verified", "failed"]
SourceIdentityCapability = Literal[
    "signed_official", "reviewed", "declared", "unknown"
]
AuthorityMetadataCapability = Literal["verified", "declared", "unknown"]
TemporalCapability = Literal["verified_at", "outside", "unknown", "not_evaluated"]
ExtractionCapability = Literal[
    "native_reviewed",
    "native_unreviewed",
    "ocr_human_reviewed",
    "ocr_unreviewed",
    "warned",
]
ProvenanceCapability = Literal["exact_segment", "derived"]


@dataclass(frozen=True, slots=True)
class EvidenceCapabilities:
    """Deterministic evidence properties; discovery scores cannot modify them."""

    integrity: IntegrityCapability
    source_identity: SourceIdentityCapability
    authority_metadata: AuthorityMetadataCapability
    temporal: TemporalCapability
    extraction: ExtractionCapability
    provenance: ProvenanceCapability
    temporal_as_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "schema_version": "deeplaw.evidence-capabilities/v1",
            **asdict(self),
        }
        value["capability_sha256"] = sha256_bytes(
            canonical_json(value).encode("utf-8")
        )
        return value

    def ids(self) -> tuple[str, ...]:
        return (
            f"integrity:{self.integrity}",
            f"source_identity:{self.source_identity}",
            f"authority_metadata:{self.authority_metadata}",
            f"temporal:{self.temporal}",
            f"extraction:{self.extraction}",
            f"provenance:{self.provenance}",
        )

    def satisfies(self, required: tuple[str, ...]) -> bool:
        return set(required).issubset(self.ids())


def capabilities_for_segment(
    *,
    collection_scope: str,
    signed_catalog_verified: bool,
    temporal_classification: str,
    as_of: str | None,
    extraction_method: str,
    extraction_review_required: bool,
    extraction_warnings: tuple[str, ...],
    exact_segment: bool = True,
) -> EvidenceCapabilities:
    if collection_scope == "official":
        source_identity: SourceIdentityCapability = (
            "signed_official" if signed_catalog_verified else "declared"
        )
        authority_metadata: AuthorityMetadataCapability = (
            "verified" if signed_catalog_verified else "declared"
        )
    elif collection_scope == "user_private":
        source_identity = "reviewed" if not extraction_review_required else "declared"
        authority_metadata = "declared"
    else:
        source_identity = "unknown"
        authority_metadata = "unknown"
    temporal: TemporalCapability = {
        "verified_in_scope": "verified_at",
        "outside": "outside",
        "unverified_metadata": "unknown",
        "not_evaluated": "not_evaluated",
    }.get(temporal_classification, "unknown")
    method = extraction_method.casefold()
    if extraction_warnings:
        extraction: ExtractionCapability = "warned"
    elif "ocr" in method:
        extraction = (
            "ocr_unreviewed" if extraction_review_required else "ocr_human_reviewed"
        )
    else:
        extraction = (
            "native_unreviewed" if extraction_review_required else "native_reviewed"
        )
    return EvidenceCapabilities(
        integrity="verified",
        source_identity=source_identity,
        authority_metadata=authority_metadata,
        temporal=temporal,
        extraction=extraction,
        provenance="exact_segment" if exact_segment else "derived",
        temporal_as_of=as_of if temporal == "verified_at" else None,
    )


def required_capabilities_for_duty(
    duty_id: str,
    *,
    temporal_evaluated: bool,
) -> tuple[str, ...]:
    common = (
        "integrity:verified",
        "provenance:exact_segment",
    )
    if duty_id == "discovery_lead":
        return ()
    if duty_id == "temporal_status_version":
        return (*common, "temporal:verified_at")
    if duty_id == "exact_citation":
        return common
    if temporal_evaluated:
        return (*common, "temporal:verified_at")
    return common
