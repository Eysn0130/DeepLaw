from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
from collections import OrderedDict
from collections.abc import Iterable
from contextlib import AbstractContextManager
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Literal, cast

from .knowledge_models import (
    ASSET_KINDS,
    ASSET_STATUSES,
    MEMORY_TIERS,
    SENSITIVITY_LEVELS,
    SOURCE_KINDS,
    TRUST_LEVELS,
    AssetKind,
    AssetStatus,
    KnowledgeAsset,
    KnowledgeCard,
    KnowledgeSearchResponse,
    MemoryTier,
    Sensitivity,
    SourceKind,
    SourceReference,
    TrustLevel,
    asset_content_sha256,
    canonical_timestamp,
    utc_now,
)
from .store import default_home
from .util import (
    canonical_json,
    compact_text,
    excerpt,
    fts_query,
    has_instruction_risk,
    search_terms,
    sha256_bytes,
    sha256_file,
    stable_id,
    strict_json_loads,
)

KNOWLEDGE_VAULT_SCHEMA = "deeplaw.knowledge-vault/v1"
KNOWLEDGE_STORAGE_SCHEMA = "deeplaw.knowledge-sqlite/v1"
KNOWLEDGE_EVENT_SCHEMA = "deeplaw.knowledge-event/v1"

VaultScope = Literal["personal", "project", "team", "domain"]
VAULT_SCOPES = frozenset(VaultScope.__args__)
RELATION_PREDICATES = frozenset(
    {
        "supports",
        "contradicts",
        "depends_on",
        "implements",
        "derived_from",
        "applies_to",
        "related_to",
    }
)

