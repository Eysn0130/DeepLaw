from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .evidence_graph import derive_relations
from .extract import ExtractionError, extract_document
from .models import BuildReport, Segment, SourceDocument
from .review_overlay import AppliedReviewOverlay, apply_review_overlay
from .segment import segment_document
from .store import (
    SCHEMA_VERSION,
    STORAGE_SCHEMA_VERSION,
    activate_release,
    create_release_database,
    database_sha256,
    verify_release_artifact,
)
from .util import canonical_date, canonical_json, sha256_bytes, sha256_file, stable_id

BUILD_REPORT_SCHEMA = "deeplaw.build-report/v1"
_MAX_SOURCE_BYTES = 512 * 1024 * 1024
_MAX_MANIFEST_BYTES = 64 * 1024 * 1024
_MAX_EXTRACTED_CHARACTERS = 20 * 1024 * 1024
_MAX_PATH_CHARACTERS = 1024
_MAX_TITLE_CHARACTERS = 500
_MAX_SOURCE_URL_CHARACTERS = 2048
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

_AUTHORITY = {
    "law": 100,
    "administrative_regulation": 90,
    "judicial_interpretation": 90,
    "prosecution_standard": 80,
    "departmental_rule": 70,
    "normative_document": 60,
    "case_reference": 40,
}


def _bounded_extraction_warnings(values: tuple[str, ...]) -> tuple[str, ...]:
    unique = tuple(dict.fromkeys(values))
    if len(unique) <= 16:
        return unique
    return (
        *unique[:15],
        f"{len(unique) - 15} additional page warnings retained in build-report.json",
    )


def _validate_public_output_bounds(
    document: SourceDocument,
    segments: list[Segment],
    *,
    relative_path: str,
) -> None:
    if len(document.extraction_method) > 100:
        raise ExtractionError(f"{relative_path}: extraction method exceeds 100 characters")
    if document.extraction_version is not None and len(document.extraction_version) > 500:
        raise ExtractionError(f"{relative_path}: extraction version exceeds 500 characters")
    if len(document.extraction_configuration) > 8 or any(
        len(value) > 200 for value in document.extraction_configuration
    ):
        raise ExtractionError(f"{relative_path}: extraction configuration exceeds output bounds")
    if len(document.extraction_warnings) > 16 or any(
        len(value) > 1000 for value in document.extraction_warnings
    ):
        raise ExtractionError(f"{relative_path}: extraction warnings exceed output bounds")
    for segment in segments:
        if len(segment.kind) > 64:
            raise ExtractionError(f"{relative_path}: segment kind exceeds 64 characters")
        if segment.heading is not None and len(segment.heading) > 500:
            raise ExtractionError(f"{relative_path}: segment heading exceeds 500 characters")
        if segment.article_label is not None and len(segment.article_label) > 100:
            raise ExtractionError(f"{relative_path}: article label exceeds 100 characters")
        if len(segment.text) > 12000:
            raise ExtractionError(f"{relative_path}: segment text exceeds 12000 characters")
        if segment.ordinal < 1 or segment.part_index < 1:
            raise ExtractionError(f"{relative_path}: segment indices must be positive")
        if any(
            value is not None and value < 1
            for value in (
                segment.page_start,
                segment.page_end,
                segment.paragraph_start,
                segment.paragraph_end,
            )
        ):
            raise ExtractionError(f"{relative_path}: segment locators must be positive")


