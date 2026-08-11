from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..knowledge_autonomy import _read_object, _validate_contract, _write_object
from ..util import canonical_json, sha256_bytes, strict_json_loads

STATEMENT_BUNDLE_ROLE = "statement_bundle"
STATEMENT_BUNDLE_SCHEMA = "deeplaw.source-compilation-artifact-bundle/v1"
STATEMENT_BUNDLE_MEMBER_ROLES = frozenset(
    {"statement", "statement_map", "statement_evidence_receipt"}
)
MAX_STATEMENT_BUNDLE_ENTRIES = 768
MAX_STATEMENT_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_STATEMENT_MEMBER_BYTES = 256 * 1024

BundleCache = dict[str, tuple[bytes, list[dict[str, Any]]]]


def _artifact_metadata(
    connection: sqlite3.Connection,
    digest: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT artifact_role, byte_size
        FROM source_compilation_artifacts_v1
        WHERE artifact_sha256 = ?
        """,
        (digest,),
    ).fetchone()


def _bundle_entries(
    connection: sqlite3.Connection,
    root: Path,
    bundle_sha256: str,
    *,
    cache: BundleCache | None,
) -> tuple[bytes, list[dict[str, Any]]]:
    if cache is not None and bundle_sha256 in cache:
        return cache[bundle_sha256]
    metadata = _artifact_metadata(connection, bundle_sha256)
    if metadata is None or metadata["artifact_role"] != STATEMENT_BUNDLE_ROLE:
        raise RuntimeError("source compilation artifact bundle metadata is invalid")
    if metadata["byte_size"] > MAX_STATEMENT_BUNDLE_BYTES:
        raise RuntimeError("source compilation artifact bundle exceeds its read bound")
    payload = _read_object(root, bundle_sha256)
    if len(payload) != metadata["byte_size"]:
        raise RuntimeError("source compilation artifact bundle byte size changed")
    value = strict_json_loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError("source compilation artifact bundle is not an object")
    _validate_contract("source-compilation-artifact-bundle.v1.schema.json", value)
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError("source compilation artifact bundle entries are invalid")
    result = (payload, entries)
    if cache is not None:
        cache[bundle_sha256] = result
    return result


def read_compilation_artifact(
    connection: sqlite3.Connection,
    root: Path,
    digest: str,
    *,
    role: str,
    maximum_bytes: int | None = None,
    bundle_cache: BundleCache | None = None,
) -> bytes:
    """Read one logical artifact from either legacy CAS bytes or a v1 bundle."""

    metadata = _artifact_metadata(connection, digest)
    if metadata is None or metadata["artifact_role"] != role:
        raise RuntimeError("source compilation artifact metadata is invalid")
    if maximum_bytes is not None and metadata["byte_size"] > maximum_bytes:
        raise RuntimeError("source compilation artifact exceeds its read bound")
    member = connection.execute(
        """
        SELECT bundle_sha256, entry_ordinal
        FROM source_compilation_artifact_bundle_members_v1
        WHERE artifact_sha256 = ?
        """,
        (digest,),
    ).fetchone()
    if member is None:
        payload = _read_object(root, digest)
    else:
        _bundle_payload, entries = _bundle_entries(
            connection,
            root,
            member["bundle_sha256"],
            cache=bundle_cache,
        )
        entry_ordinal = member["entry_ordinal"]
        if not isinstance(entry_ordinal, int) or not 1 <= entry_ordinal <= len(entries):
            raise RuntimeError("source compilation artifact bundle ordinal is invalid")
        entry = entries[entry_ordinal - 1]
        if (
            not isinstance(entry, dict)
            or entry.get("artifact_sha256") != digest
            or entry.get("artifact_role") != role
            or not isinstance(entry.get("value"), dict)
        ):
            raise RuntimeError("source compilation artifact bundle member is invalid")
        payload = canonical_json(entry["value"]).encode("utf-8")
    if len(payload) != metadata["byte_size"] or sha256_bytes(payload) != digest:
        raise RuntimeError("source compilation artifact bytes are invalid")
    return payload


def read_statement_artifact_bundle(
    connection: sqlite3.Connection,
    root: Path,
    bundle_sha256: str,
    *,
    bundle_cache: BundleCache | None = None,
) -> list[dict[str, Any]]:
    """Validate and return every logical entry in one Statement artifact bundle."""

    _payload, entries = _bundle_entries(
        connection,
        root,
        bundle_sha256,
        cache=bundle_cache,
    )
    return entries


def _bundle_payload(entries: list[dict[str, Any]]) -> bytes:
    value = {"schema_version": STATEMENT_BUNDLE_SCHEMA, "entries": entries}
    _validate_contract("source-compilation-artifact-bundle.v1.schema.json", value)
    payload = canonical_json(value).encode("utf-8")
    if len(payload) > MAX_STATEMENT_BUNDLE_BYTES:
        raise ValueError("source compilation artifact bundle exceeds its byte bound")
    return payload


def _bundle_chunks(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    empty_bytes = len(_bundle_payload([]))
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = empty_bytes
    for entry in entries:
        entry_bytes = len(canonical_json(entry).encode("utf-8"))
        separator_bytes = 1 if current else 0
        if current and (
            len(current) >= MAX_STATEMENT_BUNDLE_ENTRIES
            or current_bytes + separator_bytes + entry_bytes > MAX_STATEMENT_BUNDLE_BYTES
        ):
            chunks.append(current)
            current = []
            current_bytes = empty_bytes
            separator_bytes = 0
        if current_bytes + separator_bytes + entry_bytes > MAX_STATEMENT_BUNDLE_BYTES:
            raise ValueError("source compilation artifact bundle member exceeds its byte bound")
        current.append(entry)
        current_bytes += separator_bytes + entry_bytes
    if current:
        chunks.append(current)
    return chunks


def write_statement_artifact_bundles(
    connection: sqlite3.Connection,
    root: Path,
    artifacts: list[tuple[str, dict[str, Any]]],
    *,
    created_at: str,
) -> list[tuple[str, bytes]]:
    """Persist logical Statement artifacts in deterministic bounded CAS bundles."""

    if not artifacts:
        return []
    results: list[tuple[str, bytes]] = []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for role, value in artifacts:
        if role not in STATEMENT_BUNDLE_MEMBER_ROLES:
            raise ValueError("source compilation artifact role cannot enter a statement bundle")
        payload = canonical_json(value).encode("utf-8")
        if len(payload) > MAX_STATEMENT_MEMBER_BYTES:
            raise ValueError("statement evidence artifact exceeds its byte bound")
        digest = sha256_bytes(payload)
        if digest in seen:
            raise ValueError("statement artifact bundle contains a duplicate digest")
        seen.add(digest)
        existing = _artifact_metadata(connection, digest)
        if existing is not None:
            if existing["artifact_role"] != role or existing["byte_size"] != len(payload):
                raise RuntimeError("source compilation artifact metadata is inconsistent")
            read_compilation_artifact(
                connection,
                root,
                digest,
                role=role,
                maximum_bytes=MAX_STATEMENT_MEMBER_BYTES,
            )
            results.append((digest, payload))
            continue
        entries.append(
            {
                "artifact_sha256": digest,
                "artifact_role": role,
                "value": value,
            }
        )
        results.append((digest, payload))

    for chunk in _bundle_chunks(entries):
        bundle_payload = _bundle_payload(chunk)
        bundle_sha256, _ = _write_object(root, bundle_payload)
        connection.execute(
            """
            INSERT OR IGNORE INTO source_compilation_artifacts_v1(
                artifact_sha256, artifact_role, byte_size, media_type, created_at
            ) VALUES (?, 'statement_bundle', ?, 'application/json', ?)
            """,
            (bundle_sha256, len(bundle_payload), created_at),
        )
        bundle_metadata = _artifact_metadata(connection, bundle_sha256)
        if (
            bundle_metadata is None
            or bundle_metadata["artifact_role"] != STATEMENT_BUNDLE_ROLE
            or bundle_metadata["byte_size"] != len(bundle_payload)
        ):
            raise RuntimeError("source compilation artifact bundle metadata is inconsistent")
        for entry_ordinal, entry in enumerate(chunk, start=1):
            member_payload = canonical_json(entry["value"]).encode("utf-8")
            connection.execute(
                """
                INSERT INTO source_compilation_artifacts_v1(
                    artifact_sha256, artifact_role, byte_size, media_type, created_at
                ) VALUES (?, ?, ?, 'application/json', ?)
                """,
                (
                    entry["artifact_sha256"],
                    entry["artifact_role"],
                    len(member_payload),
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO source_compilation_artifact_bundle_members_v1(
                    artifact_sha256, bundle_sha256, entry_ordinal
                ) VALUES (?, ?, ?)
                """,
                (entry["artifact_sha256"], bundle_sha256, entry_ordinal),
            )
    return results
