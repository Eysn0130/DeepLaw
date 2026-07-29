from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
from collections import defaultdict, deque
from contextlib import AbstractContextManager, suppress
from datetime import UTC, datetime, timedelta
from functools import cache, partial
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from .knowledge_models import canonical_timestamp, utc_now
from .knowledge_store import KnowledgeVault, default_knowledge_vault
from .util import (
    canonical_json,
    compact_text,
    fts_query,
    has_instruction_risk,
    search_terms,
    sha256_bytes,
    sha256_file,
    stable_id,
    strict_json_loads,
)

AUTONOMOUS_CORE_SCHEMA = "deeplaw.autonomous-knowledge-core/v1"
KNOWLEDGE_OBJECT_SCHEMA = "deeplaw.knowledge-object/v1"
KNOWLEDGE_REVISION_SCHEMA = "deeplaw.knowledge-revision/v1"
KNOWLEDGE_RELATION_SCHEMA = "deeplaw.knowledge-relation/v3"
KNOWLEDGE_CAPSULE_SCHEMA = "deeplaw.knowledge-capsule/v2"
KNOWLEDGE_SINK_SCHEMA = "deeplaw.knowledge-sink/v1"
AUTONOMOUS_EVENT_SCHEMA = "deeplaw.autonomous-event/v1"
DERIVED_MANIFEST_SCHEMA = "deeplaw.derived-manifest/v1"
AUTONOMOUS_SNAPSHOT_SCHEMA = "deeplaw.autonomous-snapshot/v1"
AUTONOMOUS_EVENT_TYPES = frozenset(
    {
        "autonomous_core_initialized",
        "evidence_object_bound",
        "knowledge_feedback_recorded",
        "knowledge_relation_committed",
        "knowledge_revision_committed",
        "knowledge_sink_grant_enabled",
        "knowledge_sink_grant_revoked",
        "workspace_conflict_preserved",
        "workspace_location_recorded",
        "workspace_materialized",
    }
)
AUTONOMOUS_UNIQUE_OBJECT_EVENT_TYPES = AUTONOMOUS_EVENT_TYPES - {
    "workspace_location_recorded"
}

KnowledgeKind = Literal[
    "claim",
    "concept",
    "entity",
    "event",
    "decision",
    "procedure",
    "experience",
    "preference",
    "synthesis",
    "comparison",
    "skill",
    "memory",
]
Lifecycle = Literal[
    "active",
    "superseded",
    "revoked",
    "expired",
    "forgotten",
    "archived",
    "quarantined",
]
EpistemicState = Literal["supported", "tentative", "contested", "unknown"]
Scope = Literal["personal", "project", "domain"]
Sensitivity = Literal["public", "internal", "private", "restricted"]

KNOWLEDGE_KINDS = frozenset(KnowledgeKind.__args__)
LIFECYCLES = frozenset(Lifecycle.__args__)
EPISTEMIC_STATES = frozenset(EpistemicState.__args__)
SCOPES = frozenset(Scope.__args__)
SENSITIVITIES = frozenset(Sensitivity.__args__)
SENSITIVITY_ORDER = ("public", "internal", "private", "restricted")
SINK_OPERATIONS = frozenset(
    {
        "remember",
        "reflect",
        "save_synthesis",
        "upsert_concept",
        "add_relation",
        "expire",
        "forget",
        "save_skill",
        "record_feedback",
    }
)
OBJECT_OPERATION_KINDS = {
    "remember": KNOWLEDGE_KINDS - {"concept", "synthesis", "skill"},
    "reflect": frozenset({"memory"}),
    "save_synthesis": frozenset({"synthesis"}),
    "upsert_concept": frozenset({"concept"}),
    "save_skill": frozenset({"skill"}),
    "expire": KNOWLEDGE_KINDS,
    "forget": KNOWLEDGE_KINDS,
}
FEEDBACK_EVALUATOR_TYPES = frozenset({"agent_self_report", "external_check", "user"})
RELATION_PREDICATES = frozenset(
    {
        "supports",
        "contradicts",
        "depends_on",
        "implements",
        "derived_from",
        "applies_to",
        "related_to",
        "describes",
        "mentions",
        "reports",
        "contributes_to",
        "same_as",
    }
)

