from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from .util import canonical_json, normalize_text, sha256_bytes, stable_id

KNOWLEDGE_IDENTITY_SCHEMA = "deeplaw.knowledge-identity/v2"
SOURCE_IR_SCHEMA = "deeplaw.source-ir/v1"
PROPOSAL_SET_SCHEMA = "deeplaw.knowledge-proposal-set/v2"
KNOWLEDGE_LINEAGE_SCHEMA = "deeplaw.knowledge-lineage/v1"
RELATION_REVISION_SCHEMA = "deeplaw.knowledge-relation/v2"

LineageStatus = Literal[
    "new",
    "unchanged",
    "modified",
    "renamed",
    "moved",
    "split",
    "merged",
    "deleted",
    "ambiguous",
]

LINEAGE_STATUSES = frozenset(LineageStatus.__args__)

_IDENTIFIER = re.compile(
    r"^(?:collection|sourcekey|sourcerev|compilation|proposalset|knowledge|assetrev|"
    r"governance|relationkey|relationrev|lineage)_[0-9a-f]{24}$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_MAX_LOGICAL_PATH_CHARS = 2_000
_MAX_IDENTITY_ITEMS = 100_000
_MAX_SOURCE_IR_NODES = 300_000
_IDENTITY_TABLES = (
    "identity_v2",
    "collections_v2",
    "source_identities_v2",
    "source_revisions_v2",
    "source_revision_bindings_v2",
    "source_locations_v2",
    "compilations_v2",
    "source_ir_nodes_v2",
    "fragments_v2",
    "fragment_node_membership_v2",
    "legacy_fragment_bindings_v2",
    "proposal_sets_v2",
    "source_build_bindings_v2",
    "knowledge_revisions_v2",
    "asset_revision_bindings_v2",
    "proposal_membership_v2",
    "proposal_metadata_v2",
    "proposal_source_refs_v2",
    "governance_revisions_v2",
    "knowledge_lineage_v2",
    "relation_revisions_v2",
)


def canonical_collection_name(value: str) -> str:
    name = normalize_text(value)
    if not 1 <= len(name) <= 200:
        raise ValueError("collection name must be between 1 and 200 characters")
    return name


def make_collection_id(*, vault_id: str, name: str) -> str:
    canonical = canonical_collection_name(name)
    return stable_id("collection", vault_id, canonical.casefold())


def normalize_logical_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("logical source path must be a string")
    normalized = unicodedata.normalize("NFKC", value.strip()).replace("\\", "/")
    if (
        not normalized
        or len(normalized) > _MAX_LOGICAL_PATH_CHARS
        or normalized.startswith("/")
        or _WINDOWS_DRIVE.match(normalized)
        or "\x00" in normalized
    ):
        raise ValueError("logical source path must be a bounded relative path")
    parts = normalized.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("logical source path contains an unsafe segment")
    canonical = PurePosixPath(*parts).as_posix()
    if canonical in {".", ".."} or canonical.startswith("../"):
        raise ValueError("logical source path must stay inside its collection")
    return canonical


def canonical_origin_commitment(origin_uri: str | None) -> str:
    if origin_uri is None:
        return "local"
    if not isinstance(origin_uri, str) or not origin_uri.strip():
        raise ValueError("origin URI must be a non-empty canonical string")
    value = origin_uri.strip()
    if len(value) > 2_000:
        raise ValueError("origin URI exceeds the identity bound")
    parts = urlsplit(value)
    if not parts.scheme:
        raise ValueError("origin URI requires an explicit scheme")
    scheme = parts.scheme.lower()
    if scheme == "file":
        raise ValueError("absolute local file URIs cannot become canonical identity")
    hostname = parts.hostname.lower() if parts.hostname else ""
    port = parts.port
    default_port = (scheme == "https" and port == 443) or (
        scheme == "http" and port == 80
    )
    host = hostname if port is None or default_port else f"{hostname}:{port}"
    if parts.username is not None or parts.password is not None:
        raise ValueError("origin URI credentials cannot enter canonical identity")
    path = parts.path or ("/" if scheme in {"http", "https"} else "")
    return urlunsplit((scheme, host, path, parts.query, ""))


def make_source_key(*, collection_id: str, logical_path: str) -> str:
    _require_id(collection_id, "collection")
    return stable_id("sourcekey", collection_id, normalize_logical_path(logical_path))


def make_source_revision_id(
    *,
    source_key: str,
    content_sha256: str,
    media_identity: str,
    origin_commitment: str,
) -> str:
    _require_id(source_key, "sourcekey")
    _require_sha256(content_sha256, field="source content SHA-256")
    media = normalize_text(media_identity).lower()
    if not 1 <= len(media) <= 200:
        raise ValueError("source media identity is invalid")
    origin = canonical_origin_commitment(
        None if origin_commitment == "local" else origin_commitment
    )
    return stable_id("sourcerev", source_key, content_sha256, media, origin)


def make_compilation_id(
    *,
    source_revision_id: str,
    adapter: str,
    adapter_version: str,
    configuration_sha256: str,
    source_ir_schema: str,
    fragment_inventory_sha256: str,
) -> str:
    _require_id(source_revision_id, "sourcerev")
    _require_sha256(configuration_sha256, field="adapter configuration SHA-256")
    _require_sha256(fragment_inventory_sha256, field="fragment inventory SHA-256")
    adapter = _bounded_identity(adapter, field="adapter")
    adapter_version = _bounded_identity(adapter_version, field="adapter version")
    if source_ir_schema != SOURCE_IR_SCHEMA:
        raise ValueError("unsupported Source IR schema")
    return stable_id(
        "compilation",
        source_revision_id,
        adapter,
        adapter_version,
        configuration_sha256,
        source_ir_schema,
        fragment_inventory_sha256,
    )


def make_proposal_set_id(
    *,
    compilation_id: str,
    extractor: str,
    extractor_revision: str,
    model_identity: str | None,
    prompt_config_sha256: str,
    proposal_inventory_sha256: str,
    proposal_ref_graph_sha256: str,
) -> str:
    _require_id(compilation_id, "compilation")
    for field, value in (
        ("prompt/config SHA-256", prompt_config_sha256),
        ("proposal inventory SHA-256", proposal_inventory_sha256),
        ("proposal reference graph SHA-256", proposal_ref_graph_sha256),
    ):
        _require_sha256(value, field=field)
    return stable_id(
        "proposalset",
        compilation_id,
        _bounded_identity(extractor, field="extractor"),
        _bounded_identity(extractor_revision, field="extractor revision"),
        _bounded_identity(model_identity or "none", field="model identity"),
        prompt_config_sha256,
        proposal_inventory_sha256,
        proposal_ref_graph_sha256,
    )


def make_knowledge_key(
    *,
    vault_id: str,
    source_key: str,
    logical_node_key: str,
    proposal_role: str,
) -> str:
    _require_id(source_key, "sourcekey")
    return stable_id(
        "knowledge",
        vault_id,
        source_key,
        _bounded_identity(logical_node_key, field="logical node key"),
        _bounded_identity(proposal_role, field="proposal role"),
    )


def make_asset_revision_id(
    *,
    knowledge_key: str,
    knowledge_content_sha256: str,
    source_revision_ids: tuple[str, ...],
) -> str:
    _require_id(knowledge_key, "knowledge")
    _require_sha256(knowledge_content_sha256, field="knowledge revision SHA-256")
    if (
        not source_revision_ids
        or len(source_revision_ids) > 100
        or len(set(source_revision_ids)) != len(source_revision_ids)
    ):
        raise ValueError("Asset source revision inventory is invalid")
    for source_revision_id in source_revision_ids:
        _require_id(source_revision_id, "sourcerev")
    return stable_id(
        "assetrev",
        knowledge_key,
        knowledge_content_sha256,
        canonical_json(sorted(source_revision_ids)),
    )


def make_governance_revision(
    *,
    subject_kind: str,
    subject_id: str,
    trust: str,
    sensitivity: str,
    policy_id: str,
    review_status: str,
    lifecycle_status: str,
    recorded_at: str,
    activation_status: str = "inactive",
    revoked_at: str | None = None,
    export_allowed: bool = False,
) -> str:
    if subject_kind not in {"source_revision", "asset_revision", "relation_revision"}:
        raise ValueError("unsupported governance subject kind")
    if not _IDENTIFIER.fullmatch(subject_id):
        raise ValueError("governance subject identity is invalid")
    return stable_id(
        "governance",
        subject_kind,
        subject_id,
        _bounded_identity(trust, field="trust"),
        _bounded_identity(sensitivity, field="sensitivity"),
        _bounded_identity(policy_id, field="policy ID"),
        _bounded_identity(review_status, field="review status"),
        _bounded_identity(lifecycle_status, field="lifecycle status"),
        _bounded_identity(activation_status, field="activation status"),
        revoked_at or "",
        str(int(export_allowed)),
        _bounded_identity(recorded_at, field="governance timestamp"),
    )


def make_relation_key(
    *,
    vault_id: str,
    subject_knowledge_key: str,
    predicate: str,
    object_knowledge_key: str,
) -> str:
    _require_id(subject_knowledge_key, "knowledge")
    _require_id(object_knowledge_key, "knowledge")
    if subject_knowledge_key == object_knowledge_key:
        raise ValueError("knowledge relation cannot be a self-loop")
    return stable_id(
        "relationkey",
        vault_id,
        subject_knowledge_key,
        _bounded_identity(predicate, field="relation predicate"),
        object_knowledge_key,
    )


def make_relation_revision_id(
    *,
    relation_key: str,
    subject_asset_revision_id: str,
    object_asset_revision_id: str,
    evidence_refs_sha256: str,
    valid_from: str | None,
    valid_to: str | None,
    observed_at: str,
) -> str:
    _require_id(relation_key, "relationkey")
    _require_id(subject_asset_revision_id, "assetrev")
    _require_id(object_asset_revision_id, "assetrev")
    _require_sha256(evidence_refs_sha256, field="relation evidence SHA-256")
    return stable_id(
        "relationrev",
        relation_key,
        subject_asset_revision_id,
        object_asset_revision_id,
        evidence_refs_sha256,
        valid_from or "",
        valid_to or "",
        _bounded_identity(observed_at, field="observed timestamp"),
    )


def inventory_sha256(values: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json(values).encode("utf-8"))


def identity_tables_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS identity_v2 (
            schema_version TEXT PRIMARY KEY,
            installed_at TEXT NOT NULL,
            migration_source TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS collections_v2 (
            collection_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS source_identities_v2 (
            source_key TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL REFERENCES collections_v2(collection_id),
            logical_path TEXT NOT NULL,
            logical_path_folded TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(collection_id, logical_path_folded)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS source_revisions_v2 (
            source_revision_id TEXT PRIMARY KEY,
            source_key TEXT NOT NULL REFERENCES source_identities_v2(source_key),
            content_sha256 TEXT NOT NULL,
            media_identity TEXT NOT NULL,
            origin_commitment TEXT NOT NULL,
            byte_size INTEGER NOT NULL
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS source_revisions_v2_key
            ON source_revisions_v2(source_key, source_revision_id);

        CREATE TABLE IF NOT EXISTS source_revision_bindings_v2 (
            legacy_source_id TEXT PRIMARY KEY REFERENCES sources(source_id),
            source_revision_id TEXT NOT NULL
                REFERENCES source_revisions_v2(source_revision_id),
            observed_at TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS source_revision_bindings_v2_revision
            ON source_revision_bindings_v2(source_revision_id);

        CREATE TABLE IF NOT EXISTS source_locations_v2 (
            location_id TEXT PRIMARY KEY,
            legacy_source_id TEXT NOT NULL UNIQUE REFERENCES sources(source_id),
            source_revision_id TEXT NOT NULL
                REFERENCES source_revisions_v2(source_revision_id),
            collection_id TEXT NOT NULL REFERENCES collections_v2(collection_id),
            logical_path TEXT NOT NULL,
            logical_path_folded TEXT NOT NULL,
            observed_at TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS source_locations_v2_path
            ON source_locations_v2(collection_id, logical_path_folded, observed_at);

        CREATE TABLE IF NOT EXISTS compilations_v2 (
            compilation_id TEXT PRIMARY KEY,
            source_revision_id TEXT NOT NULL
                REFERENCES source_revisions_v2(source_revision_id),
            adapter TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            configuration_sha256 TEXT NOT NULL,
            source_ir_schema TEXT NOT NULL,
            fragment_inventory_sha256 TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS source_ir_nodes_v2 (
            node_id TEXT PRIMARY KEY,
            compilation_id TEXT NOT NULL REFERENCES compilations_v2(compilation_id),
            source_revision_id TEXT NOT NULL
                REFERENCES source_revisions_v2(source_revision_id),
            logical_node_key TEXT NOT NULL,
            parent_node_id TEXT REFERENCES source_ir_nodes_v2(node_id),
            ordinal INTEGER NOT NULL,
            node_type TEXT NOT NULL,
            title TEXT,
            text TEXT NOT NULL,
            locator TEXT NOT NULL,
            source_span_json TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            adapter TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            quality_flags_json TEXT NOT NULL,
            instruction_risk INTEGER NOT NULL,
            UNIQUE(compilation_id, logical_node_key)
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS source_ir_nodes_v2_parent
            ON source_ir_nodes_v2(compilation_id, parent_node_id, ordinal);

        CREATE TABLE IF NOT EXISTS fragments_v2 (
            fragment_revision_id TEXT PRIMARY KEY,
            compilation_id TEXT NOT NULL REFERENCES compilations_v2(compilation_id),
            ordinal INTEGER NOT NULL,
            locator TEXT NOT NULL,
            text_sha256 TEXT NOT NULL,
            instruction_risk INTEGER NOT NULL,
            UNIQUE(compilation_id, ordinal)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS fragment_node_membership_v2 (
            fragment_revision_id TEXT NOT NULL
                REFERENCES fragments_v2(fragment_revision_id),
            node_ordinal INTEGER NOT NULL,
            node_id TEXT NOT NULL REFERENCES source_ir_nodes_v2(node_id),
            PRIMARY KEY(fragment_revision_id, node_ordinal),
            UNIQUE(fragment_revision_id, node_id)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS legacy_fragment_bindings_v2 (
            fragment_id TEXT PRIMARY KEY REFERENCES source_fragments(fragment_id),
            legacy_source_id TEXT NOT NULL REFERENCES sources(source_id),
            fragment_revision_id TEXT NOT NULL
                REFERENCES fragments_v2(fragment_revision_id)
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS legacy_fragment_bindings_v2_revision
            ON legacy_fragment_bindings_v2(fragment_revision_id);

        CREATE TABLE IF NOT EXISTS proposal_sets_v2 (
            proposal_set_id TEXT PRIMARY KEY,
            compilation_id TEXT NOT NULL REFERENCES compilations_v2(compilation_id),
            extractor TEXT NOT NULL,
            extractor_revision TEXT NOT NULL,
            model_identity TEXT,
            prompt_config_sha256 TEXT NOT NULL,
            proposal_inventory_sha256 TEXT NOT NULL,
            proposal_ref_graph_sha256 TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS source_build_bindings_v2 (
            legacy_source_id TEXT PRIMARY KEY REFERENCES sources(source_id),
            compilation_id TEXT NOT NULL REFERENCES compilations_v2(compilation_id),
            proposal_set_id TEXT NOT NULL REFERENCES proposal_sets_v2(proposal_set_id),
            observed_at TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS knowledge_revisions_v2 (
            asset_revision_id TEXT PRIMARY KEY,
            knowledge_key TEXT NOT NULL,
            logical_node_keys_json TEXT NOT NULL,
            statement_sha256 TEXT NOT NULL,
            source_revision_ids_json TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS knowledge_revisions_v2_key
            ON knowledge_revisions_v2(knowledge_key, asset_revision_id);

        CREATE TABLE IF NOT EXISTS asset_revision_bindings_v2 (
            legacy_asset_id TEXT PRIMARY KEY REFERENCES assets(asset_id),
            legacy_source_id TEXT NOT NULL REFERENCES sources(source_id),
            asset_revision_id TEXT NOT NULL
                REFERENCES knowledge_revisions_v2(asset_revision_id),
            proposal_set_id TEXT REFERENCES proposal_sets_v2(proposal_set_id),
            proposal_ordinal INTEGER,
            observed_at TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS asset_revision_bindings_v2_revision
            ON asset_revision_bindings_v2(asset_revision_id);

        CREATE TABLE IF NOT EXISTS proposal_membership_v2 (
            proposal_set_id TEXT NOT NULL REFERENCES proposal_sets_v2(proposal_set_id),
            proposal_ordinal INTEGER NOT NULL,
            asset_revision_id TEXT NOT NULL
                REFERENCES knowledge_revisions_v2(asset_revision_id),
            knowledge_key TEXT NOT NULL,
            PRIMARY KEY(proposal_set_id, proposal_ordinal)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS proposal_metadata_v2 (
            proposal_set_id TEXT NOT NULL REFERENCES proposal_sets_v2(proposal_set_id),
            proposal_ordinal INTEGER NOT NULL,
            applicability_json TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            valid_from TEXT,
            valid_to TEXT,
            expires_at TEXT,
            project_scope TEXT,
            repository_scope TEXT,
            branch_scope TEXT,
            version_scope TEXT,
            environment_scope TEXT,
            warnings_json TEXT NOT NULL,
            PRIMARY KEY(proposal_set_id, proposal_ordinal),
            FOREIGN KEY(proposal_set_id, proposal_ordinal)
                REFERENCES proposal_membership_v2(proposal_set_id, proposal_ordinal)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS proposal_source_refs_v2 (
            asset_revision_id TEXT NOT NULL
                REFERENCES knowledge_revisions_v2(asset_revision_id),
            ref_ordinal INTEGER NOT NULL,
            source_revision_id TEXT NOT NULL
                REFERENCES source_revisions_v2(source_revision_id),
            fragment_revision_id TEXT NOT NULL
                REFERENCES fragments_v2(fragment_revision_id),
            locator TEXT NOT NULL,
            quote_sha256 TEXT NOT NULL,
            PRIMARY KEY(asset_revision_id, ref_ordinal),
            UNIQUE(asset_revision_id, source_revision_id, fragment_revision_id)
        ) WITHOUT ROWID;

        CREATE TABLE IF NOT EXISTS governance_revisions_v2 (
            governance_revision TEXT PRIMARY KEY,
            subject_kind TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            trust TEXT NOT NULL,
            sensitivity TEXT NOT NULL,
            policy_id TEXT NOT NULL,
            review_status TEXT NOT NULL,
            lifecycle_status TEXT NOT NULL,
            activation_status TEXT NOT NULL,
            revoked_at TEXT,
            export_allowed INTEGER NOT NULL,
            reviewer_id TEXT,
            recorded_at TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS governance_revisions_v2_subject
            ON governance_revisions_v2(subject_kind, subject_id, recorded_at);

        CREATE TABLE IF NOT EXISTS knowledge_lineage_v2 (
            lineage_id TEXT PRIMARY KEY,
            knowledge_key TEXT NOT NULL,
            from_asset_revision_ids_json TEXT NOT NULL,
            to_asset_revision_ids_json TEXT NOT NULL,
            status TEXT NOT NULL,
            source_revision_id TEXT NOT NULL
                REFERENCES source_revisions_v2(source_revision_id),
            mapping_evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS knowledge_lineage_v2_key
            ON knowledge_lineage_v2(knowledge_key, created_at);

        CREATE TABLE IF NOT EXISTS relation_revisions_v2 (
            relation_revision_id TEXT PRIMARY KEY,
            relation_key TEXT NOT NULL,
            legacy_relation_id TEXT UNIQUE REFERENCES relations(relation_id),
            subject_knowledge_key TEXT NOT NULL,
            object_knowledge_key TEXT NOT NULL,
            subject_asset_revision_id TEXT NOT NULL
                REFERENCES knowledge_revisions_v2(asset_revision_id),
            object_asset_revision_id TEXT NOT NULL
                REFERENCES knowledge_revisions_v2(asset_revision_id),
            predicate TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL,
            evidence_refs_sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            event_time TEXT,
            valid_from TEXT,
            valid_to TEXT,
            observed_at TEXT NOT NULL,
            reviewed_at TEXT,
            ingest_time TEXT NOT NULL,
            created_at TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS relation_revisions_v2_key
            ON relation_revisions_v2(relation_key, created_at);
    """


def install_identity_tables(
    connection: sqlite3.Connection,
    *,
    installed_at: str,
    migration_source: str,
) -> None:
    connection.executescript(identity_tables_sql())
    connection.execute(
        "INSERT OR IGNORE INTO identity_v2 VALUES (?, ?, ?)",
        (KNOWLEDGE_IDENTITY_SCHEMA, installed_at, migration_source),
    )


def register_compilation_identity(
    connection: sqlite3.Connection,
    *,
    vault_id: str,
    collection_id: str,
    collection_name: str,
    logical_path: str,
    source_key: str,
    legacy_source_id: str,
    content_sha256: str,
    media_identity: str,
    origin_uri: str | None,
    byte_size: int,
    observed_at: str,
    adapter: str,
    adapter_version: str,
    configuration: dict[str, Any],
    source_ir_nodes: list[dict[str, Any]],
    fragments: list[dict[str, Any]],
    extractor: str,
    extractor_revision: str,
    model_identity: str | None,
    prompt_configuration: dict[str, Any],
    proposals: list[dict[str, Any]],
    source_trust: str,
    source_sensitivity: str,
) -> dict[str, Any]:
    """Register one immutable v2 identity bundle inside the caller transaction."""
    if not identity_tables_present(connection):
        raise RuntimeError("Knowledge Identity v2 is not installed")
    canonical_collection = canonical_collection_name(collection_name)
    if collection_id != make_collection_id(vault_id=vault_id, name=canonical_collection):
        raise ValueError("collection identity does not match its canonical name")
    selected_path = normalize_logical_path(logical_path)
    existing_source_identity = connection.execute(
        "SELECT collection_id FROM source_identities_v2 WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    if existing_source_identity is None:
        if source_key != make_source_key(
            collection_id=collection_id,
            logical_path=selected_path,
        ):
            raise ValueError("source key does not match collection and logical path")
    elif existing_source_identity[0] != collection_id:
        raise ValueError("an existing source key cannot move across collections")
    origin_commitment = canonical_origin_commitment(origin_uri)
    source_revision_id = make_source_revision_id(
        source_key=source_key,
        content_sha256=content_sha256,
        media_identity=media_identity,
        origin_commitment=origin_commitment,
    )
    ordered_nodes = _validate_source_ir_nodes(source_ir_nodes)
    ordered_fragments = _validate_fragment_inventory(fragments, ordered_nodes)
    fragment_inventory = [
        {
            "ordinal": fragment["ordinal"],
            "locator": fragment["locator"],
            "text_sha256": fragment["text_sha256"],
            "instruction_risk": int(fragment["instruction_risk"]),
            "logical_node_keys": fragment["logical_node_keys"],
        }
        for fragment in ordered_fragments
    ]
    fragment_inventory_sha256 = inventory_sha256(fragment_inventory)
    configuration_sha256 = sha256_bytes(canonical_json(configuration).encode("utf-8"))
    compilation_id = make_compilation_id(
        source_revision_id=source_revision_id,
        adapter=adapter,
        adapter_version=adapter_version,
        configuration_sha256=configuration_sha256,
        source_ir_schema=SOURCE_IR_SCHEMA,
        fragment_inventory_sha256=fragment_inventory_sha256,
    )
    node_ids = {
        node["logical_node_key"]: stable_id(
            "irnode",
            compilation_id,
            node["logical_node_key"],
            node["content_sha256"],
        )
        for node in ordered_nodes
    }
    fragment_revision_ids = {
        fragment["fragment_id"]: stable_id(
            "irfragment",
            compilation_id,
            str(fragment["ordinal"]),
            fragment["locator"],
            fragment["text_sha256"],
        )
        for fragment in ordered_fragments
    }
    fragment_details = {
        fragment["fragment_id"]: fragment for fragment in ordered_fragments
    }

    proposal_inventory: list[dict[str, Any]] = []
    proposal_ref_graph: list[dict[str, Any]] = []
    prepared: list[dict[str, Any]] = []
    for ordinal, proposal in enumerate(proposals, start=1):
        prepared_proposal = _prepare_proposal_identity(
            connection,
            proposal=proposal,
            ordinal=ordinal,
            legacy_source_id=legacy_source_id,
            source_revision_id=source_revision_id,
            current_fragment_revision_ids=fragment_revision_ids,
            current_fragment_details=fragment_details,
        )
        prepared.append(prepared_proposal)
        proposal_inventory.append(
            {
                "ordinal": ordinal,
                "knowledge_key": prepared_proposal["knowledge_key"],
                "asset_revision_id": prepared_proposal["asset_revision_id"],
                "kind": prepared_proposal["kind"],
                "title": prepared_proposal["title"],
                "knowledge_content_sha256": prepared_proposal[
                    "knowledge_content_sha256"
                ],
                "lineage_status": prepared_proposal["lineage_status"],
                "applicability": prepared_proposal["applicability"],
                "observed_at": prepared_proposal["observed_at"],
                "valid_from": prepared_proposal["valid_from"],
                "valid_to": prepared_proposal["valid_to"],
                "expires_at": prepared_proposal["expires_at"],
                "scopes": prepared_proposal["scopes"],
                "warnings": prepared_proposal["proposal_warnings"],
            }
        )
        proposal_ref_graph.append(
            {
                "asset_revision_id": prepared_proposal["asset_revision_id"],
                "references": prepared_proposal["prepared_refs"],
            }
        )
    proposal_inventory_sha256 = inventory_sha256(proposal_inventory)
    proposal_ref_graph_sha256 = inventory_sha256(proposal_ref_graph)
    prompt_config_sha256 = sha256_bytes(
        canonical_json(prompt_configuration).encode("utf-8")
    )
    proposal_set_id = make_proposal_set_id(
        compilation_id=compilation_id,
        extractor=extractor,
        extractor_revision=extractor_revision,
        model_identity=model_identity,
        prompt_config_sha256=prompt_config_sha256,
        proposal_inventory_sha256=proposal_inventory_sha256,
        proposal_ref_graph_sha256=proposal_ref_graph_sha256,
    )

    _register_source_identity(
        connection,
        collection_id=collection_id,
        collection_name=canonical_collection,
        logical_path=selected_path,
        source_key=source_key,
        source_revision_id=source_revision_id,
        legacy_source_id=legacy_source_id,
        content_sha256=content_sha256,
        media_identity=media_identity,
        origin_commitment=origin_commitment,
        byte_size=byte_size,
        observed_at=observed_at,
    )
    _insert_exact(
        connection,
        table="compilations_v2",
        values={
            "compilation_id": compilation_id,
            "source_revision_id": source_revision_id,
            "adapter": normalize_text(adapter),
            "adapter_version": normalize_text(adapter_version),
            "configuration_sha256": configuration_sha256,
            "source_ir_schema": SOURCE_IR_SCHEMA,
            "fragment_inventory_sha256": fragment_inventory_sha256,
        },
        key="compilation_id",
    )
    _register_source_ir(
        connection,
        legacy_source_id=legacy_source_id,
        source_revision_id=source_revision_id,
        compilation_id=compilation_id,
        adapter=adapter,
        adapter_version=adapter_version,
        ordered_nodes=ordered_nodes,
        ordered_fragments=ordered_fragments,
        node_ids=node_ids,
        fragment_revision_ids=fragment_revision_ids,
    )
    _insert_exact(
        connection,
        table="proposal_sets_v2",
        values={
            "proposal_set_id": proposal_set_id,
            "compilation_id": compilation_id,
            "extractor": normalize_text(extractor),
            "extractor_revision": normalize_text(extractor_revision),
            "model_identity": normalize_text(model_identity) if model_identity else None,
            "prompt_config_sha256": prompt_config_sha256,
            "proposal_inventory_sha256": proposal_inventory_sha256,
            "proposal_ref_graph_sha256": proposal_ref_graph_sha256,
        },
        key="proposal_set_id",
    )
    _insert_exact(
        connection,
        table="source_build_bindings_v2",
        values={
            "legacy_source_id": legacy_source_id,
            "compilation_id": compilation_id,
            "proposal_set_id": proposal_set_id,
            "observed_at": observed_at,
        },
        key="legacy_source_id",
    )
    asset_identities = _register_proposal_identities(
        connection,
        prepared=prepared,
        legacy_source_id=legacy_source_id,
        source_revision_id=source_revision_id,
        proposal_set_id=proposal_set_id,
        observed_at=observed_at,
    )
    source_governance_revision = record_governance_revision(
        connection,
        subject_kind="source_revision",
        subject_id=source_revision_id,
        trust=source_trust,
        sensitivity=source_sensitivity,
        policy_id="deeplaw.local-source/v2",
        review_status="unreviewed",
        lifecycle_status="pending",
        reviewer_id=None,
        recorded_at=observed_at,
    )
    return {
        "schema_version": KNOWLEDGE_IDENTITY_SCHEMA,
        "collection_id": collection_id,
        "logical_path": selected_path,
        "source_key": source_key,
        "source_revision_id": source_revision_id,
        "compilation_id": compilation_id,
        "proposal_set_id": proposal_set_id,
        "governance_revision": source_governance_revision,
        "fragment_count": len(fragment_inventory),
        "fragment_inventory_sha256": fragment_inventory_sha256,
        "proposal_count": len(prepared),
        "proposal_inventory_sha256": proposal_inventory_sha256,
        "proposal_ref_graph_sha256": proposal_ref_graph_sha256,
        "asset_identities": asset_identities,
    }


def _validate_source_ir_nodes(
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(nodes, list) or len(nodes) > _MAX_SOURCE_IR_NODES:
        raise ValueError("Source IR node inventory exceeds the bound")
    ordered = sorted(nodes, key=lambda item: item.get("ordinal", -1))
    if [node.get("ordinal") for node in ordered] != list(range(1, len(ordered) + 1)):
        raise ValueError("Source IR node ordinals must be contiguous")
    logical_keys = [node.get("logical_node_key") for node in ordered]
    if len(set(logical_keys)) != len(logical_keys):
        raise ValueError("Source IR logical node keys must be unique")
    ordinal_by_key = {key: index for index, key in enumerate(logical_keys, start=1)}
    for node in ordered:
        logical_key = _bounded_identity(
            node.get("logical_node_key"),
            field="Source IR logical node key",
        )
        _bounded_identity(node.get("node_type"), field="Source IR node type")
        _bounded_identity(node.get("locator"), field="Source IR locator")
        _require_sha256(node.get("content_sha256"), field="Source IR content SHA-256")
        parent = node.get("parent_logical_node_key")
        if parent is not None and (
            parent not in ordinal_by_key
            or ordinal_by_key[parent] >= ordinal_by_key[logical_key]
        ):
            raise ValueError("Source IR parent must precede its child")
        if not isinstance(node.get("source_span"), dict):
            raise ValueError("Source IR source span must be an object")
        flags = node.get("quality_flags", [])
        if not isinstance(flags, list) or len(flags) > 100:
            raise ValueError("Source IR quality flags are invalid")
    return ordered


def _validate_fragment_inventory(
    fragments: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(fragments, list) or len(fragments) > _MAX_IDENTITY_ITEMS:
        raise ValueError("fragment inventory exceeds the bound")
    ordered = sorted(fragments, key=lambda item: item.get("ordinal", -1))
    if [item.get("ordinal") for item in ordered] != list(range(1, len(ordered) + 1)):
        raise ValueError("fragment ordinals must be contiguous")
    node_keys = {node["logical_node_key"] for node in nodes}
    inferred: dict[str, list[str]] = {}
    for node in nodes:
        fragment_id = node.get("fragment_id")
        if fragment_id is not None:
            inferred.setdefault(fragment_id, []).append(node["logical_node_key"])
    prepared: list[dict[str, Any]] = []
    seen_fragment_ids: set[str] = set()
    for fragment in ordered:
        fragment_id = fragment.get("fragment_id")
        if (
            not isinstance(fragment_id, str)
            or not fragment_id
            or fragment_id in seen_fragment_ids
        ):
            raise ValueError("legacy fragment identity is invalid")
        seen_fragment_ids.add(fragment_id)
        locator = _bounded_identity(fragment.get("locator"), field="fragment locator")
        text_sha256 = fragment.get("text_sha256")
        _require_sha256(text_sha256, field="fragment text SHA-256")
        logical_node_keys = list(
            fragment.get("logical_node_keys", inferred.get(fragment_id, []))
        )
        if (
            not logical_node_keys
            or len(logical_node_keys) > 100
            or len(set(logical_node_keys)) != len(logical_node_keys)
            or any(key not in node_keys for key in logical_node_keys)
        ):
            raise ValueError("fragment to Source IR mapping is invalid")
        prepared.append(
            {
                **fragment,
                "locator": locator,
                "text_sha256": text_sha256,
                "instruction_risk": bool(fragment.get("instruction_risk", False)),
                "logical_node_keys": logical_node_keys,
            }
        )
    return prepared


def _prepare_proposal_identity(
    connection: sqlite3.Connection,
    *,
    proposal: dict[str, Any],
    ordinal: int,
    legacy_source_id: str,
    source_revision_id: str,
    current_fragment_revision_ids: dict[str, str],
    current_fragment_details: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    knowledge_key = proposal.get("knowledge_key")
    _require_id(knowledge_key, "knowledge")
    revision_sha256 = proposal.get("knowledge_content_sha256")
    _require_sha256(revision_sha256, field="knowledge revision SHA-256")
    references = proposal.get("source_refs", [])
    if not isinstance(references, list) or not references or len(references) > 100:
        raise ValueError("proposal source reference graph is invalid")
    prepared_refs: list[dict[str, str]] = []
    source_revision_ids: set[str] = set()
    seen_refs: set[tuple[str, str]] = set()
    for reference in references:
        reference_source_id = reference.get("source_id")
        legacy_fragment_id = reference.get("fragment_id")
        if reference_source_id == legacy_source_id:
            ref_source_revision_id = source_revision_id
            fragment_revision_id = current_fragment_revision_ids.get(legacy_fragment_id)
            details = current_fragment_details.get(legacy_fragment_id)
        else:
            row = connection.execute(
                """
                SELECT source_revision_bindings_v2.source_revision_id,
                       legacy_fragment_bindings_v2.fragment_revision_id,
                       fragments_v2.locator,
                       fragments_v2.text_sha256
                FROM source_revision_bindings_v2
                JOIN legacy_fragment_bindings_v2 USING(legacy_source_id)
                JOIN fragments_v2 USING(fragment_revision_id)
                WHERE source_revision_bindings_v2.legacy_source_id = ?
                  AND legacy_fragment_bindings_v2.fragment_id = ?
                """,
                (reference_source_id, legacy_fragment_id),
            ).fetchone()
            ref_source_revision_id = row[0] if row is not None else None
            fragment_revision_id = row[1] if row is not None else None
            details = (
                {"locator": row[2], "text_sha256": row[3]} if row is not None else None
            )
        if ref_source_revision_id is None or fragment_revision_id is None or details is None:
            raise ValueError("proposal references an unregistered source fragment")
        locator = reference.get("locator")
        quote_sha256 = reference.get("quote_sha256")
        if locator != details["locator"] or quote_sha256 != details["text_sha256"]:
            raise ValueError("proposal reference locator or quote hash does not match evidence")
        ref_key = (ref_source_revision_id, fragment_revision_id)
        if ref_key in seen_refs:
            raise ValueError("proposal source references must be unique")
        seen_refs.add(ref_key)
        source_revision_ids.add(ref_source_revision_id)
        prepared_refs.append(
            {
                "source_revision_id": ref_source_revision_id,
                "fragment_revision_id": fragment_revision_id,
                "locator": locator,
                "quote_sha256": quote_sha256,
            }
        )
    asset_revision_id = make_asset_revision_id(
        knowledge_key=knowledge_key,
        knowledge_content_sha256=revision_sha256,
        source_revision_ids=tuple(sorted(source_revision_ids)),
    )
    lineage_status = proposal.get("lineage_status", "new")
    if lineage_status not in LINEAGE_STATUSES - {"deleted"}:
        raise ValueError("proposal lineage status is invalid")
    predecessors = list(proposal.get("predecessor_revision_ids", []))
    logical_node_keys = list(proposal.get("logical_node_keys", []))
    if len(predecessors) > 100 or len(logical_node_keys) > 100:
        raise ValueError("proposal lineage inventory exceeds the bound")
    for predecessor in predecessors:
        _require_id(predecessor, "assetrev")
    applicability = proposal.get("applicability", {})
    if not isinstance(applicability, dict):
        raise ValueError("proposal applicability must be an object")
    observed_at = _bounded_identity(
        proposal.get("observed_at"), field="proposal observed_at"
    )
    valid_from = _optional_bounded(proposal.get("valid_from"), field="valid_from")
    valid_to = _optional_bounded(proposal.get("valid_to"), field="valid_to")
    expires_at = _optional_bounded(proposal.get("expires_at"), field="expires_at")
    scopes = {
        name: _optional_bounded(proposal.get(name), field=name)
        for name in (
            "project_scope",
            "repository_scope",
            "branch_scope",
            "version_scope",
            "environment_scope",
        )
    }
    proposal_warnings = list(proposal.get("warnings", []))
    if len(proposal_warnings) > 100 or any(
        not isinstance(warning, str) or not 1 <= len(normalize_text(warning)) <= 500
        for warning in proposal_warnings
    ):
        raise ValueError("proposal warnings are invalid")
    return {
        **proposal,
        "proposal_ordinal": ordinal,
        "asset_revision_id": asset_revision_id,
        "lineage_status": lineage_status,
        "predecessor_revision_ids": predecessors,
        "logical_node_keys": logical_node_keys,
        "prepared_refs": prepared_refs,
        "source_revision_ids": sorted(source_revision_ids),
        "applicability": applicability,
        "observed_at": observed_at,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "expires_at": expires_at,
        "scopes": scopes,
        "proposal_warnings": [normalize_text(value) for value in proposal_warnings],
    }


def _register_source_identity(
    connection: sqlite3.Connection,
    *,
    collection_id: str,
    collection_name: str,
    logical_path: str,
    source_key: str,
    source_revision_id: str,
    legacy_source_id: str,
    content_sha256: str,
    media_identity: str,
    origin_commitment: str,
    byte_size: int,
    observed_at: str,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO collections_v2 VALUES (?, ?, ?)",
        (collection_id, collection_name, observed_at),
    )
    collection = connection.execute(
        "SELECT name FROM collections_v2 WHERE collection_id = ?",
        (collection_id,),
    ).fetchone()
    if collection is None or collection[0] != collection_name:
        raise RuntimeError("Identity v2 collection collision or state mismatch")
    existing_identity = connection.execute(
        "SELECT collection_id, logical_path FROM source_identities_v2 WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    if existing_identity is None:
        _insert_exact(
            connection,
            table="source_identities_v2",
            values={
                "source_key": source_key,
                "collection_id": collection_id,
                "logical_path": logical_path,
                "logical_path_folded": logical_path.casefold(),
                "created_at": observed_at,
            },
            key="source_key",
        )
    elif existing_identity[0] != collection_id:
        raise RuntimeError("Identity v2 source key collection collision or state mismatch")
    _insert_exact(
        connection,
        table="source_revisions_v2",
        values={
            "source_revision_id": source_revision_id,
            "source_key": source_key,
            "content_sha256": content_sha256,
            "media_identity": normalize_text(media_identity).lower(),
            "origin_commitment": origin_commitment,
            "byte_size": byte_size,
        },
        key="source_revision_id",
    )
    _insert_exact(
        connection,
        table="source_revision_bindings_v2",
        values={
            "legacy_source_id": legacy_source_id,
            "source_revision_id": source_revision_id,
            "observed_at": observed_at,
        },
        key="legacy_source_id",
    )
    _insert_exact(
        connection,
        table="source_locations_v2",
        values={
            "location_id": stable_id(
                "location",
                legacy_source_id,
                collection_id,
                logical_path,
            ),
            "legacy_source_id": legacy_source_id,
            "source_revision_id": source_revision_id,
            "collection_id": collection_id,
            "logical_path": logical_path,
            "logical_path_folded": logical_path.casefold(),
            "observed_at": observed_at,
        },
        key="location_id",
    )


def _register_source_ir(
    connection: sqlite3.Connection,
    *,
    legacy_source_id: str,
    source_revision_id: str,
    compilation_id: str,
    adapter: str,
    adapter_version: str,
    ordered_nodes: list[dict[str, Any]],
    ordered_fragments: list[dict[str, Any]],
    node_ids: dict[str, str],
    fragment_revision_ids: dict[str, str],
) -> None:
    for node in ordered_nodes:
        parent_key = node.get("parent_logical_node_key")
        _insert_exact(
            connection,
            table="source_ir_nodes_v2",
            values={
                "node_id": node_ids[node["logical_node_key"]],
                "compilation_id": compilation_id,
                "source_revision_id": source_revision_id,
                "logical_node_key": node["logical_node_key"],
                "parent_node_id": node_ids.get(parent_key) if parent_key else None,
                "ordinal": node["ordinal"],
                "node_type": node["node_type"],
                "title": node.get("title"),
                "text": node.get("text", ""),
                "locator": node["locator"],
                "source_span_json": canonical_json(node["source_span"]),
                "content_sha256": node["content_sha256"],
                "adapter": normalize_text(adapter),
                "adapter_version": normalize_text(adapter_version),
                "quality_flags_json": canonical_json(node.get("quality_flags", [])),
                "instruction_risk": int(bool(node.get("instruction_risk", False))),
            },
            key="node_id",
        )
    for fragment in ordered_fragments:
        fragment_revision_id = fragment_revision_ids[fragment["fragment_id"]]
        _insert_exact(
            connection,
            table="fragments_v2",
            values={
                "fragment_revision_id": fragment_revision_id,
                "compilation_id": compilation_id,
                "ordinal": fragment["ordinal"],
                "locator": fragment["locator"],
                "text_sha256": fragment["text_sha256"],
                "instruction_risk": int(fragment["instruction_risk"]),
            },
            key="fragment_revision_id",
        )
        for node_ordinal, logical_node_key in enumerate(
            fragment["logical_node_keys"], start=1
        ):
            _insert_exact(
                connection,
                table="fragment_node_membership_v2",
                values={
                    "fragment_revision_id": fragment_revision_id,
                    "node_ordinal": node_ordinal,
                    "node_id": node_ids[logical_node_key],
                },
                key=("fragment_revision_id", "node_ordinal"),
            )
        _insert_exact(
            connection,
            table="legacy_fragment_bindings_v2",
            values={
                "fragment_id": fragment["fragment_id"],
                "legacy_source_id": legacy_source_id,
                "fragment_revision_id": fragment_revision_id,
            },
            key="fragment_id",
        )


def _register_proposal_identities(
    connection: sqlite3.Connection,
    *,
    prepared: list[dict[str, Any]],
    legacy_source_id: str,
    source_revision_id: str,
    proposal_set_id: str,
    observed_at: str,
) -> list[dict[str, str]]:
    asset_identities: list[dict[str, str]] = []
    for proposal in prepared:
        asset_revision_id = proposal["asset_revision_id"]
        knowledge_key = proposal["knowledge_key"]
        ordinal = proposal["proposal_ordinal"]
        _insert_exact(
            connection,
            table="knowledge_revisions_v2",
            values={
                "asset_revision_id": asset_revision_id,
                "knowledge_key": knowledge_key,
                "logical_node_keys_json": canonical_json(proposal["logical_node_keys"]),
                "statement_sha256": proposal["knowledge_content_sha256"],
                "source_revision_ids_json": canonical_json(
                    proposal["source_revision_ids"]
                ),
            },
            key="asset_revision_id",
        )
        _insert_exact(
            connection,
            table="asset_revision_bindings_v2",
            values={
                "legacy_asset_id": proposal["legacy_asset_id"],
                "legacy_source_id": legacy_source_id,
                "asset_revision_id": asset_revision_id,
                "proposal_set_id": proposal_set_id,
                "proposal_ordinal": ordinal,
                "observed_at": observed_at,
            },
            key="legacy_asset_id",
        )
        _insert_exact(
            connection,
            table="proposal_membership_v2",
            values={
                "proposal_set_id": proposal_set_id,
                "proposal_ordinal": ordinal,
                "asset_revision_id": asset_revision_id,
                "knowledge_key": knowledge_key,
            },
            key=("proposal_set_id", "proposal_ordinal"),
        )
        _insert_exact(
            connection,
            table="proposal_metadata_v2",
            values={
                "proposal_set_id": proposal_set_id,
                "proposal_ordinal": ordinal,
                "applicability_json": canonical_json(proposal["applicability"]),
                "observed_at": proposal["observed_at"],
                "valid_from": proposal["valid_from"],
                "valid_to": proposal["valid_to"],
                "expires_at": proposal["expires_at"],
                "project_scope": proposal["scopes"]["project_scope"],
                "repository_scope": proposal["scopes"]["repository_scope"],
                "branch_scope": proposal["scopes"]["branch_scope"],
                "version_scope": proposal["scopes"]["version_scope"],
                "environment_scope": proposal["scopes"]["environment_scope"],
                "warnings_json": canonical_json(proposal["proposal_warnings"]),
            },
            key=("proposal_set_id", "proposal_ordinal"),
        )
        for ref_ordinal, reference in enumerate(proposal["prepared_refs"], start=1):
            _insert_exact(
                connection,
                table="proposal_source_refs_v2",
                values={
                    "asset_revision_id": asset_revision_id,
                    "ref_ordinal": ref_ordinal,
                    "source_revision_id": reference["source_revision_id"],
                    "fragment_revision_id": reference["fragment_revision_id"],
                    "locator": reference["locator"],
                    "quote_sha256": reference["quote_sha256"],
                },
                key=("asset_revision_id", "ref_ordinal"),
            )
        record_lineage_transition(
            connection,
            knowledge_key=knowledge_key,
            from_asset_revision_ids=tuple(proposal["predecessor_revision_ids"]),
            to_asset_revision_ids=(asset_revision_id,),
            status=proposal["lineage_status"],
            source_revision_id=source_revision_id,
            mapping_evidence=proposal.get("mapping_evidence", {}),
            created_at=observed_at,
        )
        governance_revision = record_governance_revision(
            connection,
            subject_kind="asset_revision",
            subject_id=asset_revision_id,
            trust=proposal["trust"],
            sensitivity=proposal["sensitivity"],
            policy_id="deeplaw.local-proposal/v2",
            review_status="unreviewed",
            lifecycle_status=proposal["status"],
            reviewer_id=None,
            recorded_at=observed_at,
        )
        asset_identities.append(
            {
                "legacy_asset_id": proposal["legacy_asset_id"],
                "knowledge_key": knowledge_key,
                "asset_revision_id": asset_revision_id,
                "governance_revision": governance_revision,
            }
        )
    return asset_identities


def record_lineage_transition(
    connection: sqlite3.Connection,
    *,
    knowledge_key: str,
    from_asset_revision_ids: tuple[str, ...],
    to_asset_revision_ids: tuple[str, ...],
    status: LineageStatus,
    source_revision_id: str,
    mapping_evidence: dict[str, Any],
    created_at: str,
) -> str:
    _require_id(knowledge_key, "knowledge")
    _require_id(source_revision_id, "sourcerev")
    if status not in LINEAGE_STATUSES:
        raise ValueError("knowledge lineage status is invalid")
    if (
        len(from_asset_revision_ids) > 100
        or len(to_asset_revision_ids) > 100
        or len(set(from_asset_revision_ids)) != len(from_asset_revision_ids)
        or len(set(to_asset_revision_ids)) != len(to_asset_revision_ids)
    ):
        raise ValueError("knowledge lineage revision inventory is invalid")
    revision_knowledge_keys: set[str] = set()
    for revision_id in (*from_asset_revision_ids, *to_asset_revision_ids):
        _require_id(revision_id, "assetrev")
        row = connection.execute(
            "SELECT knowledge_key FROM knowledge_revisions_v2 "
            "WHERE asset_revision_id = ?",
            (revision_id,),
        ).fetchone()
        if row is None:
            raise ValueError("knowledge lineage revision is unavailable")
        revision_knowledge_keys.add(row[0])
    expected_shape = {
        "new": (0, 1),
        "unchanged": (1, 1),
        "modified": (1, 1),
        "renamed": (1, 1),
        "moved": (1, 1),
        "deleted": (1, 0),
    }
    if status in expected_shape and (
        len(from_asset_revision_ids), len(to_asset_revision_ids)
    ) != expected_shape[status]:
        raise ValueError("knowledge lineage transition has an invalid shape")
    if status == "split" and not (
        len(from_asset_revision_ids) == 1 and len(to_asset_revision_ids) > 1
    ):
        raise ValueError("split lineage requires one predecessor and multiple successors")
    if status == "merged" and not (
        len(from_asset_revision_ids) > 1 and len(to_asset_revision_ids) == 1
    ):
        raise ValueError("merged lineage requires multiple predecessors and one successor")
    if status == "ambiguous" and not (
        from_asset_revision_ids or to_asset_revision_ids
    ):
        raise ValueError("ambiguous lineage requires at least one revision")
    cross_key_status = status in {"split", "merged", "ambiguous"}
    if cross_key_status:
        if knowledge_key not in revision_knowledge_keys:
            raise ValueError("cross-key lineage must be indexed under an involved key")
    elif revision_knowledge_keys != {knowledge_key}:
        raise ValueError("knowledge lineage revision does not belong to its key")
    lineage_id = stable_id(
        "lineage",
        knowledge_key,
        canonical_json(list(from_asset_revision_ids)),
        canonical_json(list(to_asset_revision_ids)),
        status,
        canonical_json(mapping_evidence),
        created_at,
    )
    _insert_exact(
        connection,
        table="knowledge_lineage_v2",
        values={
            "lineage_id": lineage_id,
            "knowledge_key": knowledge_key,
            "from_asset_revision_ids_json": canonical_json(
                list(from_asset_revision_ids)
            ),
            "to_asset_revision_ids_json": canonical_json(list(to_asset_revision_ids)),
            "status": status,
            "source_revision_id": source_revision_id,
            "mapping_evidence_json": canonical_json(mapping_evidence),
            "created_at": created_at,
        },
        key="lineage_id",
    )
    return lineage_id


def record_governance_revision(
    connection: sqlite3.Connection,
    *,
    subject_kind: str,
    subject_id: str,
    trust: str,
    sensitivity: str,
    policy_id: str,
    review_status: str,
    lifecycle_status: str,
    reviewer_id: str | None,
    recorded_at: str,
    activation_status: str = "inactive",
    revoked_at: str | None = None,
    export_allowed: bool = False,
) -> str:
    governance_revision = make_governance_revision(
        subject_kind=subject_kind,
        subject_id=subject_id,
        trust=trust,
        sensitivity=sensitivity,
        policy_id=policy_id,
        review_status=review_status,
        lifecycle_status=lifecycle_status,
        activation_status=activation_status,
        revoked_at=revoked_at,
        export_allowed=export_allowed,
        recorded_at=recorded_at,
    )
    _insert_exact(
        connection,
        table="governance_revisions_v2",
        values={
            "governance_revision": governance_revision,
            "subject_kind": subject_kind,
            "subject_id": subject_id,
            "trust": trust,
            "sensitivity": sensitivity,
            "policy_id": policy_id,
            "review_status": review_status,
            "lifecycle_status": lifecycle_status,
            "activation_status": activation_status,
            "revoked_at": revoked_at,
            "export_allowed": int(export_allowed),
            "reviewer_id": reviewer_id,
            "recorded_at": recorded_at,
        },
        key="governance_revision",
    )
    return governance_revision


def record_relation_revision(
    connection: sqlite3.Connection,
    *,
    vault_id: str,
    legacy_relation_id: str | None,
    subject_knowledge_key: str,
    object_knowledge_key: str,
    subject_asset_revision_id: str,
    object_asset_revision_id: str,
    predicate: str,
    evidence_refs: list[dict[str, Any]],
    status: str,
    event_time: str | None,
    valid_from: str | None,
    valid_to: str | None,
    observed_at: str,
    reviewed_at: str | None,
    ingest_time: str,
) -> dict[str, str]:
    if status not in {"proposed", "active", "superseded", "revoked", "ambiguous"}:
        raise ValueError("relation revision status is invalid")
    if not isinstance(evidence_refs, list) or not evidence_refs or len(evidence_refs) > 100:
        raise ValueError("relation revision requires bounded source evidence")
    for reference in evidence_refs:
        if set(reference) != {
            "source_revision_id",
            "fragment_revision_id",
            "locator",
            "quote_sha256",
        }:
            raise ValueError("relation evidence reference contract is invalid")
        _require_id(reference["source_revision_id"], "sourcerev")
        _require_sha256(reference["quote_sha256"], field="relation quote SHA-256")
        fragment = connection.execute(
            """
            SELECT source_revision_id, fragments_v2.locator, fragments_v2.text_sha256
            FROM fragments_v2
            JOIN compilations_v2 USING(compilation_id)
            WHERE fragment_revision_id = ?
            """,
            (reference["fragment_revision_id"],),
        ).fetchone()
        if fragment is None or tuple(fragment) != (
            reference["source_revision_id"],
            reference["locator"],
            reference["quote_sha256"],
        ):
            raise ValueError("relation evidence does not match canonical source evidence")
    relation_key = make_relation_key(
        vault_id=vault_id,
        subject_knowledge_key=subject_knowledge_key,
        predicate=predicate,
        object_knowledge_key=object_knowledge_key,
    )
    evidence_refs_sha256 = inventory_sha256(evidence_refs)
    relation_revision_id = make_relation_revision_id(
        relation_key=relation_key,
        subject_asset_revision_id=subject_asset_revision_id,
        object_asset_revision_id=object_asset_revision_id,
        evidence_refs_sha256=evidence_refs_sha256,
        valid_from=valid_from,
        valid_to=valid_to,
        observed_at=observed_at,
    )
    _insert_exact(
        connection,
        table="relation_revisions_v2",
        values={
            "relation_revision_id": relation_revision_id,
            "relation_key": relation_key,
            "legacy_relation_id": legacy_relation_id,
            "subject_knowledge_key": subject_knowledge_key,
            "object_knowledge_key": object_knowledge_key,
            "subject_asset_revision_id": subject_asset_revision_id,
            "object_asset_revision_id": object_asset_revision_id,
            "predicate": predicate,
            "evidence_refs_json": canonical_json(evidence_refs),
            "evidence_refs_sha256": evidence_refs_sha256,
            "status": status,
            "event_time": event_time,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "observed_at": observed_at,
            "reviewed_at": reviewed_at,
            "ingest_time": ingest_time,
            "created_at": ingest_time,
        },
        key="relation_revision_id",
    )
    return {
        "relation_key": relation_key,
        "relation_revision_id": relation_revision_id,
        "evidence_refs_sha256": evidence_refs_sha256,
    }


def identity_tables_present(connection: sqlite3.Connection) -> bool:
    available = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    return set(_IDENTITY_TABLES).issubset(available)


def _insert_exact(
    connection: sqlite3.Connection,
    *,
    table: str,
    values: dict[str, Any],
    key: str | tuple[str, ...],
) -> None:
    key_columns = (key,) if isinstance(key, str) else key
    if (
        table not in _IDENTITY_TABLES
        or not key_columns
        or any(column not in values for column in key_columns)
    ):
        raise ValueError("unsafe Identity v2 insert target")
    columns = tuple(values)
    placeholders = ",".join("?" for _ in columns)
    connection.execute(
        f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) "
        f"VALUES ({placeholders})",
        tuple(values[column] for column in columns),
    )
    predicate = " AND ".join(f"{column} = ?" for column in key_columns)
    row = connection.execute(
        f"SELECT {','.join(columns)} FROM {table} WHERE {predicate}",
        tuple(values[column] for column in key_columns),
    ).fetchone()
    if row is None or any(
        row[index] != values[column] for index, column in enumerate(columns)
    ):
        raise RuntimeError(f"Identity v2 {table} collision or state mismatch")


def identity_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    if not identity_tables_present(connection):
        raise RuntimeError("Knowledge Identity v2 tables are incomplete")
    tables: list[dict[str, Any]] = []
    for table in _IDENTITY_TABLES:
        columns = [
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        ]
        if not columns:
            raise RuntimeError(f"Knowledge Identity v2 table is unavailable: {table}")
        order = ", ".join(columns)
        digest = hashlib.sha256()
        count = 0
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            payload = {column: row[index] for index, column in enumerate(columns)}
            digest.update(canonical_json(payload).encode("utf-8"))
            digest.update(b"\n")
            count += 1
        tables.append(
            {"table": table, "row_count": count, "sha256": digest.hexdigest()}
        )
    return {
        "schema_version": KNOWLEDGE_IDENTITY_SCHEMA,
        "tables": tables,
        "identity_root_sha256": sha256_bytes(canonical_json(tables).encode("utf-8")),
    }


def _bounded_identity(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = normalize_text(value)
    if not 1 <= len(normalized) <= 500:
        raise ValueError(f"{field} must be a bounded canonical value")
    return normalized


def _optional_bounded(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    return _bounded_identity(value, field=field)


def _require_id(value: str, prefix: str) -> None:
    pattern = re.compile(rf"^{re.escape(prefix)}_[0-9a-f]{{24}}$")
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"expected canonical {prefix} identity")


def _require_sha256(value: str, *, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be lowercase SHA-256")