_VAULT_ID = re.compile(r"^vault_[0-9a-f]{24}$")
_ASSET_ID = re.compile(r"^asset_[0-9a-f]{24}$")
_SOURCE_ID = re.compile(r"^source_[0-9a-f]{24}$")
_FRAGMENT_ID = re.compile(r"^fragment_[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_SOURCE_BYTES = 512 * 1024 * 1024
_MAX_FRAGMENT_CHARS = 20_000
_MAX_FRAGMENTS_PER_SOURCE = 100_000
_MAX_SEARCH_LIMIT = 20
_MAX_SEARCH_CHARS = 20_000
_MAX_EVENT_PAYLOAD_BYTES = 1024 * 1024
_MAX_COMPILER_BYTES = 64 * 1024
_MAX_INTEGRITY_CACHE_ENTRIES = 32
_INTEGRITY_CACHE: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
_INTEGRITY_CACHE_LOCK = RLock()
_MAX_SOURCE_HASH_CACHE_ENTRIES = 256
_SOURCE_HASH_CACHE: OrderedDict[tuple[Any, ...], str] = OrderedDict()
_SOURCE_HASH_CACHE_LOCK = RLock()
_KNOWN_EVENT_TYPES = frozenset(
    {
        "vault_initialized",
        "source_compiled",
        "asset_proposed",
        "asset_approved",
        "asset_revoked",
        "relation_added",
    }
)


def default_knowledge_vault() -> Path:
    configured = os.environ.get("DEEPLAW_KNOWLEDGE_VAULT")
    if configured:
        return Path(configured).expanduser().absolute()
    return default_home() / "vaults" / "default"


def _validate_vault_path(path: Path, *, must_exist: bool) -> Path:
    root = path.expanduser().absolute()
    if root.is_symlink():
        raise RuntimeError(f"knowledge vault must not be a symbolic link: {root}")
    if must_exist:
        if not root.is_dir():
            raise FileNotFoundError(f"DeepLaw knowledge vault is missing: {root}")
        if os.name != "nt" and stat.S_IMODE(root.stat().st_mode) & 0o077:
            raise RuntimeError("knowledge vault directory must be accessible only by its owner")
    elif root.exists() and not root.is_dir():
        raise RuntimeError(f"knowledge vault path is not a directory: {root}")
    return root


def _owner_directory(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"knowledge vault directory must not be a symbolic link: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise RuntimeError(f"knowledge vault path is not a directory: {path}")
    os.chmod(path, 0o700)
    return path


def _write_owner_file(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise RuntimeError(f"knowledge vault file must not be a symbolic link: {path}")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _manifest_path(root: Path) -> Path:
    return root / "vault.json"


def _database_path(root: Path) -> Path:
    return root / "vault.sqlite3"


def _validate_manifest(value: Any) -> dict[str, Any]:
    expected = {
        "schema_version",
        "storage_schema",
        "vault_id",
        "name",
        "scope",
        "created_at",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise RuntimeError("knowledge vault manifest does not match its closed contract")
    if value.get("schema_version") != KNOWLEDGE_VAULT_SCHEMA:
        raise RuntimeError("unsupported knowledge vault manifest schema")
    if value.get("storage_schema") != KNOWLEDGE_STORAGE_SCHEMA:
        raise RuntimeError("unsupported knowledge vault storage schema")
    vault_id = value.get("vault_id")
    if not isinstance(vault_id, str) or not _VAULT_ID.fullmatch(vault_id):
        raise RuntimeError("knowledge vault ID is invalid")
    name = value.get("name")
    if not isinstance(name, str) or not name or name != name.strip() or len(name) > 200:
        raise RuntimeError("knowledge vault name is invalid")
    if value.get("scope") not in VAULT_SCOPES:
        raise RuntimeError("knowledge vault scope is invalid")
    try:
        canonical_timestamp(value.get("created_at"), field="vault created_at")
    except (TypeError, ValueError) as error:
        raise RuntimeError("knowledge vault created_at is invalid") from error
    return value


def _load_manifest(root: Path) -> dict[str, Any]:
    path = _manifest_path(root)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise RuntimeError("knowledge vault manifest is missing, unsafe, or too large")
    try:
        value = strict_json_loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("knowledge vault manifest is invalid") from error
    return _validate_manifest(value)


def _token_string(text: str) -> str:
    return " ".join(search_terms(text))


def initialize_knowledge_vault(
    path: str | Path,
    *,
    name: str,
    scope: VaultScope,
) -> dict[str, Any]:
    root = _validate_vault_path(Path(path), must_exist=False)
    if scope not in VAULT_SCOPES:
        raise ValueError(f"unsupported knowledge vault scope: {scope}")
    name = name.strip()
    if not name or len(name) > 200:
        raise ValueError("knowledge vault name must be between 1 and 200 characters")
    if root.exists() and any(root.iterdir()):
        manifest = _load_manifest(root)
        if manifest["name"] != name or manifest["scope"] != scope:
            raise RuntimeError("existing knowledge vault identity does not match the request")
        with KnowledgeVault(root, read_only=True) as vault:
            return vault.inspect()

    _owner_directory(root)
    _owner_directory(root / "sources")
    created_at = utc_now()
    vault_id = stable_id("vault", secrets.token_hex(32))
    manifest = {
        "schema_version": KNOWLEDGE_VAULT_SCHEMA,
        "storage_schema": KNOWLEDGE_STORAGE_SCHEMA,
        "vault_id": vault_id,
        "name": name,
        "scope": scope,
        "created_at": created_at,
    }
    _write_owner_file(
        _manifest_path(root),
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    database = _database_path(root)
    if database.exists() or database.is_symlink():
        raise RuntimeError("knowledge vault database already exists")
    connection = sqlite3.connect(database)
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

            CREATE TABLE sources (
                source_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                origin_uri TEXT,
                stored_name TEXT,
                media_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                trust TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                instruction_risk INTEGER NOT NULL,
                warnings_json TEXT NOT NULL,
                compiler_json TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE TABLE source_fragments (
                fragment_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES sources(source_id),
                ordinal INTEGER NOT NULL,
                locator TEXT NOT NULL,
                text TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                instruction_risk INTEGER NOT NULL,
                UNIQUE(source_id, ordinal)
            ) WITHOUT ROWID;

            CREATE TABLE assets (
                asset_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                memory_tier TEXT NOT NULL,
                title TEXT NOT NULL,
                statement TEXT NOT NULL,
                semantic_key TEXT,
                status TEXT NOT NULL,
                verification TEXT NOT NULL,
                trust TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                activated_at TEXT,
                expires_at TEXT,
                supersedes_asset_id TEXT REFERENCES assets(asset_id),
                origin_uri TEXT,
                content_sha256 TEXT NOT NULL
            ) WITHOUT ROWID;

            CREATE UNIQUE INDEX active_asset_semantic_key
                ON assets(semantic_key)
                WHERE status = 'active' AND semantic_key IS NOT NULL;
            CREATE INDEX assets_status_tier_kind
                ON assets(status, memory_tier, kind);
            CREATE INDEX assets_expiry
                ON assets(status, expires_at);

            CREATE TABLE relations (
                relation_id TEXT PRIMARY KEY,
                subject_asset_id TEXT NOT NULL REFERENCES assets(asset_id),
                predicate TEXT NOT NULL,
                object_asset_id TEXT NOT NULL REFERENCES assets(asset_id),
                evidence_fragment_id TEXT REFERENCES source_fragments(fragment_id),
                verification TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CHECK(subject_asset_id <> object_asset_id),
                UNIQUE(subject_asset_id, predicate, object_asset_id)
            ) WITHOUT ROWID;

            CREATE TABLE events (
                sequence INTEGER PRIMARY KEY,
                schema_version TEXT NOT NULL,
                event_type TEXT NOT NULL,
                object_id TEXT,
                payload_json TEXT NOT NULL,
                previous_hash TEXT,
                event_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE asset_search USING fts5(
                asset_id UNINDEXED,
                title_tokens,
                statement_tokens,
                semantic_tokens,
                tag_tokens,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )
        metadata = {
            "schema_version": KNOWLEDGE_STORAGE_SCHEMA,
            "vault_id": vault_id,
            "name": name,
            "scope": scope,
            "created_at": created_at,
            "revision": "0",
            "audit_head": "",
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            metadata.items(),
        )
        event_payload = {
            "schema_version": KNOWLEDGE_EVENT_SCHEMA,
            "sequence": 0,
            "event_type": "vault_initialized",
            "object_id": vault_id,
            "payload": {"name": name, "scope": scope},
            "previous_hash": None,
            "created_at": created_at,
        }
        event_hash = sha256_bytes(canonical_json(event_payload).encode("utf-8"))
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                0,
                KNOWLEDGE_EVENT_SCHEMA,
                "vault_initialized",
                vault_id,
                canonical_json({"name": name, "scope": scope}),
                None,
                event_hash,
                created_at,
            ),
        )
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'audit_head'",
            (event_hash,),
        )
        connection.commit()
    finally:
        connection.close()
    os.chmod(database, 0o600)
    return {
        **manifest,
        "revision": 0,
        "audit_head": event_hash,
        "path": str(root),
    }


class KnowledgeVault(AbstractContextManager["KnowledgeVault"]):
    def __init__(self, path: str | Path | None = None, *, read_only: bool = True) -> None:
        self.root = _validate_vault_path(
            Path(path) if path is not None else default_knowledge_vault(),
            must_exist=True,
        )
        self.manifest = _load_manifest(self.root)
        database = _database_path(self.root)
        if database.is_symlink() or not database.is_file():
            raise RuntimeError("knowledge vault database is missing or unsafe")
        sources_directory = self.root / "sources"
        if sources_directory.is_symlink() or not sources_directory.is_dir():
            raise RuntimeError("knowledge vault sources directory is missing or unsafe")
        if os.name != "nt":
            for protected_path in (
                _manifest_path(self.root),
                database,
                sources_directory,
            ):
                if stat.S_IMODE(protected_path.stat().st_mode) & 0o077:
                    raise RuntimeError(
                        "knowledge vault files must be accessible only by their owner"
                    )
        self.database = database
        self.read_only = read_only
        self._opened_database_fingerprint = self._database_file_fingerprint()
        self._integrity_cache_key: tuple[Any, ...] | None = None
        self._integrity_cache_value: dict[str, Any] | None = None
        if read_only:
            uri = f"{database.as_uri()}?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
            self.connection.execute("PRAGMA query_only = ON")
        else:
            self.connection = sqlite3.connect(database)
            self.connection.execute("PRAGMA journal_mode = DELETE")
            self.connection.execute("PRAGMA synchronous = FULL")
        try:
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA busy_timeout = 5000")
            if self.read_only:
                self.connection.execute("BEGIN")
            self._validate_identity()
            if (
                self.read_only
                and self._database_file_fingerprint()
                != self._opened_database_fingerprint
            ):
                raise RuntimeError(
                    "knowledge vault database changed while its read snapshot was opening"
                )
        except BaseException:
            self.connection.close()
            raise

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @property
    def vault_id(self) -> str:
        return cast(str, self.manifest["vault_id"])

    @property
    def revision(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'revision'"
        ).fetchone()
        if row is None:
            raise RuntimeError("knowledge vault revision is missing")
        try:
            revision = int(row["value"])
        except (TypeError, ValueError) as error:
            raise RuntimeError("knowledge vault revision is invalid") from error
        if revision < 0:
            raise RuntimeError("knowledge vault revision is invalid")
        return revision

    @property
    def audit_head(self) -> str:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'audit_head'"
        ).fetchone()
        if row is None or not _SHA256.fullmatch(row["value"]):
            raise RuntimeError("knowledge vault audit head is invalid")
        return cast(str, row["value"])

    def _validate_identity(self) -> None:
        rows = self.connection.execute("SELECT key, value FROM metadata").fetchall()
        metadata = {row["key"]: row["value"] for row in rows}
        expected = {
            "schema_version",
            "vault_id",
            "name",
            "scope",
            "created_at",
            "revision",
            "audit_head",
        }
        if set(metadata) != expected:
            raise RuntimeError("knowledge vault metadata does not match its closed contract")
        if metadata["schema_version"] != KNOWLEDGE_STORAGE_SCHEMA:
            raise RuntimeError("unsupported knowledge vault database schema")
        for field in ("vault_id", "name", "scope", "created_at"):
            if metadata[field] != str(self.manifest[field]):
                raise RuntimeError(f"knowledge vault manifest/database {field} mismatch")
        _ = self.revision
        _ = self.audit_head

    def _require_write(self) -> None:
        if self.read_only:
            raise RuntimeError("knowledge vault is open read-only")

    def _require_healthy_integrity(self) -> None:
        integrity = self.verify_integrity()
        if not integrity["valid"]:
            raise RuntimeError(
                "knowledge vault integrity is invalid; persistent operation stopped"
            )

    def _database_file_fingerprint(self) -> tuple[int, int, int, int, int]:
        stat_result = self.database.stat()
        return (
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_size,
            stat_result.st_mtime_ns,
            stat_result.st_ctime_ns,
        )

    def _append_event(
        self,
        *,
        event_type: str,
        object_id: str | None,
        payload: dict[str, Any],
    ) -> tuple[int, str]:
        self._require_write()
        payload_json = canonical_json(payload)
        if len(payload_json.encode("utf-8")) > _MAX_EVENT_PAYLOAD_BYTES:
            raise ValueError("knowledge event payload exceeds the bound")
        current_revision = self.revision
        sequence = current_revision + 1
        previous_hash = self.audit_head
        created_at = utc_now()
        event = {
            "schema_version": KNOWLEDGE_EVENT_SCHEMA,
            "sequence": sequence,
            "event_type": event_type,
            "object_id": object_id,
            "payload": payload,
            "previous_hash": previous_hash,
            "created_at": created_at,
        }
        event_hash = sha256_bytes(canonical_json(event).encode("utf-8"))
        self.connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                KNOWLEDGE_EVENT_SCHEMA,
                event_type,
                object_id,
                payload_json,
                previous_hash,
                event_hash,
                created_at,
            ),
        )
        self.connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'revision'",
            (str(sequence),),
        )
        self.connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'audit_head'",
            (event_hash,),
        )
        return sequence, event_hash

    def source_by_hash(self, content_sha256: str) -> dict[str, Any] | None:
        if not _SHA256.fullmatch(content_sha256):
            raise ValueError("source hash must be lowercase SHA-256")
        row = self.connection.execute(
            "SELECT * FROM sources WHERE content_sha256 = ? ORDER BY source_id LIMIT 1",
            (content_sha256,),
        ).fetchone()
        return self._source_row(row) if row is not None else None

    def _source_by_identity(
        self,
        *,
        content_sha256: str,
        source_kind: SourceKind,
        title: str,
        origin_uri: str | None,
        trust: TrustLevel,
        sensitivity: Sensitivity,
        compiler: dict[str, Any],
    ) -> dict[str, Any] | None:
        source_id = stable_id(
            "source",
            self.vault_id,
            source_kind,
            content_sha256,
            title,
            origin_uri or "",
            trust,
            sensitivity,
            canonical_json(compiler),
        )
        row = self.connection.execute(
            "SELECT * FROM sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        return self._source_row(row) if row is not None else None

    @staticmethod
    def _source_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "source_id": row["source_id"],
            "kind": row["kind"],
            "title": row["title"],
            "origin_uri": row["origin_uri"],
            "stored_name": row["stored_name"],
            "media_type": row["media_type"],
            "byte_size": row["byte_size"],
            "content_sha256": row["content_sha256"],
            "trust": row["trust"],
            "sensitivity": row["sensitivity"],
            "imported_at": row["imported_at"],
            "instruction_risk": bool(row["instruction_risk"]),
            "warnings": strict_json_loads(row["warnings_json"]),
            "compiler": strict_json_loads(row["compiler_json"]),
        }

    def add_compiled_source(
        self,
        *,
        source_path: Path,
        expected_byte_size: int,
        expected_content_sha256: str,
        source_kind: SourceKind,
        title: str,
        origin_uri: str | None,
        media_type: str,
        trust: TrustLevel,
        sensitivity: Sensitivity,
        instruction_risk: bool,
        warnings: tuple[str, ...],
        compiler: dict[str, Any],
        fragments: tuple[dict[str, Any], ...],
        asset_specs: tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        self._require_write()
        self._require_healthy_integrity()
        if source_kind not in SOURCE_KINDS:
            raise ValueError("unsupported knowledge source kind")
        if trust == "verified_source":
            raise ValueError(
                "verified_source is reserved for a future publisher-verification "
                "workflow; use user_provided or untrusted"
            )
        if trust not in TRUST_LEVELS or sensitivity not in SENSITIVITY_LEVELS:
            raise ValueError("unsupported knowledge source trust or sensitivity")
        if (
            not isinstance(title, str)
            or not title.strip()
            or title != title.strip()
            or len(title) > 500
        ):
            raise ValueError("knowledge source title is invalid")
        if origin_uri is not None and (
            not isinstance(origin_uri, str)
            or not origin_uri.strip()
            or origin_uri != origin_uri.strip()
            or len(origin_uri) > 2_000
        ):
            raise ValueError("knowledge source origin URI is invalid")
        if (
            not isinstance(media_type, str)
            or not media_type.strip()
            or media_type != media_type.strip()
            or len(media_type) > 200
        ):
            raise ValueError("knowledge source media type is invalid")
        if (
            not isinstance(compiler, dict)
            or compiler.get("schema_version") != "deeplaw.knowledge-compiler/v1"
        ):
            raise ValueError("knowledge source compiler identity is invalid")
        try:
            compiler_json = canonical_json(compiler)
        except (TypeError, ValueError) as error:
            raise ValueError("knowledge source compiler identity is not serializable") from error
        if len(compiler_json.encode("utf-8")) > _MAX_COMPILER_BYTES:
            raise ValueError("knowledge source compiler identity exceeds the bound")
        if len(warnings) > 64 or any(
            not isinstance(warning, str)
            or not warning.strip()
            or warning != warning.strip()
            or len(warning) > 500
            for warning in warnings
        ):
            raise ValueError("knowledge source warnings are invalid")
        source_input = source_path.expanduser().absolute()
        if source_input.is_symlink():
            raise ValueError("knowledge source must be a regular non-symlink file")
        source = source_input.resolve(strict=True)
        if not source.is_file():
            raise ValueError("knowledge source must be a regular file")
        byte_size = source.stat().st_size
        if not 1 <= byte_size <= _MAX_SOURCE_BYTES:
            raise ValueError("knowledge source is empty or exceeds 512 MiB")
        content_sha256 = sha256_file(source)
        if (
            isinstance(expected_byte_size, bool)
            or expected_byte_size != byte_size
            or not isinstance(expected_content_sha256, str)
            or not _SHA256.fullmatch(expected_content_sha256)
            or expected_content_sha256 != content_sha256
        ):
            raise RuntimeError(
                "knowledge source changed while it was being compiled"
            )
        existing = self._source_by_identity(
            content_sha256=content_sha256,
            source_kind=source_kind,
            title=title,
            origin_uri=origin_uri,
            trust=trust,
            sensitivity=sensitivity,
            compiler=compiler,
        )
        if existing is not None:
            existing_path = self.source_file_path(existing["source_id"])
            if (
                existing_path.is_symlink()
                or not existing_path.is_file()
                or existing_path.stat().st_size != existing["byte_size"]
                or sha256_file(existing_path) != existing["content_sha256"]
            ):
                raise RuntimeError(
                    "existing knowledge source failed its content-integrity check"
                )
            asset_rows = self.connection.execute(
                """
                SELECT DISTINCT assets.asset_id
                FROM assets, json_each(assets.source_refs_json) AS reference
                WHERE json_extract(reference.value, '$.source_id') = ?
                ORDER BY assets.asset_id
                """,
                (existing["source_id"],),
            ).fetchall()
            return {
                "schema_version": "deeplaw.knowledge-ingest/v1",
                "vault_id": self.vault_id,
                "revision": self.revision,
                "source": existing,
                "asset_ids": [row["asset_id"] for row in asset_rows],
                "idempotent": True,
            }
        if not fragments or len(fragments) > _MAX_FRAGMENTS_PER_SOURCE:
            raise ValueError("knowledge source must produce a bounded non-empty fragment set")
        source_id = stable_id(
            "source",
            self.vault_id,
            source_kind,
            content_sha256,
            title,
            origin_uri or "",
            trust,
            sensitivity,
            canonical_json(compiler),
        )
        suffix = source.suffix.lower()[:16]
        stored_name = f"{content_sha256}{suffix}"
        destination = self.root / "sources" / stored_name
        if destination.is_symlink():
            raise RuntimeError("knowledge source destination must not be a symbolic link")
        if not destination.exists():
            temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
            try:
                shutil.copyfile(source, temporary)
                if (
                    temporary.stat().st_size != byte_size
                    or sha256_file(temporary) != content_sha256
                ):
                    raise RuntimeError(
                        "copied knowledge source failed its integrity check"
                    )
                os.chmod(temporary, 0o600)
                os.replace(temporary, destination)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        elif (
            not destination.is_file()
            or destination.stat().st_size != byte_size
            or sha256_file(destination) != content_sha256
        ):
            raise RuntimeError(
                "existing knowledge source file does not match its content identity"
            )
        else:
            os.chmod(destination, 0o600)
        imported_at = utc_now()
        asset_ids: list[str] = []
        fragment_ids: list[str] = []
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            self.connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_id,
                    source_kind,
                    title,
                    origin_uri,
                    stored_name,
                    media_type,
                    byte_size,
                    content_sha256,
                    trust,
                    sensitivity,
                    imported_at,
                    int(instruction_risk),
                    canonical_json(list(warnings)),
                    compiler_json,
                ),
            )
            for ordinal, fragment in enumerate(fragments, start=1):
                text = fragment["text"]
                locator = fragment["locator"]
                if (
                    not isinstance(text, str)
                    or not text.strip()
                    or text != text.strip()
                    or len(text) > _MAX_FRAGMENT_CHARS
                ):
                    raise ValueError("compiled source fragment text is invalid")
                if (
                    not isinstance(locator, str)
                    or not locator.strip()
                    or locator != locator.strip()
                    or len(locator) > 2_000
                ):
                    raise ValueError("compiled source fragment locator is invalid")
                text_sha256 = sha256_bytes(text.encode("utf-8"))
                fragment_id = stable_id(
                    "fragment",
                    source_id,
                    str(ordinal),
                    locator,
                    text_sha256,
                )
                fragment_ids.append(fragment_id)
                self.connection.execute(
                    "INSERT INTO source_fragments VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        fragment_id,
                        source_id,
                        ordinal,
                        locator,
                        text,
                        text_sha256,
                        int(bool(fragment.get("instruction_risk", instruction_risk))),
                    ),
                )
            if len(asset_specs) != len(fragments):
                raise ValueError("compiled asset/fragment cardinality mismatch")
            for fragment_id, fragment, specification in zip(
                fragment_ids,
                fragments,
                asset_specs,
                strict=True,
            ):
                reference = SourceReference(
                    source_id=source_id,
                    fragment_id=fragment_id,
                    locator=fragment["locator"],
                    quote_sha256=sha256_bytes(fragment["text"].encode("utf-8")),
                )
                asset, _ = self._insert_asset(
                    kind=cast(AssetKind, specification.get("kind", "reference")),
                    memory_tier=cast(
                        MemoryTier,
                        specification.get("memory_tier", "domain"),
                    ),
                    title=specification["title"],
                    statement=specification["statement"],
                    semantic_key=specification.get("semantic_key"),
                    status="quarantined" if instruction_risk else "proposed",
                    verification="source_bound",
                    trust=trust,
                    sensitivity=sensitivity,
                    source_refs=(reference,),
                    tags=tuple(specification.get("tags", ())),
                    warnings=tuple(
                        dict.fromkeys(
                            (
                                *specification.get("warnings", ()),
                                *(
                                    ("source contains instruction-like content",)
                                    if instruction_risk
                                    else ()
                                ),
                            )
                        )
                    ),
                    expires_at=specification.get("expires_at"),
                    supersedes_asset_id=specification.get("supersedes_asset_id"),
                    origin_uri=specification.get("origin_uri"),
                    created_at=imported_at,
                )
                asset_ids.append(asset.asset_id)
            revision, audit_head = self._append_event(
                event_type="source_compiled",
                object_id=source_id,
                payload={
                    "source_sha256": content_sha256,
                    "fragment_ids": fragment_ids,
                    "asset_ids": asset_ids,
                    "instruction_risk": instruction_risk,
                    "compiler": compiler,
                },
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return {
            "schema_version": "deeplaw.knowledge-ingest/v1",
            "vault_id": self.vault_id,
            "revision": revision,
            "audit_head": audit_head,
            "source": {
                "source_id": source_id,
                "kind": source_kind,
                "title": title,
                "origin_uri": origin_uri,
                "stored_name": stored_name,
                "media_type": media_type,
                "byte_size": byte_size,
                "content_sha256": content_sha256,
                "trust": trust,
                "sensitivity": sensitivity,
                "imported_at": imported_at,
                "instruction_risk": instruction_risk,
                "warnings": list(warnings),
                "compiler": compiler,
            },
            "asset_ids": asset_ids,
            "idempotent": False,
        }

    def _insert_asset(
        self,
        *,
        kind: AssetKind,
        memory_tier: MemoryTier,
        title: str,
        statement: str,
        semantic_key: str | None,
        status: AssetStatus,
        verification: Literal["unverified", "source_bound"],
        trust: TrustLevel,
        sensitivity: Sensitivity,
        source_refs: tuple[SourceReference, ...],
        tags: tuple[str, ...],
        warnings: tuple[str, ...],
        expires_at: str | None,
        supersedes_asset_id: str | None,
        origin_uri: str | None,
        created_at: str,
    ) -> tuple[KnowledgeAsset, bool]:
        if kind not in ASSET_KINDS or memory_tier not in MEMORY_TIERS:
            raise ValueError("unsupported asset kind or memory tier")
        if status not in {"proposed", "quarantined"}:
            raise ValueError("new knowledge assets must start proposed or quarantined")
        title = title.strip()
        statement = statement.strip()
        semantic_key = semantic_key.strip() if semantic_key else None
        tags = tuple(sorted(dict.fromkeys(tag.strip() for tag in tags if tag.strip())))
        warnings = tuple(dict.fromkeys(warning.strip() for warning in warnings if warning.strip()))
        if expires_at is not None:
            expires_at = canonical_timestamp(expires_at, field="asset expires_at")
        content_sha256 = asset_content_sha256(
            kind=kind,
            memory_tier=memory_tier,
            title=title,
            statement=statement,
            semantic_key=semantic_key,
            trust=trust,
            sensitivity=sensitivity,
            source_refs=source_refs,
            tags=tags,
            warnings=warnings,
            expires_at=expires_at,
            supersedes_asset_id=supersedes_asset_id,
            origin_uri=origin_uri,
        )
        asset_id = stable_id("asset", self.vault_id, content_sha256)
        asset = KnowledgeAsset(
            asset_id=asset_id,
            vault_id=self.vault_id,
            kind=kind,
            memory_tier=memory_tier,
            title=title,
            statement=statement,
            semantic_key=semantic_key,
            status=status,
            verification=verification,
            trust=trust,
            sensitivity=sensitivity,
            source_refs=source_refs,
            tags=tags,
            warnings=warnings,
            created_at=created_at,
            activated_at=None,
            expires_at=expires_at,
            supersedes_asset_id=supersedes_asset_id,
            origin_uri=origin_uri,
            content_sha256=content_sha256,
        )
        existing = self.connection.execute(
            "SELECT asset_id FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if existing is not None:
            return self.get_asset(asset_id, include_inactive=True), False
        if supersedes_asset_id is not None:
            superseded = self.connection.execute(
                "SELECT semantic_key FROM assets WHERE asset_id = ?",
                (supersedes_asset_id,),
            ).fetchone()
            if superseded is None:
                raise ValueError("supersedes_asset_id does not exist in this vault")
            if semantic_key is None or superseded["semantic_key"] != semantic_key:
                raise ValueError("superseding assets must retain the same semantic_key")
        self.connection.execute(
            "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                asset.asset_id,
                asset.kind,
                asset.memory_tier,
                asset.title,
                asset.statement,
                asset.semantic_key,
                asset.status,
                asset.verification,
                asset.trust,
                asset.sensitivity,
                canonical_json([reference.to_dict() for reference in asset.source_refs]),
                canonical_json(list(asset.tags)),
                canonical_json(list(asset.warnings)),
                asset.created_at,
                asset.activated_at,
                asset.expires_at,
                asset.supersedes_asset_id,
                asset.origin_uri,
                asset.content_sha256,
            ),
        )
        self.connection.execute(
            "INSERT INTO asset_search VALUES (?, ?, ?, ?, ?)",
            (
                asset.asset_id,
                _token_string(asset.title),
                _token_string(asset.statement),
                _token_string(asset.semantic_key or ""),
                _token_string(" ".join(asset.tags)),
            ),
        )
        return asset, True

    def propose_asset(
        self,
        *,
        kind: AssetKind,
        memory_tier: MemoryTier,
        title: str,
        statement: str,
        semantic_key: str | None = None,
        trust: TrustLevel = "user_provided",
        sensitivity: Sensitivity = "private",
        tags: Iterable[str] = (),
        expires_at: str | None = None,
        supersedes_asset_id: str | None = None,
        origin_uri: str | None = None,
        quarantined: bool = False,
    ) -> KnowledgeAsset:
        self._require_write()
        if trust == "verified_source":
            raise ValueError(
                "verified_source is reserved for a future publisher-verification "
                "workflow; use user_provided or untrusted"
            )
        instruction_risk = has_instruction_risk(f"{title}\n{statement}")
        quarantined = quarantined or instruction_risk
        warnings = (
            (
                "instruction-like or invisible control content detected; "
                "proposal requires explicit review"
            )
            if instruction_risk
            else "proposal was quarantined before review"
        )
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            asset, inserted = self._insert_asset(
                kind=kind,
                memory_tier=memory_tier,
                title=title,
                statement=statement,
                semantic_key=semantic_key,
                status="quarantined" if quarantined else "proposed",
                verification="unverified",
                trust=trust,
                sensitivity=sensitivity,
                source_refs=(),
                tags=tuple(tags),
                warnings=(warnings,) if quarantined else (),
                expires_at=expires_at,
                supersedes_asset_id=supersedes_asset_id,
                origin_uri=origin_uri,
                created_at=utc_now(),
            )
            if not inserted:
                self.connection.rollback()
                return asset
            self._append_event(
                event_type="asset_proposed",
                object_id=asset.asset_id,
                payload={
                    "content_sha256": asset.content_sha256,
                    "status": asset.status,
                },
            )
            self.connection.commit()
            return asset
        except BaseException:
            self.connection.rollback()
            raise

    def approve_asset(
        self,
        asset_id: str,
        *,
        confirm_reviewed: bool,
        confirm_quarantined: bool = False,
    ) -> KnowledgeAsset:
        self._require_write()
        if not confirm_reviewed:
            raise ValueError("asset approval requires explicit reviewed confirmation")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            asset = self.get_asset(asset_id, include_inactive=True)
            if asset.status not in {"proposed", "quarantined"}:
                raise ValueError("only proposed or quarantined assets can be approved")
            if asset.status == "quarantined" and not confirm_quarantined:
                raise ValueError(
                    "quarantined asset approval requires a separate explicit "
                    "quarantine-risk confirmation"
                )
            if asset.expires_at is not None and asset.expires_at <= utc_now():
                raise ValueError("expired working memory cannot be approved")
            if asset.semantic_key is not None:
                current = self.connection.execute(
                    """
                    SELECT asset_id FROM assets
                    WHERE semantic_key = ? AND status = 'active'
                    """,
                    (asset.semantic_key,),
                ).fetchone()
                if current is not None:
                    if asset.supersedes_asset_id != current["asset_id"]:
                        raise ValueError(
                            "semantic_key already has an active asset; propose an explicit "
                            "superseding asset instead"
                        )
                    self.connection.execute(
                        "UPDATE assets SET status = 'superseded' WHERE asset_id = ?",
                        (current["asset_id"],),
                    )
            if asset.supersedes_asset_id is not None:
                superseded = self.connection.execute(
                    "SELECT status, semantic_key FROM assets WHERE asset_id = ?",
                    (asset.supersedes_asset_id,),
                ).fetchone()
                if superseded is None or superseded["status"] not in {
                    "active",
                    "superseded",
                }:
                    raise ValueError("superseded asset is missing or not reviewable")
                if superseded["semantic_key"] != asset.semantic_key:
                    raise ValueError("superseded asset semantic_key mismatch")
                self.connection.execute(
                    "UPDATE assets SET status = 'superseded' WHERE asset_id = ?",
                    (asset.supersedes_asset_id,),
                )
            activated_at = utc_now()
            self.connection.execute(
                """
                UPDATE assets
                SET status = 'active', verification = 'human_verified', activated_at = ?
                WHERE asset_id = ?
                """,
                (activated_at, asset_id),
            )
            self._append_event(
                event_type="asset_approved",
                object_id=asset_id,
                payload={
                    "content_sha256": asset.content_sha256,
                    "supersedes_asset_id": asset.supersedes_asset_id,
                },
            )
            self.connection.commit()
            return self.get_asset(asset_id, include_inactive=True)
        except BaseException:
            self.connection.rollback()
            raise

    def revoke_asset(
        self,
        asset_id: str,
        *,
        reason: str,
        confirm: bool,
    ) -> KnowledgeAsset:
        self._require_write()
        if not confirm:
            raise ValueError("asset revocation requires explicit confirmation")
        reason = reason.strip()
        if not reason or len(reason) > 2_000:
            raise ValueError("revocation reason must be between 1 and 2000 characters")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            asset = self.get_asset(asset_id, include_inactive=True)
            if asset.status == "revoked":
                self.connection.rollback()
                return asset
            self.connection.execute(
                "UPDATE assets SET status = 'revoked' WHERE asset_id = ?",
                (asset_id,),
            )
            self._append_event(
                event_type="asset_revoked",
                object_id=asset_id,
                payload={"reason": reason, "content_sha256": asset.content_sha256},
            )
            self.connection.commit()
            return self.get_asset(asset_id, include_inactive=True)
        except BaseException:
            self.connection.rollback()
            raise

    def add_relation(
        self,
        *,
        subject_asset_id: str,
        predicate: str,
        object_asset_id: str,
        evidence_fragment_id: str | None = None,
        confirm_reviewed: bool,
    ) -> dict[str, Any]:
        self._require_write()
        if not confirm_reviewed:
            raise ValueError("knowledge relation requires explicit reviewed confirmation")
        if predicate not in RELATION_PREDICATES:
            raise ValueError(f"unsupported knowledge relation predicate: {predicate}")
        if subject_asset_id == object_asset_id:
            raise ValueError("knowledge relation cannot be a self-loop")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._require_healthy_integrity()
            subject = self.get_asset(subject_asset_id, include_inactive=False)
            target = self.get_asset(object_asset_id, include_inactive=False)
            if evidence_fragment_id is not None:
                fragment = self.connection.execute(
                    "SELECT fragment_id FROM source_fragments WHERE fragment_id = ?",
                    (evidence_fragment_id,),
                ).fetchone()
                if fragment is None:
                    raise ValueError("relation evidence fragment does not exist")
            relation_id = stable_id(
                "relation",
                self.vault_id,
                subject.asset_id,
                predicate,
                target.asset_id,
                evidence_fragment_id or "",
            )
            existing = self.connection.execute(
                "SELECT * FROM relations WHERE relation_id = ?",
                (relation_id,),
            ).fetchone()
            if existing is not None:
                self.connection.rollback()
                return {
                    "relation_id": existing["relation_id"],
                    "subject_uri": subject.uri,
                    "predicate": existing["predicate"],
                    "object_uri": target.uri,
                    "evidence_fragment_id": existing["evidence_fragment_id"],
                    "verification": existing["verification"],
                    "created_at": existing["created_at"],
                }
            conflicting = self.connection.execute(
                """
                SELECT relation_id FROM relations
                WHERE subject_asset_id = ? AND predicate = ? AND object_asset_id = ?
                """,
                (subject.asset_id, predicate, target.asset_id),
            ).fetchone()
            if conflicting is not None:
                self.connection.rollback()
                raise ValueError(
                    "knowledge relation already exists with different evidence"
                )
            created_at = utc_now()
            self.connection.execute(
                "INSERT INTO relations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    relation_id,
                    subject.asset_id,
                    predicate,
                    target.asset_id,
                    evidence_fragment_id,
                    "human_verified",
                    created_at,
                ),
            )
            self._append_event(
                event_type="relation_added",
                object_id=relation_id,
                payload={
                    "subject_asset_id": subject.asset_id,
                    "predicate": predicate,
                    "object_asset_id": target.asset_id,
                    "evidence_fragment_id": evidence_fragment_id,
                },
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return {
            "relation_id": relation_id,
            "subject_uri": subject.uri,
            "predicate": predicate,
            "object_uri": target.uri,
            "evidence_fragment_id": evidence_fragment_id,
            "verification": "human_verified",
            "created_at": created_at,
        }

    def _row_to_asset(self, row: sqlite3.Row) -> KnowledgeAsset:
        references_payload = strict_json_loads(row["source_refs_json"])
        references = tuple(
            SourceReference(
                source_id=item["source_id"],
                fragment_id=item["fragment_id"],
                locator=item["locator"],
                quote_sha256=item["quote_sha256"],
            )
            for item in references_payload
        )
        return KnowledgeAsset(
            asset_id=row["asset_id"],
            vault_id=self.vault_id,
            kind=cast(AssetKind, row["kind"]),
            memory_tier=cast(MemoryTier, row["memory_tier"]),
            title=row["title"],
            statement=row["statement"],
            semantic_key=row["semantic_key"],
            status=cast(AssetStatus, row["status"]),
            verification=row["verification"],
            trust=row["trust"],
            sensitivity=row["sensitivity"],
            source_refs=references,
            tags=tuple(strict_json_loads(row["tags_json"])),
            warnings=tuple(strict_json_loads(row["warnings_json"])),
            created_at=row["created_at"],
            activated_at=row["activated_at"],
            expires_at=row["expires_at"],
            supersedes_asset_id=row["supersedes_asset_id"],
            origin_uri=row["origin_uri"],
            content_sha256=row["content_sha256"],
        )

    def get_asset(self, asset_id: str, *, include_inactive: bool = False) -> KnowledgeAsset:
        if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
            raise ValueError("knowledge asset ID is invalid")
        query = "SELECT * FROM assets WHERE asset_id = ?"
        parameters: tuple[Any, ...] = (asset_id,)
        if not include_inactive:
            query += " AND status = 'active' AND (expires_at IS NULL OR expires_at > ?)"
            parameters = (asset_id, utc_now())
        row = self.connection.execute(query, parameters).fetchone()
        if row is None:
            raise KeyError(f"knowledge asset is unavailable: {asset_id}")
        return self._row_to_asset(row)

    def get_fragment(self, fragment_id: str) -> dict[str, Any]:
        if not isinstance(fragment_id, str) or not _FRAGMENT_ID.fullmatch(fragment_id):
            raise ValueError("knowledge fragment ID is invalid")
        row = self.connection.execute(
            """
            SELECT source_fragments.*, sources.title AS source_title,
                   sources.content_sha256 AS source_sha256,
                   sources.origin_uri AS source_origin_uri
            FROM source_fragments
            JOIN sources USING(source_id)
            WHERE fragment_id = ?
            """,
            (fragment_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"knowledge source fragment is unavailable: {fragment_id}")
        return {
            "fragment_id": row["fragment_id"],
            "source_id": row["source_id"],
            "source_title": row["source_title"],
            "source_origin_uri": row["source_origin_uri"],
            "source_sha256": row["source_sha256"],
            "ordinal": row["ordinal"],
            "locator": row["locator"],
            "text": row["text"],
            "text_sha256": row["text_sha256"],
            "instruction_risk": bool(row["instruction_risk"]),
        }

    def source_file_path(self, source_id: str) -> Path:
        if not isinstance(source_id, str) or not _SOURCE_ID.fullmatch(source_id):
            raise ValueError("knowledge source ID is invalid")
        row = self.connection.execute(
            "SELECT stored_name FROM sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if row is None or not isinstance(row["stored_name"], str):
            raise KeyError(f"knowledge source file is unavailable: {source_id}")
        stored_name = row["stored_name"]
        if (
            not stored_name
            or Path(stored_name).name != stored_name
            or len(stored_name) > 100
        ):
            raise RuntimeError("knowledge source stored name is unsafe")
        return self.root / "sources" / stored_name

    def _source_file_check(
        self,
        source_id: str,
        *,
        cache: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if cache is not None and source_id in cache:
            return cache[source_id]
        source = self.connection.execute(
            """
            SELECT stored_name, byte_size, content_sha256
            FROM sources WHERE source_id = ?
            """,
            (source_id,),
        ).fetchone()
        source_valid = False
        reason: str | None = "source_record_missing"
        if source is not None and source["stored_name"]:
            try:
                source_path = self.source_file_path(source_id)
                if (
                    not source_path.is_symlink()
                    and source_path.is_file()
                    and source_path.stat().st_size == source["byte_size"]
                    and self._cached_source_sha256(source_path)
                    == source["content_sha256"]
                ):
                    source_valid = True
                    reason = None
                else:
                    reason = "source_file_missing_or_hash_mismatch"
            except (KeyError, OSError, RuntimeError, ValueError):
                reason = "source_file_missing_or_hash_mismatch"
        result = {
            "source_id": source_id,
            "content_sha256": (
                source["content_sha256"] if source is not None else None
            ),
            "valid": source_valid,
            "reason": reason,
        }
        if cache is not None:
            cache[source_id] = result
        return result

    @staticmethod
    def _cached_source_sha256(path: Path) -> str:
        stat_result = path.stat()
        fingerprint = (
            str(path),
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_size,
            stat_result.st_mtime_ns,
            stat_result.st_ctime_ns,
        )
        with _SOURCE_HASH_CACHE_LOCK:
            cached = _SOURCE_HASH_CACHE.get(fingerprint)
            if cached is not None:
                _SOURCE_HASH_CACHE.move_to_end(fingerprint)
                return cached
        digest = sha256_file(path)
        current = path.stat()
        current_fingerprint = (
            str(path),
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
        )
        if current_fingerprint != fingerprint:
            raise RuntimeError("knowledge source file changed during integrity verification")
        with _SOURCE_HASH_CACHE_LOCK:
            for existing_key in tuple(_SOURCE_HASH_CACHE):
                if existing_key[0] == str(path) and existing_key != fingerprint:
                    del _SOURCE_HASH_CACHE[existing_key]
            _SOURCE_HASH_CACHE[fingerprint] = digest
            _SOURCE_HASH_CACHE.move_to_end(fingerprint)
            while len(_SOURCE_HASH_CACHE) > _MAX_SOURCE_HASH_CACHE_ENTRIES:
                _SOURCE_HASH_CACHE.popitem(last=False)
        return digest

    def verify_source_files(
        self,
        source_ids: Iterable[str],
    ) -> dict[str, Any]:
        identifiers = tuple(dict.fromkeys(source_ids))
        if len(identifiers) > _MAX_FRAGMENTS_PER_SOURCE:
            raise ValueError("source-file verification exceeds its record bound")
        cache: dict[str, dict[str, Any]] = {}
        checks = [
            self._source_file_check(source_id, cache=cache)
            for source_id in identifiers
        ]
        return {
            "valid": all(check["valid"] for check in checks),
            "checked_source_files": len(checks),
            "checks": checks,
        }

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        max_chars: int = 5_000,
        kinds: Iterable[str] = (),
        memory_tiers: Iterable[str] = (),
        include_restricted: bool = False,
        include_inactive: bool = False,
    ) -> KnowledgeSearchResponse:
        self._require_healthy_integrity()
        query = query.strip()
        if not query or len(query) > 4_000:
            raise ValueError("knowledge query must be between 1 and 4000 characters")
        if isinstance(limit, bool) or not 1 <= limit <= _MAX_SEARCH_LIMIT:
            raise ValueError(f"knowledge search limit must be between 1 and {_MAX_SEARCH_LIMIT}")
        if isinstance(max_chars, bool) or not 1 <= max_chars <= _MAX_SEARCH_CHARS:
            raise ValueError(
                f"knowledge max_chars must be between 1 and {_MAX_SEARCH_CHARS}"
            )
        selected_kinds = tuple(dict.fromkeys(kinds))
        selected_tiers = tuple(dict.fromkeys(memory_tiers))
        if any(kind not in ASSET_KINDS for kind in selected_kinds):
            raise ValueError("knowledge search contains an unsupported asset kind")
        if any(tier not in MEMORY_TIERS for tier in selected_tiers):
            raise ValueError("knowledge search contains an unsupported memory tier")
        terms = search_terms(query, limit=32)
        if not terms:
            raise ValueError("knowledge query has no searchable terms")
        conditions = ["asset_search MATCH ?"]
        parameters: list[Any] = [fts_query(terms)]
        if not include_inactive:
            conditions.append("assets.status = 'active'")
            conditions.append("(assets.expires_at IS NULL OR assets.expires_at > ?)")
            parameters.append(utc_now())
        if not include_restricted:
            conditions.append("assets.sensitivity <> 'restricted'")
        if selected_kinds:
            conditions.append(
                f"assets.kind IN ({','.join('?' for _ in selected_kinds)})"
            )
            parameters.extend(selected_kinds)
        if selected_tiers:
            conditions.append(
                f"assets.memory_tier IN ({','.join('?' for _ in selected_tiers)})"
            )
            parameters.extend(selected_tiers)
        parameters.append(64)
        # Every SQL fragment above is a closed literal; caller values remain
        # bound parameters and only placeholder counts are interpolated.
        rows = self.connection.execute(
            f"""
            SELECT assets.*, bm25(asset_search, 0.0, 8.0, 3.0, 10.0, 2.0) AS rank
            FROM asset_search
            JOIN assets USING(asset_id)
            WHERE {' AND '.join(conditions)}
            ORDER BY rank, assets.asset_id
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
        compact_query = compact_text(query)
        scored: list[tuple[float, KnowledgeAsset, str]] = []
        for row in rows:
            asset = self._row_to_asset(row)
            score = -float(row["rank"])
            reasons = ["lexical"]
            if asset.semantic_key and compact_text(asset.semantic_key) == compact_query:
                score += 100.0
                reasons.append("semantic_key_exact")
            if compact_query and compact_query in compact_text(asset.title):
                score += 40.0
                reasons.append("title_exact")
            if asset.kind in {"constraint", "decision", "rule", "procedure"}:
                score += 4.0
            if asset.verification == "human_verified":
                score += 3.0
            scored.append((score, asset, "+".join(reasons)))
        scored.sort(key=lambda item: (-item[0], item[1].asset_id))
        cards: list[KnowledgeCard] = []
        total_chars = 0
        source_file_cache: dict[str, dict[str, Any]] = {}
        excluded_by_source_integrity = 0
        for score, asset, hit_reason in scored:
            remaining = max_chars - total_chars
            if remaining <= 0 or len(cards) >= limit:
                break
            if any(
                not self._source_file_check(
                    reference.source_id,
                    cache=source_file_cache,
                )["valid"]
                for reference in asset.source_refs
            ):
                excluded_by_source_integrity += 1
                continue
            card_excerpt = excerpt(asset.statement, query, max_chars=min(700, remaining))
            if not card_excerpt:
                continue
            cards.append(
                KnowledgeCard(
                    asset_id=asset.asset_id,
                    uri=asset.uri,
                    kind=asset.kind,
                    memory_tier=asset.memory_tier,
                    title=asset.title,
                    excerpt=card_excerpt,
                    semantic_key=asset.semantic_key,
                    verification=asset.verification,
                    trust=asset.trust,
                    sensitivity=asset.sensitivity,
                    directive_mode=asset.directive_mode,
                    source_refs=asset.source_refs,
                    tags=asset.tags,
                    content_sha256=asset.content_sha256,
                    score=round(score, 6),
                    hit_reason=hit_reason,
                )
            )
            total_chars += len(card_excerpt)
        gaps: list[str] = []
        if not cards:
            gaps.append("no active reviewed knowledge asset matched the task")
        if excluded_by_source_integrity:
            gaps.append(
                f"{excluded_by_source_integrity} matched asset(s) were excluded because "
                "their stored source file failed integrity verification"
            )
        return KnowledgeSearchResponse(
            vault_id=self.vault_id,
            vault_revision=self.revision,
            query=query,
            results=tuple(cards),
            gaps=tuple(gaps),
            total_excerpt_chars=total_chars,
        )

    def relations_for_assets(
        self,
        asset_ids: Iterable[str],
        *,
        limit: int = 32,
        include_restricted: bool = False,
    ) -> list[dict[str, Any]]:
        identifiers = tuple(dict.fromkeys(asset_ids))
        if not identifiers:
            return []
        if len(identifiers) > 20 or not 1 <= limit <= 64:
            raise ValueError("knowledge relation expansion exceeds its bound")
        placeholders = ",".join("?" for _ in identifiers)
        # identifiers determine only the number of bound placeholders.
        rows = self.connection.execute(
            f"""
            SELECT relations.*
            FROM relations
            JOIN assets AS subject ON subject.asset_id = relations.subject_asset_id
            JOIN assets AS object ON object.asset_id = relations.object_asset_id
            LEFT JOIN source_fragments AS relation_fragment
              ON relation_fragment.fragment_id = relations.evidence_fragment_id
            LEFT JOIN sources AS relation_source
              ON relation_source.source_id = relation_fragment.source_id
            WHERE (
                relations.subject_asset_id IN ({placeholders})
                OR relations.object_asset_id IN ({placeholders})
            )
              AND subject.status = 'active'
              AND object.status = 'active'
              AND (subject.expires_at IS NULL OR subject.expires_at > ?)
              AND (object.expires_at IS NULL OR object.expires_at > ?)
              AND (? OR (
                subject.sensitivity <> 'restricted'
                AND object.sensitivity <> 'restricted'
                AND (
                  relations.evidence_fragment_id IS NULL
                  OR relation_source.sensitivity <> 'restricted'
                )
              ))
            ORDER BY relations.relation_id
            LIMIT ?
            """,
            (
                *identifiers,
                *identifiers,
                utc_now(),
                utc_now(),
                int(include_restricted),
                limit,
            ),
        ).fetchall()
        return [dict(row) for row in rows]

    def verify_asset(self, asset_id: str) -> dict[str, Any]:
        asset = self.get_asset(asset_id, include_inactive=True)
        reference_checks: list[dict[str, Any]] = []
        source_file_checks: list[dict[str, Any]] = []
        source_file_cache: dict[str, dict[str, Any]] = {}
        valid = True
        for reference in asset.source_refs:
            try:
                fragment = self.get_fragment(reference.fragment_id)
            except KeyError:
                valid = False
                reference_checks.append(
                    {
                        "fragment_id": reference.fragment_id,
                        "valid": False,
                        "reason": "fragment_missing",
                    }
                )
                continue
            reference_valid = (
                fragment["source_id"] == reference.source_id
                and fragment["locator"] == reference.locator
                and fragment["text_sha256"] == reference.quote_sha256
                and sha256_bytes(fragment["text"].encode("utf-8"))
                == reference.quote_sha256
            )
            valid = valid and reference_valid
            reference_checks.append(
                {
                    "fragment_id": reference.fragment_id,
                    "valid": reference_valid,
                    "reason": None if reference_valid else "source_binding_mismatch",
                }
            )
            if reference.source_id in source_file_cache:
                continue
            source_check = self._source_file_check(
                reference.source_id,
                cache=source_file_cache,
            )
            valid = valid and bool(source_check["valid"])
            source_file_checks.append(source_check)
        integrity = self.verify_integrity()
        integrity_valid = valid and bool(integrity["valid"])
        agent_usable = (
            asset.status == "active"
            and asset.verification == "human_verified"
            and (asset.expires_at is None or asset.expires_at > utc_now())
            and integrity_valid
        )
        return {
            "schema_version": "deeplaw.knowledge-verification/v1",
            "vault_id": self.vault_id,
            "vault_revision": self.revision,
            "asset_id": asset.asset_id,
            "uri": asset.uri,
            "content_sha256": asset.content_sha256,
            "status": asset.status,
            "verification": asset.verification,
            "source_references": reference_checks,
            "source_files": source_file_checks,
            "audit_head": self.audit_head,
            "audit_chain_valid": integrity["audit"]["valid"],
            "state_integrity_valid": integrity["state"]["valid"],
            "integrity_valid": integrity_valid,
            "agent_usable": agent_usable,
            "valid": integrity_valid and agent_usable,
        }

    def verify_audit_chain(self) -> dict[str, Any]:
        rows = self.connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
        previous_hash: str | None = None
        expected_sequence = 0
        for row in rows:
            if row["sequence"] != expected_sequence or row["previous_hash"] != previous_hash:
                return {
                    "valid": False,
                    "event_count": len(rows),
                    "failed_sequence": expected_sequence,
                    "reason": "sequence_or_previous_hash_mismatch",
                }
            try:
                payload = strict_json_loads(row["payload_json"])
            except (json.JSONDecodeError, ValueError):
                return {
                    "valid": False,
                    "event_count": len(rows),
                    "failed_sequence": expected_sequence,
                    "reason": "event_payload_invalid",
                }
            event = {
                "schema_version": row["schema_version"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "object_id": row["object_id"],
                "payload": payload,
                "previous_hash": row["previous_hash"],
                "created_at": row["created_at"],
            }
            event_hash = sha256_bytes(canonical_json(event).encode("utf-8"))
            if event_hash != row["event_hash"]:
                return {
                    "valid": False,
                    "event_count": len(rows),
                    "failed_sequence": expected_sequence,
                    "reason": "event_hash_mismatch",
                }
            previous_hash = event_hash
            expected_sequence += 1
        valid = bool(rows) and previous_hash == self.audit_head and len(rows) == self.revision + 1
        return {
            "valid": valid,
            "event_count": len(rows),
            "failed_sequence": None if valid else expected_sequence,
            "reason": None if valid else "audit_head_or_revision_mismatch",
        }

    def verify_state_integrity(self) -> dict[str, Any]:
        asset_rows = self.connection.execute(
            "SELECT * FROM assets ORDER BY asset_id"
        ).fetchall()
        source_rows = self.connection.execute(
            "SELECT * FROM sources ORDER BY source_id"
        ).fetchall()
        fragment_rows = self.connection.execute(
            "SELECT * FROM source_fragments ORDER BY fragment_id"
        ).fetchall()
        relation_rows = self.connection.execute(
            "SELECT * FROM relations ORDER BY relation_id"
        ).fetchall()
        search_rows = self.connection.execute(
            """
            SELECT asset_id, title_tokens, statement_tokens, semantic_tokens, tag_tokens
            FROM asset_search
            ORDER BY asset_id
            """
        ).fetchall()
        event_rows = self.connection.execute(
            "SELECT * FROM events ORDER BY sequence"
        ).fetchall()
        counts = {
            "asset_count": len(asset_rows),
            "source_count": len(source_rows),
            "fragment_count": len(fragment_rows),
            "relation_count": len(relation_rows),
            "search_index_count": len(search_rows),
        }

        def failed(reason: str, object_id: str | None = None) -> dict[str, Any]:
            return {
                "valid": False,
                **counts,
                "reason": reason,
                "object_id": object_id,
            }

        try:
            assets = {
                row["asset_id"]: self._row_to_asset(row)
                for row in asset_rows
            }
            sources = {
                row["source_id"]: self._source_row(row)
                for row in source_rows
            }
            source_records = {
                row["source_id"]: row
                for row in source_rows
            }
            fragments = {
                row["fragment_id"]: dict(row)
                for row in fragment_rows
            }
            relations = {
                row["relation_id"]: dict(row)
                for row in relation_rows
            }
        except (KeyError, TypeError, ValueError):
            return failed("stored_record_contract_invalid")
        if len(assets) != len(asset_rows):
            return failed("duplicate_asset_identity")
        if len(sources) != len(source_rows):
            return failed("duplicate_source_identity")
        if len(fragments) != len(fragment_rows):
            return failed("duplicate_fragment_identity")
        if len(relations) != len(relation_rows):
            return failed("duplicate_relation_identity")

        expected_assets: dict[str, dict[str, Any]] = {}
        expected_sources: set[str] = set()
        expected_fragments: set[str] = set()
        expected_relations: dict[str, dict[str, Any]] = {}
        previous_event_at: str | None = None
        for event in event_rows:
            event_type = event["event_type"]
            object_id = event["object_id"]
            if (
                event["schema_version"] != KNOWLEDGE_EVENT_SCHEMA
                or event_type not in _KNOWN_EVENT_TYPES
            ):
                return failed("unknown_or_invalid_event_type", object_id)
            try:
                payload = strict_json_loads(event["payload_json"])
                event_at = canonical_timestamp(
                    event["created_at"],
                    field="knowledge event created_at",
                )
            except (json.JSONDecodeError, ValueError):
                return failed("event_payload_invalid", object_id)
            if previous_event_at is not None and event_at < previous_event_at:
                return failed("event_timestamp_order_invalid", object_id)
            previous_event_at = event_at
            if not isinstance(payload, dict):
                return failed("event_payload_not_object", object_id)

            if event_type == "vault_initialized":
                if (
                    event["sequence"] != 0
                    or object_id != self.vault_id
                    or payload
                    != {
                        "name": self.manifest["name"],
                        "scope": self.manifest["scope"],
                    }
                ):
                    return failed("vault_initialization_event_mismatch", object_id)
                continue

            if event_type == "source_compiled":
                expected_payload = {
                    "source_sha256",
                    "fragment_ids",
                    "asset_ids",
                    "instruction_risk",
                    "compiler",
                }
                if (
                    set(payload) != expected_payload
                    or not isinstance(object_id, str)
                    or object_id not in sources
                    or object_id in expected_sources
                    or not isinstance(payload["fragment_ids"], list)
                    or not isinstance(payload["asset_ids"], list)
                    or not isinstance(payload["instruction_risk"], bool)
                    or not isinstance(payload["compiler"], dict)
                ):
                    return failed("source_compiled_event_invalid", object_id)
                source = sources[object_id]
                source_record = source_records[object_id]
                expected_source_id = stable_id(
                    "source",
                    self.vault_id,
                    source["kind"],
                    source["content_sha256"],
                    source["title"],
                    source["origin_uri"] or "",
                    source["trust"],
                    source["sensitivity"],
                    canonical_json(source["compiler"]),
                )
                if (
                    expected_source_id != object_id
                    or source["content_sha256"] != payload["source_sha256"]
                    or source["compiler"] != payload["compiler"]
                    or source["instruction_risk"] != payload["instruction_risk"]
                    or source["kind"] not in SOURCE_KINDS
                    or source["trust"] not in TRUST_LEVELS
                    or source["sensitivity"] not in SENSITIVITY_LEVELS
                    or not isinstance(source["title"], str)
                    or source["title"] != source["title"].strip()
                    or not 1 <= len(source["title"]) <= 500
                    or (
                        source["origin_uri"] is not None
                        and (
                            not isinstance(source["origin_uri"], str)
                            or source["origin_uri"] != source["origin_uri"].strip()
                            or not 1 <= len(source["origin_uri"]) <= 2_000
                        )
                    )
                    or not isinstance(source["media_type"], str)
                    or source["media_type"] != source["media_type"].strip()
                    or not 1 <= len(source["media_type"]) <= 200
                    or isinstance(source["byte_size"], bool)
                    or not isinstance(source["byte_size"], int)
                    or not 1 <= source["byte_size"] <= _MAX_SOURCE_BYTES
                    or not _SHA256.fullmatch(source["content_sha256"])
                    or not isinstance(source["stored_name"], str)
                    or Path(source["stored_name"]).name != source["stored_name"]
                    or not source["stored_name"].startswith(source["content_sha256"])
                    or not 64 <= len(source["stored_name"]) <= 80
                    or source_record["instruction_risk"] not in {0, 1}
                    or not isinstance(source["warnings"], list)
                    or len(source["warnings"]) > 64
                    or any(
                        not isinstance(warning, str)
                        or warning != warning.strip()
                        or not 1 <= len(warning) <= 500
                        for warning in source["warnings"]
                    )
                    or source["compiler"].get("schema_version")
                    != "deeplaw.knowledge-compiler/v1"
                    or source["compiler"].get("source_sha256")
                    != source["content_sha256"]
                    or len(canonical_json(source["compiler"]).encode("utf-8"))
                    > _MAX_COMPILER_BYTES
                ):
                    return failed("source_state_mismatch", object_id)
                try:
                    canonical_timestamp(
                        source["imported_at"],
                        field="source imported_at",
                    )
                except (TypeError, ValueError):
                    return failed("source_timestamp_invalid", object_id)
                fragment_ids = payload["fragment_ids"]
                asset_ids = payload["asset_ids"]
                if (
                    not fragment_ids
                    or len(fragment_ids) != len(set(fragment_ids))
                    or len(asset_ids) != len(fragment_ids)
                    or len(asset_ids) != len(set(asset_ids))
                    or any(
                        not isinstance(fragment_id, str)
                        or fragment_id not in fragments
                        or fragment_id in expected_fragments
                        for fragment_id in fragment_ids
                    )
                    or any(
                        not isinstance(asset_id, str)
                        or asset_id not in assets
                        or asset_id in expected_assets
                        for asset_id in asset_ids
                    )
                ):
                    return failed("source_compiled_membership_invalid", object_id)
                compiled_sections: list[dict[str, Any]] = []
                for ordinal, (fragment_id, asset_id) in enumerate(
                    zip(fragment_ids, asset_ids, strict=True),
                    start=1,
                ):
                    fragment = fragments[fragment_id]
                    asset = assets[asset_id]
                    text_hash = sha256_bytes(fragment["text"].encode("utf-8"))
                    expected_fragment_id = stable_id(
                        "fragment",
                        object_id,
                        str(ordinal),
                        fragment["locator"],
                        text_hash,
                    )
                    if (
                        fragment["source_id"] != object_id
                        or fragment["ordinal"] != ordinal
                        or not isinstance(fragment["locator"], str)
                        or fragment["locator"] != fragment["locator"].strip()
                        or not 1 <= len(fragment["locator"]) <= 2_000
                        or not isinstance(fragment["text"], str)
                        or fragment["text"] != fragment["text"].strip()
                        or not 1 <= len(fragment["text"]) <= _MAX_FRAGMENT_CHARS
                        or fragment["instruction_risk"] not in {0, 1}
                        or fragment["text_sha256"] != text_hash
                        or expected_fragment_id != fragment_id
                        or len(asset.source_refs) != 1
                        or asset.source_refs[0].source_id != object_id
                        or asset.source_refs[0].fragment_id != fragment_id
                        or asset.source_refs[0].locator != fragment["locator"]
                        or asset.source_refs[0].quote_sha256 != text_hash
                        or asset.statement != fragment["text"]
                    ):
                        return failed("source_fragment_binding_mismatch", fragment_id)
                    compiled_sections.append(
                        {
                            "title": asset.title,
                            "locator": fragment["locator"],
                            "text": fragment["text"],
                            "instruction_risk": bool(fragment["instruction_risk"]),
                        }
                    )
                    expected_assets[asset_id] = {
                        "status": (
                            "quarantined"
                            if payload["instruction_risk"]
                            else "proposed"
                        ),
                        "verification": "source_bound",
                        "content_sha256": asset.content_sha256,
                        "approved": False,
                    }
                    expected_fragments.add(fragment_id)
                if (
                    payload["compiler"].get("compiled_fragment_sha256")
                    != sha256_bytes(
                        canonical_json(compiled_sections).encode("utf-8")
                    )
                ):
                    return failed("compiled_fragment_digest_mismatch", object_id)
                expected_sources.add(object_id)
                continue

            if event_type == "asset_proposed":
                if (
                    set(payload) != {"content_sha256", "status"}
                    or not isinstance(object_id, str)
                    or object_id not in assets
                    or object_id in expected_assets
                    or payload["status"] not in {"proposed", "quarantined"}
                    or payload["content_sha256"] != assets[object_id].content_sha256
                ):
                    return failed("asset_proposed_event_invalid", object_id)
                expected_assets[object_id] = {
                    "status": payload["status"],
                    "verification": "unverified",
                    "content_sha256": payload["content_sha256"],
                    "approved": False,
                }
                continue

            if event_type == "asset_approved":
                if (
                    set(payload) != {"content_sha256", "supersedes_asset_id"}
                    or not isinstance(object_id, str)
                    or object_id not in expected_assets
                    or expected_assets[object_id]["status"]
                    not in {"proposed", "quarantined"}
                    or payload["content_sha256"]
                    != expected_assets[object_id]["content_sha256"]
                ):
                    return failed("asset_approved_event_invalid", object_id)
                supersedes = payload["supersedes_asset_id"]
                if supersedes is not None:
                    if (
                        not isinstance(supersedes, str)
                        or supersedes not in expected_assets
                        or expected_assets[supersedes]["status"]
                        not in {"active", "superseded"}
                        or assets[supersedes].semantic_key
                        != assets[object_id].semantic_key
                    ):
                        return failed("asset_supersession_event_invalid", object_id)
                    expected_assets[supersedes]["status"] = "superseded"
                expected_assets[object_id]["status"] = "active"
                expected_assets[object_id]["verification"] = "human_verified"
                expected_assets[object_id]["approved"] = True
                continue

            if event_type == "asset_revoked":
                if (
                    set(payload) != {"reason", "content_sha256"}
                    or not isinstance(object_id, str)
                    or object_id not in expected_assets
                    or not isinstance(payload["reason"], str)
                    or not payload["reason"]
                    or len(payload["reason"]) > 2_000
                    or payload["content_sha256"]
                    != expected_assets[object_id]["content_sha256"]
                ):
                    return failed("asset_revoked_event_invalid", object_id)
                expected_assets[object_id]["status"] = "revoked"
                continue

            if event_type == "relation_added":
                relation_fields = {
                    "subject_asset_id",
                    "predicate",
                    "object_asset_id",
                    "evidence_fragment_id",
                }
                if (
                    set(payload) != relation_fields
                    or not isinstance(object_id, str)
                    or object_id in expected_relations
                    or payload["subject_asset_id"] not in expected_assets
                    or payload["object_asset_id"] not in expected_assets
                    or expected_assets[payload["subject_asset_id"]]["status"]
                    != "active"
                    or expected_assets[payload["object_asset_id"]]["status"]
                    != "active"
                    or payload["subject_asset_id"] == payload["object_asset_id"]
                    or payload["predicate"] not in RELATION_PREDICATES
                    or (
                        payload["evidence_fragment_id"] is not None
                        and payload["evidence_fragment_id"] not in expected_fragments
                    )
                    or stable_id(
                        "relation",
                        self.vault_id,
                        payload["subject_asset_id"],
                        payload["predicate"],
                        payload["object_asset_id"],
                        payload["evidence_fragment_id"] or "",
                    )
                    != object_id
                ):
                    return failed("relation_added_event_invalid", object_id)
                expected_relations[object_id] = dict(payload)
                continue

        if set(expected_sources) != set(sources):
            return failed("source_event_inventory_mismatch")
        if set(expected_fragments) != set(fragments):
            return failed("fragment_event_inventory_mismatch")
        if set(expected_assets) != set(assets):
            return failed("asset_event_inventory_mismatch")
        if set(expected_relations) != set(relations):
            return failed("relation_event_inventory_mismatch")

        for asset_id, expected in expected_assets.items():
            asset = assets[asset_id]
            if (
                asset.status != expected["status"]
                or asset.verification != expected["verification"]
                or asset.content_sha256 != expected["content_sha256"]
                or (asset.activated_at is not None) is not expected["approved"]
            ):
                return failed("asset_lifecycle_state_mismatch", asset_id)
            for reference in asset.source_refs:
                fragment = fragments.get(reference.fragment_id)
                if (
                    fragment is None
                    or fragment["source_id"] != reference.source_id
                    or fragment["locator"] != reference.locator
                    or fragment["text_sha256"] != reference.quote_sha256
                ):
                    return failed("asset_source_reference_mismatch", asset_id)

        for relation_id, expected in expected_relations.items():
            relation = relations[relation_id]
            if (
                relation["subject_asset_id"] != expected["subject_asset_id"]
                or relation["predicate"] != expected["predicate"]
                or relation["object_asset_id"] != expected["object_asset_id"]
                or relation["evidence_fragment_id"]
                != expected["evidence_fragment_id"]
                or relation["verification"] != "human_verified"
            ):
                return failed("relation_state_mismatch", relation_id)
            try:
                canonical_timestamp(
                    relation["created_at"],
                    field="relation created_at",
                )
            except (TypeError, ValueError):
                return failed("relation_timestamp_invalid", relation_id)

        if len(search_rows) != len(assets):
            return failed("search_index_inventory_mismatch")
        indexed_assets: set[str] = set()
        for row in search_rows:
            asset_id = row["asset_id"]
            asset = assets.get(asset_id)
            if asset is None or asset_id in indexed_assets:
                return failed("search_index_asset_mismatch", asset_id)
            indexed_assets.add(asset_id)
            if (
                row["title_tokens"] != _token_string(asset.title)
                or row["statement_tokens"] != _token_string(asset.statement)
                or row["semantic_tokens"] != _token_string(asset.semantic_key or "")
                or row["tag_tokens"] != _token_string(" ".join(asset.tags))
            ):
                return failed("search_index_content_mismatch", asset_id)
        return {
            "valid": True,
            **counts,
            "reason": None,
            "object_id": None,
        }

    def verify_integrity(self) -> dict[str, Any]:
        file_fingerprint = self._database_file_fingerprint()
        if self.read_only and file_fingerprint != self._opened_database_fingerprint:
            raise RuntimeError(
                "knowledge vault database changed while its read snapshot was pinned"
            )
        cache_key = (
            str(self.database),
            *file_fingerprint,
            self.revision,
            self.audit_head,
        )
        if (
            self._integrity_cache_key == cache_key
            and self._integrity_cache_value is not None
        ):
            return deepcopy(self._integrity_cache_value)
        with _INTEGRITY_CACHE_LOCK:
            cached = _INTEGRITY_CACHE.get(cache_key)
            if cached is not None:
                _INTEGRITY_CACHE.move_to_end(cache_key)
                self._integrity_cache_key = cache_key
                self._integrity_cache_value = deepcopy(cached)
                return deepcopy(cached)
        audit = self.verify_audit_chain()
        state = (
            self.verify_state_integrity()
            if audit["valid"]
            else {
                "valid": False,
                "reason": "audit_chain_invalid",
                "object_id": None,
            }
        )
        result = {
            "valid": bool(audit["valid"] and state["valid"]),
            "audit": audit,
            "state": state,
        }
        if self._database_file_fingerprint() != file_fingerprint:
            raise RuntimeError(
                "knowledge vault database changed during integrity verification"
            )
        self._integrity_cache_key = cache_key
        self._integrity_cache_value = deepcopy(result)
        with _INTEGRITY_CACHE_LOCK:
            for existing_key in tuple(_INTEGRITY_CACHE):
                if existing_key[0] == str(self.database) and existing_key != cache_key:
                    del _INTEGRITY_CACHE[existing_key]
            _INTEGRITY_CACHE[cache_key] = deepcopy(result)
            _INTEGRITY_CACHE.move_to_end(cache_key)
            while len(_INTEGRITY_CACHE) > _MAX_INTEGRITY_CACHE_ENTRIES:
                _INTEGRITY_CACHE.popitem(last=False)
        return result

    def latest_event_at(self) -> str:
        row = self.connection.execute(
            "SELECT created_at FROM events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("knowledge vault event history is missing")
        return canonical_timestamp(row["created_at"], field="latest event created_at")

    def audit_hash_at(self, revision: int) -> str:
        if isinstance(revision, bool) or not 0 <= revision <= self.revision:
            raise ValueError("knowledge vault revision is outside the audit history")
        row = self.connection.execute(
            "SELECT event_hash FROM events WHERE sequence = ?",
            (revision,),
        ).fetchone()
        if row is None or not _SHA256.fullmatch(row["event_hash"]):
            raise RuntimeError("knowledge vault audit event is missing or invalid")
        return cast(str, row["event_hash"])

    def inspect(self) -> dict[str, Any]:
        status_counts = {
            row["status"]: row["count"]
            for row in self.connection.execute(
                "SELECT status, COUNT(*) AS count FROM assets GROUP BY status"
            ).fetchall()
        }
        tier_counts = {
            row["memory_tier"]: row["count"]
            for row in self.connection.execute(
                """
                SELECT memory_tier, COUNT(*) AS count
                FROM assets WHERE status = 'active'
                GROUP BY memory_tier
                """
            ).fetchall()
        }
        kind_counts = {
            row["kind"]: row["count"]
            for row in self.connection.execute(
                """
                SELECT kind, COUNT(*) AS count
                FROM assets WHERE status = 'active'
                GROUP BY kind
                """
            ).fetchall()
        }
        source_count = self.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        fragment_count = self.connection.execute(
            "SELECT COUNT(*) FROM source_fragments"
        ).fetchone()[0]
        relation_count = self.connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        expired_count = self.connection.execute(
            """
            SELECT COUNT(*) FROM assets
            WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (utc_now(),),
        ).fetchone()[0]
        usable_active_count = self.connection.execute(
            """
            SELECT COUNT(*) FROM assets
            WHERE status = 'active'
              AND verification = 'human_verified'
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            (utc_now(),),
        ).fetchone()[0]
        instruction_risk_count = self.connection.execute(
            "SELECT COUNT(*) FROM sources WHERE instruction_risk = 1"
        ).fetchone()[0]
        integrity = self.verify_integrity()
        source_integrity: dict[str, Any]
        if integrity["valid"]:
            active_source_ids: set[str] = set()
            active_rows = self.connection.execute(
                """
                SELECT * FROM assets
                WHERE status = 'active'
                  AND verification = 'human_verified'
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY asset_id
                """,
                (utc_now(),),
            ).fetchall()
            for row in active_rows:
                active_source_ids.update(
                    reference.source_id
                    for reference in self._row_to_asset(row).source_refs
                )
            source_checks = [
                self._source_file_check(source_id)
                for source_id in sorted(active_source_ids)
            ]
            invalid_source_ids = [
                check["source_id"] for check in source_checks if not check["valid"]
            ]
            source_integrity = {
                "valid": not invalid_source_ids,
                "checked_active_source_files": len(source_checks),
                "invalid_active_source_file_count": len(invalid_source_ids),
                "invalid_source_ids": invalid_source_ids[:20],
                "invalid_source_ids_truncated": len(invalid_source_ids) > 20,
                "reason": (
                    None
                    if not invalid_source_ids
                    else "active_source_file_missing_or_hash_mismatch"
                ),
            }
        else:
            source_integrity = {
                "valid": False,
                "checked_active_source_files": 0,
                "invalid_active_source_file_count": 0,
                "invalid_source_ids": [],
                "invalid_source_ids_truncated": False,
                "reason": "database_integrity_invalid",
            }
        next_actions: list[str] = []
        proposed = status_counts.get("proposed", 0) + status_counts.get("quarantined", 0)
        if proposed:
            next_actions.append(
                f"review {proposed} proposed/quarantined assets before they can reach Agent context"
            )
        if expired_count:
            next_actions.append(
                f"revoke or supersede {expired_count} expired working-memory assets"
            )
        if not integrity["valid"]:
            next_actions.append("stop using the vault and restore it from a trusted backup")
        elif not source_integrity["valid"]:
            next_actions.append(
                "restore invalid active source files from a trusted content-addressed copy"
            )
        return {
            "schema_version": KNOWLEDGE_VAULT_SCHEMA,
            "storage_schema": KNOWLEDGE_STORAGE_SCHEMA,
            "vault_id": self.vault_id,
            "name": self.manifest["name"],
            "scope": self.manifest["scope"],
            "created_at": self.manifest["created_at"],
            "path": str(self.root),
            "revision": self.revision,
            "audit_head": self.audit_head,
            "audit": integrity["audit"],
            "integrity": integrity,
            "source_integrity": source_integrity,
            "source_count": source_count,
            "fragment_count": fragment_count,
            "relation_count": relation_count,
            "asset_status_counts": {
                status: status_counts.get(status, 0) for status in sorted(ASSET_STATUSES)
            },
            "active_memory_tier_counts": {
                tier: tier_counts.get(tier, 0) for tier in sorted(MEMORY_TIERS)
            },
            "active_kind_counts": {
                kind: kind_counts.get(kind, 0) for kind in sorted(ASSET_KINDS)
            },
            "expired_active_count": expired_count,
            "usable_active_count": usable_active_count,
            "instruction_risk_source_count": instruction_risk_count,
            "agent_ready": (
                integrity["valid"]
                and source_integrity["valid"]
                and usable_active_count > 0
            ),
            "next_actions": next_actions,
        }

    def all_assets(
        self,
        *,
        statuses: Iterable[str] = ("active",),
    ) -> tuple[KnowledgeAsset, ...]:
        selected = tuple(dict.fromkeys(statuses))
        if not selected or any(status not in ASSET_STATUSES for status in selected):
            raise ValueError("asset status filter is invalid")
        placeholders = ",".join("?" for _ in selected)
        rows = self.connection.execute(
            # selected values are validated enums and remain bound parameters.
            f"SELECT * FROM assets WHERE status IN ({placeholders}) ORDER BY asset_id",
            selected,
        ).fetchall()
        return tuple(self._row_to_asset(row) for row in rows)

    def all_sources(self) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
        return tuple(self._source_row(row) for row in rows)

    def all_relations(self) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute("SELECT * FROM relations ORDER BY relation_id").fetchall()
        return tuple(dict(row) for row in rows)