_KNOWLEDGE_ID = re.compile(r"^knowledge_[0-9a-f]{24}$")
_REVISION_ID = re.compile(r"^knowledgerev_[0-9a-f]{24}$")
_RELATION_REVISION_ID = re.compile(r"^relationrev_[0-9a-f]{24}$")
_GRANT_ID = re.compile(r"^grant_[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
_MAX_MARKDOWN_BYTES = 256 * 1024
_MAX_REQUEST_BYTES = 320 * 1024
_MAX_TITLE_CHARS = 500
_MAX_BODY_CHARS = 200_000
_MAX_SOURCE_REFS = 100
_MAX_RELATIONS_PER_OBJECT = 100
_MAX_TAGS = 64
_MAX_TAG_CHARS = 100
_MAX_GRANT_OPERATIONS_PER_MINUTE = 120
_MAX_GRANTS = 10_000
_MAX_OBJECTS = 1_000_000
_MAX_RECALL_LIMIT = 20
_MAX_RECALL_CHARS = 20_000
_MAX_RECALL_TERMS = 128
_MAX_RECONCILE_FILES = 10_000
_MAX_RECONCILE_BYTES = 256 * 1024 * 1024
_MAX_STAGING_RECORDS = 10_000
_MAX_STAGING_RECORD_BYTES = 64 * 1024
_MAX_LINT_ISSUES = 64
_MAX_DUPLICATE_IDS_PER_ISSUE = 20
_MAX_WIKI_ITEMS = 250
_MAX_COMMUNITY_VIEWS = 1_000
_MAX_COMMUNITY_VIEW_MEMBERS = 500
_MAX_RECALL_PROVIDER_CHARS = 24 * 1024
_SOURCE_REFERENCE_FIELDS = {
    "source_id": 200,
    "source_revision_id": 200,
    "fragment_id": 200,
    "artifact_id": 200,
    "revision_id": 200,
    "locator": 2_000,
    "uri": 4_000,
    "quote_sha256": 64,
}

_KIND_DIRECTORIES = {
    "claim": "knowledge/claims",
    "concept": "knowledge/concepts",
    "entity": "knowledge/entities",
    "event": "knowledge/events",
    "decision": "knowledge/decisions",
    "procedure": "knowledge/procedures",
    "experience": "knowledge/experiences",
    "preference": "knowledge/preferences",
    "synthesis": "knowledge/syntheses",
    "comparison": "knowledge/comparisons",
}
_WORKSPACE_DIRECTORIES = (
    "knowledge/claims",
    "knowledge/concepts",
    "knowledge/entities",
    "knowledge/events",
    "knowledge/decisions",
    "knowledge/procedures",
    "knowledge/experiences",
    "knowledge/preferences",
    "knowledge/comparisons",
    "knowledge/syntheses",
    "memory/working",
    "memory/episodic",
    "memory/semantic",
    "memory/procedural",
    "memory/reflective",
    "wiki/sources",
    "wiki/concepts",
    "wiki/entities",
    "wiki/events",
    "wiki/comparisons",
    "wiki/syntheses",
    "wiki/questions",
    "wiki/communities",
    "wiki/gaps",
    "wiki/reports",
    "skills",
    "attachments",
    "canvas",
    ".deeplaw/objects/sha256",
    ".deeplaw/staging/conflicts",
    ".deeplaw/derived/fts",
    ".deeplaw/derived/vectors",
    ".deeplaw/derived/tree",
    ".deeplaw/derived/graph",
    ".deeplaw/derived/communities",
    ".deeplaw/derived/cache",
    ".deeplaw/snapshots",
    ".deeplaw/update",
    ".deeplaw/capabilities",
)


def _contract_path(name: str) -> Path:
    packaged = Path(__file__).resolve().parent / "contracts" / name
    if packaged.is_file():
        return packaged
    repository = Path(__file__).resolve().parents[2] / "contracts" / name
    if repository.is_file():
        return repository
    raise RuntimeError(f"DeepLaw autonomous contract is missing: {name}")


@cache
def _contract_validator(name: str) -> Draft202012Validator:
    schema = strict_json_loads(_contract_path(name).read_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_contract(name: str, value: dict[str, Any]) -> None:
    error = next(_contract_validator(name).iter_errors(value), None)
    if error is not None:
        raise ValueError(f"value does not match {name}: {error.message}")


class _ClosedSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.constructor.ConstructorError(
                None,
                None,
                "YAML aliases are not allowed in Knowledge Objects",
                self.peek_event().start_mark,
            )
        return super().compose_node(parent, index)


def _construct_mapping(loader: _ClosedSafeLoader, node: Any, deep: bool = False) -> Any:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key: {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_ClosedSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _owner_directory(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"DeepLaw directory must not be a symbolic link: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise RuntimeError(f"DeepLaw path is not a directory: {path}")
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


def _atomic_owner_write(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise RuntimeError(f"DeepLaw file must not be a symbolic link: {path}")
    _owner_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _database_path(root: Path) -> Path:
    # v0.7 vaults use this path. The v3 tables are additive so there remains one
    # SQLite governance ledger during migration rather than two competing ledgers.
    return root / "vault.sqlite3"


def _object_path(root: Path, digest: str) -> Path:
    if not _SHA256.fullmatch(digest):
        raise ValueError("object digest must be lowercase SHA-256")
    return root / ".deeplaw" / "objects" / "sha256" / digest[:2] / digest[2:]


def _write_object(root: Path, payload: bytes) -> tuple[str, Path]:
    digest = sha256_bytes(payload)
    destination = _object_path(root, digest)
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise RuntimeError("content-addressed object path is unsafe")
        if destination.stat().st_size != len(payload) or sha256_file(destination) != digest:
            raise RuntimeError("content-addressed object failed exact-byte verification")
        return digest, destination
    _atomic_owner_write(destination, payload)
    return digest, destination


def _read_object(root: Path, digest: str) -> bytes:
    source = _object_path(root, digest)
    if source.is_symlink() or not source.is_file():
        raise RuntimeError("content-addressed object is missing or unsafe")
    payload = source.read_bytes()
    if sha256_bytes(payload) != digest:
        raise RuntimeError("content-addressed object failed exact-byte verification")
    return payload


def _bounded_string(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    canonical = value.strip()
    if not canonical or canonical != value or len(canonical) > maximum:
        raise ValueError(f"{field} must be a bounded canonical string")
    if "\x00" in canonical:
        raise ValueError(f"{field} contains a null byte")
    return canonical


def _optional_timestamp(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return canonical_timestamp(value, field=field)


def _timestamp_after(candidate: str, prior: str) -> str:
    candidate = canonical_timestamp(candidate, field="transaction timestamp")
    prior = canonical_timestamp(prior, field="prior transaction timestamp")
    if candidate > prior:
        return candidate
    prior_time = datetime.fromisoformat(prior.replace("Z", "+00:00"))
    return (
        (prior_time + timedelta(seconds=1))
        .astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _interval_admits(
    *,
    reference_time: str,
    valid_from: str | None,
    valid_to: str | None,
    expires_at: str | None = None,
) -> bool:
    return bool(
        (valid_from is None or valid_from <= reference_time)
        and (valid_to is None or valid_to > reference_time)
        and (expires_at is None or expires_at > reference_time)
    )


def _canonical_json_list(value: Any, *, field: str, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{field} must be a bounded array")
    return value


def _canonical_source_references(
    value: Any,
    *,
    field: str,
    maximum: int = _MAX_SOURCE_REFS,
) -> list[dict[str, str]]:
    items = _canonical_json_list(value, field=field, maximum=maximum)
    canonical: list[dict[str, str]] = []
    for index, reference in enumerate(items):
        if (
            not isinstance(reference, dict)
            or not reference
            or any(key not in _SOURCE_REFERENCE_FIELDS for key in reference)
        ):
            raise ValueError(f"{field}[{index}] does not match the closed reference contract")
        selected: dict[str, str] = {}
        for key, item in reference.items():
            maximum_chars = _SOURCE_REFERENCE_FIELDS[key]
            if (
                not isinstance(item, str)
                or item != item.strip()
                or not item
                or len(item) > maximum_chars
                or "\x00" in item
            ):
                raise ValueError(f"{field}[{index}].{key} is invalid")
            if key == "quote_sha256" and not _SHA256.fullmatch(item):
                raise ValueError(f"{field}[{index}].quote_sha256 is invalid")
            selected[key] = item
        identity_fields = {
            key
            for key in (
                "source_id",
                "source_revision_id",
                "artifact_id",
                "revision_id",
            )
            if key in selected
        }
        source_identity = identity_fields.intersection({"source_id", "source_revision_id"})
        if not identity_fields:
            raise ValueError(f"{field}[{index}] has no stable referenced identity")
        if "revision_id" in identity_fields and len(identity_fields) != 1:
            raise ValueError(f"{field}[{index}] mixes Knowledge and source identities")
        if "artifact_id" in identity_fields and len(identity_fields) != 1:
            raise ValueError(f"{field}[{index}] mixes artifact and source identities")
        if "fragment_id" in selected and not source_identity:
            raise ValueError(f"{field}[{index}] has a fragment without a source identity")
        canonical.append(cast(dict[str, str], strict_json_loads(canonical_json(selected))))
    return canonical


def _workspace_path(
    *,
    kind: str,
    knowledge_id: str,
    memory_type: str | None,
) -> str:
    if not _KNOWLEDGE_ID.fullmatch(knowledge_id):
        raise ValueError("knowledge ID is invalid")
    if kind == "memory":
        selected = memory_type or "semantic"
        if selected not in {"working", "episodic", "semantic", "procedural", "reflective"}:
            raise ValueError("memory type is invalid")
        return f"memory/{selected}/{knowledge_id}.md"
    if kind == "skill":
        return f"skills/{knowledge_id}/SKILL.md"
    directory = _KIND_DIRECTORIES.get(kind)
    if directory is None:
        raise ValueError("knowledge kind is invalid")
    return f"{directory}/{knowledge_id}.md"


def _safe_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2_000:
        raise ValueError("workspace path is invalid")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("workspace path must remain inside the Vault")
    canonical = path.as_posix()
    if canonical.startswith(".deeplaw/"):
        raise ValueError("Knowledge Objects cannot be materialized inside the trusted core")
    return canonical


def _safe_knowledge_workspace_path(value: str) -> str:
    canonical = _safe_relative_path(value)
    path = PurePosixPath(canonical)
    if path.suffix != ".md" or not path.parts or path.parts[0] not in {
        "knowledge",
        "memory",
        "skills",
    }:
        raise ValueError("Knowledge workspace path is outside its open Markdown roots")
    return canonical


def bounded_source_reference(reference: dict[str, Any]) -> dict[str, Any]:
    """Project provenance without disclosing arbitrary fields or local paths."""
    digest = sha256_bytes(canonical_json(reference).encode("utf-8"))
    allowed = {
        "source_id",
        "source_revision_id",
        "fragment_id",
        "artifact_id",
        "revision_id",
        "quote_sha256",
        "object_sha256",
    }
    projected = {
        key: value
        for key, value in reference.items()
        if key in allowed and isinstance(value, str) and len(value) <= 500
    }
    omitted = any(key not in projected for key in reference if key not in {"locator", "uri"})
    locator = reference.get("locator")
    if (
        isinstance(locator, str)
        and len(locator) <= 128
        and not locator.startswith(("/", "\\"))
        and not re.match(r"^[A-Za-z]:[\\/]", locator)
    ):
        projected["locator"] = locator
    elif locator is not None:
        omitted = True
    uri = reference.get("uri")
    if isinstance(uri, str) and len(uri) <= 256:
        parsed = urlsplit(uri)
        if (
            parsed.scheme in {"https", "http", "urn", "deeplaw"}
            and parsed.username is None
            and parsed.password is None
        ):
            projected["uri"] = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
            omitted = omitted or bool(parsed.query or parsed.fragment)
        else:
            omitted = True
    elif uri is not None:
        omitted = True
    projected["reference_sha256"] = digest
    projected["metadata_omitted"] = omitted
    return projected


def _frontmatter_dump(value: dict[str, Any]) -> str:
    return yaml.safe_dump(
        value,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=100,
    ).strip()


def render_knowledge_markdown(
    *,
    knowledge_id: str,
    revision_id: str,
    title: str,
    body: str,
    kind: str,
    lifecycle: str,
    epistemic_state: str,
    verification: str,
    scope: str,
    sensitivity: str,
    writer_id: str,
    source_free: bool,
    source_refs: list[dict[str, Any]],
    generation: dict[str, Any],
    tags: list[str],
    semantic_key: str | None,
    parent_revision_id: str | None,
    supersedes_revision_id: str | None,
    valid_from: str | None,
    valid_to: str | None,
    observed_at: str,
    recorded_at: str,
    expires_at: str | None,
    preference_basis: str | None,
    memory_type: str | None,
    skill_manifest: dict[str, Any] | None,
    quarantine_reasons: list[str],
    lifecycle_reason: str | None,
) -> bytes:
    # Canonical JSON round-tripping gives nested mappings a deterministic key
    # order before YAML serialization. The exact Markdown bytes are the
    # content-addressed half of a Knowledge Revision, so semantically equal
    # mappings must never render differently after a Ledger round trip.
    canonical_sources = cast(list[dict[str, Any]], strict_json_loads(canonical_json(source_refs)))
    canonical_generation = cast(dict[str, Any], strict_json_loads(canonical_json(generation)))
    canonical_skill = (
        cast(dict[str, Any], strict_json_loads(canonical_json(skill_manifest)))
        if skill_manifest is not None
        else None
    )
    frontmatter: dict[str, Any] = {
        "schema": KNOWLEDGE_OBJECT_SCHEMA,
        "deeplaw_id": knowledge_id,
        "revision": revision_id,
        "title": title,
        "kind": kind,
        "origin": "agent_derived",
        "authority": "agent_derived",
        "legal_authority": False,
        "verification": verification,
        "lifecycle": lifecycle,
        "epistemic_state": epistemic_state,
        "scope": scope,
        "sensitivity": sensitivity,
        "writer": writer_id,
        "source_free": source_free,
        "sources": canonical_sources,
        "generation": canonical_generation,
        "tags": tags,
        "semantic_key": semantic_key,
        "parent_revision": parent_revision_id,
        "supersedes": supersedes_revision_id,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "observed_at": observed_at,
        "recorded_at": recorded_at,
        "expires_at": expires_at,
        "quarantine_reasons": quarantine_reasons,
        "lifecycle_reason": lifecycle_reason,
    }
    if memory_type is not None:
        frontmatter["memory_type"] = memory_type
    if preference_basis is not None:
        frontmatter["preference_basis"] = preference_basis
    if canonical_skill is not None:
        frontmatter["skill"] = canonical_skill
    _validate_contract("knowledge-object.v1.schema.json", frontmatter)
    markdown = f"---\n{_frontmatter_dump(frontmatter)}\n---\n\n# {title}\n\n{body.rstrip()}\n"
    payload = markdown.encode("utf-8")
    if len(payload) > _MAX_MARKDOWN_BYTES:
        raise ValueError("Knowledge Object Markdown exceeds the 256 KiB limit")
    return payload


def parse_knowledge_markdown(
    payload: bytes,
    *,
    validate_contract: bool = True,
) -> dict[str, Any]:
    if not payload or len(payload) > _MAX_MARKDOWN_BYTES:
        raise ValueError("Knowledge Object Markdown is empty or too large")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Knowledge Object Markdown must be UTF-8") from error
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("Knowledge Object Markdown requires YAML frontmatter")
    raw_frontmatter, raw_body = text[4:].split("\n---\n", 1)
    try:
        frontmatter = yaml.load(raw_frontmatter, Loader=_ClosedSafeLoader)
    except yaml.YAMLError as error:
        raise ValueError("Knowledge Object frontmatter is invalid") from error
    if not isinstance(frontmatter, dict):
        raise ValueError("Knowledge Object frontmatter must be an object")
    if frontmatter.get("schema") != KNOWLEDGE_OBJECT_SCHEMA:
        raise ValueError("Knowledge Object schema is unsupported")
    if validate_contract:
        _validate_contract("knowledge-object.v1.schema.json", frontmatter)
    title = frontmatter.get("title")
    _bounded_string(title, field="Knowledge Object title", maximum=_MAX_TITLE_CHARS)
    body = raw_body.lstrip("\n")
    expected_heading = f"# {title}\n"
    if body.startswith(expected_heading):
        body = body[len(expected_heading) :].lstrip("\n")
    body = body.rstrip()
    if not body or len(body) > _MAX_BODY_CHARS:
        raise ValueError("Knowledge Object body is empty or too large")
    return {"frontmatter": frontmatter, "body": body, "payload": payload}


def _tables_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS autonomous_core_v3 (
            schema_version TEXT PRIMARY KEY,
            installed_at TEXT NOT NULL,
            migration_source TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS autonomous_metadata_v3 (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS content_objects_v3 (
            object_sha256 TEXT PRIMARY KEY,
            object_kind TEXT NOT NULL CHECK(object_kind IN ('evidence', 'knowledge_revision')),
            byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
            media_type TEXT NOT NULL,
            created_at TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS content_object_roles_v3 (
            object_sha256 TEXT NOT NULL REFERENCES content_objects_v3(object_sha256),
            object_role TEXT NOT NULL CHECK(object_role IN ('evidence', 'knowledge_revision')),
            created_at TEXT NOT NULL,
            PRIMARY KEY(object_sha256, object_role)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS evidence_bindings_v3 (
            binding_id TEXT PRIMARY KEY,
            legacy_source_id TEXT,
            source_revision_id TEXT,
            object_sha256 TEXT NOT NULL REFERENCES content_objects_v3(object_sha256),
            origin TEXT NOT NULL CHECK(origin IN ('official', 'user_source', 'external_import')),
            authority TEXT NOT NULL,
            verification TEXT NOT NULL,
            scope TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            lifecycle TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS knowledge_objects_v3 (
            knowledge_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            origin TEXT NOT NULL CHECK(origin = 'agent_derived'),
            authority TEXT NOT NULL CHECK(authority = 'agent_derived'),
            current_revision_id TEXT,
            workspace_path TEXT NOT NULL,
            semantic_key TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS knowledge_objects_v3_semantic
            ON knowledge_objects_v3(semantic_key, kind);

        CREATE TABLE IF NOT EXISTS knowledge_revisions_v3 (
            revision_id TEXT PRIMARY KEY,
            knowledge_id TEXT NOT NULL REFERENCES knowledge_objects_v3(knowledge_id),
            parent_revision_id TEXT REFERENCES knowledge_revisions_v3(revision_id),
            supersedes_revision_id TEXT REFERENCES knowledge_revisions_v3(revision_id),
            markdown_sha256 TEXT NOT NULL REFERENCES content_objects_v3(object_sha256),
            semantic_digest TEXT NOT NULL,
            title TEXT NOT NULL,
            semantic_key TEXT,
            kind TEXT NOT NULL,
            lifecycle TEXT NOT NULL,
            epistemic_state TEXT NOT NULL,
            origin TEXT NOT NULL CHECK(origin = 'agent_derived'),
            authority TEXT NOT NULL CHECK(authority = 'agent_derived'),
            verification TEXT NOT NULL,
            scope TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            writer_id TEXT NOT NULL,
            source_free INTEGER NOT NULL CHECK(source_free IN (0, 1)),
            source_refs_json TEXT NOT NULL,
            generation_json TEXT NOT NULL,
            tags_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            valid_from TEXT,
            valid_to TEXT,
            observed_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            expires_at TEXT,
            workspace_path TEXT NOT NULL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS knowledge_revisions_v3_object
            ON knowledge_revisions_v3(knowledge_id, recorded_at);
        CREATE INDEX IF NOT EXISTS knowledge_revisions_v3_lifecycle
            ON knowledge_revisions_v3(lifecycle, scope, sensitivity);

        CREATE TABLE IF NOT EXISTS knowledge_relations_v3 (
            relation_key TEXT PRIMARY KEY,
            current_revision_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS knowledge_relation_revisions_v3 (
            relation_revision_id TEXT PRIMARY KEY,
            relation_key TEXT NOT NULL REFERENCES knowledge_relations_v3(relation_key),
            parent_revision_id TEXT
                REFERENCES knowledge_relation_revisions_v3(relation_revision_id),
            subject_knowledge_id TEXT NOT NULL REFERENCES knowledge_objects_v3(knowledge_id),
            predicate TEXT NOT NULL,
            object_knowledge_id TEXT NOT NULL REFERENCES knowledge_objects_v3(knowledge_id),
            evidence_refs_json TEXT NOT NULL,
            source_free INTEGER NOT NULL CHECK(source_free IN (0, 1)),
            lifecycle TEXT NOT NULL,
            origin TEXT NOT NULL CHECK(origin = 'agent_derived'),
            authority TEXT NOT NULL CHECK(authority = 'agent_derived'),
            scope TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            writer_id TEXT NOT NULL,
            valid_from TEXT,
            valid_to TEXT,
            observed_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            CHECK(subject_knowledge_id <> object_knowledge_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS knowledge_relation_revisions_v3_subject
            ON knowledge_relation_revisions_v3(subject_knowledge_id, predicate);
        CREATE INDEX IF NOT EXISTS knowledge_relation_revisions_v3_object
            ON knowledge_relation_revisions_v3(object_knowledge_id, predicate);

        CREATE TABLE IF NOT EXISTS knowledge_sink_grants_v3 (
            grant_id TEXT PRIMARY KEY,
            writer_id TEXT NOT NULL,
            allowed_scope TEXT NOT NULL,
            max_sensitivity TEXT NOT NULL,
            operations_json TEXT NOT NULL,
            evaluator_types_json TEXT NOT NULL,
            token_sha256 TEXT NOT NULL,
            max_request_bytes INTEGER NOT NULL,
            max_mutations_per_minute INTEGER NOT NULL,
            max_objects INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS knowledge_sink_usage_v3 (
            mutation_id TEXT PRIMARY KEY,
            grant_id TEXT NOT NULL REFERENCES knowledge_sink_grants_v3(grant_id),
            operation TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS knowledge_feedback_v3 (
            feedback_id TEXT PRIMARY KEY,
            knowledge_id TEXT NOT NULL REFERENCES knowledge_objects_v3(knowledge_id),
            revision_id TEXT NOT NULL REFERENCES knowledge_revisions_v3(revision_id),
            grant_id TEXT NOT NULL REFERENCES knowledge_sink_grants_v3(grant_id),
            run_id TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK(outcome IN ('helpful', 'neutral', 'noisy', 'harmful')),
            evaluator_type TEXT NOT NULL CHECK(
                evaluator_type IN ('user', 'external_check', 'agent_self_report')
            ),
            note_sha256 TEXT,
            recorded_at TEXT NOT NULL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS knowledge_sink_usage_v3_rate
            ON knowledge_sink_usage_v3(grant_id, recorded_at);

        CREATE TABLE IF NOT EXISTS mutation_idempotency_v3 (
            grant_id TEXT NOT NULL REFERENCES knowledge_sink_grants_v3(grant_id),
            idempotency_key TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            result_kind TEXT NOT NULL,
            result_id TEXT NOT NULL,
            response_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(grant_id, idempotency_key)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS pending_materializations_v3 (
            revision_id TEXT PRIMARY KEY REFERENCES knowledge_revisions_v3(revision_id),
            workspace_path TEXT NOT NULL,
            markdown_sha256 TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('write', 'delete')),
            created_at TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS workspace_conflicts_v3 (
            conflict_id TEXT PRIMARY KEY,
            knowledge_id TEXT,
            base_revision_id TEXT,
            current_revision_id TEXT,
            object_sha256 TEXT NOT NULL REFERENCES content_objects_v3(object_sha256),
            workspace_path TEXT NOT NULL,
            reason TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            resolved_at TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS derived_rebuild_queue_v3 (
            queue_id TEXT PRIMARY KEY,
            input_audit_head TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT
        ) STRICT;

        CREATE TABLE IF NOT EXISTS autonomous_events_v3 (
            sequence INTEGER PRIMARY KEY,
            schema_version TEXT NOT NULL,
            event_type TEXT NOT NULL,
            object_id TEXT,
            payload_json TEXT NOT NULL,
            previous_hash TEXT,
            event_hash TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;

        CREATE VIRTUAL TABLE IF NOT EXISTS autonomous_search_v3 USING fts5(
            knowledge_id UNINDEXED,
            revision_id UNINDEXED,
            title_tokens,
            body_tokens,
            semantic_tokens,
            tag_tokens,
            tokenize = 'unicode61 remove_diacritics 2'
        );
    """


def _register_content_object(
    connection: sqlite3.Connection,
    *,
    digest: str,
    object_role: str,
    byte_size: int,
    media_type: str,
    created_at: str,
) -> None:
    if not _SHA256.fullmatch(digest) or object_role not in {
        "evidence",
        "knowledge_revision",
    }:
        raise ValueError("content object identity or role is invalid")
    if not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 0:
        raise ValueError("content object byte size is invalid")
    _bounded_string(media_type, field="content object media type", maximum=200)
    canonical_timestamp(created_at, field="content object created_at")
    connection.execute(
        "INSERT OR IGNORE INTO content_objects_v3 VALUES (?, ?, ?, ?, ?)",
        (digest, object_role, byte_size, media_type, created_at),
    )
    stored = connection.execute(
        "SELECT byte_size FROM content_objects_v3 WHERE object_sha256 = ?",
        (digest,),
    ).fetchone()
    if stored is None or stored["byte_size"] != byte_size:
        raise RuntimeError("content-addressed object metadata is inconsistent")
    connection.execute(
        "INSERT OR IGNORE INTO content_object_roles_v3 VALUES (?, ?, ?)",
        (digest, object_role, created_at),
    )


def autonomous_core_installed(path: str | Path | None = None) -> bool:
    root = Path(path) if path is not None else default_knowledge_vault()
    database = _database_path(root.expanduser().absolute())
    if not database.is_file() or database.is_symlink():
        return False
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'autonomous_core_v3'"
        ).fetchone()
        return row is not None
    finally:
        connection.close()


def initialize_autonomous_core(
    path: str | Path,
    *,
    migration_source: str = "knowledge-sqlite/v1",
) -> dict[str, Any]:
    root = Path(path).expanduser().absolute()
    with KnowledgeVault(root, read_only=True) as vault:
        integrity = vault.verify_integrity()
        if not integrity["valid"]:
            raise RuntimeError("autonomous migration requires a healthy v0.7 Vault")
        vault_id = vault.vault_id
    for relative in _WORKSPACE_DIRECTORIES:
        _owner_directory(root / relative)
    database = _database_path(root)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.executescript(_tables_sql())
        revision_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(knowledge_revisions_v3)")
        }
        if "semantic_key" not in revision_columns:
            connection.execute("ALTER TABLE knowledge_revisions_v3 ADD COLUMN semantic_key TEXT")
            for revision in connection.execute(
                "SELECT revision_id, markdown_sha256 FROM knowledge_revisions_v3"
            ).fetchall():
                parsed = parse_knowledge_markdown(
                    _read_object(root, revision["markdown_sha256"]),
                    validate_contract=False,
                )
                semantic_key = parsed["frontmatter"].get("semantic_key")
                if semantic_key is not None and not isinstance(semantic_key, str):
                    raise RuntimeError("existing Knowledge Revision semantic key is invalid")
                connection.execute(
                    "UPDATE knowledge_revisions_v3 SET semantic_key = ? WHERE revision_id = ?",
                    (semantic_key, revision["revision_id"]),
                )
            connection.commit()
        relation_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(knowledge_relation_revisions_v3)")
        }
        if "scope" not in relation_columns:
            connection.execute(
                "ALTER TABLE knowledge_relation_revisions_v3 "
                "ADD COLUMN scope TEXT NOT NULL DEFAULT 'project'"
            )
        if "sensitivity" not in relation_columns:
            connection.execute(
                "ALTER TABLE knowledge_relation_revisions_v3 "
                "ADD COLUMN sensitivity TEXT NOT NULL DEFAULT 'private'"
            )
        grant_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(knowledge_sink_grants_v3)")
        }
        if "evaluator_types_json" not in grant_columns:
            connection.execute(
                "ALTER TABLE knowledge_sink_grants_v3 "
                "ADD COLUMN evaluator_types_json TEXT NOT NULL "
                "DEFAULT '[\"agent_self_report\"]'"
            )
        connection.execute(
            """
            INSERT OR IGNORE INTO content_object_roles_v3 (
                object_sha256, object_role, created_at
            )
            SELECT object_sha256, object_kind, created_at
            FROM content_objects_v3
            """
        )
        connection.commit()
        installed_at = utc_now()
        existing = connection.execute(
            "SELECT schema_version, installed_at FROM autonomous_core_v3"
        ).fetchone()
        if existing is None:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO autonomous_core_v3 VALUES (?, ?, ?)",
                (AUTONOMOUS_CORE_SCHEMA, installed_at, migration_source),
            )
            event_payload = {
                "vault_id": vault_id,
                "migration_source": migration_source,
            }
            event = {
                "schema_version": AUTONOMOUS_EVENT_SCHEMA,
                "sequence": 0,
                "event_type": "autonomous_core_initialized",
                "object_id": vault_id,
                "payload": event_payload,
                "previous_hash": None,
                "recorded_at": installed_at,
            }
            event_hash = sha256_bytes(canonical_json(event).encode("utf-8"))
            connection.execute(
                "INSERT INTO autonomous_events_v3 VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    0,
                    AUTONOMOUS_EVENT_SCHEMA,
                    "autonomous_core_initialized",
                    vault_id,
                    canonical_json(event_payload),
                    None,
                    event_hash,
                    installed_at,
                ),
            )
            connection.executemany(
                "INSERT INTO autonomous_metadata_v3(key, value) VALUES (?, ?)",
                (
                    ("schema_version", AUTONOMOUS_CORE_SCHEMA),
                    ("vault_id", vault_id),
                    ("sequence", "0"),
                    ("audit_head", event_hash),
                    ("installed_at", installed_at),
                ),
            )
            connection.commit()
        elif existing["schema_version"] != AUTONOMOUS_CORE_SCHEMA:
            raise RuntimeError("unsupported autonomous knowledge schema")
        else:
            installed_at = existing["installed_at"]
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    if os.name != "nt":
        os.chmod(database, 0o600)
    manifest = {
        "schema_version": AUTONOMOUS_CORE_SCHEMA,
        "vault_id": vault_id,
        "ledger": "../vault.sqlite3",
        "object_store": "objects/sha256",
        "workspace": "..",
        "derived_rebuildable": True,
        "installed_at": installed_at,
    }
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(manifest).encode("utf-8"))
    _atomic_owner_write(
        root / ".deeplaw" / "manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        evidence = store.evidence_sync
        recovery = store.recovery_sync
        verification = store.verify()
    return {
        **manifest,
        "legacy_evidence": evidence,
        "recovery": recovery,
        "verification": verification,
    }


def migrate_autonomous_core(
    path: str | Path,
    *,
    backup_output: str | Path | None = None,
) -> dict[str, Any]:
    """Create a verified v0.7 rollback point, then install the additive v3 core."""
    root = Path(path).expanduser().absolute()
    if autonomous_core_installed(root):
        installed = initialize_autonomous_core(
            root,
            migration_source="autonomous-core-reconcile",
        )
        return {
            "schema_version": "deeplaw.autonomous-migration/v1",
            "vault_id": installed["vault_id"],
            "already_installed": True,
            "backup_path": None,
            "installed": installed,
            "verification": installed["verification"],
        }
    from .knowledge_store import create_knowledge_migration_backup

    backup = create_knowledge_migration_backup(root, output=backup_output)
    installed = initialize_autonomous_core(
        root,
        migration_source="knowledge-sqlite/v1+identity-v2",
    )
    if not installed["verification"]["valid"]:
        raise RuntimeError("autonomous migration failed post-install verification")
    return {
        "schema_version": "deeplaw.autonomous-migration/v1",
        "vault_id": installed["vault_id"],
        "already_installed": False,
        "backup_path": backup["backup_path"],
        "backup_sha256": backup["backup_sha256"],
        "installed": installed,
        "verification": installed["verification"],
    }


def rollback_autonomous_core(
    path: str | Path,
    *,
    backup: str | Path,
    confirm: bool,
) -> dict[str, Any]:
    """Restore the pre-v3 Vault while retaining the replaced Vault beside it."""
    from .knowledge_store import restore_knowledge_migration_backup

    result = restore_knowledge_migration_backup(
        path,
        backup=backup,
        confirm=confirm,
    )
    result["autonomous_core_present_after_rollback"] = autonomous_core_installed(path)
    if result["autonomous_core_present_after_rollback"]:
        raise RuntimeError("autonomous rollback did not restore the pre-migration schema")
    return result


_SNAPSHOT_CANONICAL_DIRECTORIES = (
    "sources",
    "inbox",
    "knowledge",
    "memory",
    "skills",
    "attachments",
    ".deeplaw/objects",
    ".deeplaw/staging",
    ".deeplaw/capabilities",
)
_SNAPSHOT_OPERATOR_DIRECTORIES = (
    "operations",
    "derived/retrieval-profiles",
)
_MAX_SNAPSHOT_FILES = 300_000
_MAX_SNAPSHOT_FILE_BYTES = 1024 * 1024 * 1024
_MAX_SNAPSHOT_TOTAL_BYTES = 64 * 1024 * 1024 * 1024
_MAX_SNAPSHOT_MANIFEST_BYTES = 128 * 1024 * 1024


def _copy_snapshot_file(source: Path, destination: Path) -> int:
    if source.is_symlink() or not source.is_file():
        raise RuntimeError("autonomous snapshot contains an unsafe source file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    source_descriptor = os.open(source, flags)
    try:
        source_status = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_status.st_mode):
            raise RuntimeError("autonomous snapshot contains an unsafe source file")
        if source_status.st_size > _MAX_SNAPSHOT_FILE_BYTES:
            raise ValueError("autonomous snapshot file exceeds its size bound")
        _owner_directory(destination.parent)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with (
                os.fdopen(source_descriptor, "rb", closefd=False) as source_stream,
                os.fdopen(descriptor, "wb") as target,
            ):
                remaining = source_status.st_size
                while remaining:
                    block = source_stream.read(min(remaining, 1024 * 1024))
                    if not block:
                        raise RuntimeError("autonomous snapshot source changed during copy")
                    target.write(block)
                    remaining -= len(block)
                if source_stream.read(1):
                    raise RuntimeError("autonomous snapshot source changed during copy")
                target.flush()
                os.fsync(target.fileno())
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    finally:
        os.close(source_descriptor)
    return source_status.st_size


def _copy_snapshot_tree(
    source: Path,
    destination: Path,
    *,
    max_entries: int = _MAX_SNAPSHOT_FILES,
    max_bytes: int = _MAX_SNAPSHOT_TOTAL_BYTES,
) -> tuple[int, int]:
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError("autonomous snapshot contains an unsafe source directory")
    if not 0 <= max_entries <= _MAX_SNAPSHOT_FILES:
        raise ValueError("autonomous snapshot remaining entry budget is invalid")
    if not 0 <= max_bytes <= _MAX_SNAPSHOT_TOTAL_BYTES:
        raise ValueError("autonomous snapshot remaining byte budget is invalid")
    _owner_directory(destination)
    paths: list[Path] = []
    discovered_bytes = 0
    for path in source.rglob("*"):
        paths.append(path)
        if len(paths) > max_entries:
            raise ValueError("autonomous snapshot exceeds its file-count bound")
        if path.is_symlink():
            raise RuntimeError("autonomous snapshot source contains a symbolic link")
        if path.is_file():
            discovered_bytes += path.stat().st_size
            if discovered_bytes > max_bytes:
                raise ValueError("autonomous snapshot exceeds its total-byte bound")
        elif not path.is_dir():
            raise RuntimeError("autonomous snapshot source contains an unsafe entry")
    copied_bytes = 0
    for path in sorted(paths, key=lambda item: item.as_posix()):
        target = destination / path.relative_to(source)
        if path.is_dir():
            _owner_directory(target)
        elif path.is_file():
            copied_bytes += _copy_snapshot_file(path, target)
            if copied_bytes > max_bytes:
                raise ValueError("autonomous snapshot exceeds its total-byte bound")
        else:
            raise RuntimeError("autonomous snapshot source contains an unsafe entry")
    return len(paths), copied_bytes


def _autonomous_snapshot_inventory(root: Path) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    total_bytes = 0
    paths: list[Path] = []
    for path in root.rglob("*"):
        paths.append(path)
        if len(paths) > _MAX_SNAPSHOT_FILES:
            raise ValueError("autonomous snapshot inventory exceeds its entry-count bound")
    for path in sorted(paths, key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise RuntimeError("autonomous snapshot contains a symbolic link")
        if not path.is_file():
            continue
        byte_size = path.stat().st_size
        total_bytes += byte_size
        if total_bytes > _MAX_SNAPSHOT_TOTAL_BYTES:
            raise ValueError("autonomous snapshot inventory exceeds its total-byte bound")
        inventory.append(
            {
                "path": path.relative_to(root).as_posix(),
                "byte_size": byte_size,
                "sha256": sha256_file(path),
            }
        )
    return inventory


def _remove_snapshot_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def create_autonomous_snapshot(
    path: str | Path,
    output: str | Path,
    *,
    include_operator_state: bool = True,
) -> dict[str, Any]:
    root = Path(path).expanduser().absolute()
    destination = Path(output).expanduser().absolute()
    if destination == root or root in destination.parents:
        raise ValueError("autonomous snapshot output must be outside the Vault")
    if destination.is_symlink() or destination.exists():
        raise FileExistsError("autonomous snapshot output must be a new directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / f".{destination.name}.{secrets.token_hex(8)}.tmp"
    if stage.exists() or stage.is_symlink():
        raise FileExistsError("autonomous snapshot staging path already exists")
    _owner_directory(stage)
    copied_root = _owner_directory(stage / "vault")
    try:
        with (
            KnowledgeVault(root, read_only=True) as legacy,
            AutonomousKnowledgeStore(
                root,
                read_only=True,
            ) as store,
        ):
            legacy_integrity = legacy.verify_integrity()
            autonomous_integrity = store.verify()
            if not legacy_integrity["valid"] or not autonomous_integrity["valid"]:
                raise RuntimeError("autonomous snapshot requires a healthy canonical Vault")
            if legacy.audit_head != store.legacy_audit_head:
                raise RuntimeError("autonomous snapshot read planes are not transaction-consistent")
            copied_bytes = _copy_snapshot_file(root / "vault.json", copied_root / "vault.json")
            autonomous_manifest = root / ".deeplaw" / "manifest.json"
            if autonomous_manifest.is_file() and not autonomous_manifest.is_symlink():
                copied_bytes += _copy_snapshot_file(
                    autonomous_manifest,
                    copied_root / ".deeplaw" / "manifest.json",
                )
            copied_entries = 0
            snapshot_directories = _SNAPSHOT_CANONICAL_DIRECTORIES + (
                _SNAPSHOT_OPERATOR_DIRECTORIES if include_operator_state else ()
            )
            for relative in snapshot_directories:
                source = root / relative
                if source.exists():
                    entry_count, byte_count = _copy_snapshot_tree(
                        source,
                        copied_root / relative,
                        max_entries=_MAX_SNAPSHOT_FILES - copied_entries,
                        max_bytes=_MAX_SNAPSHOT_TOTAL_BYTES - copied_bytes,
                    )
                    copied_entries += entry_count
                    copied_bytes += byte_count
            source_database_size = (root / "vault.sqlite3").stat().st_size
            if copied_bytes + source_database_size > _MAX_SNAPSHOT_TOTAL_BYTES:
                raise ValueError("autonomous snapshot exceeds its total-byte bound")
            destination_database = sqlite3.connect(copied_root / "vault.sqlite3")
            try:
                store.connection.backup(destination_database)
                journal_mode = destination_database.execute(
                    "PRAGMA journal_mode = DELETE"
                ).fetchone()
                if journal_mode is None or journal_mode[0].lower() != "delete":
                    raise RuntimeError(
                        "autonomous snapshot database could not enter DELETE journal mode"
                    )
                destination_database.execute("DELETE FROM autonomous_search_v3")
                destination_database.commit()
            finally:
                destination_database.close()
            if (
                copied_bytes + (copied_root / "vault.sqlite3").stat().st_size
                > _MAX_SNAPSHOT_TOTAL_BYTES
            ):
                raise ValueError("autonomous snapshot exceeds its total-byte bound")
            if os.name != "nt":
                os.chmod(copied_root / "vault.sqlite3", 0o600)
            snapshot_identity = {
                "vault_id": store.vault_id,
                "legacy_revision": legacy.revision,
                "legacy_audit_head": legacy.audit_head,
                "autonomous_sequence": store.sequence,
                "autonomous_audit_head": store.audit_head,
            }
        inventory = _autonomous_snapshot_inventory(copied_root)
        body = {
            "schema_version": AUTONOMOUS_SNAPSHOT_SCHEMA,
            **snapshot_identity,
            "created_at": utc_now(),
            "operator_state_included": include_operator_state,
            "derived_layers_included": False,
            "derived_layers_rebuild_required": True,
            "file_count": len(inventory),
            "inventory": inventory,
            "inventory_sha256": sha256_bytes(canonical_json(inventory).encode("utf-8")),
        }
        manifest = {
            **body,
            "snapshot_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
        }
        _atomic_owner_write(
            stage / "snapshot.json",
            (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        os.replace(stage, destination)
    except BaseException:
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        raise
    verification = verify_autonomous_snapshot(destination)
    if not verification["valid"]:
        _remove_snapshot_path(destination)
        raise RuntimeError("created autonomous snapshot failed self-verification")
    return {**manifest, "path": str(destination), "valid": True}


def verify_autonomous_snapshot(
    snapshot: str | Path,
    *,
    expected_vault_id: str | None = None,
) -> dict[str, Any]:
    root = Path(snapshot).expanduser().absolute()
    manifest_path = root / "snapshot.json"
    copied_root = root / "vault"
    manifest: dict[str, Any] = {}
    errors: list[str] = []
    try:
        if root.is_symlink() or not root.is_dir():
            raise RuntimeError("autonomous snapshot root is missing or unsafe")
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise RuntimeError("autonomous snapshot manifest is missing or unsafe")
        if manifest_path.stat().st_size > _MAX_SNAPSHOT_MANIFEST_BYTES:
            raise RuntimeError("autonomous snapshot manifest exceeds its size bound")
        value = strict_json_loads(manifest_path.read_bytes())
        if not isinstance(value, dict):
            raise RuntimeError("autonomous snapshot manifest must be an object")
        manifest = value
        expected_fields = {
            "schema_version",
            "vault_id",
            "legacy_revision",
            "legacy_audit_head",
            "autonomous_sequence",
            "autonomous_audit_head",
            "created_at",
            "operator_state_included",
            "derived_layers_included",
            "derived_layers_rebuild_required",
            "file_count",
            "inventory",
            "inventory_sha256",
            "snapshot_sha256",
        }
        if set(value) != expected_fields or value["schema_version"] != AUTONOMOUS_SNAPSHOT_SCHEMA:
            raise RuntimeError("autonomous snapshot manifest contract is invalid")
        body = {key: value[key] for key in expected_fields - {"snapshot_sha256"}}
        inventory = value["inventory"]
        if not isinstance(inventory, list) or any(
            not isinstance(item, dict)
            or set(item) != {"path", "byte_size", "sha256"}
            or not isinstance(item["path"], str)
            or not isinstance(item["byte_size"], int)
            or isinstance(item["byte_size"], bool)
            or item["byte_size"] < 0
            or not isinstance(item["sha256"], str)
            or not _SHA256.fullmatch(item["sha256"])
            for item in inventory
        ):
            raise RuntimeError("autonomous snapshot inventory contract is invalid")
        allowed_exact_paths = {
            "vault.json",
            "vault.sqlite3",
            ".deeplaw/manifest.json",
        }
        allowed_directories = _SNAPSHOT_CANONICAL_DIRECTORIES + (
            _SNAPSHOT_OPERATOR_DIRECTORIES if value["operator_state_included"] is True else ()
        )
        allowed_prefixes = tuple(f"{directory}/" for directory in allowed_directories)
        if any(
            item["path"] not in allowed_exact_paths
            and not item["path"].startswith(allowed_prefixes)
            for item in inventory
        ):
            raise RuntimeError("autonomous snapshot contains an undeclared storage plane")
        if (
            (expected_vault_id is not None and value["vault_id"] != expected_vault_id)
            or not isinstance(value["legacy_revision"], int)
            or isinstance(value["legacy_revision"], bool)
            or value["legacy_revision"] < 0
            or not isinstance(value["autonomous_sequence"], int)
            or isinstance(value["autonomous_sequence"], bool)
            or value["autonomous_sequence"] < 0
            or value["derived_layers_included"] is not False
            or value["derived_layers_rebuild_required"] is not True
            or not isinstance(value["operator_state_included"], bool)
            or canonical_timestamp(
                value["created_at"],
                field="autonomous snapshot created_at",
            )
            != value["created_at"]
            or not isinstance(value["file_count"], int)
            or isinstance(value["file_count"], bool)
            or value["file_count"] < 0
            or value["file_count"] != len(value["inventory"])
            or value["file_count"] > _MAX_SNAPSHOT_FILES
            or value["inventory_sha256"]
            != sha256_bytes(canonical_json(value["inventory"]).encode("utf-8"))
            or value["snapshot_sha256"] != sha256_bytes(canonical_json(body).encode("utf-8"))
            or _autonomous_snapshot_inventory(copied_root) != value["inventory"]
        ):
            raise RuntimeError("autonomous snapshot inventory or digest is invalid")
        with (
            KnowledgeVault(copied_root, read_only=True) as legacy,
            AutonomousKnowledgeStore(
                copied_root,
                read_only=True,
            ) as store,
        ):
            if (
                legacy.vault_id != value["vault_id"]
                or legacy.revision != value["legacy_revision"]
                or legacy.audit_head != value["legacy_audit_head"]
                or store.sequence != value["autonomous_sequence"]
                or store.audit_head != value["autonomous_audit_head"]
                or not legacy.verify_integrity()["valid"]
                or not store.verify()["valid"]
            ):
                raise RuntimeError("autonomous snapshot canonical state verification failed")
    except (
        KeyError,
        OSError,
        RuntimeError,
        sqlite3.DatabaseError,
        TypeError,
        ValueError,
    ) as error:
        errors.append(str(error))
    return {
        "schema_version": "deeplaw.autonomous-snapshot-verification/v1",
        "path": str(root),
        "vault_id": manifest.get("vault_id"),
        "autonomous_audit_head": manifest.get("autonomous_audit_head"),
        "errors": errors,
        "valid": not errors,
    }


def restore_autonomous_snapshot(
    destination: str | Path,
    *,
    snapshot: str | Path,
    confirm: bool,
) -> dict[str, Any]:
    if not confirm:
        raise ValueError("autonomous snapshot restore requires explicit confirmation")
    target = Path(destination).expanduser().absolute()
    snapshot_root = Path(snapshot).expanduser().absolute()
    if (
        target == snapshot_root
        or target in snapshot_root.parents
        or snapshot_root in target.parents
    ):
        raise ValueError("autonomous snapshot and restore destination must not overlap")
    verification = verify_autonomous_snapshot(snapshot_root)
    if not verification["valid"]:
        raise RuntimeError("autonomous snapshot restore requires a valid snapshot")
    vault_id = verification["vault_id"]
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise RuntimeError("autonomous snapshot restore destination is unsafe")
    target_identity: tuple[int, int] | None = None
    if target.exists():
        target_status = target.stat()
        target_identity = (target_status.st_dev, target_status.st_ino)
        with KnowledgeVault(target, read_only=True) as current:
            if current.vault_id != vault_id:
                raise RuntimeError("snapshot Vault identity does not match the restore target")
    target.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(6)
    stage = target.with_name(f".{target.name}.autonomous-restore-{token}.tmp")
    retained = target.with_name(f"{target.name}.pre-autonomous-restore-{token}")
    if stage.exists() or stage.is_symlink() or retained.exists() or retained.is_symlink():
        raise FileExistsError("autonomous snapshot restore staging path already exists")
    target_preexisting = target_identity is not None
    _copy_snapshot_tree(snapshot_root / "vault", stage)
    unchanged = verify_autonomous_snapshot(
        snapshot_root,
        expected_vault_id=cast(str, vault_id),
    )
    if not unchanged["valid"]:
        shutil.rmtree(stage)
        raise RuntimeError("autonomous snapshot changed during restore")
    try:
        with (
            KnowledgeVault(stage, read_only=True) as legacy,
            AutonomousKnowledgeStore(
                stage,
                read_only=True,
            ) as restored,
        ):
            if (
                legacy.vault_id != vault_id
                or restored.vault_id != vault_id
                or not legacy.verify_integrity()["valid"]
                or not restored.verify()["valid"]
            ):
                raise RuntimeError("restored autonomous Vault failed pre-swap verification")
    except BaseException:
        _remove_snapshot_path(stage)
        raise
    target_swapped = False
    try:
        if target_preexisting:
            if target.is_symlink() or not target.is_dir():
                raise RuntimeError("autonomous snapshot restore target changed before swap")
            current_status = target.stat()
            if (current_status.st_dev, current_status.st_ino) != target_identity:
                raise RuntimeError("autonomous snapshot restore target changed before swap")
            os.replace(target, retained)
        elif target.exists() or target.is_symlink():
            raise RuntimeError("autonomous snapshot restore target changed before swap")
        try:
            os.replace(stage, target)
            target_swapped = True
        except BaseException:
            if retained.exists():
                os.replace(retained, target)
            raise
        with AutonomousKnowledgeStore(target, read_only=True) as restored:
            post = restored.verify()
            if not post["valid"]:
                raise RuntimeError("restored autonomous Vault failed verification")
            sequence = restored.sequence
            audit_head = restored.audit_head
    except BaseException:
        if retained.exists():
            failed = target.with_name(f".{target.name}.failed-autonomous-restore-{token}.tmp")
            if target.exists() and not target.is_symlink():
                os.replace(target, failed)
            os.replace(retained, target)
            if failed.exists() and not failed.is_symlink():
                shutil.rmtree(failed)
        elif target_swapped:
            _remove_snapshot_path(target)
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        raise
    return {
        "schema_version": "deeplaw.autonomous-snapshot-restore/v1",
        "vault_id": vault_id,
        "snapshot_path": str(snapshot_root),
        "destination": str(target),
        "retained_previous_vault": str(retained) if retained.exists() else None,
        "sequence": sequence,
        "audit_head": audit_head,
        "derived_rebuild_required": True,
        "restored": True,
        "valid": True,
    }


class AutonomousKnowledgeStore(AbstractContextManager["AutonomousKnowledgeStore"]):
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        read_only: bool = True,
    ) -> None:
        self.root = (
            Path(path).expanduser().absolute() if path is not None else default_knowledge_vault()
        )
        with KnowledgeVault(self.root, read_only=True) as vault:
            self.vault_id = vault.vault_id
            opened_legacy_audit_head = vault.audit_head
            manifest_scope = vault.manifest.get("scope")
            self.vault_scope: Scope = cast(
                Scope,
                manifest_scope if manifest_scope in SCOPES else "project",
            )
        self.database = _database_path(self.root)
        if read_only:
            self.connection = sqlite3.connect(
                f"{self.database.as_uri()}?mode=ro",
                uri=True,
            )
            self.connection.execute("PRAGMA query_only = ON")
        else:
            self.connection = sqlite3.connect(self.database)
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = FULL")
        self.read_only = read_only
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        if read_only:
            self.connection.execute("BEGIN")
        row = self.connection.execute("SELECT schema_version FROM autonomous_core_v3").fetchone()
        if row is None or row["schema_version"] != AUTONOMOUS_CORE_SCHEMA:
            self.connection.close()
            raise RuntimeError("autonomous knowledge core is not initialized")
        metadata = {
            item["key"]: item["value"]
            for item in self.connection.execute("SELECT key, value FROM autonomous_metadata_v3")
        }
        if metadata.get("vault_id") != self.vault_id:
            self.connection.close()
            raise RuntimeError("autonomous knowledge core Vault identity mismatch")
        if self.legacy_audit_head != opened_legacy_audit_head:
            self.connection.close()
            raise RuntimeError("knowledge read planes changed while opening a consistent snapshot")
        self.evidence_sync: dict[str, Any] | None = None
        self.recovery_sync: dict[str, Any] | None = None
        if not read_only:
            try:
                self.evidence_sync = self.import_legacy_evidence()
                self.recovery_sync = self.recover()
            except BaseException:
                self.connection.close()
                raise

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @property
    def audit_head(self) -> str:
        row = self.connection.execute(
            "SELECT value FROM autonomous_metadata_v3 WHERE key = 'audit_head'"
        ).fetchone()
        if row is None or not _SHA256.fullmatch(row["value"]):
            raise RuntimeError("autonomous audit head is invalid")
        return cast(str, row["value"])

    @property
    def sequence(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM autonomous_metadata_v3 WHERE key = 'sequence'"
        ).fetchone()
        try:
            value = int(row["value"] if row is not None else "")
        except (TypeError, ValueError) as error:
            raise RuntimeError("autonomous audit sequence is invalid") from error
        if value < 0:
            raise RuntimeError("autonomous audit sequence is invalid")
        return value

    @property
    def legacy_audit_head(self) -> str:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = 'audit_head'"
        ).fetchone()
        if row is None or not _SHA256.fullmatch(row["value"]):
            raise RuntimeError("legacy evidence audit head is invalid")
        return cast(str, row["value"])

    def _require_write(self) -> None:
        if self.read_only:
            raise RuntimeError("autonomous knowledge store is open read-only")

    def _next_transaction_time(self, *priors: str) -> str:
        timestamp = utc_now()
        row = self.connection.execute(
            "SELECT recorded_at FROM autonomous_events_v3 ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        candidates = [*priors]
        if row is not None:
            candidates.append(row["recorded_at"])
        for prior in candidates:
            canonical_prior = canonical_timestamp(
                prior,
                field="prior transaction timestamp",
            )
            if timestamp < canonical_prior:
                timestamp = canonical_prior
        return timestamp

    def _append_event(
        self,
        *,
        event_type: str,
        object_id: str | None,
        payload: dict[str, Any],
        recorded_at: str | None = None,
    ) -> tuple[int, str]:
        self._require_write()
        if event_type not in AUTONOMOUS_EVENT_TYPES:
            raise ValueError("unsupported autonomous event type")
        if object_id is None:
            raise ValueError("autonomous object event requires an object identity")
        if event_type in AUTONOMOUS_UNIQUE_OBJECT_EVENT_TYPES and self.connection.execute(
            "SELECT 1 FROM autonomous_events_v3 WHERE event_type = ? AND object_id = ?",
            (event_type, object_id),
        ).fetchone() is not None:
            raise RuntimeError("autonomous object event already exists")
        timestamp = recorded_at or self._next_transaction_time()
        if canonical_timestamp(timestamp, field="event recorded_at") != timestamp:
            raise ValueError("autonomous event timestamp is not canonical")
        prior_event = self.connection.execute(
            "SELECT recorded_at FROM autonomous_events_v3 ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if prior_event is not None and timestamp < prior_event["recorded_at"]:
            raise RuntimeError("autonomous event transaction time moved backwards")
        sequence = self.sequence + 1
        previous_hash = self.audit_head
        event = {
            "schema_version": AUTONOMOUS_EVENT_SCHEMA,
            "sequence": sequence,
            "event_type": event_type,
            "object_id": object_id,
            "payload": payload,
            "previous_hash": previous_hash,
            "recorded_at": timestamp,
        }
        event_hash = sha256_bytes(canonical_json(event).encode("utf-8"))
        self.connection.execute(
            "INSERT INTO autonomous_events_v3 VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                AUTONOMOUS_EVENT_SCHEMA,
                event_type,
                object_id,
                canonical_json(payload),
                previous_hash,
                event_hash,
                timestamp,
            ),
        )
        self.connection.execute(
            "UPDATE autonomous_metadata_v3 SET value = ? WHERE key = 'sequence'",
            (str(sequence),),
        )
        self.connection.execute(
            "UPDATE autonomous_metadata_v3 SET value = ? WHERE key = 'audit_head'",
            (event_hash,),
        )
        return sequence, event_hash

    def import_legacy_evidence(self) -> dict[str, Any]:
        self._require_write()
        source_count = self.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        rows = self.connection.execute(
            """
            SELECT sources.source_id, sources.kind, sources.stored_name,
                   sources.content_sha256, sources.byte_size, sources.media_type,
                   sources.trust, sources.sensitivity, source_lifecycle.status,
                   source_revision_bindings_v2.source_revision_id
            FROM sources
            JOIN source_lifecycle USING(source_id)
            LEFT JOIN source_revision_bindings_v2
              ON source_revision_bindings_v2.legacy_source_id = sources.source_id
            WHERE NOT EXISTS (
                SELECT 1 FROM evidence_bindings_v3
                WHERE evidence_bindings_v3.legacy_source_id = sources.source_id
            )
            ORDER BY sources.source_id
            """
        ).fetchall()
        imported = 0
        for row in rows:
            source = self.root / "sources" / row["stored_name"]
            if source.is_symlink() or not source.is_file():
                raise RuntimeError("legacy evidence source is missing or unsafe")
            payload = source.read_bytes()
            digest, _ = _write_object(self.root, payload)
            if digest != row["content_sha256"] or len(payload) != row["byte_size"]:
                raise RuntimeError("legacy evidence bytes do not match their Ledger identity")
            recorded_at = self._next_transaction_time()
            origin = (
                "external_import"
                if row["kind"] in {"conversation", "tool_result", "web", "database", "package"}
                else "user_source"
            )
            lifecycle = {
                "active": "active",
                "pending": "active",
                "superseded": "superseded",
                "removed": "forgotten",
            }.get(row["status"], "revoked")
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                _register_content_object(
                    self.connection,
                    digest=digest,
                    object_role="evidence",
                    byte_size=len(payload),
                    media_type=row["media_type"],
                    created_at=recorded_at,
                )
                binding_id = stable_id(
                    "evidence",
                    self.vault_id,
                    row["source_revision_id"] or row["source_id"],
                    digest,
                )
                before = self.connection.total_changes
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO evidence_bindings_v3 VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        binding_id,
                        row["source_id"],
                        row["source_revision_id"],
                        digest,
                        origin,
                        row["trust"],
                        "unverified",
                        self.vault_scope,
                        row["sensitivity"],
                        lifecycle,
                        recorded_at,
                    ),
                )
                if self.connection.total_changes > before:
                    self._append_event(
                        event_type="evidence_object_bound",
                        object_id=binding_id,
                        payload={
                            "legacy_source_id": row["source_id"],
                            "source_revision_id": row["source_revision_id"],
                            "object_sha256": digest,
                            "origin": origin,
                            "authority": row["trust"],
                            "verification": "unverified",
                            "scope": self.vault_scope,
                            "sensitivity": row["sensitivity"],
                            "lifecycle": lifecycle,
                        },
                        recorded_at=recorded_at,
                    )
                    imported += 1
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise
        return {"source_count": source_count, "new_binding_count": imported}

    def enable_grant(
        self,
        *,
        writer_id: str,
        allowed_scope: Scope = "project",
        max_sensitivity: Sensitivity = "private",
        operations: tuple[str, ...] = ("remember",),
        evaluator_types: tuple[str, ...] = ("agent_self_report",),
        max_request_bytes: int = 64 * 1024,
        max_mutations_per_minute: int = 60,
        max_objects: int = 100_000,
    ) -> dict[str, Any]:
        self._require_write()
        writer_id = _bounded_string(writer_id, field="writer ID", maximum=200)
        if allowed_scope not in SCOPES:
            raise ValueError("grant scope is invalid")
        if max_sensitivity not in SENSITIVITIES:
            raise ValueError("grant sensitivity is invalid")
        selected_operations = tuple(sorted(set(operations)))
        if not selected_operations or any(
            item not in SINK_OPERATIONS for item in selected_operations
        ):
            raise ValueError("grant operations are invalid")
        selected_evaluator_types = tuple(sorted(set(evaluator_types)))
        if not selected_evaluator_types or any(
            item not in FEEDBACK_EVALUATOR_TYPES for item in selected_evaluator_types
        ):
            raise ValueError("grant feedback evaluator types are invalid")
        if not 1_024 <= max_request_bytes <= _MAX_REQUEST_BYTES:
            raise ValueError("grant request byte limit is invalid")
        if not 1 <= max_mutations_per_minute <= _MAX_GRANT_OPERATIONS_PER_MINUTE:
            raise ValueError("grant rate limit is invalid")
        if not 1 <= max_objects <= _MAX_OBJECTS:
            raise ValueError("grant object capacity is invalid")
        grant_count = self.connection.execute(
            "SELECT COUNT(*) FROM knowledge_sink_grants_v3"
        ).fetchone()[0]
        if grant_count >= _MAX_GRANTS:
            raise RuntimeError("knowledge sink grant history exceeds its capacity")
        token = secrets.token_urlsafe(48)
        token_sha256 = sha256_bytes(token.encode("utf-8"))
        created_at = self._next_transaction_time()
        grant_id = stable_id(
            "grant",
            self.vault_id,
            writer_id,
            token_sha256,
            created_at,
        )
        token_path = self.root / ".deeplaw" / "capabilities" / f"{grant_id}.token"
        _atomic_owner_write(token_path, (token + "\n").encode("utf-8"))
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            locked_grant_count = self.connection.execute(
                "SELECT COUNT(*) FROM knowledge_sink_grants_v3"
            ).fetchone()[0]
            if locked_grant_count >= _MAX_GRANTS:
                raise RuntimeError("knowledge sink grant history exceeds its capacity")
            self.connection.execute(
                """
                INSERT INTO knowledge_sink_grants_v3(
                    grant_id, writer_id, allowed_scope, max_sensitivity,
                    operations_json, evaluator_types_json, token_sha256,
                    max_request_bytes, max_mutations_per_minute, max_objects,
                    created_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant_id,
                    writer_id,
                    allowed_scope,
                    max_sensitivity,
                    canonical_json(selected_operations),
                    canonical_json(selected_evaluator_types),
                    token_sha256,
                    max_request_bytes,
                    max_mutations_per_minute,
                    max_objects,
                    created_at,
                    None,
                ),
            )
            self._append_event(
                event_type="knowledge_sink_grant_enabled",
                object_id=grant_id,
                payload={
                    "writer_id": writer_id,
                    "allowed_scope": allowed_scope,
                    "max_sensitivity": max_sensitivity,
                    "operations": list(selected_operations),
                    "evaluator_types": list(selected_evaluator_types),
                    "max_request_bytes": max_request_bytes,
                    "max_mutations_per_minute": max_mutations_per_minute,
                    "max_objects": max_objects,
                    "token_sha256": token_sha256,
                },
                recorded_at=created_at,
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            token_path.unlink(missing_ok=True)
            raise
        return {
            "schema_version": KNOWLEDGE_SINK_SCHEMA,
            "grant_id": grant_id,
            "writer_id": writer_id,
            "allowed_scope": allowed_scope,
            "max_sensitivity": max_sensitivity,
            "operations": list(selected_operations),
            "evaluator_types": list(selected_evaluator_types),
            "token_path": str(token_path),
            "created_at": created_at,
            "revoked": False,
        }

    def disable_grant(self, grant_id: str) -> dict[str, Any]:
        self._require_write()
        if not _GRANT_ID.fullmatch(grant_id):
            raise ValueError("grant ID is invalid")
        requested_revoked_at = self._next_transaction_time()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT revoked_at FROM knowledge_sink_grants_v3 WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"knowledge sink grant is unavailable: {grant_id}")
            if row["revoked_at"] is None:
                revoked_at = requested_revoked_at
                self.connection.execute(
                    "UPDATE knowledge_sink_grants_v3 SET revoked_at = ? WHERE grant_id = ?",
                    (revoked_at, grant_id),
                )
                self._append_event(
                    event_type="knowledge_sink_grant_revoked",
                    object_id=grant_id,
                    payload={"revoked_at": revoked_at},
                    recorded_at=revoked_at,
                )
            else:
                revoked_at = row["revoked_at"]
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        (self.root / ".deeplaw" / "capabilities" / f"{grant_id}.token").unlink(missing_ok=True)
        return {"grant_id": grant_id, "revoked_at": revoked_at}

    def _grant(self, grant_id: str, *, operation: str, request_bytes: int) -> sqlite3.Row:
        if not _GRANT_ID.fullmatch(grant_id):
            raise ValueError("grant ID is invalid")
        row = self.connection.execute(
            "SELECT * FROM knowledge_sink_grants_v3 WHERE grant_id = ?",
            (grant_id,),
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise PermissionError("knowledge sink grant is unavailable or revoked")
        token_path = self.root / ".deeplaw" / "capabilities" / f"{grant_id}.token"
        if token_path.is_symlink() or not token_path.is_file() or token_path.stat().st_size > 512:
            raise PermissionError("knowledge sink capability token is unavailable")
        if os.name != "nt" and stat.S_IMODE(token_path.stat().st_mode) & 0o077:
            raise PermissionError("knowledge sink capability token is not owner-only")
        token = token_path.read_text(encoding="utf-8").strip()
        if sha256_bytes(token.encode("utf-8")) != row["token_sha256"]:
            raise PermissionError("knowledge sink capability token failed verification")
        operations = strict_json_loads(row["operations_json"])
        if (
            not isinstance(operations, list)
            or not operations
            or operations != sorted(set(operations))
            or any(not isinstance(item, str) or item not in SINK_OPERATIONS for item in operations)
        ):
            raise RuntimeError("knowledge sink grant operation policy is invalid")
        self._grant_evaluator_types(row)
        if operation not in operations:
            raise PermissionError("knowledge sink operation is not granted")
        if request_bytes > row["max_request_bytes"]:
            raise ValueError("knowledge sink request exceeds its grant byte limit")
        return row

    @staticmethod
    def _grant_evaluator_types(grant: sqlite3.Row) -> list[str]:
        evaluator_types = strict_json_loads(grant["evaluator_types_json"])
        if (
            not isinstance(evaluator_types, list)
            or not evaluator_types
            or evaluator_types != sorted(set(evaluator_types))
            or any(
                not isinstance(item, str) or item not in FEEDBACK_EVALUATOR_TYPES
                for item in evaluator_types
            )
        ):
            raise RuntimeError("knowledge sink grant feedback evaluator policy is invalid")
        return evaluator_types

    def grant_status(self, grant_id: str) -> dict[str, Any]:
        """Verify one local capability and return only non-secret grant metadata."""
        if not _GRANT_ID.fullmatch(grant_id):
            raise ValueError("grant ID is invalid")
        row = self.connection.execute(
            "SELECT * FROM knowledge_sink_grants_v3 WHERE grant_id = ?",
            (grant_id,),
        ).fetchone()
        if row is None:
            raise PermissionError("knowledge sink grant is unavailable or revoked")
        operations = strict_json_loads(row["operations_json"])
        if (
            not isinstance(operations, list)
            or not operations
            or any(not isinstance(item, str) or item not in SINK_OPERATIONS for item in operations)
        ):
            raise RuntimeError("knowledge sink grant operation policy is invalid")
        self._grant(grant_id, operation=operations[0], request_bytes=0)
        evaluator_types = self._grant_evaluator_types(row)
        return {
            "grant_id": grant_id,
            "writer_id": row["writer_id"],
            "allowed_scope": row["allowed_scope"],
            "max_sensitivity": row["max_sensitivity"],
            "operations": operations,
            "evaluator_types": evaluator_types,
            "max_request_bytes": row["max_request_bytes"],
            "max_mutations_per_minute": row["max_mutations_per_minute"],
            "max_objects": row["max_objects"],
            "created_at": row["created_at"],
            "revoked": False,
        }

    def _enforce_grant_limits(
        self,
        grant: sqlite3.Row,
        *,
        enforce_object_capacity: bool,
    ) -> None:
        cutoff = (
            (datetime.now(UTC) - timedelta(minutes=1))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        recent = self.connection.execute(
            """
            SELECT COUNT(*) AS count FROM knowledge_sink_usage_v3
            WHERE grant_id = ? AND recorded_at >= ?
            """,
            (grant["grant_id"], cutoff),
        ).fetchone()["count"]
        if recent >= grant["max_mutations_per_minute"]:
            raise RuntimeError("knowledge sink rate limit exceeded")
        if enforce_object_capacity:
            object_count = self.connection.execute(
                "SELECT COUNT(*) AS count FROM knowledge_objects_v3"
            ).fetchone()["count"]
            if object_count >= grant["max_objects"]:
                raise RuntimeError("knowledge sink object capacity exceeded")

    def _idempotent_response(
        self,
        *,
        grant_id: str,
        idempotency_key: str,
        request_sha256: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT request_sha256, response_json FROM mutation_idempotency_v3
            WHERE grant_id = ? AND idempotency_key = ?
            """,
            (grant_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row["request_sha256"] != request_sha256:
            raise ValueError("idempotency key was reused with a different request")
        response = strict_json_loads(row["response_json"])
        if not isinstance(response, dict):
            raise RuntimeError("stored idempotent response is invalid")
        response["idempotent_replay"] = True
        response["audit_head"] = self.audit_head
        return response

    def _source_reference_binding(
        self,
        reference: dict[str, Any],
    ) -> dict[str, Any] | None:
        revision_id = reference.get("revision_id")
        if isinstance(revision_id, str):
            if any(key in reference for key in ("fragment_id", "locator", "uri", "quote_sha256")):
                return None
            row = self.connection.execute(
                """
                SELECT cited.scope, cited.sensitivity, cited.lifecycle,
                       current.lifecycle AS current_lifecycle
                FROM knowledge_revisions_v3 AS cited
                JOIN knowledge_objects_v3
                  ON knowledge_objects_v3.knowledge_id = cited.knowledge_id
                LEFT JOIN knowledge_revisions_v3 AS current
                  ON current.revision_id = knowledge_objects_v3.current_revision_id
                WHERE cited.revision_id = ?
                """,
                (revision_id,),
            ).fetchone()
            if row is None or row["lifecycle"] == "quarantined":
                return None
            return {
                "scope": row["scope"],
                "sensitivity": row["sensitivity"],
                "active": row["lifecycle"] == "active"
                and row["current_lifecycle"] == "active",
            }
        artifact_id = reference.get("artifact_id")
        if isinstance(artifact_id, str):
            if any(key in reference for key in ("fragment_id", "locator", "uri", "quote_sha256")):
                return None
            from .knowledge_inbox import verify_inbox_artifact

            with KnowledgeVault(self.root, read_only=True) as vault:
                verification = verify_inbox_artifact(vault, artifact_id)
            artifact = verification.get("artifact")
            if (
                verification.get("valid") is not True
                or not isinstance(artifact, dict)
                or artifact.get("sensitivity") not in SENSITIVITIES
            ):
                return None
            return {
                "scope": self.vault_scope,
                "sensitivity": cast(str, artifact["sensitivity"]),
                "active": verification.get("state") != "rejected",
            }
        source_revision_id = reference.get("source_revision_id")
        source_id = reference.get("source_id")
        if not isinstance(source_revision_id, str) and not isinstance(source_id, str):
            return None
        if isinstance(source_revision_id, str):
            binding = self.connection.execute(
                """
                SELECT evidence_bindings_v3.legacy_source_id AS source_id,
                       evidence_bindings_v3.scope,
                       evidence_bindings_v3.sensitivity,
                       evidence_bindings_v3.lifecycle,
                       evidence_bindings_v3.origin,
                       evidence_bindings_v3.authority,
                       sources.origin_uri,
                       source_lifecycle.status AS source_status
                FROM evidence_bindings_v3
                LEFT JOIN sources
                  ON sources.source_id = evidence_bindings_v3.legacy_source_id
                LEFT JOIN source_lifecycle
                  ON source_lifecycle.source_id = evidence_bindings_v3.legacy_source_id
                WHERE evidence_bindings_v3.source_revision_id = ?
                ORDER BY evidence_bindings_v3.recorded_at DESC,
                         evidence_bindings_v3.binding_id DESC
                LIMIT 1
                """,
                (source_revision_id,),
            ).fetchone()
        else:
            binding = self.connection.execute(
                """
                SELECT evidence_bindings_v3.legacy_source_id AS source_id,
                       evidence_bindings_v3.scope,
                       evidence_bindings_v3.sensitivity,
                       evidence_bindings_v3.lifecycle,
                       evidence_bindings_v3.origin,
                       evidence_bindings_v3.authority,
                       sources.origin_uri,
                       source_lifecycle.status AS source_status
                FROM evidence_bindings_v3
                LEFT JOIN sources
                  ON sources.source_id = evidence_bindings_v3.legacy_source_id
                LEFT JOIN source_lifecycle
                  ON source_lifecycle.source_id = evidence_bindings_v3.legacy_source_id
                WHERE evidence_bindings_v3.legacy_source_id = ?
                ORDER BY evidence_bindings_v3.recorded_at DESC,
                         evidence_bindings_v3.binding_id DESC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
        if binding is None or (isinstance(source_id, str) and source_id != binding["source_id"]):
            return None
        if reference.get("uri") not in {None, binding["origin_uri"]}:
            return None
        fragment_id = reference.get("fragment_id")
        if fragment_id is None:
            if "locator" in reference or "quote_sha256" in reference:
                return None
            return {
                "scope": binding["scope"],
                "sensitivity": binding["sensitivity"],
                "active": binding["lifecycle"] == "active"
                and (
                    binding["source_id"] is None
                    or binding["source_status"] == "active"
                    or (
                        binding["source_status"] == "pending"
                        and binding["origin"] == "user_source"
                        and binding["authority"] in {"user_provided", "verified_source"}
                    )
                ),
            }
        if not isinstance(fragment_id, str):
            return None
        fragment = self.connection.execute(
            """
            SELECT text_sha256, locator FROM source_fragments
            WHERE source_id = ? AND fragment_id = ?
            """,
            (binding["source_id"], fragment_id),
        ).fetchone()
        if fragment is None:
            return None
        if reference.get("quote_sha256") not in {None, fragment["text_sha256"]} or reference.get(
            "locator"
        ) not in {None, fragment["locator"]}:
            return None
        return {
            "scope": binding["scope"],
            "sensitivity": binding["sensitivity"],
            "active": binding["lifecycle"] == "active"
            and (
                binding["source_id"] is None
                or binding["source_status"] == "active"
                or (
                    binding["source_status"] == "pending"
                    and binding["origin"] == "user_source"
                    and binding["authority"] in {"user_provided", "verified_source"}
                )
            ),
        }

    def _pin_source_references(
        self,
        references: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Resolve legacy source aliases to immutable source revisions before commit."""
        pinned: list[dict[str, str]] = []
        for reference in references:
            selected = dict(reference)
            source_id = selected.get("source_id")
            if source_id is not None and "source_revision_id" not in selected:
                binding = self.connection.execute(
                    """
                    SELECT source_revision_id FROM source_revision_bindings_v2
                    WHERE legacy_source_id = ?
                    """,
                    (source_id,),
                ).fetchone()
                if binding is not None and isinstance(binding["source_revision_id"], str):
                    selected["source_revision_id"] = binding["source_revision_id"]
            pinned.append(selected)
        return _canonical_source_references(
            pinned,
            field="pinned source references",
        )

    def _source_reference_is_bound(
        self,
        reference: dict[str, Any],
        *,
        scope: str | None = None,
        max_sensitivity: str | None = None,
        require_active: bool = True,
    ) -> bool:
        binding = self._source_reference_binding(reference)
        if binding is None:
            return False
        if require_active and binding["active"] is not True:
            return False
        if scope is not None and binding["scope"] != scope:
            return False
        return not (
            max_sensitivity is not None
            and (
                max_sensitivity not in SENSITIVITIES
                or binding["sensitivity"] not in SENSITIVITIES
                or SENSITIVITY_ORDER.index(binding["sensitivity"])
                > SENSITIVITY_ORDER.index(max_sensitivity)
            )
        )

    def revision_provenance_admitted(self, revision: dict[str, Any]) -> bool:
        """Check current source lifecycle without changing immutable revision history."""
        references = revision.get("source_refs", [])
        if revision.get("verification") != "source_bound":
            return True
        return bool(references) and all(
            isinstance(reference, dict)
            and self._source_reference_is_bound(
                reference,
                scope=cast(str | None, revision.get("scope")),
                max_sensitivity=cast(str | None, revision.get("sensitivity")),
            )
            for reference in references
        )

    def relation_provenance_admitted(self, relation: dict[str, Any]) -> bool:
        """Check whether every canonical relation evidence reference remains admissible."""
        references = relation.get("evidence_refs", [])
        return not references or all(
            isinstance(reference, dict)
            and self._source_reference_is_bound(
                reference,
                scope=cast(str | None, relation.get("scope")),
                max_sensitivity=cast(str | None, relation.get("sensitivity")),
            )
            for reference in references
        )

    def remember(
        self,
        *,
        grant_id: str,
        idempotency_key: str,
        title: str,
        body: str,
        kind: KnowledgeKind = "memory",
        knowledge_id: str | None = None,
        expected_revision_id: str | None = None,
        scope: Scope = "project",
        sensitivity: Sensitivity = "private",
        epistemic_state: EpistemicState | None = None,
        source_refs: list[dict[str, Any]] | None = None,
        run_id: str | None = None,
        model_id: str | None = None,
        tool_id: str | None = None,
        generation_activity_id: str | None = None,
        tags: list[str] | None = None,
        semantic_key: str | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        expires_at: str | None = None,
        preference_basis: str | None = None,
        memory_type: str | None = None,
        requested_origin: str = "agent_derived",
        requested_authority: str = "agent_derived",
        confirm_no_case_data: bool = False,
        operation: str = "remember",
        skill_manifest: dict[str, Any] | None = None,
        lifecycle_override: Lifecycle | None = None,
        lifecycle_reason: str | None = None,
        workspace_edit_sha256: str | None = None,
    ) -> dict[str, Any]:
        self._require_write()
        if operation not in SINK_OPERATIONS - {"add_relation", "record_feedback"}:
            raise ValueError("knowledge sink object operation is invalid")
        if not confirm_no_case_data:
            raise ValueError("knowledge sink requires confirmation that no case data is present")
        idempotency_key = _bounded_string(
            idempotency_key,
            field="idempotency key",
            maximum=200,
        )
        title = _bounded_string(title, field="Knowledge title", maximum=_MAX_TITLE_CHARS)
        if (
            not isinstance(body, str)
            or body != body.strip()
            or not body
            or len(body) > _MAX_BODY_CHARS
        ):
            raise ValueError("Knowledge body must be bounded canonical text")
        if kind not in KNOWLEDGE_KINDS:
            raise ValueError("knowledge kind is invalid")
        if kind not in OBJECT_OPERATION_KINDS[operation]:
            raise ValueError("knowledge kind is not permitted for this sink operation")
        if scope not in SCOPES or sensitivity not in SENSITIVITIES:
            raise ValueError("knowledge scope or sensitivity is invalid")
        for field, value in (
            ("run_id", run_id),
            ("model_id", model_id),
            ("tool_id", tool_id),
            ("generation_activity_id", generation_activity_id),
        ):
            if value is not None:
                _bounded_string(value, field=field, maximum=500)
        if kind == "memory":
            memory_type = memory_type or "semantic"
            if memory_type not in {
                "working",
                "episodic",
                "semantic",
                "procedural",
                "reflective",
            }:
                raise ValueError("memory type is invalid")
        elif memory_type is not None:
            raise ValueError("memory type is only valid for memory knowledge")
        if kind == "preference":
            preference_basis = preference_basis or "agent_inference"
            if preference_basis not in {"direct_user_statement", "agent_inference"}:
                raise ValueError("preference basis is invalid")
        elif preference_basis is not None:
            raise ValueError("preference basis is only valid for preference knowledge")
        selected_refs = self._pin_source_references(
            _canonical_source_references(source_refs or [], field="source references")
        )
        selected_tags = _canonical_json_list(tags or [], field="tags", maximum=_MAX_TAGS)
        if len(set(selected_tags)) != len(selected_tags) or any(
            not isinstance(tag, str) or tag != tag.strip() or not 1 <= len(tag) <= _MAX_TAG_CHARS
            for tag in selected_tags
        ):
            raise ValueError("knowledge tags are invalid")
        valid_from = _optional_timestamp(valid_from, field="valid_from")
        valid_to = _optional_timestamp(valid_to, field="valid_to")
        expires_at = _optional_timestamp(expires_at, field="expires_at")
        if valid_from is not None and valid_to is not None and valid_from >= valid_to:
            raise ValueError("valid time interval is invalid")
        if lifecycle_reason is not None:
            lifecycle_reason = _bounded_string(
                lifecycle_reason,
                field="lifecycle reason",
                maximum=2_000,
            )
            if has_instruction_risk(lifecycle_reason):
                raise ValueError("lifecycle reason contains persistent prompt-injection risk")
        if workspace_edit_sha256 is not None and (
            not _SHA256.fullmatch(workspace_edit_sha256) or tool_id != "workspace-watcher"
        ):
            raise ValueError("workspace edit binding is invalid")
        if kind == "skill":
            self._validate_skill_manifest(
                skill_manifest,
                knowledge_id=knowledge_id,
                parent_revision_id=expected_revision_id,
                scope=scope,
                max_sensitivity=sensitivity,
            )
        elif skill_manifest is not None:
            raise ValueError("skill manifest is only valid for Skill knowledge")
        request = {
            "operation": operation,
            "idempotency_key": idempotency_key,
            "title": title,
            "body": body,
            "kind": kind,
            "knowledge_id": knowledge_id,
            "expected_revision_id": expected_revision_id,
            "scope": scope,
            "sensitivity": sensitivity,
            "epistemic_state": epistemic_state,
            "source_refs": selected_refs,
            "run_id": run_id,
            "model_id": model_id,
            "tool_id": tool_id,
            "generation_activity_id": generation_activity_id,
            "tags": selected_tags,
            "semantic_key": semantic_key,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "expires_at": expires_at,
            "preference_basis": preference_basis,
            "memory_type": memory_type,
            "requested_origin": requested_origin,
            "requested_authority": requested_authority,
            "skill_manifest": skill_manifest,
            "lifecycle_override": lifecycle_override,
            "lifecycle_reason": lifecycle_reason,
            "workspace_edit_sha256": workspace_edit_sha256,
        }
        request_bytes = canonical_json(request).encode("utf-8")
        if len(request_bytes) > _MAX_REQUEST_BYTES:
            raise ValueError("knowledge sink request exceeds the global byte limit")
        request_sha256 = sha256_bytes(request_bytes)
        grant = self._grant(grant_id, operation=operation, request_bytes=len(request_bytes))
        existing_response = self._idempotent_response(
            grant_id=grant_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
        )
        if existing_response is not None:
            replay_revision_id = existing_response.get("revision_id")
            if isinstance(replay_revision_id, str) and _REVISION_ID.fullmatch(replay_revision_id):
                self._materialize_pending(replay_revision_id)
                existing_response["audit_head"] = self.audit_head
                self.connection.execute(
                    """
                    UPDATE mutation_idempotency_v3 SET response_json = ?
                    WHERE grant_id = ? AND idempotency_key = ?
                    """,
                    (
                        canonical_json(existing_response),
                        grant_id,
                        idempotency_key,
                    ),
                )
                self.connection.commit()
            return existing_response
        if scope != grant["allowed_scope"]:
            raise PermissionError("knowledge request exceeds its granted scope")
        if SENSITIVITY_ORDER.index(sensitivity) > SENSITIVITY_ORDER.index(grant["max_sensitivity"]):
            raise PermissionError("knowledge request exceeds its granted sensitivity")
        if semantic_key is not None:
            semantic_key = _bounded_string(
                semantic_key,
                field="semantic key",
                maximum=300,
            )
        source_bindings_valid = bool(selected_refs) and all(
            self._source_reference_is_bound(
                reference,
                scope=scope,
                max_sensitivity=sensitivity,
                require_active=lifecycle_override is None,
            )
            for reference in selected_refs
        )
        source_free = not selected_refs and run_id is None
        verification = (
            "source_bound"
            if source_bindings_valid
            else "run_bound"
            if run_id and not selected_refs
            else "unverified"
        )
        selected_epistemic = epistemic_state or ("tentative" if source_free else "supported")
        if selected_epistemic not in EPISTEMIC_STATES:
            raise ValueError("epistemic state is invalid")
        semantic_digest = sha256_bytes(
            canonical_json(
                {
                    "kind": kind,
                    "title": compact_text(title),
                    "body": compact_text(body),
                    "semantic_key": semantic_key,
                }
            ).encode("utf-8")
        )
        quarantine_reasons: list[str] = []
        if selected_refs and not source_bindings_valid:
            quarantine_reasons.append("unverified_source_binding")
        if preference_basis == "direct_user_statement" and not selected_refs and run_id is None:
            quarantine_reasons.append("unbound_direct_user_statement")
        if requested_origin != "agent_derived" or requested_authority != "agent_derived":
            quarantine_reasons.append("authority_elevation_attempt")
        risk_fields = [
            title,
            body,
            semantic_key or "",
            *(cast(list[str], selected_tags)),
            *(item or "" for item in (run_id, model_id, tool_id, generation_activity_id)),
            canonical_json(selected_refs),
        ]
        if skill_manifest is not None:
            risk_fields.append(canonical_json(skill_manifest))
        if has_instruction_risk("\n".join(risk_fields)):
            quarantine_reasons.append("persistent_prompt_injection_risk")
        if lifecycle_override is not None:
            allowed_override = {
                ("forget", "forgotten"),
                ("expire", "expired"),
            }
            if (operation, lifecycle_override) not in allowed_override:
                raise ValueError("lifecycle override is not allowed for this operation")
            if lifecycle_reason is None:
                raise ValueError("lifecycle override requires a reason")
            lifecycle: Lifecycle = lifecycle_override
        else:
            lifecycle = "quarantined" if quarantine_reasons else "active"
        if knowledge_id is None and lifecycle == "active":
            duplicate = self.connection.execute(
                """
                SELECT knowledge_objects_v3.knowledge_id
                FROM knowledge_objects_v3
                JOIN knowledge_revisions_v3
                  ON knowledge_revisions_v3.revision_id =
                     knowledge_objects_v3.current_revision_id
                WHERE knowledge_revisions_v3.lifecycle = 'active'
                  AND knowledge_revisions_v3.kind = ?
                  AND knowledge_revisions_v3.scope = ?
                  AND knowledge_revisions_v3.sensitivity = ?
                  AND knowledge_revisions_v3.semantic_digest = ?
                ORDER BY knowledge_objects_v3.knowledge_id
                LIMIT 1
                """,
                (kind, scope, sensitivity, semantic_digest),
            ).fetchone()
            if duplicate is not None:
                raise ValueError(
                    f"exact duplicate Knowledge Object already exists: {duplicate['knowledge_id']}"
                )
        recorded_at = self._next_transaction_time()
        observed_at = recorded_at
        current_workspace_path: str | None = None
        if knowledge_id is None:
            knowledge_id = stable_id(
                "knowledge",
                self.vault_id,
                grant_id,
                idempotency_key,
            )
            parent_revision_id = None
            if expected_revision_id is not None:
                raise ValueError("new Knowledge Object cannot declare an expected revision")
        else:
            if not _KNOWLEDGE_ID.fullmatch(knowledge_id):
                raise ValueError("knowledge ID is invalid")
            row = self.connection.execute(
                """
                SELECT knowledge_objects_v3.current_revision_id,
                       knowledge_objects_v3.kind,
                       knowledge_objects_v3.workspace_path AS current_workspace_path,
                       knowledge_revisions_v3.scope AS current_scope,
                       knowledge_revisions_v3.sensitivity AS current_sensitivity
                FROM knowledge_objects_v3
                LEFT JOIN knowledge_revisions_v3
                  ON knowledge_revisions_v3.revision_id =
                     knowledge_objects_v3.current_revision_id
                WHERE knowledge_objects_v3.knowledge_id = ?
                """,
                (knowledge_id,),
            ).fetchone()
            if row is None:
                if expected_revision_id is not None:
                    raise KeyError(f"Knowledge Object is unavailable: {knowledge_id}")
                parent_revision_id = None
            else:
                if row["kind"] != kind:
                    raise ValueError("Knowledge Object kind cannot change across revisions")
                current_workspace_path = _safe_knowledge_workspace_path(
                    row["current_workspace_path"]
                )
                parent_revision_id = row["current_revision_id"]
                if parent_revision_id is None:
                    raise PermissionError(
                        "quarantined Knowledge Object requires an explicit owner restore policy"
                    )
                if expected_revision_id != parent_revision_id:
                    raise RuntimeError("Knowledge Object compare-and-swap conflict")
                if parent_revision_id is not None:
                    current_lifecycle = self.connection.execute(
                        "SELECT lifecycle FROM knowledge_revisions_v3 WHERE revision_id = ?",
                        (parent_revision_id,),
                    ).fetchone()
                    if current_lifecycle is None or current_lifecycle["lifecycle"] != "active":
                        raise PermissionError(
                            "inactive Knowledge Object requires an explicit owner restore policy"
                        )
                    if scope != row["current_scope"]:
                        raise PermissionError(
                            "ordinary Knowledge Sink revisions cannot change scope"
                        )
                    if SENSITIVITY_ORDER.index(sensitivity) < SENSITIVITY_ORDER.index(
                        row["current_sensitivity"]
                    ):
                        raise PermissionError(
                            "ordinary Knowledge Sink revisions cannot lower sensitivity"
                        )
        if parent_revision_id is not None:
            parent_recorded = self.connection.execute(
                "SELECT recorded_at FROM knowledge_revisions_v3 WHERE revision_id = ?",
                (parent_revision_id,),
            ).fetchone()
            if parent_recorded is None:
                raise RuntimeError("Knowledge Object parent revision is unavailable")
            recorded_at = _timestamp_after(recorded_at, parent_recorded["recorded_at"])
        revision_id = stable_id(
            "knowledgerev",
            knowledge_id,
            grant_id,
            idempotency_key,
            request_sha256,
        )
        workspace_path = current_workspace_path or _workspace_path(
            kind=kind,
            knowledge_id=knowledge_id,
            memory_type=memory_type,
        )
        generation = {
            "activity_id": generation_activity_id,
            "run_id": run_id,
            "model_id": model_id,
            "tool_id": tool_id,
        }
        metadata = {
            "quarantine_reasons": quarantine_reasons,
            "memory_type": memory_type,
            "preference_basis": preference_basis,
            "skill_manifest": skill_manifest,
            "lifecycle_reason": lifecycle_reason,
        }
        markdown = render_knowledge_markdown(
            knowledge_id=knowledge_id,
            revision_id=revision_id,
            title=title,
            body=body,
            kind=kind,
            lifecycle=lifecycle,
            epistemic_state=selected_epistemic,
            verification=verification,
            scope=scope,
            sensitivity=sensitivity,
            writer_id=grant["writer_id"],
            source_free=source_free,
            source_refs=selected_refs,
            generation=generation,
            tags=selected_tags,
            semantic_key=semantic_key,
            parent_revision_id=parent_revision_id,
            supersedes_revision_id=parent_revision_id,
            valid_from=valid_from,
            valid_to=valid_to,
            observed_at=observed_at,
            recorded_at=recorded_at,
            expires_at=expires_at,
            preference_basis=preference_basis,
            memory_type=memory_type,
            skill_manifest=skill_manifest,
            quarantine_reasons=quarantine_reasons,
            lifecycle_reason=lifecycle_reason,
        )
        markdown_sha256, _ = _write_object(self.root, markdown)
        workspace_file = self.root / _safe_knowledge_workspace_path(workspace_path)
        if workspace_file.exists() or workspace_file.is_symlink():
            if workspace_file.is_symlink() or not workspace_file.is_file():
                raise RuntimeError("Knowledge workspace target is unsafe")
            if workspace_file.stat().st_size > _MAX_MARKDOWN_BYTES:
                raise ValueError("Knowledge workspace target exceeds its byte limit")
            workspace_payload = workspace_file.read_bytes()
            expected_workspace_sha256: str | None = None
            if parent_revision_id is not None:
                parent_object = self.connection.execute(
                    "SELECT markdown_sha256 FROM knowledge_revisions_v3 WHERE revision_id = ?",
                    (parent_revision_id,),
                ).fetchone()
                if parent_object is None:
                    raise RuntimeError("Knowledge Object parent content is unavailable")
                expected_workspace_sha256 = parent_object["markdown_sha256"]
            workspace_sha256 = sha256_bytes(workspace_payload)
            if workspace_sha256 not in {
                expected_workspace_sha256,
                markdown_sha256,
                workspace_edit_sha256,
            }:
                self._record_workspace_conflict(
                    grant_id=grant_id,
                    writer_id=grant["writer_id"],
                    payload=workspace_payload,
                    workspace_path=workspace_path,
                    reason="unreconciled_workspace_change",
                    knowledge_id=knowledge_id,
                    base_revision_id=parent_revision_id,
                    current_revision_id=parent_revision_id,
                )
                raise RuntimeError(
                    "Knowledge workspace changed outside the Ledger; reconcile before mutation"
                )
        stage_id = stable_id("stage", revision_id, markdown_sha256)
        stage_path = self.root / ".deeplaw" / "staging" / f"{stage_id}.json"
        stage_record = {
            "schema_version": "deeplaw.knowledge-staging/v1",
            "revision_id": revision_id,
            "knowledge_id": knowledge_id,
            "workspace_path": workspace_path,
            "markdown_sha256": markdown_sha256,
            "request_sha256": request_sha256,
            "created_at": recorded_at,
        }
        _atomic_owner_write(
            stage_path,
            (json.dumps(stage_record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
        )
        response = {
            "schema_version": KNOWLEDGE_REVISION_SCHEMA,
            "knowledge_id": knowledge_id,
            "revision_id": revision_id,
            "parent_revision_id": parent_revision_id,
            "markdown_sha256": markdown_sha256,
            "workspace_path": workspace_path,
            "kind": kind,
            "origin": "agent_derived",
            "authority": "agent_derived",
            "legal_authority": False,
            "verification": verification,
            "lifecycle": lifecycle,
            "epistemic_state": selected_epistemic,
            "scope": scope,
            "sensitivity": sensitivity,
            "source_free": source_free,
            "quarantine_reasons": quarantine_reasons,
            "recorded_at": recorded_at,
            "idempotent_replay": False,
            "current_revision_id": (
                parent_revision_id if lifecycle == "quarantined" else revision_id
            ),
        }
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            locked_grant = self._grant(
                grant_id,
                operation=operation,
                request_bytes=len(request_bytes),
            )
            locked_replay = self._idempotent_response(
                grant_id=grant_id,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
            )
            if locked_replay is not None:
                self.connection.rollback()
                stage_path.unlink(missing_ok=True)
                return locked_replay
            current = self.connection.execute(
                "SELECT current_revision_id FROM knowledge_objects_v3 WHERE knowledge_id = ?",
                (knowledge_id,),
            ).fetchone()
            self._enforce_grant_limits(
                locked_grant,
                enforce_object_capacity=current is None,
            )
            if parent_revision_id is None:
                if current is not None and current["current_revision_id"] is not None:
                    raise RuntimeError("Knowledge Object identity collision")
                if current is None:
                    self.connection.execute(
                        """
                        INSERT INTO knowledge_objects_v3 VALUES
                        (?, ?, 'agent_derived', 'agent_derived', NULL, ?, ?, ?, ?)
                        """,
                        (
                            knowledge_id,
                            kind,
                            workspace_path,
                            semantic_key,
                            recorded_at,
                            recorded_at,
                        ),
                    )
            elif current is None or current["current_revision_id"] != parent_revision_id:
                raise RuntimeError("Knowledge Object compare-and-swap conflict")
            if lifecycle == "active":
                duplicate = self.connection.execute(
                    """
                    SELECT knowledge_objects_v3.knowledge_id
                    FROM knowledge_objects_v3
                    JOIN knowledge_revisions_v3
                      ON knowledge_revisions_v3.revision_id =
                         knowledge_objects_v3.current_revision_id
                    WHERE knowledge_revisions_v3.lifecycle = 'active'
                      AND knowledge_revisions_v3.kind = ?
                      AND knowledge_revisions_v3.scope = ?
                      AND knowledge_revisions_v3.sensitivity = ?
                      AND knowledge_revisions_v3.semantic_digest = ?
                      AND knowledge_objects_v3.knowledge_id <> ?
                    ORDER BY knowledge_objects_v3.knowledge_id
                    LIMIT 1
                    """,
                    (kind, scope, sensitivity, semantic_digest, knowledge_id),
                ).fetchone()
                if duplicate is not None:
                    raise ValueError(
                        "exact duplicate Knowledge Object already exists: "
                        f"{duplicate['knowledge_id']}"
                    )
            _register_content_object(
                self.connection,
                digest=markdown_sha256,
                object_role="knowledge_revision",
                byte_size=len(markdown),
                media_type="text/markdown; charset=utf-8",
                created_at=recorded_at,
            )
            self.connection.execute(
                """
                INSERT INTO knowledge_revisions_v3 (
                    revision_id, knowledge_id, parent_revision_id,
                    supersedes_revision_id, markdown_sha256, semantic_digest,
                    title, semantic_key, kind, lifecycle, epistemic_state,
                    origin, authority, verification, scope, sensitivity,
                    writer_id, source_free, source_refs_json, generation_json,
                    tags_json, metadata_json, valid_from, valid_to, observed_at,
                    recorded_at, expires_at, workspace_path
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    revision_id,
                    knowledge_id,
                    parent_revision_id,
                    parent_revision_id,
                    markdown_sha256,
                    semantic_digest,
                    title,
                    semantic_key,
                    kind,
                    lifecycle,
                    selected_epistemic,
                    "agent_derived",
                    "agent_derived",
                    verification,
                    scope,
                    sensitivity,
                    grant["writer_id"],
                    int(source_free),
                    canonical_json(selected_refs),
                    canonical_json(generation),
                    canonical_json(selected_tags),
                    canonical_json(metadata),
                    valid_from,
                    valid_to,
                    observed_at,
                    recorded_at,
                    expires_at,
                    workspace_path,
                ),
            )
            if lifecycle != "quarantined":
                self.connection.execute(
                    """
                    UPDATE knowledge_objects_v3
                    SET current_revision_id = ?, workspace_path = ?,
                        semantic_key = ?, updated_at = ?
                    WHERE knowledge_id = ?
                    """,
                    (revision_id, workspace_path, semantic_key, recorded_at, knowledge_id),
                )
            if lifecycle != "quarantined":
                action = "write" if lifecycle == "active" else "delete"
                self.connection.execute(
                    "INSERT INTO pending_materializations_v3 VALUES (?, ?, ?, ?, ?)",
                    (revision_id, workspace_path, markdown_sha256, action, recorded_at),
                )
            mutation_id = stable_id("mutation", grant_id, idempotency_key, request_sha256)
            self.connection.execute(
                "INSERT INTO knowledge_sink_usage_v3 VALUES (?, ?, ?, ?, ?)",
                (mutation_id, grant_id, operation, request_sha256, recorded_at),
            )
            _, committed_head = self._append_event(
                event_type="knowledge_revision_committed",
                object_id=revision_id,
                payload={
                    "grant_id": grant_id,
                    "idempotency_key_sha256": sha256_bytes(idempotency_key.encode("utf-8")),
                    "request_sha256": request_sha256,
                    "operation": operation,
                    "knowledge_id": knowledge_id,
                    "parent_revision_id": parent_revision_id,
                    "markdown_sha256": markdown_sha256,
                    "lifecycle": lifecycle,
                    "epistemic_state": selected_epistemic,
                    "origin": "agent_derived",
                    "authority": "agent_derived",
                    "writer_id": grant["writer_id"],
                    "scope": scope,
                    "sensitivity": sensitivity,
                    "source_free": source_free,
                    "semantic_digest": semantic_digest,
                    "verification": verification,
                    "source_refs_sha256": sha256_bytes(
                        canonical_json(selected_refs).encode("utf-8")
                    ),
                    "generation_sha256": sha256_bytes(canonical_json(generation).encode("utf-8")),
                    "tags_sha256": sha256_bytes(canonical_json(selected_tags).encode("utf-8")),
                    "metadata_sha256": sha256_bytes(canonical_json(metadata).encode("utf-8")),
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                    "expires_at": expires_at,
                    "workspace_edit_sha256": workspace_edit_sha256,
                },
                recorded_at=recorded_at,
            )
            if lifecycle != "quarantined":
                queue_id = stable_id("rebuild", revision_id, committed_head)
                self.connection.execute(
                    "INSERT INTO derived_rebuild_queue_v3 VALUES (?, ?, ?, ?, NULL)",
                    (queue_id, committed_head, "knowledge_revision_committed", recorded_at),
                )
            response["audit_head"] = self.audit_head
            self.connection.execute(
                """
                INSERT INTO mutation_idempotency_v3 VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant_id,
                    idempotency_key,
                    request_sha256,
                    "knowledge_revision",
                    revision_id,
                    canonical_json(response),
                    recorded_at,
                ),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            stage_path.unlink(missing_ok=True)
            raise
        try:
            self._materialize_pending(revision_id)
        finally:
            stage_path.unlink(missing_ok=True)
        response["audit_head"] = self.audit_head
        self.connection.execute(
            """
            UPDATE mutation_idempotency_v3 SET response_json = ?
            WHERE grant_id = ? AND idempotency_key = ?
            """,
            (canonical_json(response), grant_id, idempotency_key),
        )
        self.connection.commit()
        with suppress(Exception):
            self.rebuild_derived()
        _validate_contract("knowledge-revision.v1.schema.json", response)
        return response

    def _validate_skill_manifest(
        self,
        value: dict[str, Any] | None,
        *,
        knowledge_id: str | None = None,
        parent_revision_id: str | None = None,
        scope: str,
        max_sensitivity: str,
        require_active_sources: bool = True,
    ) -> None:
        if not isinstance(value, dict):
            raise ValueError("Skill knowledge requires a manifest")
        _validate_contract("knowledge-skill.v1.schema.json", value)
        required = {
            "purpose",
            "applies_to",
            "does_not_apply_to",
            "invocation_mode",
            "input_contract",
            "output_contract",
            "capabilities",
            "resource_limits",
            "steps",
            "success_criteria",
            "failure_conditions",
            "license",
            "host_compatibility",
            "verification_commands",
            "known_limitations",
            "lifecycle",
            "source_revision_ids",
            "evaluation_run_ids",
            "supersedes_skill_revision",
            "deprecation_reason",
        }
        if set(value) != required:
            raise ValueError("Skill manifest does not match its closed contract")
        if value["invocation_mode"] not in {"user-invoked", "model-invoked"}:
            raise ValueError("Skill invocation mode is invalid")
        if value["lifecycle"] not in {
            "draft",
            "experimental",
            "promoted",
            "deprecated",
            "revoked",
        }:
            raise ValueError("Skill lifecycle is invalid")
        for field in (
            "applies_to",
            "does_not_apply_to",
            "capabilities",
            "success_criteria",
            "failure_conditions",
            "host_compatibility",
            "verification_commands",
            "known_limitations",
            "source_revision_ids",
            "evaluation_run_ids",
        ):
            items = value[field]
            if not isinstance(items, list) or any(
                not isinstance(item, str) or not item.strip() for item in items
            ):
                raise ValueError(f"Skill {field} must be an array of non-empty strings")
        if not value["applies_to"] or not value["does_not_apply_to"]:
            raise ValueError("Skill applicability and exclusions must be explicit")
        if value["lifecycle"] == "promoted" and not value["evaluation_run_ids"]:
            raise ValueError("promoted Skill knowledge requires external evaluation runs")
        dangerous = {
            "sign",
            "publish",
            "export_private",
            "delete_irreversible",
            "grant_permission",
        }
        owner_only_terms = {
            "credential",
            "delete",
            "export",
            "network",
            "permission",
            "private",
            "publish",
            "secret",
            "shell",
            "sign",
        }
        if value["invocation_mode"] == "model-invoked" and (
            dangerous.intersection(value["capabilities"])
            or any(
                any(term in capability.lower() for term in owner_only_terms)
                for capability in value["capabilities"]
            )
        ):
            raise ValueError("model-invoked Skill declares an owner-only capability")
        if not isinstance(value["input_contract"], dict) or not isinstance(
            value["output_contract"], dict
        ):
            raise ValueError("Skill input and output contracts must be objects")
        try:
            Draft202012Validator.check_schema(value["input_contract"])
            Draft202012Validator.check_schema(value["output_contract"])
        except Exception as error:
            raise ValueError("Skill input or output contract is not valid JSON Schema") from error
        if not isinstance(value["resource_limits"], dict):
            raise ValueError("Skill resource limits must be an object")
        if any(
            isinstance(item, (int, float))
            and (isinstance(item, bool) or item < 0 or item > 1_000_000_000)
            for item in value["resource_limits"].values()
        ):
            raise ValueError("Skill resource limits are invalid")
        if not isinstance(value["steps"], list) or not value["steps"]:
            raise ValueError("Skill steps must be a non-empty array")
        for step in value["steps"]:
            if (
                not isinstance(step, dict)
                or set(step) != {"instruction", "completion_criterion"}
                or not all(isinstance(item, str) and item.strip() for item in step.values())
            ):
                raise ValueError("every Skill step requires an instruction and criterion")
        lifecycle = value["lifecycle"]
        deprecation_reason = value["deprecation_reason"]
        if lifecycle in {"deprecated", "revoked"}:
            if not isinstance(deprecation_reason, str) or not deprecation_reason.strip():
                raise ValueError("deprecated or revoked Skill requires a reason")
        elif deprecation_reason is not None:
            raise ValueError("active Skill lifecycle cannot declare a deprecation reason")
        supersedes = value["supersedes_skill_revision"]
        if parent_revision_id is None:
            if supersedes is not None:
                raise ValueError("new Skill knowledge cannot supersede a revision")
        elif supersedes != parent_revision_id:
            raise ValueError("Skill supersedes revision must match its Ledger parent")
        for revision_id in value["source_revision_ids"]:
            reference = (
                {"revision_id": revision_id}
                if _REVISION_ID.fullmatch(revision_id)
                else {"source_revision_id": revision_id}
            )
            if not self._source_reference_is_bound(
                reference,
                scope=scope,
                max_sensitivity=max_sensitivity,
                require_active=require_active_sources,
            ):
                raise ValueError(
                    "Skill source revision is not admitted in the Skill scope and sensitivity"
                )
        if lifecycle == "promoted":
            if knowledge_id is None:
                raise ValueError("promoted Skill requires an existing Skill lineage")
            for run_id in value["evaluation_run_ids"]:
                evaluated = self.connection.execute(
                    """
                    SELECT 1
                    FROM knowledge_feedback_v3
                    JOIN knowledge_revisions_v3
                      ON knowledge_revisions_v3.revision_id =
                         knowledge_feedback_v3.revision_id
                    WHERE knowledge_feedback_v3.run_id = ?
                      AND knowledge_revisions_v3.knowledge_id = ?
                      AND knowledge_feedback_v3.revision_id = ?
                      AND knowledge_revisions_v3.kind = 'skill'
                      AND knowledge_feedback_v3.outcome = 'helpful'
                      AND knowledge_feedback_v3.evaluator_type IN ('user', 'external_check')
                    """,
                    (run_id, knowledge_id, parent_revision_id),
                ).fetchone()
                if evaluated is None:
                    raise ValueError(
                        "promoted Skill evaluation is not externally bound to its lineage"
                    )

    def _materialize_pending(self, revision_id: str) -> None:
        row = self.connection.execute(
            """
            SELECT pending_materializations_v3.*,
                   knowledge_revisions_v3.knowledge_id,
                   knowledge_revisions_v3.parent_revision_id,
                   knowledge_revisions_v3.writer_id,
                   parent.markdown_sha256 AS parent_markdown_sha256,
                   knowledge_objects_v3.current_revision_id
            FROM pending_materializations_v3
            JOIN knowledge_revisions_v3 USING(revision_id)
            JOIN knowledge_objects_v3 USING(knowledge_id)
            LEFT JOIN knowledge_revisions_v3 AS parent
              ON parent.revision_id = knowledge_revisions_v3.parent_revision_id
            WHERE pending_materializations_v3.revision_id = ?
            """,
            (revision_id,),
        ).fetchone()
        if row is None:
            return
        relative = _safe_knowledge_workspace_path(row["workspace_path"])
        destination = self.root / relative
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink() or not destination.is_file():
                raise RuntimeError("workspace materialization target is unsafe")
            if destination.stat().st_size > _MAX_MARKDOWN_BYTES:
                raise ValueError("workspace materialization target exceeds its byte limit")
            workspace_payload = destination.read_bytes()
            workspace_sha256 = sha256_bytes(workspace_payload)
            expected_hashes = {
                row["markdown_sha256"],
                row["parent_markdown_sha256"],
            }
            if workspace_sha256 not in expected_hashes:
                committed_event = self.connection.execute(
                    """
                    SELECT payload_json FROM autonomous_events_v3
                    WHERE event_type = 'knowledge_revision_committed' AND object_id = ?
                    """,
                    (revision_id,),
                ).fetchone()
                if committed_event is None:
                    raise RuntimeError("pending materialization commit event is unavailable")
                event_payload = strict_json_loads(committed_event["payload_json"])
                grant_id = (
                    event_payload.get("grant_id") if isinstance(event_payload, dict) else None
                )
                if not isinstance(grant_id, str) or not _GRANT_ID.fullmatch(grant_id):
                    raise RuntimeError("pending materialization writer capability is invalid")
                if event_payload.get("workspace_edit_sha256") != workspace_sha256:
                    self._record_workspace_conflict(
                        grant_id=grant_id,
                        writer_id=row["writer_id"],
                        payload=workspace_payload,
                        workspace_path=relative,
                        reason="concurrent_workspace_change",
                        knowledge_id=row["knowledge_id"],
                        base_revision_id=row["parent_revision_id"],
                        current_revision_id=row["current_revision_id"],
                    )
        if row["action"] == "delete":
            destination.unlink(missing_ok=True)
        else:
            payload = _read_object(self.root, row["markdown_sha256"])
            _atomic_owner_write(destination, payload)
        completed_at = self._next_transaction_time(row["created_at"])
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            locked = self.connection.execute(
                "SELECT workspace_path, markdown_sha256, action "
                "FROM pending_materializations_v3 WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()
            if locked is None:
                self.connection.rollback()
                return
            if (
                locked["workspace_path"] != row["workspace_path"]
                or locked["markdown_sha256"] != row["markdown_sha256"]
                or locked["action"] != row["action"]
            ):
                raise RuntimeError("pending workspace materialization changed concurrently")
            self.connection.execute(
                "DELETE FROM pending_materializations_v3 WHERE revision_id = ?",
                (revision_id,),
            )
            materialization_payload = {
                "workspace_path": relative,
                "markdown_sha256": row["markdown_sha256"],
                "action": row["action"],
            }
            existing_materialization = self.connection.execute(
                """
                SELECT payload_json FROM autonomous_events_v3
                WHERE event_type = 'workspace_materialized' AND object_id = ?
                """,
                (revision_id,),
            ).fetchone()
            if existing_materialization is None:
                self._append_event(
                    event_type="workspace_materialized",
                    object_id=revision_id,
                    payload=materialization_payload,
                    recorded_at=completed_at,
                )
            elif strict_json_loads(existing_materialization["payload_json"]) != (
                materialization_payload
            ):
                raise RuntimeError("existing workspace materialization event is inconsistent")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def recover(self) -> dict[str, Any]:
        self._require_write()
        recovered: list[str] = []
        for row in self.connection.execute(
            "SELECT revision_id FROM pending_materializations_v3 ORDER BY created_at"
        ).fetchall():
            self._materialize_pending(row["revision_id"])
            recovered.append(row["revision_id"])
        cleaned_staging = 0
        discarded_staging = 0
        staging = self.root / ".deeplaw" / "staging"
        for scanned_staging, path in enumerate(
            sorted(staging.iterdir(), key=lambda item: item.name),
            start=1,
        ):
            if scanned_staging > _MAX_STAGING_RECORDS:
                raise ValueError("autonomous recovery exceeds its staging-record bound")
            if path.name == "conflicts":
                if path.is_symlink() or not path.is_dir():
                    raise RuntimeError("autonomous conflict staging root is unsafe")
                continue
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("autonomous staging contains an unsafe entry")
            if not 1 <= path.stat().st_size <= _MAX_STAGING_RECORD_BYTES:
                raise ValueError("autonomous staging record exceeds its byte bound")
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                path.unlink()
                discarded_staging += 1
                continue
            if path.suffix != ".json":
                raise RuntimeError("autonomous staging contains an unknown entry")
            try:
                record = strict_json_loads(path.read_bytes())
                expected_fields = {
                    "schema_version",
                    "revision_id",
                    "knowledge_id",
                    "workspace_path",
                    "markdown_sha256",
                    "request_sha256",
                    "created_at",
                }
                if (
                    not isinstance(record, dict)
                    or set(record) != expected_fields
                    or record["schema_version"] != "deeplaw.knowledge-staging/v1"
                    or not _REVISION_ID.fullmatch(record["revision_id"])
                    or not _KNOWLEDGE_ID.fullmatch(record["knowledge_id"])
                    or not _SHA256.fullmatch(record["markdown_sha256"])
                    or not _SHA256.fullmatch(record["request_sha256"])
                    or canonical_timestamp(
                        record["created_at"], field="staging created_at"
                    )
                    != record["created_at"]
                ):
                    raise ValueError("staging record contract is invalid")
                workspace_path = _safe_knowledge_workspace_path(record["workspace_path"])
            except (
                KeyError,
                TypeError,
                json.JSONDecodeError,
                UnicodeDecodeError,
                ValueError,
            ) as error:
                raise RuntimeError("autonomous staging record is invalid") from error
            committed = self.connection.execute(
                """
                SELECT knowledge_id, markdown_sha256, workspace_path
                FROM knowledge_revisions_v3 WHERE revision_id = ?
                """,
                (record["revision_id"],),
            ).fetchone()
            if committed is not None:
                if (
                    committed["knowledge_id"] != record["knowledge_id"]
                    or committed["markdown_sha256"] != record["markdown_sha256"]
                    or committed["workspace_path"] != workspace_path
                ):
                    raise RuntimeError("autonomous staging record does not match its revision")
                path.unlink()
                cleaned_staging += 1
            else:
                # SQLite commit is atomic. With no revision row, the staged
                # intent never became canonical; retain any CAS bytes for a
                # future owner-governed orphan GC and remove only the abandoned
                # intent.
                path.unlink()
                discarded_staging += 1
        return {
            "schema_version": "deeplaw.knowledge-recovery/v1",
            "recovered_revision_ids": recovered,
            "cleaned_staging_count": cleaned_staging,
            "discarded_uncommitted_staging_count": discarded_staging,
            "pending_count": self.connection.execute(
                "SELECT COUNT(*) FROM pending_materializations_v3"
            ).fetchone()[0],
        }

    def _record_workspace_conflict(
        self,
        *,
        grant_id: str,
        writer_id: str,
        payload: bytes,
        workspace_path: str,
        reason: str,
        knowledge_id: str | None,
        base_revision_id: str | None,
        current_revision_id: str | None,
    ) -> dict[str, Any]:
        self._require_write()
        digest, _ = _write_object(self.root, payload)
        detected_at = self._next_transaction_time()
        conflict_id = stable_id(
            "conflict",
            self.vault_id,
            workspace_path,
            digest,
            current_revision_id or "",
        )
        preserved = self.root / ".deeplaw" / "staging" / "conflicts" / f"{conflict_id}.md"
        _atomic_owner_write(preserved, _read_object(self.root, digest))
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            _register_content_object(
                self.connection,
                digest=digest,
                object_role="knowledge_revision",
                byte_size=len(payload),
                media_type="text/markdown; charset=utf-8",
                created_at=detected_at,
            )
            inserted = self.connection.execute(
                """
                INSERT OR IGNORE INTO workspace_conflicts_v3 VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    conflict_id,
                    knowledge_id,
                    base_revision_id,
                    current_revision_id,
                    digest,
                    workspace_path,
                    reason,
                    detected_at,
                ),
            ).rowcount
            if inserted:
                self._append_event(
                    event_type="workspace_conflict_preserved",
                    object_id=conflict_id,
                    payload={
                        "grant_id": grant_id,
                        "writer_id": writer_id,
                        "knowledge_id": knowledge_id,
                        "base_revision_id": base_revision_id,
                        "current_revision_id": current_revision_id,
                        "object_sha256": digest,
                        "workspace_path": workspace_path,
                        "reason": reason,
                    },
                    recorded_at=detected_at,
                )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return {
            "conflict_id": conflict_id,
            "knowledge_id": knowledge_id,
            "base_revision_id": base_revision_id,
            "current_revision_id": current_revision_id,
            "object_sha256": digest,
            "workspace_path": workspace_path,
            "preserved_path": str(preserved),
            "reason": reason,
        }

    def _restore_current_workspace(self, knowledge_id: str) -> str | None:
        try:
            current = self.get_current(knowledge_id, include_inactive=True)
        except KeyError:
            return None
        if current["lifecycle"] != "active":
            return None
        relative = _safe_knowledge_workspace_path(current["workspace_path"])
        _atomic_owner_write(
            self.root / relative,
            _read_object(self.root, current["markdown_sha256"]),
        )
        return relative

    def _record_workspace_move(
        self,
        *,
        grant_id: str,
        writer_id: str,
        knowledge_id: str,
        previous_path: str,
        workspace_path: str,
    ) -> None:
        selected = _safe_knowledge_workspace_path(workspace_path)
        current_owner = self.connection.execute(
            """
            SELECT knowledge_id FROM knowledge_objects_v3
            WHERE workspace_path = ? AND knowledge_id <> ?
            """,
            (selected, knowledge_id),
        ).fetchone()
        if current_owner is not None:
            raise RuntimeError("workspace path is already owned by another Knowledge Object")
        previous = self.root / _safe_knowledge_workspace_path(previous_path)
        destination = self.root / selected
        if previous != destination and previous.exists():
            if previous.is_symlink() or not previous.is_file():
                raise RuntimeError("previous workspace location is unsafe")
            _atomic_owner_write(destination, previous.read_bytes())
        recorded_at = self._next_transaction_time()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                UPDATE knowledge_objects_v3 SET workspace_path = ?, updated_at = ?
                WHERE knowledge_id = ?
                """,
                (selected, recorded_at, knowledge_id),
            )
            self._append_event(
                event_type="workspace_location_recorded",
                object_id=knowledge_id,
                payload={
                    "grant_id": grant_id,
                    "writer_id": writer_id,
                    "previous_path": previous_path,
                    "workspace_path": selected,
                },
                recorded_at=recorded_at,
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        if previous != destination and not previous.is_symlink():
            previous.unlink(missing_ok=True)

    def reconcile_workspace(
        self,
        *,
        grant_id: str,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        """Reconcile open Markdown edits without accepting last-writer-wins conflicts."""
        self._require_write()
        if not confirm_no_case_data:
            raise ValueError(
                "workspace reconcile requires confirmation that no case data is present"
            )
        # Validate the grant and bind all watcher writes to its writer identity.
        grant = self.grant_status(grant_id)
        record_conflict = partial(
            self._record_workspace_conflict,
            grant_id=grant_id,
            writer_id=grant["writer_id"],
        )
        record_move = partial(
            self._record_workspace_move,
            grant_id=grant_id,
            writer_id=grant["writer_id"],
        )
        current_rows = {
            row["knowledge_id"]: dict(row)
            for row in self.connection.execute(
                """
                SELECT knowledge_objects_v3.knowledge_id,
                       knowledge_objects_v3.current_revision_id,
                       knowledge_objects_v3.workspace_path,
                       knowledge_objects_v3.kind
                FROM knowledge_objects_v3
                """
            )
        }
        current_paths = {
            row["workspace_path"]: knowledge_id
            for knowledge_id, row in current_rows.items()
            if row["current_revision_id"] is not None
        }
        scanned: list[tuple[str, bytes, dict[str, Any]]] = []
        conflicts: list[dict[str, Any]] = []
        unmanaged: list[str] = []
        restored: list[str] = []
        scanned_entries = 0
        scanned_bytes = 0
        markdown_paths: list[Path] = []
        for root_name in ("knowledge", "memory", "skills"):
            directory = self.root / root_name
            if directory.is_symlink() or not directory.is_dir():
                raise RuntimeError("workspace knowledge directory is missing or unsafe")
            for path in directory.rglob("*"):
                scanned_entries += 1
                if scanned_entries > _MAX_RECONCILE_FILES:
                    raise ValueError("workspace reconcile exceeds its entry-count bound")
                if path.is_symlink():
                    raise RuntimeError("workspace contains a symbolic-link entry")
                if path.is_dir():
                    continue
                if not path.is_file():
                    raise RuntimeError("workspace contains an unsafe entry")
                if path.suffix == ".md":
                    markdown_paths.append(path)
        for path in sorted(markdown_paths, key=lambda item: item.as_posix()):
            byte_size = path.stat().st_size
            scanned_bytes += byte_size
            if byte_size > _MAX_MARKDOWN_BYTES:
                raise ValueError("workspace contains an oversized Knowledge Object")
            if scanned_bytes > _MAX_RECONCILE_BYTES:
                raise ValueError("workspace reconcile exceeds its total-byte bound")
            payload = path.read_bytes()
            relative = path.relative_to(self.root).as_posix()
            try:
                parsed = parse_knowledge_markdown(payload, validate_contract=False)
                frontmatter = cast(dict[str, Any], parsed["frontmatter"])
                existing_id = frontmatter.get("deeplaw_id")
                existing = (
                    self.get_current(existing_id, include_inactive=True)
                    if isinstance(existing_id, str) and existing_id in current_rows
                    else None
                )
                try:
                    _validate_contract(
                        "knowledge-object.v1.schema.json",
                        frontmatter,
                    )
                except ValueError:
                    if existing is None:
                        raise
                    normalized = dict(frontmatter)
                    normalized_sources = normalized.get("sources", [])
                    if not isinstance(normalized_sources, list):
                        raise
                    normalized_source_free = (
                        not normalized_sources and existing["generation"].get("run_id") is None
                    )
                    normalized.update(
                        {
                            "kind": existing["kind"],
                            "origin": existing["origin"],
                            "authority": existing["authority"],
                            "legal_authority": False,
                            "lifecycle": existing["lifecycle"],
                            "scope": existing["scope"],
                            "sensitivity": existing["sensitivity"],
                            "writer": existing["writer_id"],
                            "verification": (
                                "unverified"
                                if normalized_sources
                                else "run_bound"
                                if existing["generation"].get("run_id")
                                else "unverified"
                            ),
                            "source_free": normalized_source_free,
                            "generation": existing["generation"],
                            "parent_revision": existing["parent_revision_id"],
                            "supersedes": existing["supersedes_revision_id"],
                            "observed_at": existing["observed_at"],
                            "recorded_at": existing["recorded_at"],
                            "quarantine_reasons": existing["metadata"].get(
                                "quarantine_reasons",
                                [],
                            ),
                            "lifecycle_reason": existing["metadata"].get("lifecycle_reason"),
                        }
                    )
                    _validate_contract(
                        "knowledge-object.v1.schema.json",
                        normalized,
                    )
                if existing is None and (
                    frontmatter["scope"] != grant["allowed_scope"]
                    or (
                        SENSITIVITY_ORDER.index(frontmatter["sensitivity"])
                        > SENSITIVITY_ORDER.index(grant["max_sensitivity"])
                    )
                ):
                    raise ValueError("Markdown edit exceeds the watcher grant")
                _canonical_source_references(
                    frontmatter["sources"],
                    field="Markdown source references",
                )
                valid_from = _optional_timestamp(
                    frontmatter.get("valid_from"),
                    field="Markdown valid_from",
                )
                valid_to = _optional_timestamp(
                    frontmatter.get("valid_to"),
                    field="Markdown valid_to",
                )
                if valid_from is not None and valid_to is not None and valid_from >= valid_to:
                    raise ValueError("Markdown valid interval is invalid")
                if frontmatter["kind"] == "skill":
                    self._validate_skill_manifest(
                        cast(dict[str, Any] | None, frontmatter.get("skill")),
                        knowledge_id=cast(
                            str | None,
                            frontmatter.get("deeplaw_id"),
                        ),
                        parent_revision_id=cast(
                            str | None,
                            frontmatter.get("parent_revision"),
                        ),
                        scope=cast(str, frontmatter["scope"]),
                        max_sensitivity=cast(str, frontmatter["sensitivity"]),
                    )
            except ValueError:
                current_id = current_paths.get(relative)
                if current_id is None and KNOWLEDGE_OBJECT_SCHEMA.encode() not in payload[:32_768]:
                    unmanaged.append(relative)
                    continue
                conflict = record_conflict(
                    payload=payload,
                    workspace_path=relative,
                    reason="invalid_markdown_contract",
                    knowledge_id=current_id,
                    base_revision_id=None,
                    current_revision_id=(
                        cast(str, current_rows[current_id]["current_revision_id"])
                        if current_id is not None
                        else None
                    ),
                )
                conflicts.append(conflict)
                path.unlink(missing_ok=True)
                if current_id is not None:
                    restored_path = self._restore_current_workspace(current_id)
                    if restored_path is not None:
                        restored.append(restored_path)
                continue
            scanned.append((relative, payload, parsed))
        by_id: dict[str, list[tuple[str, bytes, dict[str, Any]]]] = defaultdict(list)
        for item in scanned:
            knowledge_id = item[2]["frontmatter"].get("deeplaw_id")
            if not isinstance(knowledge_id, str) or not _KNOWLEDGE_ID.fullmatch(knowledge_id):
                conflict = record_conflict(
                    payload=item[1],
                    workspace_path=item[0],
                    reason="invalid_or_missing_stable_id",
                    knowledge_id=None,
                    base_revision_id=None,
                    current_revision_id=None,
                )
                conflicts.append(conflict)
                (self.root / item[0]).unlink(missing_ok=True)
                by_id[f"invalid:{conflict['conflict_id']}"] = []
                continue
            by_id[knowledge_id].append(item)
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for knowledge_id, files in sorted(by_id.items()):
            if knowledge_id.startswith("invalid:"):
                continue
            if len(files) > 1:
                current = current_rows.get(knowledge_id)
                for relative, payload, parsed in files:
                    conflict = record_conflict(
                        payload=payload,
                        workspace_path=relative,
                        reason="duplicate_stable_id",
                        knowledge_id=knowledge_id,
                        base_revision_id=cast(str | None, parsed["frontmatter"].get("revision")),
                        current_revision_id=(
                            cast(str, current["current_revision_id"])
                            if current is not None
                            else None
                        ),
                    )
                    conflicts.append(conflict)
                    (self.root / relative).unlink(missing_ok=True)
                if current is not None:
                    restored_path = self._restore_current_workspace(knowledge_id)
                    if restored_path is not None:
                        restored.append(restored_path)
                continue
            relative, payload, parsed = files[0]
            seen.add(knowledge_id)
            frontmatter = parsed["frontmatter"]
            current_row = current_rows.get(knowledge_id)
            if current_row is None or current_row["current_revision_id"] is None:
                kind = frontmatter.get("kind")
                if kind not in KNOWLEDGE_KINDS:
                    conflict = record_conflict(
                        payload=payload,
                        workspace_path=relative,
                        reason="unsupported_kind",
                        knowledge_id=knowledge_id,
                        base_revision_id=cast(str | None, frontmatter.get("revision")),
                        current_revision_id=None,
                    )
                    conflicts.append(conflict)
                    (self.root / relative).unlink(missing_ok=True)
                    continue
                source_refs = cast(list[dict[str, Any]], frontmatter.get("sources", []))
                requested_authority = str(frontmatter.get("authority", "agent_derived"))
                if frontmatter.get("lifecycle") != "active":
                    requested_authority = "governance_metadata_edit_attempt"
                result = self.remember(
                    grant_id=grant_id,
                    idempotency_key=f"reconcile-new:{knowledge_id}:{sha256_bytes(payload)}",
                    title=cast(str, frontmatter["title"]),
                    body=cast(str, parsed["body"]),
                    kind=cast(KnowledgeKind, kind),
                    knowledge_id=knowledge_id,
                    scope=cast(Scope, frontmatter.get("scope", "project")),
                    sensitivity=cast(Sensitivity, frontmatter.get("sensitivity", "private")),
                    epistemic_state=cast(
                        EpistemicState,
                        frontmatter.get("epistemic_state", "tentative"),
                    ),
                    source_refs=source_refs,
                    tool_id="workspace-watcher",
                    tags=cast(list[str], frontmatter.get("tags", [])),
                    semantic_key=cast(str | None, frontmatter.get("semantic_key")),
                    valid_from=valid_from,
                    valid_to=valid_to,
                    expires_at=cast(str | None, frontmatter.get("expires_at")),
                    memory_type=cast(str | None, frontmatter.get("memory_type")),
                    preference_basis=cast(
                        str | None,
                        frontmatter.get("preference_basis"),
                    ),
                    requested_origin=str(frontmatter.get("origin", "agent_derived")),
                    requested_authority=requested_authority,
                    confirm_no_case_data=True,
                    operation=(
                        "upsert_concept"
                        if kind == "concept"
                        else "save_synthesis"
                        if kind == "synthesis"
                        else "save_skill"
                        if kind == "skill"
                        else "remember"
                    ),
                    skill_manifest=cast(
                        dict[str, Any] | None,
                        frontmatter.get("skill"),
                    ),
                    workspace_edit_sha256=sha256_bytes(payload),
                )
                results.append(result)
                if result["lifecycle"] == "active" and result["workspace_path"] != relative:
                    record_move(
                        knowledge_id=knowledge_id,
                        previous_path=result["workspace_path"],
                        workspace_path=relative,
                    )
                elif result["lifecycle"] == "quarantined":
                    conflicts.append(
                        record_conflict(
                            payload=payload,
                            workspace_path=relative,
                            reason="new_markdown_quarantined",
                            knowledge_id=knowledge_id,
                            base_revision_id=cast(
                                str | None,
                                frontmatter.get("revision"),
                            ),
                            current_revision_id=None,
                        )
                    )
                    (self.root / relative).unlink(missing_ok=True)
                continue
            current = self.get_current(knowledge_id, include_inactive=True)
            if current["lifecycle"] != "active":
                conflict = record_conflict(
                    payload=payload,
                    workspace_path=relative,
                    reason="inactive_workspace_materialization",
                    knowledge_id=knowledge_id,
                    base_revision_id=cast(str | None, frontmatter.get("revision")),
                    current_revision_id=current["revision_id"],
                )
                conflicts.append(conflict)
                (self.root / relative).unlink(missing_ok=True)
                continue
            base_revision = frontmatter.get("revision")
            if base_revision != current["revision_id"]:
                conflict = record_conflict(
                    payload=payload,
                    workspace_path=relative,
                    reason="stale_base_revision",
                    knowledge_id=knowledge_id,
                    base_revision_id=cast(str | None, base_revision),
                    current_revision_id=current["revision_id"],
                )
                conflicts.append(conflict)
                (self.root / relative).unlink(missing_ok=True)
                restored_path = self._restore_current_workspace(knowledge_id)
                if restored_path is not None:
                    restored.append(restored_path)
                continue
            if sha256_bytes(payload) == current["markdown_sha256"]:
                if relative != current_row["workspace_path"]:
                    record_move(
                        knowledge_id=knowledge_id,
                        previous_path=current_row["workspace_path"],
                        workspace_path=relative,
                    )
                    results.append(
                        {
                            "knowledge_id": knowledge_id,
                            "revision_id": current["revision_id"],
                            "workspace_path": relative,
                            "change": "moved",
                        }
                    )
                continue
            governed_fields = {
                "kind": current["kind"],
                "origin": current["origin"],
                "authority": current["authority"],
                "legal_authority": False,
                "lifecycle": current["lifecycle"],
                "scope": current["scope"],
                "sensitivity": current["sensitivity"],
                "writer": current["writer_id"],
                "verification": current["verification"],
                "source_free": current["source_free"],
                "generation": current["generation"],
                "parent_revision": current["parent_revision_id"],
                "supersedes": current["supersedes_revision_id"],
                "observed_at": current["observed_at"],
                "recorded_at": current["recorded_at"],
                "quarantine_reasons": current["metadata"].get(
                    "quarantine_reasons",
                    [],
                ),
                "lifecycle_reason": current["metadata"].get("lifecycle_reason"),
            }
            governed_changes = [
                field for field, value in governed_fields.items() if frontmatter.get(field) != value
            ]
            if governed_changes:
                requested_origin = str(frontmatter.get("origin", "agent_derived"))
                requested_authority = str(frontmatter.get("authority", "agent_derived"))
                if requested_origin == "agent_derived" and requested_authority == "agent_derived":
                    # Any other governance-field edit still has to travel
                    # through the normal quarantine path. This marker cannot
                    # confer authority because remember() only ever commits
                    # agent_derived origin and authority.
                    requested_authority = "governance_metadata_edit_attempt"
                result = self.remember(
                    grant_id=grant_id,
                    idempotency_key=(
                        f"reconcile-governance:{knowledge_id}:{sha256_bytes(payload)}"
                    ),
                    title=cast(str, frontmatter["title"]),
                    body=cast(str, parsed["body"]),
                    kind=cast(KnowledgeKind, current["kind"]),
                    knowledge_id=knowledge_id,
                    expected_revision_id=current["revision_id"],
                    scope=cast(Scope, current["scope"]),
                    sensitivity=cast(Sensitivity, current["sensitivity"]),
                    epistemic_state=cast(
                        EpistemicState,
                        frontmatter.get("epistemic_state", current["epistemic_state"]),
                    ),
                    source_refs=cast(list[dict[str, Any]], frontmatter.get("sources", [])),
                    run_id=cast(str | None, current["generation"].get("run_id")),
                    model_id=cast(str | None, current["generation"].get("model_id")),
                    tool_id="workspace-watcher",
                    generation_activity_id=cast(
                        str | None, current["generation"].get("activity_id")
                    ),
                    tags=cast(list[str], frontmatter.get("tags", current["tags"])),
                    semantic_key=cast(
                        str | None,
                        frontmatter.get("semantic_key", current["semantic_key"]),
                    ),
                    valid_from=cast(str | None, frontmatter.get("valid_from")),
                    valid_to=cast(str | None, frontmatter.get("valid_to")),
                    expires_at=cast(str | None, frontmatter.get("expires_at")),
                    memory_type=cast(str | None, frontmatter.get("memory_type")),
                    preference_basis=cast(
                        str | None,
                        frontmatter.get("preference_basis"),
                    ),
                    requested_origin=requested_origin,
                    requested_authority=requested_authority,
                    confirm_no_case_data=True,
                    operation=(
                        "upsert_concept"
                        if current["kind"] == "concept"
                        else "save_synthesis"
                        if current["kind"] == "synthesis"
                        else "save_skill"
                        if current["kind"] == "skill"
                        else "remember"
                    ),
                    skill_manifest=cast(
                        dict[str, Any] | None,
                        frontmatter.get("skill"),
                    ),
                    workspace_edit_sha256=sha256_bytes(payload),
                )
                results.append(result)
                conflict = record_conflict(
                    payload=payload,
                    workspace_path=relative,
                    reason="governance_metadata_edit_attempt",
                    knowledge_id=knowledge_id,
                    base_revision_id=cast(str, base_revision),
                    current_revision_id=current["revision_id"],
                )
                conflicts.append(conflict)
                (self.root / relative).unlink(missing_ok=True)
                restored_path = self._restore_current_workspace(knowledge_id)
                if restored_path is not None:
                    restored.append(restored_path)
                continue
            result = self.remember(
                grant_id=grant_id,
                idempotency_key=(f"reconcile-edit:{knowledge_id}:{sha256_bytes(payload)}"),
                title=cast(str, frontmatter["title"]),
                body=cast(str, parsed["body"]),
                kind=cast(KnowledgeKind, current["kind"]),
                knowledge_id=knowledge_id,
                expected_revision_id=current["revision_id"],
                scope=cast(Scope, current["scope"]),
                sensitivity=cast(Sensitivity, current["sensitivity"]),
                epistemic_state=cast(
                    EpistemicState,
                    frontmatter.get("epistemic_state", current["epistemic_state"]),
                ),
                source_refs=cast(list[dict[str, Any]], frontmatter.get("sources", [])),
                run_id=cast(str | None, current["generation"].get("run_id")),
                model_id=cast(str | None, current["generation"].get("model_id")),
                tool_id="workspace-watcher",
                generation_activity_id=cast(str | None, current["generation"].get("activity_id")),
                tags=cast(list[str], frontmatter.get("tags", current["tags"])),
                semantic_key=cast(
                    str | None,
                    frontmatter.get("semantic_key", current["semantic_key"]),
                ),
                valid_from=cast(str | None, frontmatter.get("valid_from")),
                valid_to=cast(str | None, frontmatter.get("valid_to")),
                expires_at=cast(str | None, frontmatter.get("expires_at")),
                memory_type=cast(str | None, frontmatter.get("memory_type")),
                preference_basis=cast(
                    str | None,
                    frontmatter.get("preference_basis"),
                ),
                requested_origin="agent_derived",
                requested_authority="agent_derived",
                confirm_no_case_data=True,
                operation=(
                    "upsert_concept"
                    if current["kind"] == "concept"
                    else "save_synthesis"
                    if current["kind"] == "synthesis"
                    else "save_skill"
                    if current["kind"] == "skill"
                    else "remember"
                ),
                skill_manifest=cast(
                    dict[str, Any] | None,
                    frontmatter.get("skill"),
                ),
                workspace_edit_sha256=sha256_bytes(payload),
            )
            results.append(result)
            if result["lifecycle"] == "active" and result["workspace_path"] != relative:
                record_move(
                    knowledge_id=knowledge_id,
                    previous_path=result["workspace_path"],
                    workspace_path=relative,
                )
            elif result["lifecycle"] == "quarantined":
                conflicts.append(
                    record_conflict(
                        payload=payload,
                        workspace_path=relative,
                        reason="edited_markdown_quarantined",
                        knowledge_id=knowledge_id,
                        base_revision_id=cast(str, base_revision),
                        current_revision_id=current["revision_id"],
                    )
                )
                (self.root / relative).unlink(missing_ok=True)
                restored_path = self._restore_current_workspace(knowledge_id)
                if restored_path is not None:
                    restored.append(restored_path)
        for knowledge_id in current_rows:
            if knowledge_id in seen:
                continue
            restored_path = self._restore_current_workspace(knowledge_id)
            if restored_path is not None:
                restored.append(restored_path)
        return {
            "schema_version": "deeplaw.workspace-reconcile/v1",
            "scanned_file_count": len(scanned),
            "committed": results,
            "conflicts": conflicts,
            "unmanaged_markdown": sorted(unmanaged),
            "restored_paths": sorted(set(restored)),
            "audit_head": self.audit_head,
        }

    def get_current(self, knowledge_id: str, *, include_inactive: bool = False) -> dict[str, Any]:
        if not _KNOWLEDGE_ID.fullmatch(knowledge_id):
            raise ValueError("knowledge ID is invalid")
        row = self.connection.execute(
            """
            SELECT knowledge_objects_v3.current_revision_id,
                   knowledge_objects_v3.workspace_path AS current_workspace_path,
                   knowledge_revisions_v3.*
            FROM knowledge_objects_v3
            JOIN knowledge_revisions_v3
              ON knowledge_revisions_v3.revision_id = knowledge_objects_v3.current_revision_id
            WHERE knowledge_objects_v3.knowledge_id = ?
            """,
            (knowledge_id,),
        ).fetchone()
        if row is None or (not include_inactive and row["lifecycle"] not in {"active"}):
            raise KeyError(f"Knowledge Object is unavailable: {knowledge_id}")
        return self._revision_row(row, include_body=True)

    def get_at(self, knowledge_id: str, *, recorded_at: str) -> dict[str, Any]:
        """Read the latest immutable revision known at one transaction-time instant."""
        if not _KNOWLEDGE_ID.fullmatch(knowledge_id):
            raise ValueError("knowledge ID is invalid")
        instant = canonical_timestamp(recorded_at, field="knowledge transaction time")
        current = self.connection.execute(
            """
            SELECT knowledge_revisions_v3.lifecycle
            FROM knowledge_objects_v3
            JOIN knowledge_revisions_v3
              ON knowledge_revisions_v3.revision_id =
                 knowledge_objects_v3.current_revision_id
            WHERE knowledge_objects_v3.knowledge_id = ?
            """,
            (knowledge_id,),
        ).fetchone()
        if current is not None and current["lifecycle"] in {"forgotten", "revoked"}:
            raise KeyError(f"Knowledge Object is unavailable: {knowledge_id}")
        row = self.connection.execute(
            """
            SELECT knowledge_revisions_v3.*
            FROM knowledge_revisions_v3
            WHERE knowledge_revisions_v3.knowledge_id = ?
              AND knowledge_revisions_v3.recorded_at <= ?
              AND knowledge_revisions_v3.lifecycle <> 'quarantined'
            ORDER BY knowledge_revisions_v3.recorded_at DESC,
                     knowledge_revisions_v3.revision_id DESC
            LIMIT 1
            """,
            (knowledge_id, instant),
        ).fetchone()
        if row is None:
            raise KeyError(f"Knowledge Object is unavailable: {knowledge_id}")
        return self._revision_row(row, include_body=True)

    def _revision_row(self, row: sqlite3.Row, *, include_body: bool) -> dict[str, Any]:
        row_fields = frozenset(row.keys())
        value = {
            "schema_version": KNOWLEDGE_REVISION_SCHEMA,
            "knowledge_id": row["knowledge_id"],
            "revision_id": row["revision_id"],
            "parent_revision_id": row["parent_revision_id"],
            "supersedes_revision_id": row["supersedes_revision_id"],
            "markdown_sha256": row["markdown_sha256"],
            "title": row["title"],
            "kind": row["kind"],
            "lifecycle": row["lifecycle"],
            "epistemic_state": row["epistemic_state"],
            "origin": row["origin"],
            "authority": row["authority"],
            "verification": row["verification"],
            "scope": row["scope"],
            "sensitivity": row["sensitivity"],
            "writer_id": row["writer_id"],
            "source_free": bool(row["source_free"]),
            "source_refs": strict_json_loads(row["source_refs_json"]),
            "generation": strict_json_loads(row["generation_json"]),
            "tags": strict_json_loads(row["tags_json"]),
            "metadata": strict_json_loads(row["metadata_json"]),
            "semantic_key": row["semantic_key"],
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "observed_at": row["observed_at"],
            "recorded_at": row["recorded_at"],
            "expires_at": row["expires_at"],
            "workspace_path": (
                row["current_workspace_path"]
                if "current_workspace_path" in row_fields
                else row["workspace_path"]
            ),
            "legal_authority": False,
        }
        if include_body:
            payload = _read_object(self.root, row["markdown_sha256"])
            parsed = parse_knowledge_markdown(payload)
            value["body"] = parsed["body"]
        return value

    def history(self, knowledge_id: str) -> dict[str, Any]:
        if not _KNOWLEDGE_ID.fullmatch(knowledge_id):
            raise ValueError("knowledge ID is invalid")
        rows = self.connection.execute(
            """
            SELECT knowledge_revisions_v3.*
            FROM knowledge_revisions_v3
            WHERE knowledge_revisions_v3.knowledge_id = ?
            ORDER BY recorded_at, revision_id
            """,
            (knowledge_id,),
        ).fetchall()
        if not rows:
            raise KeyError(f"Knowledge Object is unavailable: {knowledge_id}")
        return {
            "schema_version": "deeplaw.knowledge-lineage/v2",
            "knowledge_id": knowledge_id,
            "revisions": [self._revision_row(row, include_body=False) for row in rows],
            "current_revision_id": self.connection.execute(
                "SELECT current_revision_id FROM knowledge_objects_v3 WHERE knowledge_id = ?",
                (knowledge_id,),
            ).fetchone()[0],
            "audit_head": self.audit_head,
        }

    def _relation_governance(
        self,
        *,
        grant: sqlite3.Row,
        subject_knowledge_id: str,
        object_knowledge_id: str,
        evidence_refs: list[dict[str, str]],
    ) -> tuple[str, str]:
        scope = grant["allowed_scope"]
        sensitivity_levels: list[int] = []
        reference_time = utc_now()
        for endpoint in (subject_knowledge_id, object_knowledge_id):
            try:
                current_endpoint = self.get_current(endpoint)
            except KeyError:
                raise KeyError("relation endpoint is unavailable in the granted boundary") from None
            if current_endpoint["scope"] != scope:
                raise KeyError("relation endpoint is unavailable in the granted boundary")
            endpoint_level = SENSITIVITY_ORDER.index(current_endpoint["sensitivity"])
            if endpoint_level > SENSITIVITY_ORDER.index(grant["max_sensitivity"]):
                raise KeyError("relation endpoint is unavailable in the granted boundary")
            if (
                not self.revision_provenance_admitted(current_endpoint)
                or (
                    current_endpoint["expires_at"] is not None
                    and current_endpoint["expires_at"] <= reference_time
                )
                or (
                    current_endpoint["valid_from"] is not None
                    and current_endpoint["valid_from"] > reference_time
                )
                or (
                    current_endpoint["valid_to"] is not None
                    and current_endpoint["valid_to"] <= reference_time
                )
            ):
                raise ValueError("relation endpoint is not currently admitted")
            sensitivity_levels.append(endpoint_level)
        for reference in evidence_refs:
            binding = self._source_reference_binding(reference)
            if binding is None:
                raise ValueError("relation evidence is not bound to the trusted Ledger")
            if binding["active"] is not True:
                raise ValueError("relation evidence is not currently active")
            if binding["scope"] != scope:
                raise PermissionError("relation evidence exceeds its granted scope")
            binding_level = SENSITIVITY_ORDER.index(binding["sensitivity"])
            if binding_level > SENSITIVITY_ORDER.index(grant["max_sensitivity"]):
                raise PermissionError("relation evidence exceeds its granted sensitivity")
            sensitivity_levels.append(binding_level)
        return scope, SENSITIVITY_ORDER[max(sensitivity_levels)]

    def add_relation(
        self,
        *,
        grant_id: str,
        idempotency_key: str,
        subject_knowledge_id: str,
        predicate: str,
        object_knowledge_id: str,
        expected_relation_revision_id: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        self._require_write()
        if not confirm_no_case_data:
            raise ValueError("knowledge sink requires confirmation that no case data is present")
        if not _KNOWLEDGE_ID.fullmatch(subject_knowledge_id) or not _KNOWLEDGE_ID.fullmatch(
            object_knowledge_id
        ):
            raise ValueError("relation endpoint identity is invalid")
        if subject_knowledge_id == object_knowledge_id:
            raise ValueError("knowledge relation cannot be a self-loop")
        if predicate not in RELATION_PREDICATES:
            raise ValueError("knowledge relation predicate is invalid")
        if expected_relation_revision_id is not None and not _RELATION_REVISION_ID.fullmatch(
            expected_relation_revision_id
        ):
            raise ValueError("expected relation revision identity is invalid")
        selected_refs = self._pin_source_references(
            _canonical_source_references(evidence_refs or [], field="relation evidence")
        )
        if has_instruction_risk(canonical_json(selected_refs)):
            raise ValueError("relation evidence metadata contains persistent prompt-injection risk")
        valid_from = _optional_timestamp(valid_from, field="relation valid_from")
        valid_to = _optional_timestamp(valid_to, field="relation valid_to")
        if valid_from is not None and valid_to is not None and valid_from >= valid_to:
            raise ValueError("relation valid interval is invalid")
        request = {
            "operation": "add_relation",
            "subject_knowledge_id": subject_knowledge_id,
            "predicate": predicate,
            "object_knowledge_id": object_knowledge_id,
            "expected_relation_revision_id": expected_relation_revision_id,
            "evidence_refs": selected_refs,
            "valid_from": valid_from,
            "valid_to": valid_to,
        }
        idempotency_key = _bounded_string(idempotency_key, field="idempotency key", maximum=200)
        request_bytes = canonical_json(request).encode("utf-8")
        request_sha256 = sha256_bytes(request_bytes)
        grant = self._grant(
            grant_id,
            operation="add_relation",
            request_bytes=len(request_bytes),
        )
        existing = self._idempotent_response(
            grant_id=grant_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
        )
        if existing is not None:
            return existing
        relation_scope, relation_sensitivity = self._relation_governance(
            grant=grant,
            subject_knowledge_id=subject_knowledge_id,
            object_knowledge_id=object_knowledge_id,
            evidence_refs=selected_refs,
        )
        relation_key = stable_id(
            "relationkey",
            self.vault_id,
            subject_knowledge_id,
            predicate,
            object_knowledge_id,
        )
        current = self.connection.execute(
            "SELECT current_revision_id FROM knowledge_relations_v3 WHERE relation_key = ?",
            (relation_key,),
        ).fetchone()
        parent_revision_id = current["current_revision_id"] if current is not None else None
        if parent_revision_id is None:
            if expected_relation_revision_id is not None:
                raise ValueError("new Knowledge relation cannot declare an expected revision")
        elif expected_relation_revision_id != parent_revision_id:
            raise RuntimeError("Knowledge relation compare-and-swap conflict")
        recorded_at = self._next_transaction_time()
        if parent_revision_id is not None:
            parent_recorded = self.connection.execute(
                "SELECT recorded_at FROM knowledge_relation_revisions_v3 "
                "WHERE relation_revision_id = ?",
                (parent_revision_id,),
            ).fetchone()
            if parent_recorded is None:
                raise RuntimeError("Knowledge relation parent revision is unavailable")
            recorded_at = _timestamp_after(recorded_at, parent_recorded["recorded_at"])
        relation_revision_id = stable_id(
            "relationrev",
            relation_key,
            grant_id,
            idempotency_key,
            request_sha256,
        )
        response = {
            "schema_version": KNOWLEDGE_RELATION_SCHEMA,
            "relation_key": relation_key,
            "relation_revision_id": relation_revision_id,
            "parent_revision_id": parent_revision_id,
            "subject_knowledge_id": subject_knowledge_id,
            "predicate": predicate,
            "object_knowledge_id": object_knowledge_id,
            "origin": "agent_derived",
            "authority": "agent_derived",
            "legal_authority": False,
            "source_free": not selected_refs,
            "lifecycle": "active",
            "scope": relation_scope,
            "sensitivity": relation_sensitivity,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "recorded_at": recorded_at,
            "idempotent_replay": False,
        }
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            locked_grant = self._grant(
                grant_id,
                operation="add_relation",
                request_bytes=len(request_bytes),
            )
            locked_replay = self._idempotent_response(
                grant_id=grant_id,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
            )
            if locked_replay is not None:
                self.connection.rollback()
                return locked_replay
            self._enforce_grant_limits(
                locked_grant,
                enforce_object_capacity=False,
            )
            locked_scope, locked_sensitivity = self._relation_governance(
                grant=locked_grant,
                subject_knowledge_id=subject_knowledge_id,
                object_knowledge_id=object_knowledge_id,
                evidence_refs=selected_refs,
            )
            if (locked_scope, locked_sensitivity) != (
                relation_scope,
                relation_sensitivity,
            ):
                raise RuntimeError("Knowledge relation governance changed during commit")
            locked = self.connection.execute(
                "SELECT current_revision_id FROM knowledge_relations_v3 WHERE relation_key = ?",
                (relation_key,),
            ).fetchone()
            locked_revision = locked["current_revision_id"] if locked is not None else None
            if locked_revision != parent_revision_id:
                raise RuntimeError("Knowledge relation compare-and-swap conflict")
            if locked is None:
                for endpoint in (subject_knowledge_id, object_knowledge_id):
                    relation_count = self.connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM knowledge_relations_v3
                        JOIN knowledge_relation_revisions_v3
                          ON knowledge_relation_revisions_v3.relation_revision_id =
                             knowledge_relations_v3.current_revision_id
                        WHERE knowledge_relation_revisions_v3.lifecycle = 'active'
                          AND (
                            knowledge_relation_revisions_v3.subject_knowledge_id = ?
                            OR knowledge_relation_revisions_v3.object_knowledge_id = ?
                          )
                        """,
                        (endpoint, endpoint),
                    ).fetchone()[0]
                    if relation_count >= _MAX_RELATIONS_PER_OBJECT:
                        raise RuntimeError("knowledge relation capacity exceeded for an endpoint")
                self.connection.execute(
                    "INSERT INTO knowledge_relations_v3 VALUES (?, ?, ?, ?)",
                    (relation_key, relation_revision_id, recorded_at, recorded_at),
                )
            self.connection.execute(
                """
                INSERT INTO knowledge_relation_revisions_v3(
                    relation_revision_id, relation_key, parent_revision_id,
                    subject_knowledge_id, predicate, object_knowledge_id,
                    evidence_refs_json, source_free, lifecycle, origin, authority,
                    scope, sensitivity, writer_id, valid_from, valid_to, observed_at,
                    recorded_at
                ) VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'agent_derived', 'agent_derived',
                 ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation_revision_id,
                    relation_key,
                    parent_revision_id,
                    subject_knowledge_id,
                    predicate,
                    object_knowledge_id,
                    canonical_json(selected_refs),
                    int(not selected_refs),
                    relation_scope,
                    relation_sensitivity,
                    locked_grant["writer_id"],
                    valid_from,
                    valid_to,
                    recorded_at,
                    recorded_at,
                ),
            )
            self.connection.execute(
                "UPDATE knowledge_relations_v3 SET current_revision_id = ?, updated_at = ? "
                "WHERE relation_key = ?",
                (relation_revision_id, recorded_at, relation_key),
            )
            mutation_id = stable_id("mutation", grant_id, idempotency_key, request_sha256)
            self.connection.execute(
                "INSERT INTO knowledge_sink_usage_v3 VALUES (?, ?, ?, ?, ?)",
                (mutation_id, grant_id, "add_relation", request_sha256, recorded_at),
            )
            self._append_event(
                event_type="knowledge_relation_committed",
                object_id=relation_revision_id,
                payload={
                    "grant_id": grant_id,
                    "idempotency_key_sha256": sha256_bytes(idempotency_key.encode("utf-8")),
                    "request_sha256": request_sha256,
                    "operation": "add_relation",
                    "relation_key": relation_key,
                    "parent_revision_id": parent_revision_id,
                    "subject_knowledge_id": subject_knowledge_id,
                    "predicate": predicate,
                    "object_knowledge_id": object_knowledge_id,
                    "source_free": not selected_refs,
                    "scope": relation_scope,
                    "sensitivity": relation_sensitivity,
                    "writer_id": locked_grant["writer_id"],
                    "origin": "agent_derived",
                    "authority": "agent_derived",
                    "evidence_refs_sha256": sha256_bytes(
                        canonical_json(selected_refs).encode("utf-8")
                    ),
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                },
                recorded_at=recorded_at,
            )
            response["audit_head"] = self.audit_head
            self.connection.execute(
                "INSERT INTO mutation_idempotency_v3 VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    grant_id,
                    idempotency_key,
                    request_sha256,
                    "relation_revision",
                    relation_revision_id,
                    canonical_json(response),
                    recorded_at,
                ),
            )
            queue_id = stable_id("rebuild", relation_revision_id, self.audit_head)
            self.connection.execute(
                "INSERT INTO derived_rebuild_queue_v3 VALUES (?, ?, ?, ?, NULL)",
                (queue_id, self.audit_head, "relation_revision_committed", recorded_at),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        with suppress(Exception):
            self.rebuild_derived()
        _validate_contract("knowledge-relation.v3.schema.json", response)
        return response

    def record_feedback(
        self,
        *,
        grant_id: str,
        idempotency_key: str,
        knowledge_id: str,
        revision_id: str,
        run_id: str,
        outcome: str,
        evaluator_type: str,
        feedback_note: str | None = None,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        self._require_write()
        if not confirm_no_case_data:
            raise ValueError(
                "knowledge feedback requires confirmation that no case data is present"
            )
        if not _KNOWLEDGE_ID.fullmatch(knowledge_id) or not _REVISION_ID.fullmatch(revision_id):
            raise ValueError("knowledge feedback identity is invalid")
        idempotency_key = _bounded_string(
            idempotency_key,
            field="idempotency key",
            maximum=200,
        )
        run_id = _bounded_string(run_id, field="feedback run ID", maximum=500)
        if outcome not in {"helpful", "neutral", "noisy", "harmful"}:
            raise ValueError("knowledge feedback outcome is invalid")
        if evaluator_type not in FEEDBACK_EVALUATOR_TYPES:
            raise ValueError("knowledge feedback evaluator type is invalid")
        if feedback_note is not None:
            feedback_note = _bounded_string(
                feedback_note,
                field="feedback note",
                maximum=4_000,
            )
        request = {
            "operation": "record_feedback",
            "knowledge_id": knowledge_id,
            "revision_id": revision_id,
            "run_id": run_id,
            "outcome": outcome,
            "evaluator_type": evaluator_type,
            "feedback_note_sha256": (
                sha256_bytes(feedback_note.encode("utf-8")) if feedback_note is not None else None
            ),
        }
        request_bytes = canonical_json(request).encode("utf-8")
        request_sha256 = sha256_bytes(request_bytes)
        grant = self._grant(
            grant_id,
            operation="record_feedback",
            request_bytes=len(request_bytes),
        )
        if evaluator_type not in self._grant_evaluator_types(grant):
            raise PermissionError(
                "knowledge feedback evaluator type is not granted to this capability"
            )
        existing = self._idempotent_response(
            grant_id=grant_id,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
        )
        if existing is not None:
            return existing
        revision = self.connection.execute(
            """
            SELECT knowledge_revisions_v3.scope, knowledge_revisions_v3.sensitivity
            FROM knowledge_revisions_v3
            WHERE knowledge_id = ? AND revision_id = ?
            """,
            (knowledge_id, revision_id),
        ).fetchone()
        if revision is None:
            raise KeyError("knowledge feedback target revision is unavailable")
        if revision["scope"] != grant["allowed_scope"]:
            raise PermissionError("knowledge feedback target exceeds its granted scope")
        if SENSITIVITY_ORDER.index(revision["sensitivity"]) > SENSITIVITY_ORDER.index(
            grant["max_sensitivity"]
        ):
            raise PermissionError("knowledge feedback target exceeds its granted sensitivity")
        recorded_at = self._next_transaction_time()
        feedback_id = stable_id(
            "feedback",
            knowledge_id,
            revision_id,
            grant_id,
            idempotency_key,
            request_sha256,
        )
        response = {
            "schema_version": "deeplaw.knowledge-feedback/v1",
            "feedback_id": feedback_id,
            "knowledge_id": knowledge_id,
            "revision_id": revision_id,
            "run_id": run_id,
            "outcome": outcome,
            "evaluator_type": evaluator_type,
            "feedback_note_sha256": request["feedback_note_sha256"],
            "task_success_authority": (
                "external_evidence"
                if evaluator_type in {"user", "external_check"}
                else "self_report_only"
            ),
            "recorded_at": recorded_at,
            "idempotent_replay": False,
        }
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            locked_grant = self._grant(
                grant_id,
                operation="record_feedback",
                request_bytes=len(request_bytes),
            )
            if evaluator_type not in self._grant_evaluator_types(locked_grant):
                raise PermissionError(
                    "knowledge feedback evaluator type is not granted to this capability"
                )
            locked_replay = self._idempotent_response(
                grant_id=grant_id,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
            )
            if locked_replay is not None:
                self.connection.rollback()
                return locked_replay
            self._enforce_grant_limits(
                locked_grant,
                enforce_object_capacity=False,
            )
            locked_revision = self.connection.execute(
                """
                SELECT knowledge_revisions_v3.scope, knowledge_revisions_v3.sensitivity
                FROM knowledge_revisions_v3
                WHERE knowledge_id = ? AND revision_id = ?
                """,
                (knowledge_id, revision_id),
            ).fetchone()
            if locked_revision is None:
                raise KeyError("knowledge feedback target revision is unavailable")
            if locked_revision["scope"] != locked_grant["allowed_scope"]:
                raise PermissionError("knowledge feedback target exceeds its granted scope")
            if SENSITIVITY_ORDER.index(locked_revision["sensitivity"]) > SENSITIVITY_ORDER.index(
                locked_grant["max_sensitivity"]
            ):
                raise PermissionError("knowledge feedback target exceeds its granted sensitivity")
            self.connection.execute(
                "INSERT INTO knowledge_feedback_v3 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    feedback_id,
                    knowledge_id,
                    revision_id,
                    grant_id,
                    run_id,
                    outcome,
                    evaluator_type,
                    request["feedback_note_sha256"],
                    recorded_at,
                ),
            )
            mutation_id = stable_id("mutation", grant_id, idempotency_key, request_sha256)
            self.connection.execute(
                "INSERT INTO knowledge_sink_usage_v3 VALUES (?, ?, ?, ?, ?)",
                (mutation_id, grant_id, "record_feedback", request_sha256, recorded_at),
            )
            self._append_event(
                event_type="knowledge_feedback_recorded",
                object_id=feedback_id,
                payload={
                    "grant_id": grant_id,
                    "idempotency_key_sha256": sha256_bytes(idempotency_key.encode("utf-8")),
                    "request_sha256": request_sha256,
                    "operation": "record_feedback",
                    "knowledge_id": knowledge_id,
                    "revision_id": revision_id,
                    "run_id": run_id,
                    "outcome": outcome,
                    "evaluator_type": evaluator_type,
                    "feedback_note_sha256": request["feedback_note_sha256"],
                },
                recorded_at=recorded_at,
            )
            response["audit_head"] = self.audit_head
            self.connection.execute(
                "INSERT INTO mutation_idempotency_v3 VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    grant_id,
                    idempotency_key,
                    request_sha256,
                    "knowledge_feedback",
                    feedback_id,
                    canonical_json(response),
                    recorded_at,
                ),
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return response

    def forget(
        self,
        *,
        grant_id: str,
        idempotency_key: str,
        knowledge_id: str,
        expected_revision_id: str,
        reason: str,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError("knowledge sink requires confirmation that no case data is present")
        grant = self._grant(grant_id, operation="forget", request_bytes=0)
        current = self.get_current(knowledge_id)
        if current["scope"] != grant["allowed_scope"] or SENSITIVITY_ORDER.index(
            current["sensitivity"]
        ) > SENSITIVITY_ORDER.index(grant["max_sensitivity"]):
            raise KeyError(f"Knowledge Object is unavailable: {knowledge_id}")
        if current["revision_id"] != expected_revision_id:
            raise RuntimeError("Knowledge Object compare-and-swap conflict")
        reason = _bounded_string(reason, field="forget reason", maximum=2_000)
        result = self.remember(
            grant_id=grant_id,
            idempotency_key=idempotency_key,
            title=current["title"],
            body=current["body"],
            kind=cast(KnowledgeKind, current["kind"]),
            knowledge_id=knowledge_id,
            expected_revision_id=expected_revision_id,
            scope=cast(Scope, current["scope"]),
            sensitivity=cast(Sensitivity, current["sensitivity"]),
            epistemic_state=cast(EpistemicState, current["epistemic_state"]),
            source_refs=cast(list[dict[str, Any]], current["source_refs"]),
            run_id=cast(str | None, current["generation"].get("run_id")),
            model_id=cast(str | None, current["generation"].get("model_id")),
            tool_id=cast(str | None, current["generation"].get("tool_id")),
            generation_activity_id=cast(str | None, current["generation"].get("activity_id")),
            tags=cast(list[str], current["tags"]),
            semantic_key=cast(str | None, current["semantic_key"]),
            valid_from=cast(str | None, current["valid_from"]),
            valid_to=cast(str | None, current["valid_to"]),
            expires_at=cast(str | None, current["expires_at"]),
            memory_type=cast(str | None, current["metadata"].get("memory_type")),
            preference_basis=cast(
                str | None,
                current["metadata"].get("preference_basis"),
            ),
            confirm_no_case_data=confirm_no_case_data,
            operation="forget",
            skill_manifest=cast(dict[str, Any] | None, current["metadata"].get("skill_manifest")),
            lifecycle_override="forgotten",
            lifecycle_reason=reason,
        )
        return result

    def expire(
        self,
        *,
        grant_id: str,
        idempotency_key: str,
        knowledge_id: str,
        expected_revision_id: str,
        reason: str,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError("knowledge sink requires confirmation that no case data is present")
        grant = self._grant(grant_id, operation="expire", request_bytes=0)
        current = self.get_current(knowledge_id)
        if current["scope"] != grant["allowed_scope"] or SENSITIVITY_ORDER.index(
            current["sensitivity"]
        ) > SENSITIVITY_ORDER.index(grant["max_sensitivity"]):
            raise KeyError(f"Knowledge Object is unavailable: {knowledge_id}")
        if current["revision_id"] != expected_revision_id:
            raise RuntimeError("Knowledge Object compare-and-swap conflict")
        reason = _bounded_string(reason, field="expiry reason", maximum=2_000)
        return self.remember(
            grant_id=grant_id,
            idempotency_key=idempotency_key,
            title=current["title"],
            body=current["body"],
            kind=cast(KnowledgeKind, current["kind"]),
            knowledge_id=knowledge_id,
            expected_revision_id=expected_revision_id,
            scope=cast(Scope, current["scope"]),
            sensitivity=cast(Sensitivity, current["sensitivity"]),
            epistemic_state=cast(EpistemicState, current["epistemic_state"]),
            source_refs=cast(list[dict[str, Any]], current["source_refs"]),
            run_id=cast(str | None, current["generation"].get("run_id")),
            model_id=cast(str | None, current["generation"].get("model_id")),
            tool_id=cast(str | None, current["generation"].get("tool_id")),
            generation_activity_id=cast(str | None, current["generation"].get("activity_id")),
            tags=cast(list[str], current["tags"]),
            semantic_key=cast(str | None, current["semantic_key"]),
            valid_from=cast(str | None, current["valid_from"]),
            valid_to=cast(str | None, current["valid_to"]),
            expires_at=cast(str | None, current["expires_at"]),
            memory_type=cast(str | None, current["metadata"].get("memory_type")),
            preference_basis=cast(
                str | None,
                current["metadata"].get("preference_basis"),
            ),
            confirm_no_case_data=confirm_no_case_data,
            operation="expire",
            skill_manifest=cast(
                dict[str, Any] | None,
                current["metadata"].get("skill_manifest"),
            ),
            lifecycle_override="expired",
            lifecycle_reason=reason,
        )

    def expire_due(
        self,
        *,
        grant_id: str,
        as_of: str | None = None,
        confirm_no_case_data: bool = False,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "knowledge maintenance requires confirmation that no case data is present"
            )
        grant = self._grant(grant_id, operation="expire", request_bytes=1_024)
        reference = canonical_timestamp(as_of, field="maintenance as_of") if as_of else utc_now()
        rows = self.connection.execute(
            """
            SELECT knowledge_objects_v3.knowledge_id,
                   knowledge_objects_v3.current_revision_id
            FROM knowledge_objects_v3
            JOIN knowledge_revisions_v3
              ON knowledge_revisions_v3.revision_id =
                 knowledge_objects_v3.current_revision_id
            WHERE knowledge_revisions_v3.lifecycle = 'active'
              AND knowledge_revisions_v3.expires_at IS NOT NULL
              AND knowledge_revisions_v3.expires_at <= ?
              AND knowledge_revisions_v3.scope = ?
            ORDER BY knowledge_objects_v3.knowledge_id
            """,
            (reference, grant["allowed_scope"]),
        ).fetchall()
        expired: list[str] = []
        for row in rows:
            current = self.get_current(row["knowledge_id"])
            if SENSITIVITY_ORDER.index(current["sensitivity"]) > SENSITIVITY_ORDER.index(
                grant["max_sensitivity"]
            ):
                continue
            self.expire(
                grant_id=grant_id,
                idempotency_key=(f"expire-due:{row['knowledge_id']}:{row['current_revision_id']}"),
                knowledge_id=row["knowledge_id"],
                expected_revision_id=row["current_revision_id"],
                reason=f"TTL elapsed at {reference}",
                confirm_no_case_data=confirm_no_case_data,
            )
            expired.append(row["knowledge_id"])
        return {
            "schema_version": "deeplaw.knowledge-maintenance/v1",
            "as_of": reference,
            "expired_knowledge_ids": expired,
            "expired_count": len(expired),
            "audit_head": self.audit_head,
        }

    def recall(
        self,
        query: str,
        *,
        scope: Scope = "project",
        max_sensitivity: Sensitivity = "private",
        limit: int = 5,
        max_chars: int = 5_000,
        as_of: str | None = None,
        kinds: tuple[str, ...] = (),
        force_canonical_lexical: bool = False,
    ) -> dict[str, Any]:
        query = _bounded_string(query, field="knowledge query", maximum=5_000)
        if scope not in SCOPES or max_sensitivity not in SENSITIVITIES:
            raise ValueError("recall scope or sensitivity is invalid")
        if not 1 <= limit <= _MAX_RECALL_LIMIT or not 200 <= max_chars <= _MAX_RECALL_CHARS:
            raise ValueError("recall budget is invalid")
        if (
            len(kinds) > len(KNOWLEDGE_KINDS)
            or any(not isinstance(kind, str) for kind in kinds)
            or len(set(kinds)) != len(kinds)
            or any(kind not in KNOWLEDGE_KINDS for kind in kinds)
        ):
            raise ValueError("recall kind filter is invalid")
        if as_of is not None:
            as_of = canonical_timestamp(as_of, field="recall as_of")
        reference_time = as_of or utc_now()
        terms = search_terms(query, limit=_MAX_RECALL_TERMS, cover_tail=True)
        exact_id = query if _KNOWLEDGE_ID.fullmatch(query) else None
        candidate_ids: list[str] = []
        channels: dict[str, list[str]] = defaultdict(list)
        derived_manifest_path = self.root / ".deeplaw" / "derived" / "manifest.json"
        derived_manifest_sha256 = None
        derived_lexical_ready = False
        try:
            if (
                not derived_manifest_path.is_symlink()
                and derived_manifest_path.is_file()
                and derived_manifest_path.stat().st_size <= 4 * 1024 * 1024
            ):
                derived_manifest_sha256 = sha256_file(derived_manifest_path)
                derived_manifest = strict_json_loads(derived_manifest_path.read_bytes())
                derived_lexical_ready = bool(
                    not force_canonical_lexical
                    and isinstance(derived_manifest, dict)
                    and derived_manifest.get("schema_version") == DERIVED_MANIFEST_SCHEMA
                    and derived_manifest.get("input_audit_head") == self.audit_head
                    and derived_manifest.get("legacy_audit_head") == self.legacy_audit_head
                    and derived_manifest.get("knowledge_revision_count")
                    == self.connection.execute(
                        "SELECT COUNT(*) FROM autonomous_search_v3"
                    ).fetchone()[0]
                    and self.connection.execute(
                        "SELECT COUNT(*) FROM derived_rebuild_queue_v3 "
                        "WHERE completed_at IS NULL"
                    ).fetchone()[0]
                    == 0
                )
        except (OSError, TypeError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError):
            derived_manifest_sha256 = None
            derived_lexical_ready = False
        if exact_id is not None:
            candidate_ids.append(exact_id)
            channels[exact_id].append("exact")
        expression = "" if exact_id is not None else fts_query(terms)
        lexical_query_failed = False
        if expression and as_of is None and derived_lexical_ready:
            try:
                rows = self.connection.execute(
                    """
                    SELECT knowledge_id FROM autonomous_search_v3
                    WHERE autonomous_search_v3 MATCH ?
                    ORDER BY bm25(autonomous_search_v3), knowledge_id
                    LIMIT 200
                    """,
                    (expression,),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
                lexical_query_failed = True
            for row in rows:
                if row["knowledge_id"] not in candidate_ids:
                    candidate_ids.append(row["knowledge_id"])
                channels[row["knowledge_id"]].append("lexical")
        temporal_scan_truncated = False
        if as_of is not None and terms and exact_id is None:
            temporal_rows = self.connection.execute(
                """
                WITH ranked AS (
                    SELECT knowledge_revisions_v3.knowledge_id,
                           knowledge_revisions_v3.revision_id,
                           knowledge_revisions_v3.markdown_sha256,
                           knowledge_revisions_v3.title,
                           knowledge_revisions_v3.tags_json,
                           knowledge_revisions_v3.semantic_key,
                           ROW_NUMBER() OVER (
                               PARTITION BY knowledge_revisions_v3.knowledge_id
                               ORDER BY knowledge_revisions_v3.recorded_at DESC,
                                        knowledge_revisions_v3.revision_id DESC
                           ) AS rank
                    FROM knowledge_revisions_v3
                    WHERE knowledge_revisions_v3.recorded_at <= ?
                      AND knowledge_revisions_v3.lifecycle <> 'quarantined'
                )
                SELECT * FROM ranked WHERE rank = 1
                ORDER BY knowledge_id LIMIT 501
                """,
                (as_of,),
            ).fetchall()
            temporal_scan_truncated = len(temporal_rows) > 500
            for row in temporal_rows[:500]:
                body = parse_knowledge_markdown(_read_object(self.root, row["markdown_sha256"]))[
                    "body"
                ]
                tags = strict_json_loads(row["tags_json"])
                haystack = compact_text(
                    " ".join(
                        (
                            row["title"],
                            body,
                            row["semantic_key"] or "",
                            *tags,
                        )
                    )
                )
                if any(compact_text(term) in haystack for term in terms):
                    if row["knowledge_id"] not in candidate_ids:
                        candidate_ids.append(row["knowledge_id"])
                    channels[row["knowledge_id"]].append("temporal_lexical")
        canonical_scan_truncated = False
        if (
            as_of is None
            and exact_id is None
            and terms
            and (not derived_lexical_ready or lexical_query_failed or not candidate_ids)
        ):
            admitted_sensitivities = SENSITIVITY_ORDER[
                : SENSITIVITY_ORDER.index(max_sensitivity) + 1
            ]
            placeholders = ",".join("?" for _ in admitted_sensitivities)
            rows = self.connection.execute(
                "SELECT knowledge_objects_v3.knowledge_id "
                "FROM knowledge_objects_v3 "
                "JOIN knowledge_revisions_v3 ON knowledge_revisions_v3.revision_id = "
                "knowledge_objects_v3.current_revision_id "
                "WHERE knowledge_revisions_v3.lifecycle = 'active' "
                "AND knowledge_revisions_v3.scope = ? "
                f"AND knowledge_revisions_v3.sensitivity IN ({placeholders}) "
                "ORDER BY knowledge_objects_v3.updated_at DESC, "
                "knowledge_objects_v3.knowledge_id LIMIT 501",
                (scope, *admitted_sensitivities),
            ).fetchall()
            canonical_scan_truncated = len(rows) > 500
            fallback_ids: list[str] = []
            for row in rows[:500]:
                try:
                    current = self.get_current(row["knowledge_id"], include_inactive=True)
                except KeyError:
                    continue
                haystack = compact_text(
                    " ".join(
                        (
                            current["title"],
                            current["body"],
                            current.get("semantic_key") or "",
                            *current["tags"],
                        )
                    )
                )
                if any(compact_text(term) in haystack for term in terms):
                    fallback_ids.append(row["knowledge_id"])
                    channels[row["knowledge_id"]].append("lexical_fallback")
            if not derived_lexical_ready:
                candidate_ids = fallback_ids + [
                    item for item in candidate_ids if item not in fallback_ids
                ]
            else:
                candidate_ids.extend(item for item in fallback_ids if item not in candidate_ids)
        if candidate_ids and as_of is None:
            graph_seed_ids: list[str] = []
            for candidate_id in candidate_ids[:100]:
                try:
                    seed = self.get_current(candidate_id, include_inactive=True)
                except KeyError:
                    continue
                if (
                    seed["lifecycle"] == "active"
                    and self.revision_provenance_admitted(seed)
                    and seed["scope"] == scope
                    and SENSITIVITY_ORDER.index(seed["sensitivity"])
                    <= SENSITIVITY_ORDER.index(max_sensitivity)
                    and (not kinds or seed["kind"] in kinds)
                    and (seed["expires_at"] is None or seed["expires_at"] > reference_time)
                    and (seed["valid_from"] is None or seed["valid_from"] <= reference_time)
                    and (seed["valid_to"] is None or seed["valid_to"] > reference_time)
                ):
                    graph_seed_ids.append(candidate_id)
            seeds = tuple(graph_seed_ids)
        else:
            seeds = ()
        if seeds:
            placeholders = ",".join("?" for _ in seeds)
            relation_rows = self.connection.execute(
                f"""
                SELECT knowledge_relation_revisions_v3.subject_knowledge_id,
                       knowledge_relation_revisions_v3.object_knowledge_id,
                       knowledge_relation_revisions_v3.scope,
                       knowledge_relation_revisions_v3.sensitivity,
                       knowledge_relation_revisions_v3.valid_from,
                       knowledge_relation_revisions_v3.valid_to,
                       knowledge_relation_revisions_v3.evidence_refs_json
                FROM knowledge_relations_v3
                JOIN knowledge_relation_revisions_v3
                  ON knowledge_relation_revisions_v3.relation_revision_id =
                     knowledge_relations_v3.current_revision_id
                WHERE knowledge_relation_revisions_v3.lifecycle = 'active'
                  AND (
                    knowledge_relation_revisions_v3.subject_knowledge_id IN ({placeholders})
                    OR knowledge_relation_revisions_v3.object_knowledge_id IN ({placeholders})
                  )
                ORDER BY knowledge_relation_revisions_v3.relation_key
                LIMIT 500
                """,
                (*seeds, *seeds),
            ).fetchall()
            for row in relation_rows:
                if row["scope"] != scope or SENSITIVITY_ORDER.index(
                    row["sensitivity"]
                ) > SENSITIVITY_ORDER.index(max_sensitivity):
                    continue
                relation = {
                    **dict(row),
                    "evidence_refs": strict_json_loads(row["evidence_refs_json"]),
                }
                if not self.relation_provenance_admitted(relation):
                    continue
                if (
                    relation["valid_from"] is not None
                    and relation["valid_from"] > reference_time
                ) or (
                    relation["valid_to"] is not None
                    and relation["valid_to"] <= reference_time
                ):
                    continue
                for candidate in (
                    row["subject_knowledge_id"],
                    row["object_knowledge_id"],
                ):
                    if candidate not in candidate_ids:
                        candidate_ids.append(candidate)
                    channels[candidate].append("graph")
        selected: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        selected_chars = 0
        selected_provider_chars = 0
        admitted_candidate_count = 0
        candidate_state_receipts: list[dict[str, Any]] = []
        for knowledge_id in candidate_ids:
            try:
                current = (
                    self.get_at(knowledge_id, recorded_at=as_of)
                    if as_of is not None
                    else self.get_current(knowledge_id, include_inactive=True)
                )
            except KeyError:
                continue
            if current["scope"] != scope or SENSITIVITY_ORDER.index(
                current["sensitivity"]
            ) > SENSITIVITY_ORDER.index(max_sensitivity):
                # Even opaque IDs or aggregate counts would disclose the
                # existence of knowledge outside this read boundary.
                continue
            admitted_candidate_count += 1
            reasons: list[str] = []
            provenance_admitted = self.revision_provenance_admitted(current)
            if current["lifecycle"] != "active":
                reasons.append(f"lifecycle:{current['lifecycle']}")
            if not provenance_admitted:
                reasons.append("source_provenance_inactive")
            if kinds and current["kind"] not in kinds:
                reasons.append("kind")
            if current["expires_at"] is not None and current["expires_at"] <= reference_time:
                reasons.append("expired")
            if current["valid_from"] is not None and current["valid_from"] > reference_time:
                reasons.append("not_yet_valid")
            if current["valid_to"] is not None and current["valid_to"] <= reference_time:
                reasons.append("no_longer_valid")
            candidate_state_receipts.append(
                {
                    "candidate_sha256": sha256_bytes(knowledge_id.encode("utf-8")),
                    "revision_id": current["revision_id"],
                    "lifecycle": current["lifecycle"],
                    "provenance_admitted": provenance_admitted,
                    "reasons": reasons,
                }
            )
            if reasons:
                rejected.append(
                    {
                        "candidate_sha256": sha256_bytes(knowledge_id.encode("utf-8")),
                        "reason": ",".join(reasons),
                    }
                )
                continue
            remaining = max_chars - selected_chars
            if remaining < 100 or len(selected) >= limit:
                rejected.append(
                    {
                        "candidate_sha256": sha256_bytes(knowledge_id.encode("utf-8")),
                        "reason": "selection_budget",
                    }
                )
                continue
            body = current.pop("body")
            excerpt = (
                body if len(body) <= min(1_600, remaining) else body[: min(1_599, remaining)] + "…"
            )
            current["content"] = excerpt
            current["content_truncated"] = excerpt != body
            source_refs = current.get("source_refs", [])
            bounded_refs: list[dict[str, Any]] = []
            for reference in source_refs[:1]:
                bounded_refs.append(bounded_source_reference(reference))
            current["source_refs"] = bounded_refs
            current["source_ref_count"] = len(source_refs)
            current["source_refs_truncated"] = len(source_refs) > len(bounded_refs)
            current["tags"] = current.get("tags", [])[:16]
            metadata = current.get("metadata", {})
            current["metadata"] = {
                "quarantine_reasons": metadata.get("quarantine_reasons", []),
                "memory_type": metadata.get("memory_type"),
                "preference_basis": metadata.get("preference_basis"),
            }
            current["channels"] = sorted(set(channels[knowledge_id]))
            current["selection_reason"] = ",".join(current["channels"])
            provider_chars = len(canonical_json(current))
            if selected_provider_chars + provider_chars > _MAX_RECALL_PROVIDER_CHARS:
                rejected.append(
                    {
                        "candidate_sha256": sha256_bytes(knowledge_id.encode("utf-8")),
                        "reason": "provider_payload_budget",
                    }
                )
                continue
            selected.append(current)
            selected_chars += len(excerpt)
            selected_provider_chars += provider_chars
        admitted_relations = self._relations_at(as_of)

        def has_admitted_contradiction(item: dict[str, Any]) -> bool:
            for relation in admitted_relations:
                if relation["predicate"] != "contradicts":
                    continue
                if relation["scope"] != scope or SENSITIVITY_ORDER.index(
                    relation["sensitivity"]
                ) > SENSITIVITY_ORDER.index(max_sensitivity):
                    continue
                if not self.relation_provenance_admitted(relation):
                    continue
                endpoints = {
                    relation["subject_knowledge_id"],
                    relation["object_knowledge_id"],
                }
                if item["knowledge_id"] not in endpoints:
                    continue
                if (
                    relation["valid_from"] is not None and relation["valid_from"] > reference_time
                ) or (relation["valid_to"] is not None and relation["valid_to"] <= reference_time):
                    continue
                other_id = next(value for value in endpoints if value != item["knowledge_id"])
                try:
                    other = (
                        self.get_at(other_id, recorded_at=as_of)
                        if as_of is not None
                        else self.get_current(other_id)
                    )
                except KeyError:
                    continue
                if (
                    other["lifecycle"] == "active"
                    and self.revision_provenance_admitted(other)
                    and other["scope"] == scope
                    and SENSITIVITY_ORDER.index(other["sensitivity"])
                    <= SENSITIVITY_ORDER.index(max_sensitivity)
                    and (other["expires_at"] is None or other["expires_at"] > reference_time)
                    and (other["valid_from"] is None or other["valid_from"] <= reference_time)
                    and (other["valid_to"] is None or other["valid_to"] > reference_time)
                ):
                    return True
            return False

        contradictions = []
        for item in selected:
            if item["epistemic_state"] == "contested" or has_admitted_contradiction(item):
                contradictions.append(
                    {
                        "knowledge_id": item["knowledge_id"],
                        "revision_id": item["revision_id"],
                        "reason": (
                            "epistemic_state:contested"
                            if item["epistemic_state"] == "contested"
                            else "active_contradicts_relation"
                        ),
                    }
                )
        planned_channels = sorted(
            {channel for values in channels.values() for channel in values}
            | (
                {"lexical"}
                if expression and as_of is None and derived_lexical_ready
                else set()
            )
            | ({"temporal_lexical"} if as_of is not None else set())
        )
        plan = {
            "schema_version": "deeplaw.knowledge-query-plan/v2",
            "intent": "autonomous_knowledge_recall",
            "query_sha256": sha256_bytes(query.encode("utf-8")),
            "channels": planned_channels,
            "scope": scope,
            "max_sensitivity": max_sensitivity,
            "as_of": as_of,
            "filters": {"kinds": sorted(kinds)},
            "budget": {
                "items": limit,
                "characters": max_chars,
                "provider_characters": _MAX_RECALL_PROVIDER_CHARS,
            },
            "audit_head": self.audit_head,
            "legacy_audit_head": self.legacy_audit_head,
            "candidate_count": admitted_candidate_count,
            "candidate_state_sha256": sha256_bytes(
                canonical_json(candidate_state_receipts).encode("utf-8")
            ),
            "derived_manifest_sha256": derived_manifest_sha256,
            "derived_lexical_ready": derived_lexical_ready,
        }
        _validate_contract("knowledge-query-plan.v2.schema.json", plan)
        plan_sha256 = sha256_bytes(canonical_json(plan).encode("utf-8"))
        gaps: list[str] = []
        if not selected:
            gaps.append("no admitted autonomous knowledge matched the query")
        if rejected:
            gaps.append("some candidates were rejected by admission or selection budgets")
        if temporal_scan_truncated:
            gaps.append("historical lexical scan reached its 500-object resource bound")
        if not derived_lexical_ready and as_of is None:
            gaps.append("derived lexical state was stale; bounded canonical fallback was used")
        if canonical_scan_truncated:
            gaps.append("canonical lexical fallback reached its 500-object resource bound")
        selection_receipts = [
            {
                "knowledge_id": item["knowledge_id"],
                "revision_id": item["revision_id"],
                "selection_reason": item["selection_reason"],
            }
            for item in selected
        ]
        selection_sha256 = sha256_bytes(canonical_json(selection_receipts).encode("utf-8"))
        result = {
            "schema_version": "deeplaw.autonomous-recall/v1",
            "vault_id": self.vault_id,
            "query": query,
            "query_plan": plan,
            "query_plan_sha256": plan_sha256,
            "selection_receipts": selection_receipts,
            "selection_sha256": selection_sha256,
            "results": selected,
            "contradictions": contradictions,
            "rejected": rejected[:32],
            "gaps": gaps,
            "budget": {
                "max_items": limit,
                "selected_items": len(selected),
                "max_characters": max_chars,
                "selected_characters": selected_chars,
                "max_provider_characters": _MAX_RECALL_PROVIDER_CHARS,
                "selected_provider_characters": selected_provider_chars,
            },
            "audit_head": self.audit_head,
            "authority_changed_by_ranking": False,
        }
        result["recall_digest"] = sha256_bytes(canonical_json(result).encode("utf-8"))
        return result

    def explain_recall(
        self,
        query: str,
        *,
        scope: Scope = "project",
        max_sensitivity: Sensitivity = "private",
        limit: int = 5,
        max_chars: int = 5_000,
        as_of: str | None = None,
        kinds: tuple[str, ...] = (),
        force_canonical_lexical: bool = False,
    ) -> dict[str, Any]:
        recall = self.recall(
            query,
            scope=scope,
            max_sensitivity=max_sensitivity,
            limit=limit,
            max_chars=max_chars,
            as_of=as_of,
            kinds=kinds,
            force_canonical_lexical=force_canonical_lexical,
        )
        return {
            "schema_version": "deeplaw.knowledge-query-explanation/v1",
            "vault_id": self.vault_id,
            "query": recall["query"],
            "query_plan": recall["query_plan"],
            "query_plan_sha256": recall["query_plan_sha256"],
            "selection_receipts": recall["selection_receipts"],
            "selection_sha256": recall["selection_sha256"],
            "contradictions": recall["contradictions"],
            "rejected": recall["rejected"],
            "gaps": recall["gaps"],
            "budget": recall["budget"],
            "audit_head": recall["audit_head"],
            "authority_changed_by_ranking": False,
        }

    def list_conflicts(self, *, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 500:
            raise ValueError("workspace conflict limit is invalid")
        rows = self.connection.execute(
            """
            SELECT conflict_id, knowledge_id, base_revision_id, current_revision_id,
                   object_sha256, workspace_path, reason, detected_at
            FROM workspace_conflicts_v3
            WHERE resolved_at IS NULL
            ORDER BY detected_at, conflict_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        total = self.connection.execute(
            "SELECT COUNT(*) FROM workspace_conflicts_v3 WHERE resolved_at IS NULL"
        ).fetchone()[0]
        return {
            "schema_version": "deeplaw.workspace-conflicts/v1",
            "vault_id": self.vault_id,
            "conflicts": [dict(row) for row in rows],
            "returned_count": len(rows),
            "total_count": total,
            "truncated": total > len(rows),
            "audit_head": self.audit_head,
        }

    def build_capsule(
        self,
        *,
        task: str,
        goal: str | None = None,
        scope: Scope = "project",
        max_sensitivity: Sensitivity = "private",
        limit: int = 8,
        max_chars: int = 8_000,
        as_of: str | None = None,
        kinds: tuple[str, ...] = (),
        confirm_no_case_data: bool = False,
        force_canonical_lexical: bool = False,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError("Knowledge Capsule requires confirmation that no case data is present")
        task = _bounded_string(task, field="Capsule task", maximum=5_000)
        selected_goal = (
            _bounded_string(goal, field="Capsule goal", maximum=2_000) if goal is not None else None
        )
        selected_as_of = (
            canonical_timestamp(as_of, field="Capsule as_of") if as_of is not None else None
        )
        query = f"{task} {selected_goal or ''}".strip()
        recall = self.recall(
            query,
            scope=scope,
            max_sensitivity=max_sensitivity,
            limit=limit,
            max_chars=max_chars,
            as_of=selected_as_of,
            kinds=kinds,
            force_canonical_lexical=force_canonical_lexical,
        )
        memory = [item for item in recall["results"] if item["kind"] == "memory"]
        derived = [item for item in recall["results"] if item["kind"] != "memory"]
        sections = {
            "official_evidence": [],
            "user_private_evidence": [],
            "source_derived_knowledge": [],
            "agent_derived_knowledge": derived,
            "agent_memory": memory,
            "contradictions": recall["contradictions"],
            "limitations": [
                "Agent-derived knowledge is not human verification, legal authority, "
                "or permission.",
            ],
            "gaps": recall["gaps"],
            "receipts": [
                {
                    "knowledge_id": item["knowledge_id"],
                    "revision_id": item["revision_id"],
                    "markdown_sha256": item["markdown_sha256"],
                }
                for item in recall["results"]
            ],
        }
        capsule = {
            "schema_version": KNOWLEDGE_CAPSULE_SCHEMA,
            "vault_id": self.vault_id,
            "task": task,
            "goal": selected_goal,
            "as_of": selected_as_of,
            "query_plan": recall["query_plan"],
            "query_plan_sha256": recall["query_plan_sha256"],
            "sections": sections,
            "budget": recall["budget"],
            "audit_head": self.audit_head,
            "created_at": utc_now(),
            "capsule_id": "",
            "capsule_digest": "",
        }
        digest_body = {
            key: value
            for key, value in capsule.items()
            if key not in {"capsule_id", "capsule_digest"}
        }
        digest = sha256_bytes(canonical_json(digest_body).encode("utf-8"))
        capsule["capsule_digest"] = digest
        capsule["capsule_id"] = stable_id("capsule", self.vault_id, digest)
        if len(canonical_json(capsule)) > 65_536:
            raise RuntimeError("Knowledge Capsule exceeds its hard 64 KiB provider budget")
        _validate_contract("knowledge-capsule.v2.schema.json", capsule)
        return capsule

    def semantic_lint(self) -> dict[str, Any]:
        current_rows = self.connection.execute(
            """
            SELECT knowledge_revisions_v3.*
            FROM knowledge_objects_v3
            JOIN knowledge_revisions_v3
              ON knowledge_revisions_v3.revision_id = knowledge_objects_v3.current_revision_id
            ORDER BY knowledge_objects_v3.knowledge_id
            """
        ).fetchall()
        issues: list[dict[str, Any]] = []
        issue_count = 0

        def add_issue(issue: dict[str, Any]) -> None:
            nonlocal issue_count
            issue_count += 1
            if len(issues) < _MAX_LINT_ISSUES:
                issues.append(issue)

        semantic_index: dict[tuple[str, str], list[str]] = defaultdict(list)
        digest_index: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
        known_ids = {row["knowledge_id"] for row in current_rows}
        linked_ids: set[str] = set()
        for row in current_rows:
            if row["semantic_key"]:
                semantic_index[(row["kind"], row["semantic_key"])].append(row["knowledge_id"])
            if row["lifecycle"] == "active":
                digest_index[
                    (
                        row["kind"],
                        row["scope"],
                        row["sensitivity"],
                        row["semantic_digest"],
                    )
                ].append(row["knowledge_id"])
            if row["source_free"]:
                add_issue(
                    {
                        "code": "source_free",
                        "severity": "info",
                        "knowledge_id": row["knowledge_id"],
                    }
                )
            if row["lifecycle"] == "active" and not self.revision_provenance_admitted(
                self._revision_row(row, include_body=False)
            ):
                add_issue(
                    {
                        "code": "source_provenance_inactive",
                        "severity": "warning",
                        "knowledge_id": row["knowledge_id"],
                    }
                )
            payload = _read_object(self.root, row["markdown_sha256"])
            body = parse_knowledge_markdown(payload)["body"]
            for link in _WIKILINK.findall(body):
                if _KNOWLEDGE_ID.fullmatch(link):
                    linked_ids.add(link)
                    if link not in known_ids:
                        add_issue(
                            {
                                "code": "broken_wikilink",
                                "severity": "warning",
                                "knowledge_id": row["knowledge_id"],
                                "target_sha256": sha256_bytes(link.encode("utf-8")),
                            }
                        )
        for (kind, semantic_key), knowledge_ids in semantic_index.items():
            if len(knowledge_ids) > 1:
                add_issue(
                    {
                        "code": "duplicate_semantic_key",
                        "severity": "warning",
                        "kind": kind,
                        "semantic_key": semantic_key,
                        "knowledge_ids": knowledge_ids[:_MAX_DUPLICATE_IDS_PER_ISSUE],
                        "knowledge_id_count": len(knowledge_ids),
                        "knowledge_ids_truncated": len(knowledge_ids)
                        > _MAX_DUPLICATE_IDS_PER_ISSUE,
                    }
                )
        for (kind, scope, sensitivity, digest), knowledge_ids in digest_index.items():
            if len(knowledge_ids) > 1:
                add_issue(
                    {
                        "code": "exact_semantic_duplicate",
                        "severity": "error",
                        "kind": kind,
                        "scope": scope,
                        "sensitivity": sensitivity,
                        "semantic_digest": digest,
                        "knowledge_ids": knowledge_ids[:_MAX_DUPLICATE_IDS_PER_ISSUE],
                        "knowledge_id_count": len(knowledge_ids),
                        "knowledge_ids_truncated": len(knowledge_ids)
                        > _MAX_DUPLICATE_IDS_PER_ISSUE,
                    }
                )
        relation_rows = self._current_relations()
        for relation in relation_rows:
            if not self.relation_provenance_admitted(relation):
                add_issue(
                    {
                        "code": "relation_provenance_inactive",
                        "severity": "warning",
                        "relation_sha256": sha256_bytes(
                            relation["relation_key"].encode("utf-8")
                        ),
                    }
                )
                continue
            linked_ids.add(relation["subject_knowledge_id"])
            linked_ids.add(relation["object_knowledge_id"])
        for row in current_rows:
            if row["lifecycle"] == "active" and row["knowledge_id"] not in linked_ids:
                add_issue(
                    {
                        "code": "orphan",
                        "severity": "info",
                        "knowledge_id": row["knowledge_id"],
                    }
                )
        report = {
            "schema_version": "deeplaw.semantic-lint/v1",
            "vault_id": self.vault_id,
            "audit_head": self.audit_head,
            "issue_count": issue_count,
            "returned_issue_count": len(issues),
            "issues_truncated": issue_count > len(issues),
            "issues": issues,
            "generated_at": utc_now(),
            "derived": True,
            "authority": "none",
        }
        return report

    def _current_relations(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT knowledge_relation_revisions_v3.*
            FROM knowledge_relations_v3
            JOIN knowledge_relation_revisions_v3
              ON knowledge_relation_revisions_v3.relation_revision_id =
                 knowledge_relations_v3.current_revision_id
            JOIN knowledge_objects_v3 AS subject_object
              ON subject_object.knowledge_id =
                 knowledge_relation_revisions_v3.subject_knowledge_id
            JOIN knowledge_revisions_v3 AS subject_revision
              ON subject_revision.revision_id = subject_object.current_revision_id
            JOIN knowledge_objects_v3 AS object_object
              ON object_object.knowledge_id =
                 knowledge_relation_revisions_v3.object_knowledge_id
            JOIN knowledge_revisions_v3 AS object_revision
              ON object_revision.revision_id = object_object.current_revision_id
            WHERE knowledge_relation_revisions_v3.lifecycle = 'active'
              AND subject_revision.lifecycle = 'active'
              AND object_revision.lifecycle = 'active'
            ORDER BY knowledge_relation_revisions_v3.relation_key
            """
        ).fetchall()
        return [
            {
                **dict(row),
                "evidence_refs": strict_json_loads(row["evidence_refs_json"]),
                "source_free": bool(row["source_free"]),
            }
            for row in rows
        ]

    def _relations_at(self, recorded_at: str | None) -> list[dict[str, Any]]:
        if recorded_at is None:
            return self._current_relations()
        instant = canonical_timestamp(recorded_at, field="relation transaction time")
        rows = self.connection.execute(
            """
            WITH ranked AS (
                SELECT knowledge_relation_revisions_v3.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY relation_key
                           ORDER BY recorded_at DESC, relation_revision_id DESC
                       ) AS rank
                FROM knowledge_relation_revisions_v3
                WHERE recorded_at <= ?
            )
            SELECT * FROM ranked
            WHERE rank = 1 AND lifecycle = 'active'
            ORDER BY relation_key
            """,
            (instant,),
        ).fetchall()
        return [
            {
                **dict(row),
                "evidence_refs": strict_json_loads(row["evidence_refs_json"]),
                "source_free": bool(row["source_free"]),
            }
            for row in rows
        ]

    def graph(
        self,
        *,
        knowledge_id: str | None = None,
        scope: Scope = "project",
        max_sensitivity: Sensitivity = "private",
        limit: int = 100,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        if knowledge_id is not None and not _KNOWLEDGE_ID.fullmatch(knowledge_id):
            raise ValueError("knowledge ID is invalid")
        if scope not in SCOPES or max_sensitivity not in SENSITIVITIES:
            raise ValueError("graph scope or sensitivity is invalid")
        if not 1 <= limit <= 500:
            raise ValueError("graph relation limit is invalid")
        selected_as_of = (
            canonical_timestamp(as_of, field="graph as_of") if as_of is not None else None
        )
        reference_time = selected_as_of or utc_now()
        admitted: dict[str, dict[str, Any]] = {}
        rejected: list[dict[str, str]] = []
        relations: list[dict[str, Any]] = []
        for relation in self._relations_at(selected_as_of):
            if knowledge_id is not None and knowledge_id not in {
                relation["subject_knowledge_id"],
                relation["object_knowledge_id"],
            }:
                continue
            if relation["scope"] != scope or SENSITIVITY_ORDER.index(
                relation["sensitivity"]
            ) > SENSITIVITY_ORDER.index(max_sensitivity):
                continue
            if not self.relation_provenance_admitted(relation):
                rejected.append(
                    {
                        "candidate_sha256": sha256_bytes(
                            relation["relation_key"].encode("utf-8")
                        ),
                        "reason": "relation_evidence_inactive",
                    }
                )
                continue
            endpoints: list[dict[str, Any]] = []
            denied = False
            for endpoint_id in (
                relation["subject_knowledge_id"],
                relation["object_knowledge_id"],
            ):
                try:
                    current = (
                        self.get_at(endpoint_id, recorded_at=selected_as_of)
                        if selected_as_of is not None
                        else self.get_current(endpoint_id)
                    )
                except KeyError:
                    denied = True
                    break
                if current["scope"] != scope or SENSITIVITY_ORDER.index(
                    current["sensitivity"]
                ) > SENSITIVITY_ORDER.index(max_sensitivity):
                    denied = True
                    break
                if (
                    current["lifecycle"] != "active"
                    or not self.revision_provenance_admitted(current)
                    or (
                        current["expires_at"] is not None
                        and current["expires_at"] <= reference_time
                    )
                    or (
                        current["valid_from"] is not None and current["valid_from"] > reference_time
                    )
                    or (current["valid_to"] is not None and current["valid_to"] <= reference_time)
                ):
                    rejected.append(
                        {
                            "candidate_sha256": sha256_bytes(
                                relation["relation_key"].encode("utf-8")
                            ),
                            "reason": "endpoint_lifecycle_or_temporal_interval",
                        }
                    )
                    denied = True
                    break
                endpoints.append(current)
            if denied:
                continue
            if (relation["valid_from"] is not None and relation["valid_from"] > reference_time) or (
                relation["valid_to"] is not None and relation["valid_to"] <= reference_time
            ):
                rejected.append(
                    {
                        "candidate_sha256": sha256_bytes(relation["relation_key"].encode("utf-8")),
                        "reason": "relation_temporal_interval",
                    }
                )
                continue
            for endpoint in endpoints:
                admitted[endpoint["knowledge_id"]] = {
                    "knowledge_id": endpoint["knowledge_id"],
                    "revision_id": endpoint["revision_id"],
                    "title": endpoint["title"],
                    "kind": endpoint["kind"],
                    "lifecycle": endpoint["lifecycle"],
                    "origin": endpoint["origin"],
                    "authority": endpoint["authority"],
                    "scope": endpoint["scope"],
                    "sensitivity": endpoint["sensitivity"],
                }
            relation_card = {
                key: relation[key]
                for key in (
                    "relation_key",
                    "relation_revision_id",
                    "subject_knowledge_id",
                    "predicate",
                    "object_knowledge_id",
                    "valid_from",
                    "valid_to",
                    "recorded_at",
                    "origin",
                    "authority",
                    "scope",
                    "sensitivity",
                    "writer_id",
                )
            }
            relation_card["evidence_refs"] = [
                bounded_source_reference(reference)
                for reference in relation["evidence_refs"][:1]
                if isinstance(reference, dict)
            ]
            relation_card["evidence_ref_count"] = len(relation["evidence_refs"])
            relation_card["source_free"] = relation["source_free"]
            relation_card["legal_authority"] = False
            relations.append(relation_card)
            if len(relations) >= limit:
                break
        return {
            "schema_version": "deeplaw.knowledge-graph-view/v1",
            "vault_id": self.vault_id,
            "knowledge_id": knowledge_id,
            "as_of": selected_as_of,
            "nodes": sorted(admitted.values(), key=lambda item: item["knowledge_id"]),
            "relations": relations,
            "rejected": rejected[:100],
            "budget": {"max_relations": limit, "selected_relations": len(relations)},
            "audit_head": self.audit_head,
            "derived_adjacency": True,
            "canonical_relation_revisions": True,
        }

    def inspect(self) -> dict[str, Any]:
        verification = self.verify()
        counts = {
            "knowledge_objects": self.connection.execute(
                "SELECT COUNT(*) FROM knowledge_objects_v3"
            ).fetchone()[0],
            "active_knowledge": self.connection.execute(
                """
                SELECT COUNT(*) FROM knowledge_objects_v3
                JOIN knowledge_revisions_v3
                  ON knowledge_revisions_v3.revision_id =
                     knowledge_objects_v3.current_revision_id
                WHERE knowledge_revisions_v3.lifecycle = 'active'
                """
            ).fetchone()[0],
            "relation_revisions": self.connection.execute(
                "SELECT COUNT(*) FROM knowledge_relation_revisions_v3"
            ).fetchone()[0],
            "feedback_events": self.connection.execute(
                "SELECT COUNT(*) FROM knowledge_feedback_v3"
            ).fetchone()[0],
            "active_grants": self.connection.execute(
                "SELECT COUNT(*) FROM knowledge_sink_grants_v3 WHERE revoked_at IS NULL"
            ).fetchone()[0],
            "pending_rebuilds": self.connection.execute(
                "SELECT COUNT(*) FROM derived_rebuild_queue_v3 WHERE completed_at IS NULL"
            ).fetchone()[0],
        }
        return {
            "schema_version": "deeplaw.autonomous-inspection/v1",
            "vault_id": self.vault_id,
            "installed": True,
            "agent_ready": verification["valid"],
            "counts": counts,
            "verification": verification,
            "audit_head": self.audit_head,
        }

    def rebuild_derived(self) -> dict[str, Any]:
        self._require_write()
        reference_time = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            candidate_rows = self.connection.execute(
                """
                SELECT knowledge_objects_v3.workspace_path AS current_workspace_path,
                       knowledge_revisions_v3.*
                FROM knowledge_objects_v3
                JOIN knowledge_revisions_v3
                  ON knowledge_revisions_v3.revision_id =
                     knowledge_objects_v3.current_revision_id
                WHERE knowledge_revisions_v3.lifecycle = 'active'
                ORDER BY knowledge_objects_v3.knowledge_id
                """
            ).fetchall()
            rows = [
                row
                for row in candidate_rows
                if self.revision_provenance_admitted(
                    self._revision_row(row, include_body=False)
                )
                and _interval_admits(
                    reference_time=reference_time,
                    valid_from=row["valid_from"],
                    valid_to=row["valid_to"],
                    expires_at=row["expires_at"],
                )
            ]
            admitted_ids = {row["knowledge_id"] for row in rows}
            relations = [
                relation
                for relation in self._current_relations()
                if self.relation_provenance_admitted(relation)
                and relation["subject_knowledge_id"] in admitted_ids
                and relation["object_knowledge_id"] in admitted_ids
                and _interval_admits(
                    reference_time=reference_time,
                    valid_from=relation["valid_from"],
                    valid_to=relation["valid_to"],
                )
            ]
            lint = self.semantic_lint()
            input_audit_head = self.audit_head
            pending_queue_ids = [
                row["queue_id"]
                for row in self.connection.execute(
                    "SELECT queue_id FROM derived_rebuild_queue_v3 "
                    "WHERE completed_at IS NULL ORDER BY created_at, queue_id"
                )
            ]
            self.connection.execute("DELETE FROM autonomous_search_v3")
            fts_rows: list[tuple[str, str, str, str, str, str]] = []
            for row in rows:
                payload = _read_object(self.root, row["markdown_sha256"])
                body = parse_knowledge_markdown(payload)["body"]
                tags = strict_json_loads(row["tags_json"])
                fts_row = (
                    row["knowledge_id"],
                    row["revision_id"],
                    " ".join(search_terms(row["title"])),
                    " ".join(search_terms(body)),
                    " ".join(search_terms(row["semantic_key"] or "")),
                    " ".join(search_terms(" ".join(tags))),
                )
                self.connection.execute(
                    "INSERT INTO autonomous_search_v3 VALUES (?, ?, ?, ?, ?, ?)",
                    fts_row,
                )
                fts_rows.append(fts_row)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        communities = self._communities(rows, relations)
        community_directory = self.root / "wiki" / "communities"
        if community_directory.is_symlink() or not community_directory.is_dir():
            raise RuntimeError("derived community directory is missing or unsafe")
        for stale in community_directory.glob("community_*.md"):
            if stale.is_symlink() or not stale.is_file():
                raise RuntimeError("derived community view is unsafe")
            stale.unlink()
        generated_files: list[dict[str, Any]] = []

        def write(relative: str, content: str) -> None:
            payload = content.encode("utf-8")
            if len(payload) > _MAX_MARKDOWN_BYTES:
                raise RuntimeError("derived workspace file exceeds its hard byte limit")
            destination = self.root / relative
            _atomic_owner_write(destination, payload)
            generated_files.append(
                {
                    "path": relative,
                    "byte_size": len(payload),
                    "sha256": sha256_bytes(payload),
                }
            )

        overview_lines = [
            "---",
            "schema: deeplaw.living-wiki-overview/v1",
            "derived_view: true",
            f"audit_head: {input_audit_head}",
            "authority: none",
            "---",
            "",
            "# DeepLaw Living Wiki",
            "",
            f"Current autonomous Knowledge Objects: {len(rows)}",
            f"Current canonical relations: {len(relations)}",
            f"Semantic lint issues: {lint['issue_count']}",
            "",
            "## Knowledge",
            "",
        ]
        overview_rows = rows[:_MAX_WIKI_ITEMS]
        overview_lines.extend(
            f"- [[{PurePosixPath(row['current_workspace_path']).with_suffix('').as_posix()}|"
            f"{row['title']}]] — `{row['kind']}` / "
            f"`{row['epistemic_state']}`"
            for row in overview_rows
        )
        if len(rows) > len(overview_rows):
            overview_lines.extend(
                [
                    "",
                    f"> View truncated to {len(overview_rows)} of {len(rows)} Knowledge Objects.",
                ]
            )
        overview_lines.extend(
            [
                "",
                "> This is a rebuildable navigation view. Authority remains in the "
                "Ledger and evidence.",
                "",
            ]
        )
        write("wiki/overview.md", "\n".join(overview_lines))
        write("wiki/index.md", "\n".join(overview_lines))
        lint_json = json.dumps(lint, ensure_ascii=False, indent=2, sort_keys=True)
        write(
            "wiki/gaps/semantic-lint.md",
            "---\nschema: deeplaw.semantic-lint-view/v1\nderived_view: true\n"
            f"audit_head: {input_audit_head}\n---\n\n"
            f"# Semantic Lint\n\n```json\n{lint_json}\n```\n",
        )
        workspace_paths = {
            row["knowledge_id"]: PurePosixPath(row["current_workspace_path"])
            .with_suffix("")
            .as_posix()
            for row in rows
        }
        for community in communities[:_MAX_COMMUNITY_VIEWS]:
            visible_members = community["knowledge_ids"][:_MAX_COMMUNITY_VIEW_MEMBERS]
            lines = [
                "---",
                "schema: deeplaw.community-view/v1",
                "derived_view: true",
                f"audit_head: {input_audit_head}",
                "authority: none",
                "---",
                "",
                f"# Community {community['community_id']}",
                "",
                f"Members: {len(community['knowledge_ids'])}",
                "",
                *[f"- [[{workspace_paths[item]}|{item}]]" for item in visible_members],
                "",
            ]
            if len(community["knowledge_ids"]) > len(visible_members):
                lines.extend(
                    [
                        f"> View truncated to {len(visible_members)} members.",
                        "",
                    ]
                )
            write(
                f"wiki/communities/{community['community_id']}.md",
                "\n".join(lines),
            )
        nodes = []
        positions: dict[str, str] = {}
        for index, row in enumerate(rows[:500]):
            node_id = stable_id("canvasnode", row["knowledge_id"])
            positions[row["knowledge_id"]] = node_id
            nodes.append(
                {
                    "id": node_id,
                    "type": "file",
                    "file": row["current_workspace_path"],
                    "x": (index % 5) * 420,
                    "y": (index // 5) * 260,
                    "width": 360,
                    "height": 200,
                }
            )
        edges = []
        for relation in relations[:1_000]:
            source = positions.get(relation["subject_knowledge_id"])
            target = positions.get(relation["object_knowledge_id"])
            if source and target:
                edges.append(
                    {
                        "id": relation["relation_revision_id"],
                        "fromNode": source,
                        "toNode": target,
                        "label": relation["predicate"],
                    }
                )
        canvas_payload = (
            json.dumps(
                {"nodes": nodes, "edges": edges},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        write("canvas/knowledge-graph.canvas", canvas_payload)
        manifest = {
            "schema_version": DERIVED_MANIFEST_SCHEMA,
            "input_audit_head": input_audit_head,
            "legacy_audit_head": self.legacy_audit_head,
            "generator": "deeplaw.knowledge-autonomy/v1",
            "generator_version": "1",
            "configuration": {
                "fts_tokenizer": "unicode61 remove_diacritics 2",
                "community_algorithm": "deterministic-connected-components",
                "canvas_node_limit": 500,
                "canvas_edge_limit": 1_000,
                "wiki_item_limit": _MAX_WIKI_ITEMS,
                "community_view_limit": _MAX_COMMUNITY_VIEWS,
                "community_member_limit": _MAX_COMMUNITY_VIEW_MEMBERS,
                "semantic_lint_issue_limit": _MAX_LINT_ISSUES,
            },
            "fts_rows_sha256": sha256_bytes(canonical_json(fts_rows).encode("utf-8")),
            "knowledge_revision_count": len(rows),
            "knowledge_revision_ids_sha256": sha256_bytes(
                canonical_json([row["revision_id"] for row in rows]).encode("utf-8")
            ),
            "relation_revision_count": len(relations),
            "relation_revision_ids_sha256": sha256_bytes(
                canonical_json([item["relation_revision_id"] for item in relations]).encode("utf-8")
            ),
            "files": sorted(generated_files, key=lambda item: item["path"]),
            "generated_at": utc_now(),
        }
        manifest["manifest_sha256"] = sha256_bytes(canonical_json(manifest).encode("utf-8"))
        _atomic_owner_write(
            self.root / ".deeplaw" / "derived" / "manifest.json",
            (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        completed_at = utc_now()
        if pending_queue_ids:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.executemany(
                    "UPDATE derived_rebuild_queue_v3 SET completed_at = ? "
                    "WHERE queue_id = ? AND completed_at IS NULL",
                    ((completed_at, queue_id) for queue_id in pending_queue_ids),
                )
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise
        return {
            **manifest,
            "knowledge_count": len(rows),
            "relation_count": len(relations),
            "community_count": len(communities),
            "lint": lint,
        }

    @staticmethod
    def _communities(
        rows: list[sqlite3.Row],
        relations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        adjacency: dict[str, set[str]] = {row["knowledge_id"]: set() for row in rows}
        for relation in relations:
            subject = relation["subject_knowledge_id"]
            object_id = relation["object_knowledge_id"]
            if subject in adjacency and object_id in adjacency:
                adjacency[subject].add(object_id)
                adjacency[object_id].add(subject)
        unseen = set(adjacency)
        communities: list[dict[str, Any]] = []
        while unseen:
            start = min(unseen)
            queue: deque[str] = deque([start])
            members: list[str] = []
            unseen.remove(start)
            while queue:
                current = queue.popleft()
                members.append(current)
                for neighbor in sorted(adjacency[current]):
                    if neighbor in unseen:
                        unseen.remove(neighbor)
                        queue.append(neighbor)
            community_id = stable_id("community", *sorted(members))
            communities.append({"community_id": community_id, "knowledge_ids": sorted(members)})
        return sorted(communities, key=lambda item: item["community_id"])

    def verify(self) -> dict[str, Any]:
        failures: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        verification_time = utc_now()
        event_payloads: dict[tuple[str, str], dict[str, Any]] = {}
        event_recorded_at: dict[tuple[str, str], str] = {}
        replay_events: list[dict[str, Any]] = []
        previous_hash: str | None = None
        previous_recorded_at: str | None = None
        expected_sequence = 0
        for row in self.connection.execute("SELECT * FROM autonomous_events_v3 ORDER BY sequence"):
            try:
                payload = strict_json_loads(row["payload_json"])
                if not isinstance(payload, dict):
                    raise ValueError("autonomous event payload must be an object")
                event = {
                    "schema_version": row["schema_version"],
                    "sequence": row["sequence"],
                    "event_type": row["event_type"],
                    "object_id": row["object_id"],
                    "payload": payload,
                    "previous_hash": row["previous_hash"],
                    "recorded_at": row["recorded_at"],
                }
                event_hash = sha256_bytes(canonical_json(event).encode("utf-8"))
                canonical_event_time = canonical_timestamp(
                    row["recorded_at"],
                    field="autonomous event recorded_at",
                )
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                failures.append(
                    {
                        "code": "event_payload_invalid",
                        "object_id": str(row["sequence"]),
                    }
                )
                break
            if (
                row["schema_version"] != AUTONOMOUS_EVENT_SCHEMA
                or row["event_type"] not in AUTONOMOUS_EVENT_TYPES
                or row["sequence"] != expected_sequence
                or row["object_id"] is None
                or row["previous_hash"] != previous_hash
                or row["event_hash"] != event_hash
                or canonical_event_time != row["recorded_at"]
                or (previous_recorded_at is not None and row["recorded_at"] < previous_recorded_at)
            ):
                failures.append({"code": "event_chain_invalid", "object_id": str(row["sequence"])})
                break
            previous_hash = event_hash
            previous_recorded_at = row["recorded_at"]
            expected_sequence += 1
            event_key = (row["event_type"], row["object_id"])
            if (
                event_key in event_payloads
                and row["event_type"] in AUTONOMOUS_UNIQUE_OBJECT_EVENT_TYPES
            ):
                failures.append(
                    {
                        "code": "duplicate_object_event",
                        "object_id": row["object_id"],
                    }
                )
            event_payloads[event_key] = payload
            event_recorded_at[event_key] = row["recorded_at"]
            replay_events.append(event)
        if previous_hash != self.audit_head or expected_sequence - 1 != self.sequence:
            failures.append({"code": "audit_head_invalid", "object_id": self.vault_id})
        integrity = self.connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            failures.append({"code": "sqlite_integrity_invalid", "object_id": self.vault_id})
        foreign_keys = self.connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            failures.append({"code": "foreign_key_invalid", "object_id": self.vault_id})
        try:
            with KnowledgeVault(self.root, read_only=True) as legacy:
                legacy_integrity = legacy.verify_integrity()
                legacy_audit_head = legacy.audit_head
            if (
                legacy_integrity.get("valid") is not True
                or legacy_audit_head != self.legacy_audit_head
            ):
                raise RuntimeError("legacy evidence governance is invalid")
        except (OSError, RuntimeError, sqlite3.DatabaseError, ValueError):
            failures.append({"code": "legacy_core_invalid", "object_id": self.vault_id})
        try:
            manifest_path = self.root / ".deeplaw" / "manifest.json"
            if manifest_path.is_symlink() or not manifest_path.is_file():
                raise RuntimeError("autonomous manifest is missing or unsafe")
            manifest = strict_json_loads(manifest_path.read_bytes())
            expected_manifest_fields = {
                "schema_version",
                "vault_id",
                "ledger",
                "object_store",
                "workspace",
                "derived_rebuildable",
                "installed_at",
                "manifest_sha256",
            }
            if not isinstance(manifest, dict) or set(manifest) != expected_manifest_fields:
                raise ValueError("autonomous manifest contract is invalid")
            manifest_body = {
                key: value for key, value in manifest.items() if key != "manifest_sha256"
            }
            core_row = self.connection.execute(
                "SELECT schema_version, installed_at FROM autonomous_core_v3"
            ).fetchone()
            installed_metadata = self.connection.execute(
                "SELECT value FROM autonomous_metadata_v3 WHERE key = 'installed_at'"
            ).fetchone()
            if not (
                manifest["schema_version"] == AUTONOMOUS_CORE_SCHEMA
                and manifest["vault_id"] == self.vault_id
                and manifest["ledger"] == "../vault.sqlite3"
                and manifest["object_store"] == "objects/sha256"
                and manifest["workspace"] == ".."
                and manifest["derived_rebuildable"] is True
                and canonical_timestamp(
                    manifest["installed_at"],
                    field="autonomous installed_at",
                )
                == manifest["installed_at"]
                and core_row is not None
                and core_row["schema_version"] == AUTONOMOUS_CORE_SCHEMA
                and core_row["installed_at"] == manifest["installed_at"]
                and installed_metadata is not None
                and installed_metadata["value"] == manifest["installed_at"]
                and manifest["manifest_sha256"]
                == sha256_bytes(canonical_json(manifest_body).encode("utf-8"))
            ):
                raise ValueError("autonomous manifest identity is invalid")
        except (
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            failures.append({"code": "autonomous_manifest_invalid", "object_id": self.vault_id})
        grant_writers = {
            row["grant_id"]: row["writer_id"]
            for row in self.connection.execute(
                "SELECT grant_id, writer_id FROM knowledge_sink_grants_v3"
            )
        }
        expected_unique_event_objects = {
            "autonomous_core_initialized": {self.vault_id},
            "evidence_object_bound": {
                row["binding_id"]
                for row in self.connection.execute(
                    "SELECT binding_id FROM evidence_bindings_v3"
                )
            },
            "knowledge_feedback_recorded": {
                row["feedback_id"]
                for row in self.connection.execute(
                    "SELECT feedback_id FROM knowledge_feedback_v3"
                )
            },
            "knowledge_relation_committed": {
                row["relation_revision_id"]
                for row in self.connection.execute(
                    "SELECT relation_revision_id FROM knowledge_relation_revisions_v3"
                )
            },
            "knowledge_revision_committed": {
                row["revision_id"]
                for row in self.connection.execute(
                    "SELECT revision_id FROM knowledge_revisions_v3"
                )
            },
            "knowledge_sink_grant_enabled": set(grant_writers),
            "knowledge_sink_grant_revoked": {
                row["grant_id"]
                for row in self.connection.execute(
                    "SELECT grant_id FROM knowledge_sink_grants_v3 WHERE revoked_at IS NOT NULL"
                )
            },
            "workspace_conflict_preserved": {
                row["conflict_id"]
                for row in self.connection.execute(
                    "SELECT conflict_id FROM workspace_conflicts_v3"
                )
            },
            "workspace_materialized": {
                row["revision_id"]
                for row in self.connection.execute(
                    "SELECT revision_id FROM knowledge_revisions_v3 "
                    "WHERE lifecycle <> 'quarantined'"
                )
            },
        }
        for event_type, expected_objects in expected_unique_event_objects.items():
            actual_objects = {
                object_id
                for candidate_type, object_id in event_payloads
                if candidate_type == event_type
            }
            if actual_objects != expected_objects:
                failures.append(
                    {
                        "code": "event_domain_set_invalid",
                        "object_id": event_type,
                    }
                )
        core_identity = self.connection.execute(
            "SELECT installed_at, migration_source FROM autonomous_core_v3 "
            "WHERE schema_version = ?",
            (AUTONOMOUS_CORE_SCHEMA,),
        ).fetchone()
        genesis = event_payloads.get(("autonomous_core_initialized", self.vault_id))
        if not (
            replay_events
            and replay_events[0]["event_type"] == "autonomous_core_initialized"
            and replay_events[0]["object_id"] == self.vault_id
            and core_identity is not None
            and genesis
            == {
                "vault_id": self.vault_id,
                "migration_source": core_identity["migration_source"],
            }
            and event_recorded_at.get(("autonomous_core_initialized", self.vault_id))
            == core_identity["installed_at"]
        ):
            failures.append(
                {
                    "code": "autonomous_genesis_invalid",
                    "object_id": self.vault_id,
                }
            )
        replay_revisions = {
            row["revision_id"]: dict(row)
            for row in self.connection.execute(
                "SELECT revision_id, knowledge_id, lifecycle, workspace_path "
                "FROM knowledge_revisions_v3"
            )
        }
        replay_objects = {
            row["knowledge_id"]: dict(row)
            for row in self.connection.execute(
                "SELECT knowledge_id, current_revision_id, workspace_path "
                "FROM knowledge_objects_v3"
            )
        }
        replay_locations: dict[str, str] = {}
        for event in replay_events:
            event_type = event["event_type"]
            object_id = event["object_id"]
            payload = event["payload"]
            if event_type == "knowledge_revision_committed":
                revision = replay_revisions.get(object_id)
                if revision is None or revision["lifecycle"] == "quarantined":
                    continue
                knowledge_id = revision["knowledge_id"]
                prior_location = replay_locations.get(knowledge_id)
                if prior_location is None:
                    replay_locations[knowledge_id] = revision["workspace_path"]
                elif prior_location != revision["workspace_path"]:
                    failures.append(
                        {
                            "code": "workspace_location_replay_invalid",
                            "object_id": knowledge_id,
                        }
                    )
            elif event_type == "workspace_location_recorded":
                knowledge = replay_objects.get(object_id)
                previous_path = payload.get("previous_path")
                workspace_path = payload.get("workspace_path")
                try:
                    previous_path = _safe_knowledge_workspace_path(previous_path)
                    workspace_path = _safe_knowledge_workspace_path(workspace_path)
                except (TypeError, ValueError):
                    previous_path = None
                    workspace_path = None
                if not (
                    knowledge is not None
                    and previous_path is not None
                    and workspace_path is not None
                    and previous_path != workspace_path
                    and replay_locations.get(object_id) == previous_path
                    and grant_writers.get(payload.get("grant_id")) == payload.get("writer_id")
                ):
                    failures.append(
                        {
                            "code": "workspace_location_replay_invalid",
                            "object_id": object_id,
                        }
                    )
                    continue
                replay_locations[object_id] = workspace_path
        for knowledge_id, knowledge in replay_objects.items():
            if knowledge["current_revision_id"] is not None and replay_locations.get(
                knowledge_id
            ) != knowledge["workspace_path"]:
                failures.append(
                    {
                        "code": "workspace_location_replay_invalid",
                        "object_id": knowledge_id,
                    }
                )
        grant_evaluator_types: dict[str, set[str]] = {}
        for row in self.connection.execute(
            "SELECT grant_id, evaluator_types_json FROM knowledge_sink_grants_v3"
        ):
            try:
                evaluator_types = strict_json_loads(row["evaluator_types_json"])
                if (
                    not isinstance(evaluator_types, list)
                    or not evaluator_types
                    or evaluator_types != sorted(set(evaluator_types))
                    or any(
                        not isinstance(item, str) or item not in FEEDBACK_EVALUATOR_TYPES
                        for item in evaluator_types
                    )
                ):
                    raise ValueError("stored grant evaluator policy is invalid")
                grant_evaluator_types[row["grant_id"]] = set(evaluator_types)
            except (TypeError, ValueError, json.JSONDecodeError):
                grant_evaluator_types[row["grant_id"]] = set()
        relation_index = {
            row["relation_revision_id"]: dict(row)
            for row in self.connection.execute(
                "SELECT relation_revision_id, relation_key, recorded_at "
                "FROM knowledge_relation_revisions_v3"
            )
        }
        for row in self.connection.execute(
            """
            SELECT knowledge_objects_v3.knowledge_id,
                   knowledge_objects_v3.current_revision_id,
                   knowledge_objects_v3.kind AS object_kind,
                   knowledge_objects_v3.origin AS object_origin,
                   knowledge_objects_v3.authority AS object_authority,
                   knowledge_objects_v3.semantic_key AS object_semantic_key,
                   knowledge_objects_v3.workspace_path AS object_workspace_path,
                   knowledge_revisions_v3.knowledge_id AS revision_owner,
                   knowledge_revisions_v3.kind AS revision_kind,
                   knowledge_revisions_v3.origin AS revision_origin,
                   knowledge_revisions_v3.authority AS revision_authority,
                   knowledge_revisions_v3.lifecycle AS revision_lifecycle,
                   knowledge_revisions_v3.semantic_key AS revision_semantic_key,
                   knowledge_revisions_v3.workspace_path AS revision_workspace_path,
                   (
                     SELECT candidate.revision_id
                     FROM knowledge_revisions_v3 AS candidate
                     WHERE candidate.knowledge_id = knowledge_objects_v3.knowledge_id
                       AND candidate.lifecycle <> 'quarantined'
                     ORDER BY candidate.recorded_at DESC, candidate.revision_id DESC
                     LIMIT 1
                   ) AS expected_current_revision_id
            FROM knowledge_objects_v3
            LEFT JOIN knowledge_revisions_v3
              ON knowledge_revisions_v3.revision_id =
                 knowledge_objects_v3.current_revision_id
            WHERE knowledge_objects_v3.current_revision_id IS NOT NULL
            """
        ):
            latest_move = event_payloads.get(("workspace_location_recorded", row["knowledge_id"]))
            expected_workspace_path = row["revision_workspace_path"]
            move_valid = True
            if latest_move is not None:
                expected_workspace_path = latest_move.get("workspace_path")
                move_valid = bool(
                    grant_writers.get(latest_move.get("grant_id")) == latest_move.get("writer_id")
                    and isinstance(latest_move.get("previous_path"), str)
                    and isinstance(expected_workspace_path, str)
                )
            if not (
                row["revision_owner"] == row["knowledge_id"]
                and row["current_revision_id"] == row["expected_current_revision_id"]
                and row["object_kind"] == row["revision_kind"]
                and row["object_origin"] == row["revision_origin"] == "agent_derived"
                and row["object_authority"] == row["revision_authority"] == "agent_derived"
                and row["revision_lifecycle"] != "quarantined"
                and row["object_semantic_key"] == row["revision_semantic_key"]
                and row["object_workspace_path"] == expected_workspace_path
                and move_valid
            ):
                failures.append(
                    {
                        "code": "current_revision_identity_invalid",
                        "object_id": row["knowledge_id"],
                    }
                )
        for row in self.connection.execute(
            """
            SELECT knowledge_objects_v3.knowledge_id,
                   COUNT(knowledge_revisions_v3.revision_id) AS revision_count,
                   SUM(
                     CASE WHEN knowledge_revisions_v3.lifecycle <> 'quarantined'
                          THEN 1 ELSE 0 END
                   ) AS non_quarantined_count
            FROM knowledge_objects_v3
            LEFT JOIN knowledge_revisions_v3 USING(knowledge_id)
            WHERE knowledge_objects_v3.current_revision_id IS NULL
            GROUP BY knowledge_objects_v3.knowledge_id
            """
        ):
            if row["revision_count"] < 1 or row["non_quarantined_count"]:
                failures.append(
                    {
                        "code": "missing_current_revision_invalid",
                        "object_id": row["knowledge_id"],
                    }
                )
        expected_legacy_evidence_bindings = {
            stable_id(
                "evidence",
                self.vault_id,
                row["source_revision_id"] or row["source_id"],
                row["content_sha256"],
            )
            for row in self.connection.execute(
                """
                SELECT sources.source_id, sources.content_sha256,
                       source_revision_bindings_v2.source_revision_id
                FROM sources
                LEFT JOIN source_revision_bindings_v2
                  ON source_revision_bindings_v2.legacy_source_id = sources.source_id
                """
            )
        }
        actual_legacy_evidence_bindings = {
            row["binding_id"]
            for row in self.connection.execute(
                "SELECT binding_id FROM evidence_bindings_v3 "
                "WHERE legacy_source_id IS NOT NULL"
            )
        }
        if actual_legacy_evidence_bindings != expected_legacy_evidence_bindings:
            failures.append(
                {
                    "code": "evidence_binding_set_invalid",
                    "object_id": self.vault_id,
                }
            )
        for row in self.connection.execute(
            """
            SELECT evidence_bindings_v3.*, evidence_role.object_role,
                   sources.content_sha256 AS legacy_sha256,
                   sources.sensitivity AS legacy_sensitivity
            FROM evidence_bindings_v3
            JOIN content_objects_v3 USING(object_sha256)
            LEFT JOIN content_object_roles_v3 AS evidence_role
              ON evidence_role.object_sha256 = evidence_bindings_v3.object_sha256
             AND evidence_role.object_role = 'evidence'
            LEFT JOIN sources
              ON sources.source_id = evidence_bindings_v3.legacy_source_id
            ORDER BY evidence_bindings_v3.binding_id
            """
        ):
            committed = event_payloads.get(("evidence_object_bound", row["binding_id"]))
            if not (
                row["object_role"] == "evidence"
                and row["origin"] in {"official", "user_source", "external_import"}
                and row["scope"] in SCOPES
                and row["sensitivity"] in SENSITIVITIES
                and (
                    row["legacy_source_id"] is None
                    or (
                        row["legacy_sha256"] == row["object_sha256"]
                        and row["legacy_sensitivity"] == row["sensitivity"]
                    )
                )
                and committed is not None
                and committed.get("legacy_source_id") == row["legacy_source_id"]
                and committed.get("source_revision_id") == row["source_revision_id"]
                and committed.get("object_sha256") == row["object_sha256"]
                and committed.get("origin") == row["origin"]
                and committed.get("authority") == row["authority"]
                and committed.get("verification") == row["verification"]
                and committed.get("scope") == row["scope"]
                and committed.get("sensitivity") == row["sensitivity"]
                and committed.get("lifecycle") == row["lifecycle"]
                and event_recorded_at.get(("evidence_object_bound", row["binding_id"]))
                == row["recorded_at"]
            ):
                failures.append(
                    {
                        "code": "evidence_binding_invalid",
                        "object_id": row["binding_id"],
                    }
                )
        revision_index = {
            row["revision_id"]: dict(row)
            for row in self.connection.execute(
                "SELECT revision_id, knowledge_id, recorded_at FROM knowledge_revisions_v3"
            )
        }
        current_workspace_by_revision = {
            row["current_revision_id"]: row["workspace_path"]
            for row in self.connection.execute(
                "SELECT current_revision_id, workspace_path FROM knowledge_objects_v3 "
                "WHERE current_revision_id IS NOT NULL"
            )
        }
        for row in self.connection.execute(
            "SELECT knowledge_revisions_v3.* FROM knowledge_revisions_v3 "
            "ORDER BY knowledge_revisions_v3.revision_id"
        ):
            try:
                content_role = self.connection.execute(
                    """
                    SELECT 1 FROM content_object_roles_v3
                    WHERE object_sha256 = ? AND object_role = 'knowledge_revision'
                    """,
                    (row["markdown_sha256"],),
                ).fetchone()
                payload = _read_object(self.root, row["markdown_sha256"])
                parsed = parse_knowledge_markdown(payload)
                source_refs = strict_json_loads(row["source_refs_json"])
                generation = strict_json_loads(row["generation_json"])
                tags = strict_json_loads(row["tags_json"])
                metadata = strict_json_loads(row["metadata_json"])
                if not all(
                    (
                        isinstance(source_refs, list),
                        isinstance(generation, dict),
                        isinstance(tags, list),
                        isinstance(metadata, dict),
                    )
                ):
                    raise ValueError("knowledge revision JSON columns are invalid")
                canonical_refs = _canonical_source_references(
                    source_refs,
                    field="stored source references",
                )
                if canonical_refs != source_refs:
                    raise ValueError("stored source references are not canonical")
                if set(generation) != {
                    "activity_id",
                    "run_id",
                    "model_id",
                    "tool_id",
                } or any(
                    value is not None
                    and (
                        not isinstance(value, str)
                        or not value
                        or value != value.strip()
                        or len(value) > 500
                    )
                    for value in generation.values()
                ):
                    raise ValueError("knowledge generation metadata is invalid")
                if (
                    len(tags) > _MAX_TAGS
                    or len(set(tags)) != len(tags)
                    or any(
                        not isinstance(tag, str)
                        or not tag
                        or tag != tag.strip()
                        or len(tag) > _MAX_TAG_CHARS
                        for tag in tags
                    )
                ):
                    raise ValueError("knowledge tags are invalid")
                if set(metadata) != {
                    "quarantine_reasons",
                    "memory_type",
                    "preference_basis",
                    "skill_manifest",
                    "lifecycle_reason",
                }:
                    raise ValueError("knowledge revision metadata is not closed")
                if (
                    row["kind"] not in KNOWLEDGE_KINDS
                    or row["lifecycle"] not in LIFECYCLES
                    or row["epistemic_state"] not in EPISTEMIC_STATES
                    or row["origin"] != "agent_derived"
                    or row["authority"] != "agent_derived"
                    or row["verification"] not in {"unverified", "source_bound", "run_bound"}
                    or row["scope"] not in SCOPES
                    or row["sensitivity"] not in SENSITIVITIES
                    or row["parent_revision_id"] != row["supersedes_revision_id"]
                ):
                    raise ValueError("knowledge revision governance fields are invalid")
                _safe_knowledge_workspace_path(row["workspace_path"])
                if row["semantic_key"] is not None:
                    _bounded_string(
                        row["semantic_key"],
                        field="stored semantic key",
                        maximum=300,
                    )
                for field in (
                    "valid_from",
                    "valid_to",
                    "observed_at",
                    "recorded_at",
                    "expires_at",
                ):
                    timestamp = row[field]
                    if (
                        timestamp is not None
                        and canonical_timestamp(
                            timestamp,
                            field=f"stored {field}",
                        )
                        != timestamp
                    ):
                        raise ValueError("knowledge temporal metadata is not canonical")
                if (
                    row["valid_from"] is not None
                    and row["valid_to"] is not None
                    and row["valid_from"] >= row["valid_to"]
                ):
                    raise ValueError("knowledge valid-time interval is invalid")
                parent_id = row["parent_revision_id"]
                if parent_id is not None:
                    parent = revision_index.get(parent_id)
                    if (
                        parent is None
                        or parent["knowledge_id"] != row["knowledge_id"]
                        or parent["recorded_at"] >= row["recorded_at"]
                    ):
                        raise ValueError("knowledge revision lineage is invalid")
                source_free = bool(row["source_free"])
                source_bindings_valid = bool(source_refs) and all(
                    self._source_reference_is_bound(
                        reference,
                        scope=row["scope"],
                        max_sensitivity=row["sensitivity"],
                        require_active=False,
                    )
                    for reference in source_refs
                )
                if source_free:
                    if (
                        source_refs
                        or generation["run_id"] is not None
                        or row["verification"] != "unverified"
                    ):
                        raise ValueError("source-free knowledge provenance is invalid")
                elif row["verification"] == "source_bound":
                    if not source_bindings_valid:
                        raise ValueError("source-bound knowledge provenance is invalid")
                elif row["verification"] == "run_bound":
                    if source_refs or not generation["run_id"]:
                        raise ValueError("run-bound knowledge provenance is invalid")
                elif not source_refs:
                    raise ValueError("unverified bound knowledge provenance is invalid")
                quarantine_reasons = metadata["quarantine_reasons"]
                if not isinstance(quarantine_reasons, list):
                    raise ValueError("knowledge quarantine metadata is invalid")
                if row["lifecycle"] == "active" and quarantine_reasons:
                    raise ValueError("active knowledge cannot retain quarantine reasons")
                if row["lifecycle"] == "quarantined" and not quarantine_reasons:
                    raise ValueError("quarantined knowledge requires a reason")
                if row["lifecycle"] != "quarantined" and quarantine_reasons:
                    raise ValueError("non-quarantined knowledge cannot retain quarantine reasons")
                if (
                    row["lifecycle"] in {"active", "quarantined"}
                    and metadata["lifecycle_reason"] is not None
                ):
                    raise ValueError("active or quarantined knowledge has a lifecycle reason")
                if (
                    row["lifecycle"]
                    in {
                        "superseded",
                        "revoked",
                        "expired",
                        "forgotten",
                        "archived",
                    }
                    and not metadata["lifecycle_reason"]
                ):
                    raise ValueError("inactive knowledge requires a lifecycle reason")
                if row["kind"] == "memory":
                    if metadata["memory_type"] not in {
                        "working",
                        "episodic",
                        "semantic",
                        "procedural",
                        "reflective",
                    }:
                        raise ValueError("memory revision metadata is invalid")
                elif metadata["memory_type"] is not None:
                    raise ValueError("non-memory revision has memory metadata")
                if row["kind"] == "preference":
                    if metadata["preference_basis"] not in {
                        "direct_user_statement",
                        "agent_inference",
                    }:
                        raise ValueError("preference revision metadata is invalid")
                elif metadata["preference_basis"] is not None:
                    raise ValueError("non-preference revision has preference metadata")
                if row["kind"] == "skill":
                    self._validate_skill_manifest(
                        metadata["skill_manifest"],
                        knowledge_id=row["knowledge_id"],
                        parent_revision_id=row["parent_revision_id"],
                        scope=row["scope"],
                        max_sensitivity=row["sensitivity"],
                        require_active_sources=False,
                    )
                elif metadata["skill_manifest"] is not None:
                    raise ValueError("non-Skill revision has Skill metadata")
                expected_markdown = render_knowledge_markdown(
                    knowledge_id=row["knowledge_id"],
                    revision_id=row["revision_id"],
                    title=row["title"],
                    body=parsed["body"],
                    kind=row["kind"],
                    lifecycle=row["lifecycle"],
                    epistemic_state=row["epistemic_state"],
                    verification=row["verification"],
                    scope=row["scope"],
                    sensitivity=row["sensitivity"],
                    writer_id=row["writer_id"],
                    source_free=bool(row["source_free"]),
                    source_refs=source_refs,
                    generation=generation,
                    tags=tags,
                    semantic_key=row["semantic_key"],
                    parent_revision_id=row["parent_revision_id"],
                    supersedes_revision_id=row["supersedes_revision_id"],
                    valid_from=row["valid_from"],
                    valid_to=row["valid_to"],
                    observed_at=row["observed_at"],
                    recorded_at=row["recorded_at"],
                    expires_at=row["expires_at"],
                    preference_basis=metadata.get("preference_basis"),
                    memory_type=metadata.get("memory_type"),
                    skill_manifest=metadata.get("skill_manifest"),
                    quarantine_reasons=metadata.get("quarantine_reasons", []),
                    lifecycle_reason=metadata.get("lifecycle_reason"),
                )
                semantic_digest = sha256_bytes(
                    canonical_json(
                        {
                            "kind": row["kind"],
                            "title": compact_text(row["title"]),
                            "body": compact_text(parsed["body"]),
                            "semantic_key": row["semantic_key"],
                        }
                    ).encode("utf-8")
                )
                committed = event_payloads.get(("knowledge_revision_committed", row["revision_id"]))
                event_matches = bool(
                    committed is not None
                    and committed.get("knowledge_id") == row["knowledge_id"]
                    and committed.get("parent_revision_id") == row["parent_revision_id"]
                    and committed.get("markdown_sha256") == row["markdown_sha256"]
                    and committed.get("lifecycle") == row["lifecycle"]
                    and committed.get("epistemic_state") == row["epistemic_state"]
                    and committed.get("origin") == row["origin"]
                    and committed.get("authority") == row["authority"]
                    and committed.get("writer_id") == row["writer_id"]
                    and grant_writers.get(committed.get("grant_id")) == row["writer_id"]
                    and _SHA256.fullmatch(str(committed.get("idempotency_key_sha256", "")))
                    and _SHA256.fullmatch(str(committed.get("request_sha256", "")))
                    and committed.get("scope") == row["scope"]
                    and committed.get("sensitivity") == row["sensitivity"]
                    and committed.get("source_free") == bool(row["source_free"])
                    and committed.get("semantic_digest") == semantic_digest
                    and committed.get("verification") == row["verification"]
                    and committed.get("source_refs_sha256")
                    == sha256_bytes(canonical_json(source_refs).encode("utf-8"))
                    and committed.get("generation_sha256")
                    == sha256_bytes(canonical_json(generation).encode("utf-8"))
                    and committed.get("tags_sha256")
                    == sha256_bytes(canonical_json(tags).encode("utf-8"))
                    and committed.get("metadata_sha256")
                    == sha256_bytes(canonical_json(metadata).encode("utf-8"))
                    and committed.get("valid_from") == row["valid_from"]
                    and committed.get("valid_to") == row["valid_to"]
                    and committed.get("expires_at") == row["expires_at"]
                    and (
                        committed.get("workspace_edit_sha256") is None
                        or (
                            _SHA256.fullmatch(str(committed.get("workspace_edit_sha256")))
                            and generation["tool_id"] == "workspace-watcher"
                        )
                    )
                    and event_recorded_at.get(("knowledge_revision_committed", row["revision_id"]))
                    == row["recorded_at"]
                )
                materialized = event_payloads.get(("workspace_materialized", row["revision_id"]))
                expected_action = "write" if row["lifecycle"] == "active" else "delete"
                materialization_matches = (
                    materialized is None
                    if row["lifecycle"] == "quarantined"
                    else bool(
                        materialized is not None
                        and materialized.get("workspace_path")
                        in {
                            row["workspace_path"],
                            current_workspace_by_revision.get(row["revision_id"]),
                        }
                        and materialized.get("markdown_sha256") == row["markdown_sha256"]
                        and materialized.get("action") == expected_action
                        and event_recorded_at.get(("workspace_materialized", row["revision_id"]))
                        >= row["recorded_at"]
                    )
                )
                if (
                    content_role is None
                    or expected_markdown != payload
                    or semantic_digest != row["semantic_digest"]
                    or not event_matches
                    or not materialization_matches
                ):
                    raise ValueError("knowledge revision binding is inconsistent")
            except (
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                failures.append(
                    {
                        "code": "knowledge_revision_binding_invalid",
                        "object_id": row["revision_id"],
                    }
                )
        for row in self.connection.execute(
            """
            SELECT knowledge_relations_v3.relation_key,
                   knowledge_relation_revisions_v3.relation_key AS revision_owner,
                   knowledge_relations_v3.current_revision_id,
                   (
                     SELECT candidate.relation_revision_id
                     FROM knowledge_relation_revisions_v3 AS candidate
                     WHERE candidate.relation_key = knowledge_relations_v3.relation_key
                     ORDER BY candidate.recorded_at DESC,
                              candidate.relation_revision_id DESC
                     LIMIT 1
                   ) AS expected_current_revision_id
            FROM knowledge_relations_v3
            LEFT JOIN knowledge_relation_revisions_v3
              ON knowledge_relation_revisions_v3.relation_revision_id =
                 knowledge_relations_v3.current_revision_id
            """
        ):
            if (
                row["revision_owner"] != row["relation_key"]
                or row["current_revision_id"] != row["expected_current_revision_id"]
            ):
                failures.append(
                    {
                        "code": "current_relation_identity_invalid",
                        "object_id": row["relation_key"],
                    }
                )
        for row in self.connection.execute(
            """
            SELECT knowledge_relations_v3.relation_key AS current_relation_key,
                   knowledge_relations_v3.current_revision_id,
                   knowledge_relation_revisions_v3.*
            FROM knowledge_relation_revisions_v3
            JOIN knowledge_relations_v3 USING(relation_key)
            ORDER BY knowledge_relation_revisions_v3.relation_revision_id
            """
        ):
            try:
                evidence_refs = strict_json_loads(row["evidence_refs_json"])
                if not isinstance(evidence_refs, list):
                    raise ValueError("relation evidence is invalid")
                if (
                    _canonical_source_references(
                        evidence_refs,
                        field="stored relation evidence",
                    )
                    != evidence_refs
                ):
                    raise ValueError("stored relation evidence is not canonical")
                if evidence_refs and not all(
                    self._source_reference_is_bound(
                        reference,
                        scope=row["scope"],
                        max_sensitivity=row["sensitivity"],
                        require_active=False,
                    )
                    for reference in evidence_refs
                ):
                    raise ValueError("stored relation evidence is not bound")
                expected_key = stable_id(
                    "relationkey",
                    self.vault_id,
                    row["subject_knowledge_id"],
                    row["predicate"],
                    row["object_knowledge_id"],
                )
                committed = event_payloads.get(
                    ("knowledge_relation_committed", row["relation_revision_id"])
                )
                parent_valid = True
                if row["parent_revision_id"] is not None:
                    parent = relation_index.get(row["parent_revision_id"])
                    parent_valid = bool(
                        parent is not None
                        and parent["relation_key"] == row["relation_key"]
                        and parent["recorded_at"] < row["recorded_at"]
                    )
                for field in ("valid_from", "valid_to", "observed_at", "recorded_at"):
                    timestamp = row[field]
                    if (
                        timestamp is not None
                        and canonical_timestamp(
                            timestamp,
                            field=f"stored relation {field}",
                        )
                        != timestamp
                    ):
                        raise ValueError("relation temporal metadata is not canonical")
                if (
                    row["valid_from"] is not None
                    and row["valid_to"] is not None
                    and row["valid_from"] >= row["valid_to"]
                ):
                    raise ValueError("relation valid-time interval is invalid")
                _bounded_string(
                    row["writer_id"],
                    field="stored relation writer",
                    maximum=200,
                )
                if not (
                    row["relation_key"] == expected_key
                    and row["predicate"] in RELATION_PREDICATES
                    and row["origin"] == "agent_derived"
                    and row["authority"] == "agent_derived"
                    and row["lifecycle"] == "active"
                    and row["scope"] in SCOPES
                    and row["sensitivity"] in SENSITIVITIES
                    and row["source_free"] == int(not evidence_refs)
                    and parent_valid
                    and committed is not None
                    and committed.get("relation_key") == row["relation_key"]
                    and committed.get("parent_revision_id") == row["parent_revision_id"]
                    and committed.get("subject_knowledge_id") == row["subject_knowledge_id"]
                    and committed.get("predicate") == row["predicate"]
                    and committed.get("object_knowledge_id") == row["object_knowledge_id"]
                    and committed.get("source_free") == bool(row["source_free"])
                    and committed.get("writer_id") == row["writer_id"]
                    and grant_writers.get(committed.get("grant_id")) == row["writer_id"]
                    and _SHA256.fullmatch(str(committed.get("idempotency_key_sha256", "")))
                    and _SHA256.fullmatch(str(committed.get("request_sha256", "")))
                    and committed.get("origin") == row["origin"]
                    and committed.get("authority") == row["authority"]
                    and committed.get("scope") == row["scope"]
                    and committed.get("sensitivity") == row["sensitivity"]
                    and committed.get("evidence_refs_sha256")
                    == sha256_bytes(canonical_json(evidence_refs).encode("utf-8"))
                    and committed.get("valid_from") == row["valid_from"]
                    and committed.get("valid_to") == row["valid_to"]
                    and event_recorded_at.get(
                        ("knowledge_relation_committed", row["relation_revision_id"])
                    )
                    == row["recorded_at"]
                ):
                    raise ValueError("relation revision binding is inconsistent")
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                failures.append(
                    {
                        "code": "relation_revision_binding_invalid",
                        "object_id": row["relation_revision_id"],
                    }
                )
        for row in self.connection.execute(
            "SELECT * FROM knowledge_feedback_v3 ORDER BY feedback_id"
        ):
            committed = event_payloads.get(("knowledge_feedback_recorded", row["feedback_id"]))
            try:
                valid_feedback = bool(
                    committed is not None
                    and committed.get("grant_id") == row["grant_id"]
                    and _SHA256.fullmatch(str(committed.get("idempotency_key_sha256", "")))
                    and _SHA256.fullmatch(str(committed.get("request_sha256", "")))
                    and committed.get("knowledge_id") == row["knowledge_id"]
                    and committed.get("revision_id") == row["revision_id"]
                    and committed.get("run_id") == row["run_id"]
                    and committed.get("outcome") == row["outcome"]
                    and committed.get("evaluator_type") == row["evaluator_type"]
                    and committed.get("feedback_note_sha256") == row["note_sha256"]
                    and row["outcome"] in {"helpful", "neutral", "noisy", "harmful"}
                    and row["evaluator_type"] in FEEDBACK_EVALUATOR_TYPES
                    and row["evaluator_type"] in grant_evaluator_types.get(row["grant_id"], set())
                    and (row["note_sha256"] is None or _SHA256.fullmatch(row["note_sha256"]))
                    and canonical_timestamp(
                        row["recorded_at"],
                        field="stored feedback recorded_at",
                    )
                    == row["recorded_at"]
                    and event_recorded_at.get(("knowledge_feedback_recorded", row["feedback_id"]))
                    == row["recorded_at"]
                )
            except (TypeError, ValueError):
                valid_feedback = False
            if not valid_feedback:
                failures.append(
                    {
                        "code": "knowledge_feedback_binding_invalid",
                        "object_id": row["feedback_id"],
                    }
                )
        expected_usage_ids: set[str] = set()
        for row in self.connection.execute(
            "SELECT * FROM mutation_idempotency_v3 ORDER BY grant_id, idempotency_key"
        ):
            try:
                idempotency_key = _bounded_string(
                    row["idempotency_key"],
                    field="stored idempotency key",
                    maximum=200,
                )
                if not _SHA256.fullmatch(row["request_sha256"]):
                    raise ValueError("stored request digest is invalid")
                if (
                    canonical_timestamp(
                        row["recorded_at"],
                        field="stored mutation recorded_at",
                    )
                    != row["recorded_at"]
                ):
                    raise ValueError("stored mutation time is invalid")
                response = strict_json_loads(row["response_json"])
                if not isinstance(response, dict):
                    raise ValueError("stored mutation response is invalid")
                result_contract = {
                    "knowledge_revision": (
                        "knowledge_revision_committed",
                        "revision_id",
                        "knowledge_revisions_v3",
                        "revision_id",
                        KNOWLEDGE_REVISION_SCHEMA,
                    ),
                    "relation_revision": (
                        "knowledge_relation_committed",
                        "relation_revision_id",
                        "knowledge_relation_revisions_v3",
                        "relation_revision_id",
                        KNOWLEDGE_RELATION_SCHEMA,
                    ),
                    "knowledge_feedback": (
                        "knowledge_feedback_recorded",
                        "feedback_id",
                        "knowledge_feedback_v3",
                        "feedback_id",
                        "deeplaw.knowledge-feedback/v1",
                    ),
                }.get(row["result_kind"])
                if result_contract is None:
                    raise ValueError("stored mutation result kind is invalid")
                event_type, response_id_field, table, table_id, schema_version = result_contract
                if (
                    response.get(response_id_field) != row["result_id"]
                    or response.get("schema_version") != schema_version
                    or not isinstance(response.get("idempotent_replay"), bool)
                    or response.get("recorded_at") != row["recorded_at"]
                ):
                    raise ValueError("stored mutation response binding is invalid")
                known_response_head = self.connection.execute(
                    "SELECT 1 FROM autonomous_events_v3 WHERE event_hash = ?",
                    (response.get("audit_head"),),
                ).fetchone()
                result_exists = self.connection.execute(
                    f"SELECT 1 FROM {table} WHERE {table_id} = ?",
                    (row["result_id"],),
                ).fetchone()
                mutation_id = stable_id(
                    "mutation",
                    row["grant_id"],
                    idempotency_key,
                    row["request_sha256"],
                )
                expected_usage_ids.add(mutation_id)
                usage = self.connection.execute(
                    "SELECT * FROM knowledge_sink_usage_v3 WHERE mutation_id = ?",
                    (mutation_id,),
                ).fetchone()
                committed = event_payloads.get((event_type, row["result_id"]))
                if not (
                    result_exists is not None
                    and known_response_head is not None
                    and usage is not None
                    and usage["grant_id"] == row["grant_id"]
                    and usage["request_sha256"] == row["request_sha256"]
                    and usage["recorded_at"] == row["recorded_at"]
                    and usage["operation"] in SINK_OPERATIONS
                    and committed is not None
                    and committed.get("grant_id") == row["grant_id"]
                    and committed.get("request_sha256") == row["request_sha256"]
                    and committed.get("idempotency_key_sha256")
                    == sha256_bytes(idempotency_key.encode("utf-8"))
                    and committed.get("operation") == usage["operation"]
                    and event_recorded_at.get((event_type, row["result_id"])) == row["recorded_at"]
                ):
                    raise ValueError("mutation audit binding is invalid")
            except (
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
                sqlite3.DatabaseError,
            ):
                failures.append(
                    {
                        "code": "mutation_idempotency_binding_invalid",
                        "object_id": f"{row['grant_id']}:{row['idempotency_key']}",
                    }
                )
        actual_usage_ids = {
            row["mutation_id"]
            for row in self.connection.execute("SELECT mutation_id FROM knowledge_sink_usage_v3")
        }
        if actual_usage_ids != expected_usage_ids:
            failures.append({"code": "mutation_usage_set_invalid", "object_id": self.vault_id})
        for row in self.connection.execute(
            "SELECT * FROM workspace_conflicts_v3 ORDER BY conflict_id"
        ):
            try:
                content_role = self.connection.execute(
                    """
                    SELECT 1 FROM content_object_roles_v3
                    WHERE object_sha256 = ? AND object_role = 'knowledge_revision'
                    """,
                    (row["object_sha256"],),
                ).fetchone()
                preserved = (
                    self.root / ".deeplaw" / "staging" / "conflicts" / f"{row['conflict_id']}.md"
                )
                committed = event_payloads.get(("workspace_conflict_preserved", row["conflict_id"]))
                recorded = event_recorded_at.get(
                    ("workspace_conflict_preserved", row["conflict_id"])
                )
                _safe_knowledge_workspace_path(row["workspace_path"])
                _bounded_string(
                    row["reason"],
                    field="workspace conflict reason",
                    maximum=200,
                )
                if (
                    canonical_timestamp(
                        row["detected_at"],
                        field="workspace conflict detected_at",
                    )
                    != row["detected_at"]
                ):
                    raise ValueError("workspace conflict time is invalid")
                if (
                    row["resolved_at"] is not None
                    and canonical_timestamp(
                        row["resolved_at"],
                        field="workspace conflict resolved_at",
                    )
                    != row["resolved_at"]
                ):
                    raise ValueError("workspace conflict resolution time is invalid")
                if not (
                    content_role is not None
                    and committed is not None
                    and recorded == row["detected_at"]
                    and committed.get("knowledge_id") == row["knowledge_id"]
                    and committed.get("base_revision_id") == row["base_revision_id"]
                    and committed.get("current_revision_id") == row["current_revision_id"]
                    and committed.get("object_sha256") == row["object_sha256"]
                    and committed.get("workspace_path") == row["workspace_path"]
                    and committed.get("reason") == row["reason"]
                    and grant_writers.get(committed.get("grant_id")) == committed.get("writer_id")
                    and not preserved.is_symlink()
                    and preserved.is_file()
                    and sha256_file(preserved) == row["object_sha256"]
                ):
                    raise ValueError("workspace conflict binding is invalid")
            except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                failures.append(
                    {
                        "code": "workspace_conflict_binding_invalid",
                        "object_id": row["conflict_id"],
                    }
                )
        object_count = 0
        for row in self.connection.execute("SELECT * FROM content_objects_v3"):
            object_count += 1
            try:
                path = _object_path(self.root, row["object_sha256"])
                roles = self.connection.execute(
                    "SELECT object_role FROM content_object_roles_v3 WHERE object_sha256 = ?",
                    (row["object_sha256"],),
                ).fetchall()
                object_valid = bool(
                    not path.is_symlink()
                    and path.is_file()
                    and path.stat().st_size == row["byte_size"]
                    and sha256_file(path) == row["object_sha256"]
                    and roles
                    and row["object_kind"] in {item["object_role"] for item in roles}
                    and isinstance(row["media_type"], str)
                    and 1 <= len(row["media_type"]) <= 200
                    and canonical_timestamp(
                        row["created_at"],
                        field="content object created_at",
                    )
                    == row["created_at"]
                )
            except (OSError, TypeError, ValueError):
                object_valid = False
            if not object_valid:
                failures.append(
                    {"code": "content_object_invalid", "object_id": row["object_sha256"]}
                )
        for row in self.connection.execute(
            """
            SELECT content_object_roles_v3.*,
                   content_objects_v3.object_sha256 AS bound_object_sha256
            FROM content_object_roles_v3
            LEFT JOIN content_objects_v3 USING(object_sha256)
            ORDER BY content_object_roles_v3.object_sha256,
                     content_object_roles_v3.object_role
            """
        ):
            try:
                if (
                    not _SHA256.fullmatch(row["object_sha256"])
                    or row["object_role"] not in {"evidence", "knowledge_revision"}
                    or row["bound_object_sha256"] != row["object_sha256"]
                    or canonical_timestamp(
                        row["created_at"],
                        field="content object role created_at",
                    )
                    != row["created_at"]
                ):
                    raise ValueError("content object role is invalid")
            except (TypeError, ValueError):
                failures.append(
                    {
                        "code": "content_object_role_invalid",
                        "object_id": f"{row['object_sha256']}:{row['object_role']}",
                    }
                )
        expected_role_times: dict[tuple[str, str], str] = {}
        for table, digest_column, time_column, role in (
            ("evidence_bindings_v3", "object_sha256", "recorded_at", "evidence"),
            (
                "knowledge_revisions_v3",
                "markdown_sha256",
                "recorded_at",
                "knowledge_revision",
            ),
            (
                "workspace_conflicts_v3",
                "object_sha256",
                "detected_at",
                "knowledge_revision",
            ),
        ):
            for row in self.connection.execute(
                f"SELECT {digest_column} AS digest, MIN({time_column}) AS first_seen "
                f"FROM {table} GROUP BY {digest_column}"
            ):
                key = (row["digest"], role)
                previous = expected_role_times.get(key)
                if previous is None or row["first_seen"] < previous:
                    expected_role_times[key] = row["first_seen"]
        actual_role_times = {
            (row["object_sha256"], row["object_role"]): row["created_at"]
            for row in self.connection.execute(
                "SELECT object_sha256, object_role, created_at FROM content_object_roles_v3"
            )
        }
        if actual_role_times != expected_role_times:
            failures.append(
                {"code": "content_object_role_set_invalid", "object_id": self.vault_id}
            )
        for row in self.connection.execute(
            "SELECT object_sha256, object_kind, created_at FROM content_objects_v3"
        ):
            if expected_role_times.get(
                (row["object_sha256"], row["object_kind"])
            ) != row["created_at"]:
                failures.append(
                    {
                        "code": "content_object_first_observation_invalid",
                        "object_id": row["object_sha256"],
                    }
                )
        workspace_checked = 0
        for row in self.connection.execute(
            """
            SELECT knowledge_objects_v3.workspace_path AS current_workspace_path,
                   knowledge_revisions_v3.*
            FROM knowledge_objects_v3
            JOIN knowledge_revisions_v3
              ON knowledge_revisions_v3.revision_id = knowledge_objects_v3.current_revision_id
            WHERE knowledge_revisions_v3.lifecycle = 'active'
            """
        ):
            workspace_checked += 1
            try:
                path = self.root / _safe_knowledge_workspace_path(
                    row["current_workspace_path"]
                )
                valid_workspace = bool(
                    not path.is_symlink()
                    and path.is_file()
                    and sha256_file(path) == row["markdown_sha256"]
                )
            except (OSError, TypeError, ValueError):
                valid_workspace = False
            if not valid_workspace:
                failures.append(
                    {"code": "workspace_revision_mismatch", "object_id": row["revision_id"]}
                )
        expected_search: list[tuple[str, str, str, str, str, str]] = []
        for row in self.connection.execute(
            """
                SELECT knowledge_objects_v3.knowledge_id, knowledge_revisions_v3.*
                FROM knowledge_objects_v3
                JOIN knowledge_revisions_v3
                  ON knowledge_revisions_v3.revision_id =
                     knowledge_objects_v3.current_revision_id
                WHERE knowledge_revisions_v3.lifecycle = 'active'
                ORDER BY knowledge_objects_v3.knowledge_id
                """
        ):
            try:
                if not self.revision_provenance_admitted(
                    self._revision_row(row, include_body=False)
                ) or not _interval_admits(
                    reference_time=verification_time,
                    valid_from=row["valid_from"],
                    valid_to=row["valid_to"],
                    expires_at=row["expires_at"],
                ):
                    continue
                body = parse_knowledge_markdown(_read_object(self.root, row["markdown_sha256"]))[
                    "body"
                ]
                tags = strict_json_loads(row["tags_json"])
                if not isinstance(tags, list):
                    raise ValueError("knowledge tags are invalid")
                expected_search.append(
                    (
                        row["knowledge_id"],
                        row["revision_id"],
                        " ".join(search_terms(row["title"])),
                        " ".join(search_terms(body)),
                        " ".join(search_terms(row["semantic_key"] or "")),
                        " ".join(search_terms(" ".join(tags))),
                    )
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
        actual_search = [
            (
                row["knowledge_id"],
                row["revision_id"],
                row["title_tokens"],
                row["body_tokens"],
                row["semantic_tokens"],
                row["tag_tokens"],
            )
            for row in self.connection.execute(
                "SELECT knowledge_id, revision_id, title_tokens, body_tokens, "
                "semantic_tokens, tag_tokens FROM autonomous_search_v3 "
                "ORDER BY knowledge_id, revision_id"
            )
        ]
        if actual_search != expected_search:
            warnings.append({"code": "derived_search_stale", "object_id": self.vault_id})
        try:
            derived_manifest_path = self.root / ".deeplaw" / "derived" / "manifest.json"
            if (
                derived_manifest_path.is_symlink()
                or not derived_manifest_path.is_file()
                or derived_manifest_path.stat().st_size > 4 * 1024 * 1024
            ):
                raise RuntimeError("derived manifest is missing or unsafe")
            derived_manifest = strict_json_loads(derived_manifest_path.read_bytes())
            if not isinstance(derived_manifest, dict):
                raise ValueError("derived manifest must be an object")
            manifest_digest = derived_manifest.get("manifest_sha256")
            manifest_body = {
                key: value for key, value in derived_manifest.items() if key != "manifest_sha256"
            }
            files = derived_manifest.get("files")
            expected_manifest_fields = {
                "schema_version",
                "input_audit_head",
                "legacy_audit_head",
                "generator",
                "generator_version",
                "configuration",
                "fts_rows_sha256",
                "knowledge_revision_count",
                "knowledge_revision_ids_sha256",
                "relation_revision_count",
                "relation_revision_ids_sha256",
                "files",
                "generated_at",
                "manifest_sha256",
            }
            expected_configuration = {
                "fts_tokenizer": "unicode61 remove_diacritics 2",
                "community_algorithm": "deterministic-connected-components",
                "canvas_node_limit": 500,
                "canvas_edge_limit": 1_000,
                "wiki_item_limit": _MAX_WIKI_ITEMS,
                "community_view_limit": _MAX_COMMUNITY_VIEWS,
                "community_member_limit": _MAX_COMMUNITY_VIEW_MEMBERS,
                "semantic_lint_issue_limit": _MAX_LINT_ISSUES,
            }
            if (
                set(derived_manifest) != expected_manifest_fields
                or derived_manifest.get("schema_version") != DERIVED_MANIFEST_SCHEMA
                or derived_manifest.get("generator") != "deeplaw.knowledge-autonomy/v1"
                or derived_manifest.get("generator_version") != "1"
                or derived_manifest.get("configuration") != expected_configuration
                or not isinstance(files, list)
                or len(files) > 10_000
                or canonical_timestamp(
                    derived_manifest.get("generated_at"),
                    field="derived generated_at",
                )
                != derived_manifest.get("generated_at")
                or not _SHA256.fullmatch(str(derived_manifest.get("input_audit_head", "")))
                or not _SHA256.fullmatch(str(derived_manifest.get("legacy_audit_head", "")))
                or manifest_digest != sha256_bytes(canonical_json(manifest_body).encode("utf-8"))
            ):
                raise ValueError("derived manifest contract is invalid")
            seen_derived_paths: set[str] = set()
            for item in files:
                if not isinstance(item, dict) or set(item) != {
                    "path",
                    "byte_size",
                    "sha256",
                }:
                    raise ValueError("derived file manifest is invalid")
                relative = _safe_relative_path(item["path"])
                if (
                    relative in seen_derived_paths
                    or not relative.startswith(("wiki/", "canvas/"))
                    or not isinstance(item["byte_size"], int)
                    or isinstance(item["byte_size"], bool)
                    or not 0 <= item["byte_size"] <= _MAX_MARKDOWN_BYTES
                    or not isinstance(item["sha256"], str)
                    or not _SHA256.fullmatch(item["sha256"])
                ):
                    raise ValueError("derived file escaped its allowed workspace")
                seen_derived_paths.add(relative)
                path = self.root / relative
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.stat().st_size != item["byte_size"]
                    or sha256_file(path) != item["sha256"]
                ):
                    raise ValueError("derived file hash is invalid")
            current_knowledge_revisions = [item[1] for item in expected_search]
            current_knowledge_ids = {item[0] for item in expected_search}
            current_relation_revisions = [
                relation["relation_revision_id"]
                for relation in self._current_relations()
                if self.relation_provenance_admitted(relation)
                and relation["subject_knowledge_id"] in current_knowledge_ids
                and relation["object_knowledge_id"] in current_knowledge_ids
                and _interval_admits(
                    reference_time=verification_time,
                    valid_from=relation["valid_from"],
                    valid_to=relation["valid_to"],
                )
            ]
            known_event_hash = self.connection.execute(
                "SELECT 1 FROM autonomous_events_v3 WHERE event_hash = ?",
                (derived_manifest.get("input_audit_head"),),
            ).fetchone()
            if not (
                derived_manifest.get("knowledge_revision_count") == len(current_knowledge_revisions)
                and derived_manifest.get("knowledge_revision_ids_sha256")
                == sha256_bytes(canonical_json(current_knowledge_revisions).encode("utf-8"))
                and derived_manifest.get("relation_revision_count")
                == len(current_relation_revisions)
                and derived_manifest.get("relation_revision_ids_sha256")
                == sha256_bytes(canonical_json(current_relation_revisions).encode("utf-8"))
                and derived_manifest.get("fts_rows_sha256")
                == sha256_bytes(canonical_json(expected_search).encode("utf-8"))
                and known_event_hash is not None
                and derived_manifest.get("input_audit_head") == self.audit_head
                and derived_manifest.get("legacy_audit_head") == self.legacy_audit_head
            ):
                raise ValueError("derived manifest inputs are stale")
        except (
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            warnings.append({"code": "derived_manifest_stale", "object_id": self.vault_id})
        grant_rows = self.connection.execute(
            "SELECT * FROM knowledge_sink_grants_v3 ORDER BY grant_id LIMIT ?",
            (_MAX_GRANTS + 1,),
        ).fetchall()
        if len(grant_rows) > _MAX_GRANTS:
            failures.append(
                {
                    "code": "knowledge_sink_grant_capacity_exceeded",
                    "object_id": self.vault_id,
                }
            )
        expected_capability_files: set[str] = set()
        for grant in grant_rows[:_MAX_GRANTS]:
            try:
                operations = strict_json_loads(grant["operations_json"])
                evaluator_types = strict_json_loads(grant["evaluator_types_json"])
                enabled = event_payloads.get(("knowledge_sink_grant_enabled", grant["grant_id"]))
                revoked = event_payloads.get(("knowledge_sink_grant_revoked", grant["grant_id"]))
                if not (
                    isinstance(operations, list)
                    and operations
                    and operations == sorted(set(operations))
                    and all(item in SINK_OPERATIONS for item in operations)
                    and isinstance(evaluator_types, list)
                    and evaluator_types
                    and evaluator_types == sorted(set(evaluator_types))
                    and all(item in FEEDBACK_EVALUATOR_TYPES for item in evaluator_types)
                    and grant["allowed_scope"] in SCOPES
                    and grant["max_sensitivity"] in SENSITIVITIES
                    and _SHA256.fullmatch(grant["token_sha256"])
                    and 1_024 <= grant["max_request_bytes"] <= _MAX_REQUEST_BYTES
                    and 1 <= grant["max_mutations_per_minute"] <= _MAX_GRANT_OPERATIONS_PER_MINUTE
                    and 1 <= grant["max_objects"] <= _MAX_OBJECTS
                    and canonical_timestamp(
                        grant["created_at"],
                        field="knowledge sink grant created_at",
                    )
                    == grant["created_at"]
                    and enabled is not None
                    and enabled.get("writer_id") == grant["writer_id"]
                    and enabled.get("allowed_scope") == grant["allowed_scope"]
                    and enabled.get("max_sensitivity") == grant["max_sensitivity"]
                    and enabled.get("operations") == operations
                    and enabled.get("evaluator_types", ["agent_self_report"]) == evaluator_types
                    and enabled.get("max_request_bytes") == grant["max_request_bytes"]
                    and enabled.get("max_mutations_per_minute") == grant["max_mutations_per_minute"]
                    and enabled.get("max_objects") == grant["max_objects"]
                    and enabled.get("token_sha256") == grant["token_sha256"]
                    and event_recorded_at.get(("knowledge_sink_grant_enabled", grant["grant_id"]))
                    == grant["created_at"]
                    and (
                        (grant["revoked_at"] is None and revoked is None)
                        or (
                            grant["revoked_at"] is not None
                            and canonical_timestamp(
                                grant["revoked_at"],
                                field="knowledge sink grant revoked_at",
                            )
                            == grant["revoked_at"]
                            and revoked is not None
                            and revoked.get("revoked_at") == grant["revoked_at"]
                            and event_recorded_at.get(
                                ("knowledge_sink_grant_revoked", grant["grant_id"])
                            )
                            == grant["revoked_at"]
                        )
                    )
                ):
                    raise ValueError("knowledge sink grant binding is invalid")
            except (TypeError, ValueError, json.JSONDecodeError):
                failures.append(
                    {
                        "code": "knowledge_sink_grant_binding_invalid",
                        "object_id": grant["grant_id"],
                    }
                )
            token_path = self.root / ".deeplaw" / "capabilities" / f"{grant['grant_id']}.token"
            if grant["revoked_at"] is not None:
                if token_path.exists() or token_path.is_symlink():
                    failures.append(
                        {
                            "code": "revoked_grant_token_present",
                            "object_id": grant["grant_id"],
                        }
                    )
                continue
            expected_capability_files.add(f"{grant['grant_id']}.token")
            token_valid = False
            try:
                token_valid = bool(
                    not token_path.is_symlink()
                    and token_path.is_file()
                    and token_path.stat().st_size <= 512
                    and not (os.name != "nt" and stat.S_IMODE(token_path.stat().st_mode) & 0o077)
                    and sha256_bytes(token_path.read_text(encoding="utf-8").strip().encode("utf-8"))
                    == grant["token_sha256"]
                )
            except (OSError, UnicodeDecodeError):
                token_valid = False
            if not token_valid:
                failures.append(
                    {
                        "code": "active_grant_token_invalid",
                        "object_id": grant["grant_id"],
                    }
                )
        capability_root = self.root / ".deeplaw" / "capabilities"
        actual_capability_files: set[str] = set()
        capability_inventory_valid = bool(
            not capability_root.is_symlink()
            and capability_root.is_dir()
            and not (
                os.name != "nt"
                and stat.S_IMODE(capability_root.stat().st_mode) & 0o077
            )
        )
        if capability_inventory_valid:
            for index, path in enumerate(capability_root.iterdir(), start=1):
                if index > _MAX_GRANTS:
                    capability_inventory_valid = False
                    break
                if path.is_symlink() or not path.is_file():
                    capability_inventory_valid = False
                    break
                actual_capability_files.add(path.name)
        if (
            not capability_inventory_valid
            or actual_capability_files != expected_capability_files
        ):
            failures.append(
                {
                    "code": "knowledge_sink_capability_inventory_invalid",
                    "object_id": self.vault_id,
                }
            )
        pending_count = self.connection.execute(
            "SELECT COUNT(*) FROM pending_materializations_v3"
        ).fetchone()[0]
        if pending_count:
            failures.append({"code": "pending_materialization", "object_id": self.vault_id})
        staging_recovery_count = 0
        try:
            staging_root = self.root / ".deeplaw" / "staging"
            if staging_root.is_symlink() or not staging_root.is_dir():
                raise RuntimeError("autonomous staging root is missing or unsafe")
            for path in sorted(staging_root.iterdir(), key=lambda item: item.name):
                if path.name == "conflicts":
                    if path.is_symlink() or not path.is_dir():
                        raise RuntimeError("autonomous conflict staging root is unsafe")
                    continue
                staging_recovery_count += 1
                if staging_recovery_count > _MAX_STAGING_RECORDS:
                    raise ValueError("autonomous staging inventory exceeds its bound")
                if path.is_symlink() or not path.is_file():
                    raise RuntimeError("autonomous staging inventory is unsafe")
        except (OSError, RuntimeError, ValueError):
            failures.append(
                {"code": "staging_inventory_invalid", "object_id": self.vault_id}
            )
        if staging_recovery_count:
            failures.append(
                {"code": "staging_recovery_required", "object_id": self.vault_id}
            )
        return {
            "schema_version": "deeplaw.autonomous-verification/v1",
            "vault_id": self.vault_id,
            "valid": not failures,
            "failures": failures,
            "warnings": warnings,
            "derived_ready": not warnings,
            "sequence": self.sequence,
            "audit_head": self.audit_head,
            "legacy_audit_head": self.legacy_audit_head,
            "content_object_count": object_count,
            "workspace_checked_count": workspace_checked,
            "pending_materialization_count": pending_count,
            "staging_recovery_count": staging_recovery_count,
            "conflict_count": self.connection.execute(
                "SELECT COUNT(*) FROM workspace_conflicts_v3 WHERE resolved_at IS NULL"
            ).fetchone()[0],
        }
