from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from .evidence_graph import RELATION_TYPES, EvidenceRelation
from .models import Segment, SourceDocument
from .util import canonical_date, canonical_json, compact_text, search_terms, sha256_file

SCHEMA_VERSION = "deeplaw.release/v2"
STORAGE_SCHEMA_VERSION = "deeplaw.sqlite/v4"
_RELEASE_ID = re.compile(r"^lawrel_[0-9a-f]{32}$")
_RELATION_ID = re.compile(r"^lawedge_[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_RELEASE_MANIFEST_BYTES = 64 * 1024
_RELEASE_REQUIRED_FIELDS = {
    "schema_version",
    "release_id",
    "package_name",
    "document_count",
    "segment_count",
    "source_manifest_sha256",
    "derivation_sha256",
    "ingestion_schema",
    "storage_schema",
    "storage_engine",
    "database_sha256",
    "temporal_status",
    "redistribution_status",
    "vector_index",
    "derived_wiki",
}
_RELEASE_OPTIONAL_FIELDS = {
    "retrieved_on",
    "reviewed_on",
    "package_qa_reviewed_on",
    "review_overlay_schema",
    "review_overlay_sha256",
    "reviewer_kind",
    "review_scope",
    "review_covered_documents",
}


def _validate_release_manifest(manifest: Any, *, directory_name: str) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise RuntimeError("release manifest must be an object")
    fields = set(manifest)
    missing = _RELEASE_REQUIRED_FIELDS - fields
    unknown = fields - _RELEASE_REQUIRED_FIELDS - _RELEASE_OPTIONAL_FIELDS
    if missing or unknown:
        raise RuntimeError(
            "release manifest fields do not match the closed v2 contract: "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    release_id = manifest.get("release_id")
    if (
        not isinstance(release_id, str)
        or not _RELEASE_ID.fullmatch(release_id)
        or release_id != directory_name
    ):
        raise RuntimeError("release manifest ID does not match its directory")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("unsupported release manifest schema")
    if manifest.get("ingestion_schema") != "deeplaw.ingestion/v1":
        raise RuntimeError("unsupported release ingestion schema")
    if manifest.get("storage_schema") != STORAGE_SCHEMA_VERSION:
        raise RuntimeError("unsupported release storage schema")
    package_name = manifest.get("package_name")
    if package_name is not None and (
        not isinstance(package_name, str) or len(package_name) > 500
    ):
        raise RuntimeError("release package_name is invalid")
    for field_name in ("retrieved_on", "reviewed_on", "package_qa_reviewed_on"):
        value = manifest.get(field_name)
        if value is not None:
            if not isinstance(value, str):
                raise RuntimeError(f"release {field_name} is invalid")
            try:
                canonical_date(value, field=f"release {field_name}")
            except ValueError as error:
                raise RuntimeError(f"release {field_name} is invalid") from error
    for field_name in ("document_count", "segment_count"):
        value = manifest.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise RuntimeError(f"release {field_name} is invalid")
    for field_name in ("source_manifest_sha256", "derivation_sha256", "database_sha256"):
        value = manifest.get(field_name)
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise RuntimeError(f"release {field_name} is invalid")
    review_overlay_sha256 = manifest.get("review_overlay_sha256")
    if review_overlay_sha256 is not None and (
        not isinstance(review_overlay_sha256, str)
        or not _SHA256.fullmatch(review_overlay_sha256)
    ):
        raise RuntimeError("release review_overlay_sha256 is invalid")
    review_overlay_schema = manifest.get("review_overlay_schema")
    if review_overlay_schema is not None and review_overlay_schema != "deeplaw.review-overlay/v1":
        raise RuntimeError("release review_overlay_schema is invalid")
    reviewer_kind = manifest.get("reviewer_kind")
    if reviewer_kind is not None and reviewer_kind not in {"ai_precheck", "human", "mixed"}:
        raise RuntimeError("release reviewer_kind is invalid")
    review_scope = manifest.get("review_scope")
    if review_scope is not None and (
        not isinstance(review_scope, str) or not review_scope or len(review_scope) > 2000
    ):
        raise RuntimeError("release review_scope is invalid")
    review_covered_documents = manifest.get("review_covered_documents")
    if review_covered_documents is not None and (
        isinstance(review_covered_documents, bool)
        or not isinstance(review_covered_documents, int)
        or not 1 <= review_covered_documents <= manifest["document_count"]
    ):
        raise RuntimeError("release review_covered_documents is invalid")
    storage_engine = manifest.get("storage_engine")
    if not isinstance(storage_engine, dict) or set(storage_engine) != {"sqlite"}:
        raise RuntimeError("release storage_engine is invalid")
    sqlite_version = storage_engine.get("sqlite")
    if not isinstance(sqlite_version, str) or not sqlite_version or len(sqlite_version) > 64:
        raise RuntimeError("release SQLite version is invalid")
    if manifest.get("temporal_status") not in {
        "requires_human_review",
        "partially_verified",
        "verified",
    }:
        raise RuntimeError("release temporal_status is invalid")
    if manifest.get("redistribution_status") not in {"not_assessed", "approved", "restricted"}:
        raise RuntimeError("release redistribution_status is invalid")
    has_review_outcome = (
        manifest["temporal_status"] != "requires_human_review"
        or manifest["redistribution_status"] != "not_assessed"
    )
    if has_review_outcome and (
        manifest.get("reviewed_on") is None
        or review_overlay_schema is None
        or review_overlay_sha256 is None
        or reviewer_kind is None
        or review_scope is None
        or review_covered_documents is None
    ):
        raise RuntimeError("release review outcome lacks a complete review-overlay binding")
    if manifest["temporal_status"] == "verified" and (
        reviewer_kind not in {"human", "mixed"}
        or review_covered_documents != manifest["document_count"]
    ):
        raise RuntimeError(
            "verified release requires full human temporal-review coverage"
        )
    if manifest["redistribution_status"] == "approved" and (
        reviewer_kind not in {"human", "mixed"}
        or review_covered_documents != manifest["document_count"]
    ):
        raise RuntimeError(
            "approved release requires full human redistribution-review coverage"
        )
    if not isinstance(manifest.get("vector_index"), bool) or not isinstance(
        manifest.get("derived_wiki"), bool
    ):
        raise RuntimeError("release derived-index flags are invalid")
    return manifest


def _token_string(text: str) -> str:
    return " ".join(search_terms(text))


def create_release_database(
    path: Path,
    *,
    release_id: str,
    release_metadata: dict[str, Any],
    documents: list[SourceDocument],
    segments: list[Segment],
    relations: tuple[EvidenceRelation, ...] = (),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode = DELETE;
            PRAGMA synchronous = FULL;
            PRAGMA foreign_keys = ON;

            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE documents (
                document_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                document_number TEXT,
                aliases_json TEXT NOT NULL,
                normalized_names TEXT NOT NULL,
                promulgated_on TEXT,
                jurisdiction TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                format TEXT NOT NULL,
                official_source TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                document_type TEXT NOT NULL,
                issuer TEXT NOT NULL,
                authority_rank INTEGER NOT NULL,
                effective_from TEXT,
                effective_to TEXT,
                status TEXT NOT NULL,
                note TEXT,
                extraction_method TEXT NOT NULL,
                extraction_version TEXT,
                extraction_configuration_json TEXT NOT NULL,
                extraction_review_required INTEGER NOT NULL,
                extraction_warnings_json TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE segments (
                segment_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(document_id),
                ordinal INTEGER NOT NULL,
                kind TEXT NOT NULL,
                heading TEXT,
                article_label TEXT,
                part_index INTEGER NOT NULL,
                page_start INTEGER,
                page_end INTEGER,
                paragraph_start INTEGER,
                paragraph_end INTEGER,
                text TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                UNIQUE(document_id, ordinal)
            ) WITHOUT ROWID;

            CREATE INDEX segments_document_article
                ON segments(document_id, article_label, ordinal);
            CREATE INDEX documents_type_effective
                ON documents(document_type, effective_from, effective_to);

            CREATE TABLE legal_edges (
                relation_id TEXT PRIMARY KEY,
                subject_document_id TEXT NOT NULL REFERENCES documents(document_id),
                predicate TEXT NOT NULL,
                object_document_id TEXT NOT NULL REFERENCES documents(document_id),
                provenance_segment_id TEXT NOT NULL REFERENCES segments(segment_id),
                evidence_sha256 TEXT NOT NULL,
                derivation TEXT NOT NULL,
                review_status TEXT NOT NULL,
                valid_from TEXT,
                valid_to TEXT,
                CHECK(subject_document_id <> object_document_id)
            ) WITHOUT ROWID;

            CREATE INDEX legal_edges_subject
                ON legal_edges(subject_document_id, predicate, object_document_id);
            CREATE INDEX legal_edges_object
                ON legal_edges(object_document_id, predicate, subject_document_id);

            CREATE VIRTUAL TABLE segment_search USING fts5(
                segment_id UNINDEXED,
                title_tokens,
                body_tokens,
                locator_tokens,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "release_id": release_id,
            "release_metadata": canonical_json(release_metadata),
        }
        connection.executemany("INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items())

        for document in documents:
            connection.execute(
                """
                INSERT INTO documents VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    document.document_id,
                    document.title,
                    compact_text(document.title),
                    document.document_number,
                    canonical_json(list(document.aliases)),
                    " ".join(
                        compact_text(value)
                        for value in (
                            document.title,
                            document.document_number or "",
                            *document.aliases,
                        )
                        if value
                    ),
                    document.promulgated_on,
                    document.jurisdiction,
                    document.relative_path,
                    document.format,
                    document.official_source,
                    document.source_sha256,
                    document.byte_size,
                    document.document_type,
                    document.issuer,
                    document.authority_rank,
                    document.effective_from,
                    document.effective_to,
                    document.status,
                    document.note,
                    document.extraction_method,
                    document.extraction_version,
                    canonical_json(list(document.extraction_configuration)),
                    int(document.extraction_review_required),
                    canonical_json(list(document.extraction_warnings)),
                ),
            )

        by_document = {document.document_id: document for document in documents}
        for segment in segments:
            document = by_document[segment.document_id]
            connection.execute(
                """
                INSERT INTO segments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment.segment_id,
                    segment.document_id,
                    segment.ordinal,
                    segment.kind,
                    segment.heading,
                    segment.article_label,
                    segment.part_index,
                    segment.page_start,
                    segment.page_end,
                    segment.paragraph_start,
                    segment.paragraph_end,
                    segment.text,
                    segment.text_sha256,
                ),
            )
            locator = " ".join(
                value for value in (segment.heading, segment.article_label, segment.kind) if value
            )
            connection.execute(
                "INSERT INTO segment_search VALUES (?, ?, ?, ?)",
                (
                    segment.segment_id,
                    _token_string(
                        " ".join(
                            (
                                document.title,
                                document.document_number or "",
                                *document.aliases,
                            )
                        )
                    ),
                    _token_string(segment.text),
                    _token_string(locator),
                ),
            )
        by_segment = {segment.segment_id: segment for segment in segments}
        for relation in relations:
            provenance = by_segment.get(relation.provenance_segment_id)
            if (
                not _RELATION_ID.fullmatch(relation.relation_id)
                or relation.subject_document_id not in by_document
                or relation.object_document_id not in by_document
                or relation.subject_document_id == relation.object_document_id
                or relation.predicate not in RELATION_TYPES
                or provenance is None
                or provenance.document_id != relation.subject_document_id
                or relation.evidence_sha256 != provenance.text_sha256
                or relation.review_status != "deterministic_exact"
                or not relation.derivation
                or len(relation.derivation) > 200
            ):
                raise ValueError("legal relation violates the deterministic provenance contract")
            for field_name, value in (
                ("valid_from", relation.valid_from),
                ("valid_to", relation.valid_to),
            ):
                if value is not None:
                    canonical_date(value, field=f"relation {field_name}")
            if (
                relation.valid_from
                and relation.valid_to
                and relation.valid_to <= relation.valid_from
            ):
                raise ValueError("legal relation valid_to must be after valid_from")
            connection.execute(
                """
                INSERT INTO legal_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation.relation_id,
                    relation.subject_document_id,
                    relation.predicate,
                    relation.object_document_id,
                    relation.provenance_segment_id,
                    relation.evidence_sha256,
                    relation.derivation,
                    relation.review_status,
                    relation.valid_from,
                    relation.valid_to,
                ),
            )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        connection.execute("PRAGMA optimize")
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary, path)


def connect_readonly(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve(strict=True)
    uri = f"{resolved.as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def verify_release_artifact(path: Path) -> dict[str, Any]:
    database = path.expanduser().absolute()
    if database.is_symlink() or not database.is_file():
        raise RuntimeError(f"release database must be a regular non-symlink file: {database}")
    if database.parent.is_symlink():
        raise RuntimeError(f"release directory must not be a symbolic link: {database.parent}")
    manifest_path = database.parent / "release.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError(f"release manifest is missing or unsafe: {manifest_path}")
    if manifest_path.stat().st_size > _MAX_RELEASE_MANIFEST_BYTES:
        raise RuntimeError(f"release manifest exceeds the 64 KiB limit: {manifest_path}")
    try:
        manifest_bytes = manifest_path.read_bytes()
        if len(manifest_bytes) > _MAX_RELEASE_MANIFEST_BYTES:
            raise RuntimeError(f"release manifest exceeds the 64 KiB limit: {manifest_path}")
        manifest = json.loads(manifest_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"release manifest cannot be read: {manifest_path}") from error
    manifest = _validate_release_manifest(manifest, directory_name=database.parent.name)
    expected_hash = manifest.get("database_sha256")
    actual_hash = database_sha256(database)
    if expected_hash != actual_hash:
        raise RuntimeError("release database SHA-256 does not match release.json")
    return manifest


def default_home() -> Path:
    configured = os.environ.get("DEEPLAW_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".deeplaw"


def resolve_active_database(
    *,
    explicit_db: str | Path | None = None,
    home: str | Path | None = None,
) -> Path:
    if explicit_db:
        database = Path(explicit_db).expanduser().absolute()
        if not database.exists():
            raise FileNotFoundError(database)
        return database
    env_db = os.environ.get("DEEPLAW_DB")
    if env_db:
        database = Path(env_db).expanduser().absolute()
        if not database.exists():
            raise FileNotFoundError(database)
        return database

    root = (Path(home).expanduser() if home else default_home()).absolute()
    if root.is_symlink():
        raise RuntimeError(f"DeepLaw home must not be a symbolic link: {root}")
    active = root / "ACTIVE"
    if active.is_symlink() or not active.is_file():
        raise FileNotFoundError(
            f"DeepLaw has no active release at {active}; run `deeplaw build --activate`"
        )
    if active.stat().st_size > 128:
        raise RuntimeError(f"DeepLaw ACTIVE pointer is too large: {active}")
    release_id = active.read_text(encoding="utf-8").strip()
    if not _RELEASE_ID.fullmatch(release_id):
        raise RuntimeError(f"invalid DeepLaw ACTIVE pointer: {active}")
    releases_root = root / "releases"
    if releases_root.is_symlink() or not releases_root.is_dir():
        raise RuntimeError(f"DeepLaw releases directory is missing or unsafe: {releases_root}")
    release_dir = releases_root / release_id
    database = release_dir / "deeplaw.sqlite3"
    if release_dir.is_symlink() or database.is_symlink():
        raise RuntimeError("DeepLaw active release must not contain symbolic links")
    resolved_releases = releases_root.resolve(strict=True)
    resolved_release = release_dir.resolve(strict=True)
    try:
        resolved_release.relative_to(resolved_releases)
    except ValueError as error:
        raise RuntimeError("DeepLaw active release escapes the configured home") from error
    return (resolved_release / "deeplaw.sqlite3").absolute()


def activate_release(output_root: Path, release_id: str) -> Path:
    if not _RELEASE_ID.fullmatch(release_id):
        raise ValueError(f"invalid DeepLaw release ID: {release_id}")
    var_root = output_root.parent
    active = var_root / "ACTIVE"
    temporary = active.with_suffix(".tmp")
    temporary.write_text(f"{release_id}\n", encoding="utf-8")
    os.replace(temporary, active)
    return active


def release_info(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute("SELECT key, value FROM metadata").fetchall()
    metadata = {row["key"]: row["value"] for row in rows}
    raw_release = metadata.get("release_metadata", "{}")
    return {
        "schema_version": metadata.get("schema_version"),
        "release_id": metadata.get("release_id"),
        "release": json.loads(raw_release),
        "document_count": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "segment_count": connection.execute("SELECT COUNT(*) FROM segments").fetchone()[0],
    }


def database_sha256(path: Path) -> str:
    return sha256_file(path)
