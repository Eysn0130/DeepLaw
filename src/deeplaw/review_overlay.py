from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .util import canonical_date, sha256_bytes

REVIEW_OVERLAY_SCHEMA = "deeplaw.review-overlay/v1"
_MAX_OVERLAY_BYTES = 4 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DOCUMENT_STATUSES = {
    "unknown",
    "unverified_current",
    "verified_current",
    "verified_historical",
    "not_yet_effective",
    "repealed",
    "superseded",
}
_DOCUMENT_TYPES = {
    "law",
    "administrative_regulation",
    "judicial_interpretation",
    "prosecution_standard",
    "departmental_rule",
    "normative_document",
    "case_reference",
}
_REVIEW_STATES = {"unreviewed", "ai_prechecked", "human_reviewed", "not_applicable"}
_RELATION_TYPES = {"cites", "amends", "repeals", "replaces", "implements", "exception_to"}
_RELATION_REVIEW_STATES = {"ai_prechecked", "human_reviewed"}
_PACKAGE_KEYS = {
    "reviewedAsOf",
    "reviewerKind",
    "reviewScope",
    "temporalStatus",
    "redistributionStatus",
}
_DOCUMENT_KEYS = {
    "sourceSha256",
    "path",
    "title",
    "documentNumber",
    "aliases",
    "promulgatedOn",
    "effectiveDate",
    "effectiveTo",
    "status",
    "documentType",
    "issuer",
    "authorityRank",
    "canonicalAuthorityUrl",
    "retrievalSourceUrl",
    "sourceReviewStatus",
    "temporalReviewStatus",
    "extractionReviewStatus",
    "redistributionStatus",
    "statusAsOf",
    "evidenceUrls",
    "relations",
    "note",
}
_RELATION_KEYS = {
    "predicate",
    "targetSourceSha256",
    "validFrom",
    "validTo",
    "reviewStatus",
    "evidenceUrl",
}


@dataclass(frozen=True, slots=True)
class AppliedReviewOverlay:
    manifest: dict[str, Any]
    overlay_sha256: str
    reviewed_as_of: str
    reviewer_kind: str
    review_scope: str
    temporal_status: str
    redistribution_status: str
    covered_documents: int