def classify_document(relative_path: str, title: str) -> tuple[str, str, int]:
    if relative_path.startswith("04-"):
        document_type = "case_reference"
    elif "立案追诉标准" in title:
        document_type = "prosecution_standard"
    elif "解释" in title and ("最高人民法院" in title or "最高人民检察院" in title):
        document_type = "judicial_interpretation"
    elif title.startswith("中华人民共和国") and ("法" in title or "修正案" in title):
        document_type = "law"
    elif "条例" in title and "实施细则" not in title:
        document_type = "administrative_regulation"
    elif any(token in title for token in ("办法", "规定", "实施细则")):
        document_type = "departmental_rule"
    else:
        document_type = "normative_document"

    if "最高人民法院、最高人民检察院" in title:
        issuer = "最高人民法院、最高人民检察院"
    elif "最高人民法院" in title:
        issuer = "最高人民法院"
    elif "最高人民检察院、公安部" in title:
        issuer = "最高人民检察院、公安部"
    elif title.startswith("中国人民银行"):
        issuer = "中国人民银行"
    else:
        issuer = "待人工复核"
    return document_type, issuer, _AUTHORITY[document_type]


def _release_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    package = manifest.get("package", {})
    documents = []
    for document in manifest.get("documents", []):
        documents.append(
            {
                key: document.get(key)
                for key in (
                    "path",
                    "title",
                    "format",
                    "officialSource",
                    "canonicalAuthorityUrl",
                    "retrievalSourceUrl",
                    "byteSize",
                    "sha256",
                    "effectiveDate",
                    "effectiveTo",
                    "promulgatedOn",
                    "status",
                    "documentNumber",
                    "aliases",
                    "jurisdiction",
                    "documentType",
                    "issuer",
                    "authorityRank",
                    "relations",
                    "sourceReviewStatus",
                    "temporalReviewStatus",
                    "extractionReviewStatus",
                    "redistributionStatus",
                    "statusAsOf",
                    "evidenceUrls",
                    "note",
                )
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "package_name": package.get("name"),
        "retrieved_on": package.get("retrievedOn"),
        "package_qa_reviewed_on": package.get("reviewedOn"),
        "reviewed_on": package.get("reviewedAsOf"),
        "review_overlay_schema": package.get("reviewOverlaySchema"),
        "review_overlay_sha256": package.get("reviewOverlaySha256"),
        "reviewer_kind": package.get("reviewerKind"),
        "review_scope": package.get("reviewScope"),
        "temporal_status": package.get("temporalStatus"),
        "redistribution_status": package.get("redistributionStatus"),
        "documents": documents,
    }


def build_release(
    *,
    source_root: Path,
    manifest_path: Path,
    output_root: Path,
    activate: bool = False,
    pdf_fallback: str = "off",
    allow_needs_ocr: bool = False,
    review_overlay_path: Path | None = None,
    reviewed_pages_root: Path | None = None,
) -> tuple[Path, BuildReport]:
    source_root = source_root.expanduser().resolve(strict=True)
    manifest_path = manifest_path.expanduser().resolve(strict=True)
    output_root = output_root.expanduser().resolve()
    if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise ValueError("manifest exceeds the 64 MiB limit")
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = sha256_bytes(manifest_bytes)
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"manifest is not valid UTF-8 JSON: {manifest_path}") from error
    if not isinstance(manifest, dict):
        raise ValueError("manifest root must be an object")
    applied_review: AppliedReviewOverlay | None = None
    if review_overlay_path is not None:
        applied_review = apply_review_overlay(manifest, review_overlay_path)
        manifest = applied_review.manifest
    if reviewed_pages_root is not None:
        reviewed_pages_root = reviewed_pages_root.expanduser().resolve(strict=True)
        if reviewed_pages_root.is_symlink() or not reviewed_pages_root.is_dir():
            raise ValueError("reviewed-pages root must be a non-symlink directory")
    if not isinstance(manifest.get("package", {}), dict):
        raise ValueError("manifest package must be an object")
    package = manifest.get("package", {})
    package_name = package.get("name")
    if package_name is not None and (
        not isinstance(package_name, str) or not package_name.strip() or len(package_name) > 500
    ):
        raise ValueError("manifest package.name must be a non-empty string of at most 500 chars")
    for field_name in ("retrievedOn", "reviewedOn", "reviewedAsOf"):
        if package.get(field_name) is not None:
            if not isinstance(package[field_name], str):
                raise ValueError(f"manifest package.{field_name} must be a date string")
            canonical_date(package[field_name], field=f"package.{field_name}")
    report = BuildReport(schema_version=BUILD_REPORT_SCHEMA, release_id="pending")
    documents: list[SourceDocument] = []
    all_segments = []
    seen_paths: set[str] = set()
    seen_hashes: set[str] = set()

    raw_documents = manifest.get("documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise ValueError("manifest documents must be a non-empty list")
    if len(raw_documents) > 10_000:
        raise ValueError("manifest contains too many documents")
    if not all(isinstance(raw, dict) for raw in raw_documents):
        raise ValueError("every manifest document must be an object")
    payload = _release_payload(manifest)
    declared_count = manifest.get("package", {}).get("documentCount")
    if declared_count is not None and declared_count != len(raw_documents):
        raise ValueError(
            f"manifest documentCount mismatch: expected {declared_count}, got {len(raw_documents)}"
        )

    for raw in raw_documents:
        relative_path = str(raw.get("path", "")).strip()
        if not relative_path or relative_path in seen_paths:
            raise ValueError(f"invalid or duplicate manifest path: {relative_path!r}")
        if len(relative_path) > _MAX_PATH_CHARACTERS:
            raise ValueError(f"manifest path is too long: {relative_path[:120]}")
        path_parts = Path(relative_path).parts
        if Path(relative_path).is_absolute() or any(part in {".", ".."} for part in path_parts):
            raise ValueError(f"manifest path must be normalized and relative: {relative_path}")
        seen_paths.add(relative_path)
        declared_path = source_root
        for part in path_parts:
            declared_path /= part
            if declared_path.is_symlink():
                raise ValueError(f"manifest path contains a symbolic link: {relative_path}")
        source_path = declared_path.resolve(strict=True)
        try:
            source_path.relative_to(source_root)
        except ValueError as error:
            raise ValueError(f"manifest path escapes source root: {relative_path}") from error
        if not stat.S_ISREG(source_path.stat().st_mode):
            raise ValueError(f"manifest path is not a regular file: {relative_path}")
        expected_size = int(raw.get("byteSize", -1))
        actual_size = source_path.stat().st_size
        if actual_size > _MAX_SOURCE_BYTES:
            raise ValueError(f"source file exceeds the 512 MiB limit: {relative_path}")
        if expected_size != actual_size:
            raise ValueError(
                f"byte size mismatch for {relative_path}: "
                f"expected {expected_size}, got {actual_size}"
            )
        expected_hash = str(raw.get("sha256", "")).lower()
        if not _SHA256.fullmatch(expected_hash):
            raise ValueError(f"invalid SHA-256 declaration for {relative_path}")
        actual_hash = sha256_file(source_path)
        if expected_hash != actual_hash:
            raise ValueError(f"SHA-256 mismatch for {relative_path}")
        if actual_hash in seen_hashes:
            raise ValueError(f"duplicate source SHA-256 in manifest: {relative_path}")
        seen_hashes.add(actual_hash)

        title = str(raw.get("title", "")).strip()
        if not title:
            raise ValueError(f"manifest title is required for {relative_path}")
        if len(title) > _MAX_TITLE_CHARACTERS:
            raise ValueError(f"manifest title exceeds 500 characters for {relative_path}")
        format_name = str(raw.get("format", source_path.suffix.lstrip("."))).upper()
        expected_suffix = {"DOCX": ".docx", "PDF": ".pdf"}.get(format_name)
        if expected_suffix is None or source_path.suffix.lower() != expected_suffix:
            raise ValueError(f"format/path mismatch for {relative_path}: {format_name}")
        official_source = str(raw.get("officialSource", "")).strip()
        parsed_source = urlparse(official_source)
        try:
            source_hostname = parsed_source.hostname
            _ = parsed_source.port
        except ValueError as error:
            raise ValueError(f"officialSource is malformed for {relative_path}") from error
        if (
            len(official_source) > _MAX_SOURCE_URL_CHARACTERS
            or any(ord(character) < 32 for character in official_source)
            or parsed_source.scheme != "https"
            or not parsed_source.netloc
            or not source_hostname
            or parsed_source.username is not None
            or parsed_source.password is not None
        ):
            raise ValueError(f"officialSource must be HTTPS for {relative_path}")

        inferred_type, inferred_issuer, inferred_authority = classify_document(relative_path, title)
        document_type = str(raw.get("documentType") or inferred_type)
        if document_type not in _AUTHORITY:
            raise ValueError(f"unsupported documentType for {relative_path}: {document_type}")
        issuer = str(raw.get("issuer") or inferred_issuer).strip()
        if not issuer or len(issuer) > 200:
            raise ValueError(f"invalid issuer for {relative_path}")
        authority_rank = int(raw.get("authorityRank", inferred_authority))
        if not 0 <= authority_rank <= 100:
            raise ValueError(f"invalid authorityRank for {relative_path}: {authority_rank}")
        normalized_dates: dict[str, str] = {}
        for field_name in ("effectiveDate", "effectiveTo", "promulgatedOn"):
            if raw.get(field_name) is not None:
                if not isinstance(raw[field_name], str):
                    raise ValueError(f"{field_name} must be a date string for {relative_path}")
                normalized_dates[field_name] = canonical_date(
                    raw[field_name], field=f"{field_name} for {relative_path}"
                )
        effective_from = normalized_dates.get("effectiveDate")
        effective_to = normalized_dates.get("effectiveTo")
        if effective_from and effective_to and effective_to <= effective_from:
            raise ValueError(f"effectiveTo must be after effectiveDate for {relative_path}")
        raw_aliases = raw.get("aliases") or []
        if not isinstance(raw_aliases, list) or not all(
            isinstance(alias, str) and alias.strip() for alias in raw_aliases
        ):
            raise ValueError(f"aliases must be a list of non-empty strings for {relative_path}")
        aliases = tuple(dict.fromkeys(alias.strip() for alias in raw_aliases))
        if len(aliases) > 32 or any(len(alias) > 200 for alias in aliases):
            raise ValueError(f"aliases exceed the allowed size for {relative_path}")
        document_number = str(raw.get("documentNumber") or "").strip() or None
        if document_number and len(document_number) > 200:
            raise ValueError(f"documentNumber is too long for {relative_path}")
        jurisdiction = str(raw.get("jurisdiction") or "CN").strip()
        if not jurisdiction or len(jurisdiction) > 64:
            raise ValueError(f"invalid jurisdiction for {relative_path}")
        status = str(raw.get("status") or "unverified_current").strip()
        if status not in _DOCUMENT_STATUSES:
            raise ValueError(f"unsupported status for {relative_path}: {status}")
        if status.startswith("verified_") and not effective_from:
            raise ValueError(f"verified status requires effectiveDate for {relative_path}")
        raw_note = raw.get("note")
        if raw_note is not None and not isinstance(raw_note, str):
            raise ValueError(f"note must be a string for {relative_path}")
        note = raw_note.strip() if isinstance(raw_note, str) else None
        if note and len(note) > 4000:
            raise ValueError(f"note exceeds 4000 characters for {relative_path}")
        document_id = stable_id("doc", actual_hash, title)
        source_document = SourceDocument(
            document_id=document_id,
            title=title,
            document_number=document_number,
            aliases=aliases,
            promulgated_on=normalized_dates.get("promulgatedOn"),
            jurisdiction=jurisdiction,
            relative_path=relative_path,
            format=format_name,
            official_source=official_source,
            source_sha256=actual_hash,
            byte_size=actual_size,
            document_type=document_type,
            issuer=issuer,
            authority_rank=authority_rank,
            effective_from=effective_from,
            effective_to=effective_to,
            status=status,
            note=note,
        )
        reviewed_pages_path: Path | None = None
        if reviewed_pages_root is not None and format_name == "PDF":
            candidate_review = reviewed_pages_root / f"{actual_hash}.reviewed-pages.json"
            if candidate_review.is_symlink():
                raise ValueError(f"reviewed-pages file must not be a symlink: {relative_path}")
            if candidate_review.exists():
                if not candidate_review.is_file():
                    raise ValueError(
                        f"reviewed-pages path must be a regular file: {relative_path}"
                    )
                reviewed_pages_path = candidate_review
        try:
            extraction = extract_document(
                source_path,
                format_name,
                pdf_fallback=pdf_fallback,
                reviewed_pages_path=reviewed_pages_path,
            )
        except ExtractionError as error:
            raise ExtractionError(f"{relative_path}: {error}") from error
        if extraction.quality.needs_ocr and not allow_needs_ocr:
            raise ExtractionError(
                f"{relative_path}: PDF text quality gate failed; rerun with --pdf-fallback "
                "vision-consensus, or "
                "--allow-needs-ocr for an explicitly incomplete candidate release"
            )
        if source_path.stat().st_size != actual_size or sha256_file(source_path) != actual_hash:
            raise RuntimeError(f"source changed while it was being extracted: {relative_path}")
        if extraction.quality.character_count > _MAX_EXTRACTED_CHARACTERS:
            raise ExtractionError(f"{relative_path}: extracted text exceeds the 20 MiB limit")
        public_extraction_warnings = _bounded_extraction_warnings(
            extraction.quality.warnings
        )
        source_document = replace(
            source_document,
            extraction_method=extraction.quality.extractor,
            extraction_version=extraction.quality.extractor_version,
            extraction_configuration=extraction.quality.configuration,
            extraction_review_required=(
                extraction.quality.review_required
                or extraction.quality.needs_ocr
                or bool(public_extraction_warnings)
            ),
            extraction_warnings=public_extraction_warnings,
        )
        segments = list(segment_document(document_id, extraction.blocks))
        if not segments:
            raise ExtractionError(f"{relative_path}: extraction produced no segments")
        _validate_public_output_bounds(
            source_document,
            segments,
            relative_path=relative_path,
        )

        documents.append(source_document)
        all_segments.extend(segments)
        report.document_count += 1
        report.segment_count += len(segments)
        report.source_bytes += actual_size
        extractor = extraction.quality.extractor
        report.extractors[extractor] = report.extractors.get(extractor, 0) + 1
        for warning in extraction.quality.warnings:
            report.warnings.append({"path": relative_path, "warning": warning})
        report.documents.append(
            {
                "document_id": document_id,
                "path": relative_path,
                "title": title,
                "format": format_name,
                "source_sha256": actual_hash,
                "extractor": extractor,
                "extractor_version": extraction.quality.extractor_version,
                "extractor_configuration": list(extraction.quality.configuration),
                "extracted_text_sha256": sha256_bytes(
                    "\n".join(block.text for block in extraction.blocks).encode("utf-8")
                ),
                "characters": extraction.quality.character_count,
                "pages": extraction.quality.page_count,
                "segments": len(segments),
                "needs_ocr": extraction.quality.needs_ocr,
                "review_required": extraction.quality.review_required,
                "reviewed_page_count": extraction.quality.reviewed_page_count,
                "page_evidence": [
                    asdict(page_evidence)
                    for page_evidence in extraction.quality.page_evidence
                ],
            }
        )

    relations = derive_relations(documents, all_segments)
    report.relation_count = len(relations)
    derivation_payload = {
        "ingestion_schema": "deeplaw.ingestion/v1",
        "release_schema": SCHEMA_VERSION,
        "storage_schema": STORAGE_SCHEMA_VERSION,
        "storage_engine": {"sqlite": sqlite3.sqlite_version},
        "segmentation": {"algorithm": "deterministic-article-structure/v1", "max_chars": 4500},
        "source_manifest": payload,
        "documents": [asdict(document) for document in documents],
        "segments": [
            {
                "segment_id": segment.segment_id,
                "document_id": segment.document_id,
                "ordinal": segment.ordinal,
                "text_sha256": segment.text_sha256,
            }
            for segment in all_segments
        ],
        "relations": [relation.to_dict() for relation in relations],
        "extractors": [
            {
                "path": item["path"],
                "extractor": item["extractor"],
                "extractor_version": item["extractor_version"],
                "extractor_configuration": item["extractor_configuration"],
                "extracted_text_sha256": item["extracted_text_sha256"],
                "review_required": item["review_required"],
                "reviewed_page_count": item["reviewed_page_count"],
                "page_evidence": item["page_evidence"],
            }
            for item in report.documents
        ],
    }
    derivation_sha256 = sha256_bytes(canonical_json(derivation_payload).encode("utf-8"))
    release_id = stable_id("lawrel", derivation_sha256, length=32)
    report.release_id = release_id
    release_dir = output_root / release_id
    release_metadata = {
        "schema_version": SCHEMA_VERSION,
        "release_id": release_id,
        "package_name": payload["package_name"],
        "retrieved_on": payload["retrieved_on"],
        "reviewed_on": payload["reviewed_on"],
        "document_count": len(documents),
        "segment_count": len(all_segments),
        "source_manifest_sha256": manifest_sha256,
        "derivation_sha256": derivation_sha256,
        "ingestion_schema": "deeplaw.ingestion/v1",
        "storage_schema": STORAGE_SCHEMA_VERSION,
        "storage_engine": {"sqlite": sqlite3.sqlite_version},
        "temporal_status": (
            applied_review.temporal_status if applied_review else "requires_human_review"
        ),
        "redistribution_status": (
            applied_review.redistribution_status if applied_review else "not_assessed"
        ),
        "vector_index": False,
        "derived_wiki": False,
    }
    if applied_review is not None:
        release_metadata.update(
            {
                "review_overlay_schema": "deeplaw.review-overlay/v1",
                "review_overlay_sha256": applied_review.overlay_sha256,
                "reviewed_on": applied_review.reviewed_as_of,
                "reviewer_kind": applied_review.reviewer_kind,
                "review_scope": applied_review.review_scope,
                "review_covered_documents": applied_review.covered_documents,
            }
        )
    if payload["package_qa_reviewed_on"] is not None:
        release_metadata["package_qa_reviewed_on"] = payload["package_qa_reviewed_on"]
    output_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=".deeplaw-build-", dir=output_root))
    try:
        database_path = staging_dir / "deeplaw.sqlite3"
        create_release_database(
            database_path,
            release_id=release_id,
            release_metadata=release_metadata,
            documents=documents,
            segments=all_segments,
            relations=relations,
        )
        release_metadata["database_sha256"] = database_sha256(database_path)
        os.chmod(database_path, 0o444)
        (staging_dir / "release.json").write_text(
            json.dumps(release_metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (staging_dir / "build-report.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(staging_dir / "release.json", 0o444)
        os.chmod(staging_dir / "build-report.json", 0o444)
        if release_dir.exists():
            existing_manifest_path = release_dir / "release.json"
            existing_database_path = release_dir / "deeplaw.sqlite3"
            existing_report_path = release_dir / "build-report.json"
            staged_report_path = staging_dir / "build-report.json"
            if (
                not existing_manifest_path.is_file()
                or not existing_database_path.is_file()
                or existing_report_path.is_symlink()
                or not existing_report_path.is_file()
            ):
                raise RuntimeError(f"existing immutable release is incomplete: {release_dir}")
            existing = verify_release_artifact(existing_database_path)
            if (
                existing != release_metadata
                or existing.get("database_sha256") != database_sha256(existing_database_path)
                or sha256_file(existing_report_path) != sha256_file(staged_report_path)
            ):
                raise RuntimeError(f"existing immutable release failed verification: {release_dir}")
        else:
            os.replace(staging_dir, release_dir)
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
    if activate:
        activate_release(output_root, release_id)
    return release_dir, report