def _require_object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _require_https(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError(f"{field} must be a non-empty HTTPS URL")
    parsed = urlparse(value)
    try:
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as error:
        raise ValueError(f"{field} is malformed") from error
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{field} must be a non-credential HTTPS URL")
    return value


def _optional_https(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return _require_https(value, field=field)


def _optional_date(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a date string or null")
    return canonical_date(value, field=field)


def _validate_relation(
    value: Any,
    *,
    field: str,
    source_hashes: set[str],
) -> dict[str, Any]:
    relation = _require_object(value, field=field)
    unknown = set(relation) - _RELATION_KEYS
    if unknown:
        raise ValueError(f"{field} contains unknown fields: {sorted(unknown)}")
    required = {"predicate", "targetSourceSha256", "reviewStatus", "evidenceUrl"}
    missing = required - set(relation)
    if missing:
        raise ValueError(f"{field} is missing required fields: {sorted(missing)}")
    if relation["predicate"] not in _RELATION_TYPES:
        raise ValueError(f"{field}.predicate is unsupported")
    target_hash = relation["targetSourceSha256"]
    if not isinstance(target_hash, str) or not _SHA256.fullmatch(target_hash):
        raise ValueError(f"{field}.targetSourceSha256 is invalid")
    if target_hash not in source_hashes:
        raise ValueError(f"{field}.targetSourceSha256 is not present in the source manifest")
    if relation["reviewStatus"] not in _RELATION_REVIEW_STATES:
        raise ValueError(f"{field}.reviewStatus is unsupported")
    _require_https(relation["evidenceUrl"], field=f"{field}.evidenceUrl")
    valid_from = _optional_date(relation.get("validFrom"), field=f"{field}.validFrom")
    valid_to = _optional_date(relation.get("validTo"), field=f"{field}.validTo")
    if valid_from and valid_to and valid_to <= valid_from:
        raise ValueError(f"{field}.validTo must be after validFrom")
    return deepcopy(relation)


def _validated_document(
    value: Any,
    *,
    index: int,
    source_hashes: set[str],
) -> dict[str, Any]:
    field = f"documents[{index}]"
    document = _require_object(value, field=field)
    unknown = set(document) - _DOCUMENT_KEYS
    if unknown:
        raise ValueError(f"{field} contains unknown fields: {sorted(unknown)}")
    required = {
        "sourceSha256",
        "path",
        "title",
        "status",
        "documentType",
        "issuer",
        "authorityRank",
        "canonicalAuthorityUrl",
        "retrievalSourceUrl",
        "sourceReviewStatus",
        "temporalReviewStatus",
        "extractionReviewStatus",
        "redistributionStatus",
        "statusAsOf",
        "evidenceUrls",
        "relations",
    }
    missing = required - set(document)
    if missing:
        raise ValueError(f"{field} is missing required fields: {sorted(missing)}")
    source_hash = document["sourceSha256"]
    if not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash):
        raise ValueError(f"{field}.sourceSha256 is invalid")
    if (
        not isinstance(document["path"], str)
        or not document["path"]
        or len(document["path"]) > 1024
    ):
        raise ValueError(f"{field}.path must be a non-empty string")
    if not isinstance(document["title"], str) or not document["title"].strip():
        raise ValueError(f"{field}.title must be a non-empty string")
    if len(document["title"]) > 500:
        raise ValueError(f"{field}.title exceeds 500 characters")
    if document["status"] not in _DOCUMENT_STATUSES:
        raise ValueError(f"{field}.status is unsupported")
    if document["documentType"] not in _DOCUMENT_TYPES:
        raise ValueError(f"{field}.documentType is unsupported")
    if (
        not isinstance(document["issuer"], str)
        or not document["issuer"].strip()
        or len(document["issuer"]) > 200
    ):
        raise ValueError(f"{field}.issuer must be a non-empty string")
    if (
        not isinstance(document["authorityRank"], int)
        or isinstance(document["authorityRank"], bool)
        or not 0 <= document["authorityRank"] <= 100
    ):
        raise ValueError(f"{field}.authorityRank must be an integer from 0 through 100")
    _optional_https(document["canonicalAuthorityUrl"], field=f"{field}.canonicalAuthorityUrl")
    _require_https(document["retrievalSourceUrl"], field=f"{field}.retrievalSourceUrl")
    for name in ("sourceReviewStatus", "temporalReviewStatus", "extractionReviewStatus"):
        if document[name] not in _REVIEW_STATES:
            raise ValueError(f"{field}.{name} is unsupported")
    if document["redistributionStatus"] not in {"not_assessed", "approved", "restricted"}:
        raise ValueError(f"{field}.redistributionStatus is unsupported")
    canonical_date(document["statusAsOf"], field=f"{field}.statusAsOf")
    effective_from = _optional_date(document.get("effectiveDate"), field=f"{field}.effectiveDate")
    effective_to = _optional_date(document.get("effectiveTo"), field=f"{field}.effectiveTo")
    _optional_date(document.get("promulgatedOn"), field=f"{field}.promulgatedOn")
    if effective_from and effective_to and effective_to <= effective_from:
        raise ValueError(f"{field}.effectiveTo must be after effectiveDate")
    document_number = document.get("documentNumber")
    if document_number is not None and (
        not isinstance(document_number, str)
        or not document_number.strip()
        or len(document_number) > 200
    ):
        raise ValueError(f"{field}.documentNumber must be a non-empty string or null")
    aliases = document.get("aliases", [])
    if not isinstance(aliases, list) or not all(
        isinstance(alias, str) and alias.strip() and len(alias) <= 200 for alias in aliases
    ):
        raise ValueError(f"{field}.aliases must be a list of non-empty strings")
    if len(aliases) > 32 or len(set(aliases)) != len(aliases):
        raise ValueError(f"{field}.aliases must contain at most 32 unique values")
    evidence_urls = document["evidenceUrls"]
    if not isinstance(evidence_urls, list) or not evidence_urls or len(evidence_urls) > 16:
        raise ValueError(f"{field}.evidenceUrls must contain 1 through 16 URLs")
    for url_index, url in enumerate(evidence_urls):
        _require_https(url, field=f"{field}.evidenceUrls[{url_index}]")
    if len(set(evidence_urls)) != len(evidence_urls):
        raise ValueError(f"{field}.evidenceUrls must contain unique URLs")
    raw_relations = document["relations"]
    if not isinstance(raw_relations, list) or len(raw_relations) > 32:
        raise ValueError(f"{field}.relations must be a list with at most 32 entries")
    document["relations"] = [
        _validate_relation(
            relation,
            field=f"{field}.relations[{relation_index}]",
            source_hashes=source_hashes,
        )
        for relation_index, relation in enumerate(raw_relations)
    ]
    note = document.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 4000):
        raise ValueError(f"{field}.note must be a string of at most 4000 characters")
    return deepcopy(document)


def apply_review_overlay(
    manifest: dict[str, Any],
    overlay_path: Path,
) -> AppliedReviewOverlay:
    """Validate and apply a hash-bound legal-review sidecar to a source manifest.

    The sidecar is administrative input. It can narrow a candidate document or
    add reviewed metadata, but cannot add source files or change their bytes.
    """

    if not isinstance(manifest, dict):
        raise ValueError("source manifest must be an object")
    raw_documents = manifest.get("documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise ValueError("source manifest documents must be a non-empty list")
    overlay_path = overlay_path.expanduser().resolve(strict=True)
    if overlay_path.stat().st_size > _MAX_OVERLAY_BYTES:
        raise ValueError("review overlay exceeds the 4 MiB limit")
    raw_bytes = overlay_path.read_bytes()
    try:
        overlay = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("review overlay is not valid UTF-8 JSON") from error
    overlay = _require_object(overlay, field="review overlay")
    unknown_root = set(overlay) - {"schemaVersion", "package", "documents"}
    if unknown_root:
        raise ValueError(f"review overlay contains unknown fields: {sorted(unknown_root)}")
    if overlay.get("schemaVersion") != REVIEW_OVERLAY_SCHEMA:
        raise ValueError(f"unsupported review overlay schema: {overlay.get('schemaVersion')}")
    package = _require_object(overlay.get("package"), field="package")
    unknown_package = set(package) - _PACKAGE_KEYS
    if unknown_package:
        raise ValueError(
            f"review overlay package contains unknown fields: {sorted(unknown_package)}"
        )
    if set(package) != _PACKAGE_KEYS:
        raise ValueError(f"review overlay package must contain exactly: {sorted(_PACKAGE_KEYS)}")
    reviewed_as_of = canonical_date(package["reviewedAsOf"], field="package.reviewedAsOf")
    reviewer_kind = package["reviewerKind"]
    if reviewer_kind not in {"ai_precheck", "human", "mixed"}:
        raise ValueError("package.reviewerKind is unsupported")
    review_scope = package["reviewScope"]
    if not isinstance(review_scope, str) or not review_scope.strip() or len(review_scope) > 2000:
        raise ValueError("package.reviewScope must be a non-empty string of at most 2000 chars")
    temporal_status = package["temporalStatus"]
    if temporal_status not in {"requires_human_review", "partially_verified", "verified"}:
        raise ValueError("package.temporalStatus is unsupported")
    redistribution_status = package["redistributionStatus"]
    if redistribution_status not in {"not_assessed", "approved", "restricted"}:
        raise ValueError("package.redistributionStatus is unsupported")
    if reviewer_kind == "ai_precheck" and temporal_status == "verified":
        raise ValueError("an AI-only review overlay cannot mark temporal metadata verified")
    if reviewer_kind == "ai_precheck" and redistribution_status == "approved":
        raise ValueError("an AI-only review overlay cannot approve redistribution")

    manifest_by_hash: dict[str, dict[str, Any]] = {}
    manifest_by_path: dict[str, dict[str, Any]] = {}
    for index, raw_document in enumerate(raw_documents):
        source_document = _require_object(raw_document, field=f"source documents[{index}]")
        source_hash = source_document.get("sha256")
        path = source_document.get("path")
        if not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash):
            raise ValueError(f"source documents[{index}].sha256 is invalid")
        if not isinstance(path, str) or not path:
            raise ValueError(f"source documents[{index}].path is invalid")
        if source_hash in manifest_by_hash or path in manifest_by_path:
            raise ValueError("source manifest contains duplicate path or SHA-256 values")
        manifest_by_hash[source_hash] = source_document
        manifest_by_path[path] = source_document
    source_hashes = set(manifest_by_hash)

    raw_reviews = overlay.get("documents")
    if not isinstance(raw_reviews, list) or not raw_reviews or len(raw_reviews) > 10_000:
        raise ValueError("review overlay documents must be a non-empty list")
    reviews = [
        _validated_document(review, index=index, source_hashes=source_hashes)
        for index, review in enumerate(raw_reviews)
    ]
    seen_hashes: set[str] = set()
    seen_paths: set[str] = set()
    for index, review in enumerate(reviews):
        source_hash = review["sourceSha256"]
        path = review["path"]
        if source_hash in seen_hashes or path in seen_paths:
            raise ValueError("review overlay contains a duplicate path or source SHA-256")
        seen_hashes.add(source_hash)
        seen_paths.add(path)
        source_document = manifest_by_hash.get(source_hash)
        if source_document is None or source_document is not manifest_by_path.get(path):
            raise ValueError(f"documents[{index}] does not bind the same source path and SHA-256")

    if temporal_status == "verified":
        if seen_hashes != source_hashes:
            raise ValueError(
                "verified temporal metadata requires full source-manifest coverage"
            )
        for index, review in enumerate(reviews):
            if review["temporalReviewStatus"] != "human_reviewed":
                raise ValueError(
                    "verified temporal metadata requires every document to be human-reviewed"
                )
            if review["status"] in {"unknown", "unverified_current"}:
                raise ValueError(
                    f"documents[{index}].status cannot remain unverified in a verified package"
                )
            effective_from = review.get("effectiveDate")
            effective_to = review.get("effectiveTo")
            if not effective_from:
                raise ValueError(
                    f"documents[{index}].effectiveDate is required in a verified package"
                )
            if (
                review["status"] in {"verified_historical", "repealed", "superseded"}
                and not effective_to
            ):
                raise ValueError(
                    f"documents[{index}].effectiveTo is required for a non-current verified status"
                )
            if review["statusAsOf"] != reviewed_as_of:
                raise ValueError(
                    f"documents[{index}].statusAsOf must equal package.reviewedAsOf"
                )
            if review["status"] == "not_yet_effective" and effective_from <= reviewed_as_of:
                raise ValueError(
                    f"documents[{index}] marked not_yet_effective must start after reviewedAsOf"
                )
            if review["status"] == "verified_current" and effective_from > reviewed_as_of:
                raise ValueError(
                    f"documents[{index}] marked verified_current must start by reviewedAsOf"
                )
            if (
                review["status"] == "verified_current"
                and effective_to is not None
                and effective_to <= reviewed_as_of
            ):
                raise ValueError(
                    f"documents[{index}] marked verified_current cannot end by reviewedAsOf"
                )
            if (
                review["status"] in {"verified_historical", "repealed", "superseded"}
                and effective_to > reviewed_as_of
            ):
                raise ValueError(
                    f"documents[{index}] non-current interval must end by reviewedAsOf"
                )

    if redistribution_status == "approved":
        if seen_hashes != source_hashes:
            raise ValueError("redistribution approval requires full source-manifest coverage")
        for review in reviews:
            if (
                review["redistributionStatus"] != "approved"
                or review["sourceReviewStatus"] != "human_reviewed"
            ):
                raise ValueError(
                    "redistribution approval requires every source to be human-reviewed "
                    "and approved"
                )

    merged = deepcopy(manifest)
    merged_documents_by_hash = {item["sha256"]: item for item in merged["documents"]}
    for review in reviews:
        target = merged_documents_by_hash[review["sourceSha256"]]
        for source_field, target_field in (
            ("title", "title"),
            ("documentNumber", "documentNumber"),
            ("aliases", "aliases"),
            ("promulgatedOn", "promulgatedOn"),
            ("effectiveDate", "effectiveDate"),
            ("effectiveTo", "effectiveTo"),
            ("status", "status"),
            ("documentType", "documentType"),
            ("issuer", "issuer"),
            ("authorityRank", "authorityRank"),
            ("retrievalSourceUrl", "retrievalSourceUrl"),
            ("sourceReviewStatus", "sourceReviewStatus"),
            ("temporalReviewStatus", "temporalReviewStatus"),
            ("extractionReviewStatus", "extractionReviewStatus"),
            ("redistributionStatus", "redistributionStatus"),
            ("statusAsOf", "statusAsOf"),
            ("evidenceUrls", "evidenceUrls"),
            ("relations", "relations"),
            ("note", "note"),
        ):
            if source_field not in review:
                continue
            value = deepcopy(review[source_field])
            nullable_fields = {
                "documentNumber",
                "promulgatedOn",
                "effectiveDate",
                "effectiveTo",
                "note",
            }
            if value is None and target_field in nullable_fields:
                target.pop(target_field, None)
            else:
                target[target_field] = value
        canonical_authority_url = review["canonicalAuthorityUrl"]
        target["canonicalAuthorityUrl"] = canonical_authority_url
        if canonical_authority_url is not None:
            target["officialSource"] = canonical_authority_url
    merged_package = merged.setdefault("package", {})
    merged_package["reviewOverlaySchema"] = REVIEW_OVERLAY_SCHEMA
    merged_package["reviewOverlaySha256"] = sha256_bytes(raw_bytes)
    merged_package["reviewedAsOf"] = reviewed_as_of
    merged_package["reviewerKind"] = reviewer_kind
    merged_package["reviewScope"] = review_scope
    merged_package["temporalStatus"] = temporal_status
    merged_package["redistributionStatus"] = redistribution_status
    return AppliedReviewOverlay(
        manifest=merged,
        overlay_sha256=sha256_bytes(raw_bytes),
        reviewed_as_of=reviewed_as_of,
        reviewer_kind=reviewer_kind,
        review_scope=review_scope,
        temporal_status=temporal_status,
        redistribution_status=redistribution_status,
        covered_documents=len(reviews),
    )
