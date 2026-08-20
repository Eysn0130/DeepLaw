from __future__ import annotations

import json
import tempfile
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from ..knowledge_autonomy import (
    AutonomousKnowledgeStore,
    _atomic_owner_write,
    _interval_admits,
    _read_object,
    _validate_contract,
)
from ..knowledge_intelligence import detect_communities
from ..util import canonical_json, sha256_bytes, sha256_file, stable_id, strict_json_loads
from ..wiki import (
    build_link_index,
    build_living_wiki_manifest_v3,
    build_page_registry,
    build_resolver_index,
)
from .incremental import (
    activate_projection,
    begin_transaction,
    build_change_set,
    discard_transaction,
    prepare_activation,
    read_previous_manifest,
    read_previous_v3,
    recover_projection,
)
from .profiles import projection_profile as resolve_projection_profile

LIVING_WIKI_SCHEMA = "deeplaw.living-wiki-manifest/v2"
LIVING_WIKI_GENERATOR = "deeplaw.living-wiki-projector/2"
INDEX_SHARD_SIZE = 200
SOURCE_FRAGMENT_SHARD_SIZE = 64
STATEMENT_EVIDENCE_SHARD_SIZE = 64
CANVAS_NODE_LIMIT = 200
CANVAS_EDGE_LIMIT = 400
MAX_DERIVED_FILE_BYTES = 256 * 1024
V3_MANIFEST_PATH = ".deeplaw/derived/wiki/v3/manifest.json"

_KIND_DIRECTORIES = {
    "claim": "claims",
    "concept": "concepts",
    "entity": "entities",
    "event": "events",
    "decision": "decisions",
    "procedure": "procedures",
    "experience": "experiences",
    "preference": "preferences",
    "synthesis": "syntheses",
    "comparison": "comparisons",
    "skill": "skills",
    "memory": "memory",
}


def _frontmatter(
    *,
    schema: str,
    audit_head: str,
    fields: dict[str, Any] | None = None,
) -> list[str]:
    metadata: dict[str, Any] = {
        "schema": schema,
        "derived_view": "true",
        "audit_head": audit_head,
        "authority": "none",
        "legal_authority": "false",
        "verification": "projection_only",
        "freshness": "not_applicable",
        "lifecycle": "active",
        "semantic_status": "not_applicable",
        "revision": "not_applicable",
    }
    metadata.update(fields or {})
    lines = [
        "---",
        *[f"{key}: {value}" for key, value in metadata.items()],
        "---",
        "",
        "## Governance",
        "",
        f"- Authority: `{metadata['authority']}`",
        f"- Verification: `{metadata['verification']}`",
        f"- Freshness: `{metadata['freshness']}`",
        f"- Lifecycle: `{metadata['lifecycle']}`",
        f"- Semantic Status: `{metadata['semantic_status']}`",
        f"- Revision: `{metadata['revision']}`",
        "",
        "### Evidence",
        "",
        "- Projection metadata only; consult the exact Source Evidence or Ledger record.",
        "",
        "### History",
        "",
        "- Projection history is bounded by the registered audit head and revision identity.",
        "",
        "### Gap",
        "",
        "- `not_applicable` for this projection view unless a page-specific gap is listed.",
        "",
        "### Contradiction",
        "",
        "- `not_applicable` for this projection view unless an admitted contradiction is listed.",
        "",
    ]
    return lines


def _wiki_link(path: str, title: str) -> str:
    target = PurePosixPath(path).with_suffix("").as_posix()
    safe_title = title.replace("|", "¦").replace("]", "）")
    return f"[[{target}|{safe_title}]]"


def _fragment_anchor(fragment_id: str) -> str:
    """Return the stable, Obsidian-compatible HTML/heading anchor for a Source Fragment."""

    return f"fragment-{fragment_id.replace(':', '-')}"


def _write(
    root: Path,
    *,
    relative: str,
    content: str,
    generated: list[dict[str, Any]],
) -> None:
    payload = content.rstrip().encode("utf-8") + b"\n"
    if len(payload) > MAX_DERIVED_FILE_BYTES:
        raise RuntimeError(f"Living Wiki file exceeds its byte bound: {relative}")
    destination = root / relative
    _atomic_owner_write(destination, payload)
    generated.append(
        {
            "path": relative,
            "byte_size": len(payload),
            "sha256": sha256_bytes(payload),
        }
    )


def _page_record(
    *,
    page_id: str,
    namespace: str,
    path: str,
    kind: str,
    revision_id: str,
    audit_head: str,
    payload: bytes,
    scope: str,
    sensitivity: str,
    lifecycle: str = "active",
    freshness: str = "fresh",
    input_refs: list[str] | None = None,
    knowledge_id: str | None = None,
    semantic_key: str | None = None,
    aliases: list[str] | None = None,
    title: str | None = None,
    anchors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a caller-owned registry row from explicit identity/governance inputs."""

    refs = sorted(set(input_refs or [audit_head]))
    if len(refs) > 256:
        refs = [*refs[:255], stable_id("input-set", canonical_json(refs))]
    return {
        "page_id": page_id,
        "namespace": namespace,
        "canonical_page_path": path,
        "kind": kind,
        "revision_id": revision_id,
        "audit_head": audit_head,
        "byte_size": len(payload),
        "sha256": sha256_bytes(payload),
        "scope": scope,
        "sensitivity": sensitivity,
        "lifecycle": lifecycle,
        "freshness": freshness,
        "authority": "none",
        "input_refs": refs,
        **({"knowledge_id": knowledge_id} if knowledge_id else {}),
        **({"semantic_key": semantic_key} if semantic_key else {}),
        "aliases": sorted(set(aliases or [])),
        **({"title": title} if title else {}),
        **({"anchors": anchors} if anchors else {}),
    }


def _current_rows(
    store: AutonomousKnowledgeStore,
    *,
    reference_time: str,
) -> list[dict[str, Any]]:
    candidates = store.connection.execute(
        """
        SELECT knowledge_objects_v3.workspace_path AS current_workspace_path,
               knowledge_revisions_v3.*
        FROM knowledge_objects_v3
        JOIN knowledge_revisions_v3
          ON knowledge_revisions_v3.revision_id =
             knowledge_objects_v3.current_revision_id
        WHERE knowledge_revisions_v3.lifecycle = 'active'
        ORDER BY knowledge_revisions_v3.kind,
                 knowledge_revisions_v3.title,
                 knowledge_revisions_v3.knowledge_id
        """
    ).fetchall()
    rows: list[dict[str, Any]] = []
    for row in candidates:
        if not _interval_admits(
            reference_time=reference_time,
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            expires_at=row["expires_at"],
        ):
            continue
        revision = store._revision_row(row, include_body=True)
        if not store.revision_provenance_admitted(revision):
            continue
        # The canonical revision keeps validated aliases inside governance metadata.  Promote
        # that exact field for the page-record seam; registry validation still owns normalization
        # and bounds, and no editable path/frontmatter text is consulted here.
        revision["aliases"] = revision["metadata"].get("aliases", [])
        dependency_states = [
            item["freshness"]
            for item in store.connection.execute(
                """
                SELECT freshness FROM knowledge_dependencies_v1
                WHERE consumer_revision_id = ?
                ORDER BY freshness
                """,
                (revision["revision_id"],),
            )
        ]
        dependency_states.extend(
            item["freshness"]
            for item in store.connection.execute(
                """
                SELECT freshness FROM revision_dependencies_v1
                WHERE consumer_kind = 'knowledge_revision'
                  AND consumer_revision_id = ?
                ORDER BY freshness
                """,
                (revision["revision_id"],),
            )
        )
        revision["freshness"] = (
            "invalidated"
            if "invalidated" in dependency_states
            else "stale"
            if "stale" in dependency_states
            else "unknown"
            if not dependency_states or "unknown" in dependency_states
            else "fresh"
        )
        applicability = store.connection.execute(
            """
            SELECT source_compilation_staged_objects_v1.prepared_json
            FROM source_compilation_staged_objects_v1
            WHERE prepared_revision_id = ?
            LIMIT 1
            """,
            (revision["revision_id"],),
        ).fetchone()
        revision["applicability"] = None
        if applicability is not None:
            prepared = strict_json_loads(applicability["prepared_json"])
            if isinstance(prepared, dict):
                revision["applicability"] = prepared.get("applicability")
        revision["synthesis_inputs"] = None
        if revision["kind"] == "synthesis":
            input_set = store.connection.execute(
                """
                SELECT * FROM synthesis_input_sets_v1
                WHERE synthesis_revision_id = ?
                """,
                (revision["revision_id"],),
            ).fetchone()
            if input_set is not None:
                revision["synthesis_inputs"] = {
                    "source_revision_ids": strict_json_loads(
                        input_set["source_revision_ids_json"]
                    ),
                    "knowledge_revision_ids": strict_json_loads(
                        input_set["knowledge_revision_ids_json"]
                    ),
                    "relation_revision_ids": strict_json_loads(
                        input_set["relation_revision_ids_json"]
                    ),
                    "compilation_run_ids": strict_json_loads(
                        input_set["compilation_run_ids_json"]
                    ),
                    "input_set_sha256": input_set["input_set_sha256"],
                }
        generation = revision.get("generation")
        revision["semantic_status"] = "not_recorded"
        compilation_run_id: str | None = None
        if isinstance(generation, dict):
            activity_id = generation.get("activity_id")
            run_id = generation.get("run_id")
            if isinstance(activity_id, str) and activity_id.startswith("compilationrun_"):
                compilation_run_id = activity_id
            elif isinstance(run_id, str) and run_id.startswith("compilationrun_"):
                compilation_run_id = run_id
        if compilation_run_id is not None:
            semantic = store.connection.execute(
                """
                SELECT semantic_status
                FROM semantic_compilation_runs_v2
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()
            if semantic is not None and isinstance(semantic["semantic_status"], str):
                revision["semantic_status"] = semantic["semantic_status"]
        rows.append(revision)
    return rows


def _current_relations(
    store: AutonomousKnowledgeStore,
    *,
    admitted_ids: set[str],
    reference_time: str,
) -> list[dict[str, Any]]:
    return [
        relation
        for relation in store._current_relations(reference_time=reference_time)
        if relation["subject_knowledge_id"] in admitted_ids
        and relation["object_knowledge_id"] in admitted_ids
        and store.relation_provenance_admitted(relation)
    ]


def _statement_freshness(
    store: AutonomousKnowledgeStore,
    *,
    knowledge_revision_id: str,
    source_refs: list[dict[str, Any]],
) -> str:
    """Return the worst direct Source dependency freshness for a statement."""

    order = {"fresh": 0, "unknown": 1, "stale": 2, "invalidated": 3}
    freshness = "fresh"
    for reference in source_refs:
        row = store.connection.execute(
            """
            SELECT freshness
            FROM knowledge_dependencies_v1
            WHERE consumer_kind = 'knowledge_revision'
              AND consumer_revision_id = ?
              AND source_revision_id = ?
              AND fragment_id = ?
              AND dependency_kind = 'direct'
            LIMIT 1
            """,
            (
                knowledge_revision_id,
                reference.get("source_revision_id"),
                reference.get("fragment_id"),
            ),
        ).fetchone()
        candidate = row["freshness"] if row is not None else "unknown"
        if candidate not in order:
            candidate = "unknown"
        if order[candidate] > order[freshness]:
            freshness = candidate
    return freshness


def _current_statements(
    store: AutonomousKnowledgeStore,
    *,
    knowledge_revision_id: str,
) -> list[dict[str, Any]]:
    """Read persisted statement/map/receipt identities without changing Knowledge content."""

    rows = store.connection.execute(
        """
        SELECT statements.statement_id, statements.knowledge_revision_id,
               statements.ordinal, statements.statement_text,
               statements.statement_sha256, statements.statement_type,
               statements.support_status, statements.valid_from,
               statements.valid_to, statements.limitation,
               statements.input_set_sha256, statements.statement_json,
               maps.map_sha256, maps.map_json, maps.char_start, maps.char_end,
               receipts.receipt_sha256, receipts.artifact_sha256,
               receipts.compilation_run_id, receipts.transaction_audit_head,
               receipts.commit_audit_head, receipts.recorded_at
        FROM knowledge_statements_v1 AS statements
        LEFT JOIN statement_evidence_maps_v1 AS maps
          ON maps.statement_id = statements.statement_id
        LEFT JOIN statement_evidence_receipts_v1 AS receipts
          ON receipts.statement_id = statements.statement_id
        WHERE statements.knowledge_revision_id = ?
        ORDER BY statements.ordinal, statements.statement_id
        """,
        (knowledge_revision_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        statement = strict_json_loads(row["statement_json"])
        if not isinstance(statement, dict):
            raise RuntimeError("persisted statement artifact is not an object")
        source_refs = statement.get("source_refs", [])
        if not isinstance(source_refs, list):
            raise RuntimeError("persisted statement source references are invalid")
        map_value = strict_json_loads(row["map_json"]) if row["map_json"] else None
        if not isinstance(map_value, dict) or map_value.get("statement_id") != row["statement_id"]:
            raise RuntimeError("persisted statement map is invalid")
        if not isinstance(row["receipt_sha256"], str) or not row["receipt_sha256"]:
            raise RuntimeError("persisted statement receipt is missing")
        result.append(
            {
                "statement_id": row["statement_id"],
                "knowledge_revision_id": row["knowledge_revision_id"],
                "ordinal": row["ordinal"],
                "statement_text": row["statement_text"],
                "statement_sha256": row["statement_sha256"],
                "statement_type": row["statement_type"],
                "support_status": row["support_status"],
                "valid_from": row["valid_from"],
                "valid_to": row["valid_to"],
                "limitation": row["limitation"],
                "input_set_sha256": row["input_set_sha256"],
                "source_refs": source_refs,
                "knowledge_revision_refs": statement.get("knowledge_revision_refs", []),
                "relation_revision_refs": statement.get("relation_revision_refs", []),
                "gaps": statement.get("gaps", []),
                "map_sha256": row["map_sha256"],
                "char_start": row["char_start"],
                "char_end": row["char_end"],
                "receipt_sha256": row["receipt_sha256"],
                "receipt_artifact_sha256": row["artifact_sha256"],
                "compilation_run_id": row["compilation_run_id"],
                "transaction_audit_head": row["transaction_audit_head"],
                "commit_audit_head": row["commit_audit_head"],
                "recorded_at": row["recorded_at"],
                "freshness": _statement_freshness(
                    store,
                    knowledge_revision_id=knowledge_revision_id,
                    source_refs=source_refs,
                ),
            }
        )
    return result


def _statement_evidence_lines(
    statements: list[dict[str, Any]],
    source_fragment_links: dict[tuple[str, str], tuple[str, str]] | None,
) -> list[str]:
    lines: list[str] = []
    links = source_fragment_links or {}
    for statement in statements:
        statement_id = statement["statement_id"]
        anchor = f"statement-{statement_id}"
        lines.extend(
            [
                f'<a id="{anchor}"></a>',
                f"### Statement {statement['ordinal']} · `{statement_id}`",
                "",
                f"- Statement type: `{statement['statement_type']}`",
                f"- Support status: `{statement['support_status']}`",
                f"- Freshness: `{statement['freshness']}`",
                f"- Statement SHA-256: `{statement['statement_sha256']}`",
                f"- Receipt ID: `receipt:{statement['receipt_sha256'] or 'missing'}`",
                f"- Receipt digest: `{statement['receipt_sha256'] or 'missing'}`",
                f"- Statement text (exact persisted text): {statement['statement_text']}",
            ]
        )
        source_refs = statement.get("source_refs", [])
        if source_refs:
            lines.append("- Source Evidence:")
            for reference in source_refs:
                source_revision_id = reference.get("source_revision_id")
                fragment_id = reference.get("fragment_id")
                locator = str(reference.get("locator", "locator omitted")).replace("`", "'")
                quote_sha = reference.get("quote_sha256", "missing")
                target = links.get((source_revision_id, fragment_id))
                if target is not None:
                    page, fragment_anchor = target
                    evidence_link = (
                        f"[[{PurePosixPath(page).with_suffix('').as_posix()}"
                        f"#{fragment_anchor}|Source fragment {fragment_id}]]"
                    )
                else:
                    evidence_link = f"`{fragment_id}`"
                lines.append(
                    f"  - {evidence_link} · source revision `{source_revision_id}` · "
                    f"locator `{locator}` · quote SHA-256 `{quote_sha}`"
                )
        else:
            lines.append("- Source Evidence: explicit gap; no exact Source Fragment reference.")
        limitation = statement.get("limitation")
        lines.append(f"- Limitation: {limitation if limitation else 'none recorded.'}")
        gaps = statement.get("gaps", [])
        if gaps:
            lines.append(
                "- Gaps: "
                + "; ".join(
                    f"{item.get('gap_id', 'gap')}: {item.get('reason', 'unspecified')}"
                    for item in gaps
                )
            )
        else:
            lines.append("- Gaps: none recorded.")
    return lines


def _statement_evidence_page(
    *,
    row: dict[str, Any],
    audit_head: str,
    statements: list[dict[str, Any]],
    shard_index: int,
    shard_count: int,
    source_fragment_links: dict[tuple[str, str], tuple[str, str]] | None,
) -> str:
    lines = _frontmatter(
        schema="deeplaw.living-wiki-statement-evidence-shard/v1",
        audit_head=audit_head,
        fields={
            "knowledge_id": row["knowledge_id"],
            "revision_id": row["revision_id"],
            "shard": str(shard_index),
            "shard_count": str(shard_count),
            "statement_count": str(len(statements)),
            "freshness": row["freshness"],
            "lifecycle": row["lifecycle"],
            "revision": row["revision_id"],
        },
    )
    lines.extend(
        [
            f"# {row['title']} · Statement Evidence {shard_index:04d}",
            "",
            f"- Canonical Knowledge page: {_wiki_link(row['_page_path'], row['title'])}",
            f"- Bounded shard: {shard_index} of {shard_count}",
            "",
            "## Statement Evidence",
            "",
        ]
    )
    lines.extend(_statement_evidence_lines(statements, source_fragment_links))
    return "\n".join(lines)


def _object_page(
    *,
    row: dict[str, Any],
    audit_head: str,
    page_paths: dict[str, str],
    titles: dict[str, str],
    relations: list[dict[str, Any]],
    statements: list[dict[str, Any]] | None = None,
    statement_shards: list[dict[str, Any]] | None = None,
    source_fragment_links: dict[tuple[str, str], tuple[str, str]] | None = None,
) -> str:
    outgoing = [
        relation
        for relation in relations
        if relation["subject_knowledge_id"] == row["knowledge_id"]
    ]
    incoming = [
        relation for relation in relations if relation["object_knowledge_id"] == row["knowledge_id"]
    ]
    contradictions = [
        relation for relation in [*outgoing, *incoming] if relation["predicate"] == "contradicts"
    ]
    lines = _frontmatter(
        schema="deeplaw.living-wiki-page/v2",
        audit_head=audit_head,
        fields={
            "knowledge_id": row["knowledge_id"],
            "revision_id": row["revision_id"],
            "kind": row["kind"],
            "freshness": row["freshness"],
            "verification": row["verification"],
            "lifecycle": row["lifecycle"],
            "semantic_status": row.get("semantic_status", "not_recorded"),
            "revision": row["revision_id"],
        },
    )
    lines.extend(
        [
            f"# {row['title']}",
            "",
            "## Summary",
            "",
            row["body"],
            "",
            "## Epistemic and Authority",
            "",
            f"- Epistemic state: `{row['epistemic_state']}`",
            f"- Verification: `{row['verification']}`",
            "- Origin / Authority: `agent_derived` / `agent_derived`",
            "- Legal Authority: `false`",
            f"- Lifecycle: `{row['lifecycle']}`",
            f"- Freshness: `{row['freshness']}`",
            f"- Scope / sensitivity: `{row['scope']}` / `{row['sensitivity']}`",
            "",
            "## Applicability",
            "",
        ]
    )
    applicability = row["applicability"]
    if isinstance(applicability, dict):
        lines.append(str(applicability.get("description", "Not stated.")))
        for label, field in (
            ("Scopes", "scopes"),
            ("Conditions", "conditions"),
            ("Exclusions", "exclusions"),
        ):
            values = applicability.get(field, [])
            if values:
                lines.append(f"- {label}: " + "; ".join(str(value) for value in values))
    else:
        lines.append(
            "No compiler-specific applicability boundary was recorded; consult the evidence."
        )
    if row["kind"] == "synthesis":
        lines.extend(["", "## Exact Synthesis input set", ""])
        synthesis_inputs = row["synthesis_inputs"]
        if isinstance(synthesis_inputs, dict):
            lines.append(f"- Input-set digest: `{synthesis_inputs['input_set_sha256']}`")
            for label, field in (
                ("Source Revisions", "source_revision_ids"),
                ("Knowledge Revisions", "knowledge_revision_ids"),
                ("Relation Revisions", "relation_revision_ids"),
                ("Compilation Runs", "compilation_run_ids"),
            ):
                values = synthesis_inputs[field]
                lines.append(f"- {label}:")
                if values:
                    lines.extend(f"  - `{value}`" for value in values)
                else:
                    lines.append("  - Explicitly empty.")
        else:
            lines.append("- Explicit gap: no governed Synthesis input set is registered.")
    lines.extend(["", "## Evidence", ""])
    for reference in row["source_refs"]:
        source_revision_id = reference.get("source_revision_id")
        fragment_id = reference.get("fragment_id")
        locator = reference.get("locator", "locator omitted")
        if source_revision_id:
            lines.append(
                f"- [[wiki/sources/{source_revision_id}|{source_revision_id}]]"
                f" · `{fragment_id or 'whole-source'}` · `{locator}`"
            )
    if not row["source_refs"]:
        lines.append("- Explicit gap: this revision has no Source binding.")
    statement_rows = statements or []
    lines.extend(["", "## Statement Evidence", ""])
    if not statement_rows:
        lines.append("- Explicit gap: no persisted Statement Evidence Map is registered.")
    elif statement_shards:
        lines.append(
            f"- {len(statement_rows)} persisted Statements are retained in bounded evidence "
            "shards; this canonical page remains below the Wiki page byte limit."
        )
        for shard in statement_shards:
            shard_label = (
                f"Statements {shard['first_ordinal']}-{shard['last_ordinal']}"
            )
            lines.append(
                f"- {_wiki_link(shard['path'], shard_label)} "
                f"({shard['count']} Statements)"
            )
    else:
        lines.extend(_statement_evidence_lines(statement_rows, source_fragment_links))
    lines.extend(["", "## Relations", ""])
    for relation in sorted(
        outgoing,
        key=lambda item: (
            item["predicate"],
            titles.get(item["object_knowledge_id"], ""),
        ),
    ):
        target = relation["object_knowledge_id"]
        lines.append(
            f"- `{relation['predicate']}` → "
            f"{_wiki_link(page_paths[target], titles[target])} "
            f"(`{target}`, `{relation['relation_revision_id']}`)"
        )
    for relation in sorted(
        incoming,
        key=lambda item: (
            item["predicate"],
            titles.get(item["subject_knowledge_id"], ""),
        ),
    ):
        source = relation["subject_knowledge_id"]
        lines.append(
            f"- ← `{relation['predicate']}` "
            f"{_wiki_link(page_paths[source], titles[source])} "
            f"(`{source}`, `{relation['relation_revision_id']}`)"
        )
    if not outgoing and not incoming:
        lines.append("- No admitted canonical relation.")
    lines.extend(["", "## Contradictions", ""])
    if contradictions:
        for relation in contradictions:
            peer = (
                relation["object_knowledge_id"]
                if relation["subject_knowledge_id"] == row["knowledge_id"]
                else relation["subject_knowledge_id"]
            )
            lines.append(
                f"- {_wiki_link(page_paths[peer], titles[peer])} "
                f"(`{relation['relation_revision_id']}`)"
            )
    else:
        lines.append("- No admitted contradiction relation.")
    lines.extend(
        [
            "",
            "## Revision lineage",
            "",
            f"- Current revision: `{row['revision_id']}`",
            f"- Parent revision: `{row['parent_revision_id'] or 'none'}`",
            f"- Recorded at: `{row['recorded_at']}`",
            f"- Canonical Markdown: {_wiki_link(row['workspace_path'], row['title'])}",
            "",
            "## Limits",
            "",
            "- This page is a rebuildable projection, not evidence or Authority.",
            "- Source applicability and unresolved gaps require evidence-first verification.",
        ]
    )
    return "\n".join(lines)


def _source_pages(
    store: AutonomousKnowledgeStore,
    *,
    output_root: Path,
    audit_head: str,
    generated: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    page_paths: dict[str, str],
    run_status_overrides: dict[str, str],
) -> list[dict[str, Any]]:
    sources = store.connection.execute(
        """
        SELECT source_revisions_v2.source_revision_id,
               source_revisions_v2.source_key,
               source_revisions_v2.content_sha256,
               source_revisions_v2.media_identity,
               source_revisions_v2.byte_size,
               sources.title,
               sources.kind,
               sources.media_type,
               sources.warnings_json,
               sources.compiler_json,
               source_lifecycle.status,
               sources.trust,
               sources.sensitivity
        FROM source_revisions_v2
        LEFT JOIN source_revision_bindings_v2 USING(source_revision_id)
        LEFT JOIN sources
          ON sources.source_id = source_revision_bindings_v2.legacy_source_id
        LEFT JOIN source_lifecycle USING(source_id)
        ORDER BY source_revisions_v2.source_revision_id
        """
    ).fetchall()
    result: list[dict[str, Any]] = []
    for source in sources:
        binding = store._source_reference_binding(
            {"source_revision_id": source["source_revision_id"]}
        )
        if binding is None or binding["active"] is not True:
            continue
        dependencies = store.connection.execute(
            """
            SELECT freshness, COUNT(*) AS count
            FROM knowledge_dependencies_v1
            WHERE source_revision_id = ?
            GROUP BY freshness ORDER BY freshness
            """,
            (source["source_revision_id"],),
        ).fetchall()
        stored_runs = store.connection.execute(
            """
            SELECT runs.compilation_run_id, runs.status, runs.compiler_profile,
                   runs.compiler_profile_version, runs.created_at, runs.completed_at,
                   semantic.semantic_status, semantic.observation_packet_count,
                   semantic.observed_packet_count, semantic.observation_count,
                   semantic.inventory_sha256, semantic.publication_plan_sha256,
                   semantic.quality_receipt_sha256,
                   semantic.source_summary_revision_id
            FROM source_compilation_runs_v1 AS runs
            LEFT JOIN semantic_compilation_runs_v2 AS semantic
              USING(compilation_run_id)
            WHERE runs.source_revision_id = ?
            ORDER BY runs.created_at DESC
            """,
            (source["source_revision_id"],),
        ).fetchall()
        runs = [
            {
                **dict(run),
                "status": run_status_overrides.get(
                    run["compilation_run_id"],
                    run["status"],
                ),
                "duty_reports": [
                    strict_json_loads(item["report_json"])
                    for item in store.connection.execute(
                        """
                        SELECT report_json FROM semantic_duty_reports_v1
                        WHERE compilation_run_id = ? ORDER BY duty_type
                        """,
                        (run["compilation_run_id"],),
                    )
                ],
            }
            for run in stored_runs
        ]
        compilation = store.connection.execute(
            """
            SELECT compilations_v2.compilation_id, compilations_v2.adapter,
                   compilations_v2.adapter_version,
                   compilations_v2.configuration_sha256,
                   compilations_v2.fragment_inventory_sha256,
                   (
                       SELECT COUNT(*) FROM source_ir_nodes_v2
                       WHERE source_ir_nodes_v2.compilation_id =
                             compilations_v2.compilation_id
                   ) AS node_count,
                   (
                       SELECT COUNT(*) FROM fragments_v2
                       WHERE fragments_v2.compilation_id =
                             compilations_v2.compilation_id
                   ) AS fragment_count,
                   (
                       SELECT COUNT(*) FROM fragments_v2
                       WHERE fragments_v2.compilation_id =
                             compilations_v2.compilation_id
                         AND fragments_v2.instruction_risk = 1
                   ) AS risky_fragment_count
            FROM compilations_v2
            WHERE compilations_v2.source_revision_id = ?
            ORDER BY compilations_v2.compilation_id
            LIMIT 1
            """,
            (source["source_revision_id"],),
        ).fetchone()
        fragments = store.connection.execute(
            """
            SELECT legacy_fragment_bindings_v2.fragment_id,
                   fragments_v2.fragment_revision_id,
                   fragments_v2.ordinal,
                   fragments_v2.locator,
                   fragments_v2.text_sha256
            FROM compilations_v2
            JOIN fragments_v2 USING(compilation_id)
            JOIN legacy_fragment_bindings_v2 USING(fragment_revision_id)
            WHERE compilations_v2.source_revision_id = ?
            ORDER BY fragments_v2.ordinal, fragments_v2.fragment_revision_id
            """,
            (source["source_revision_id"],),
        ).fetchall()
        fragment_shards: list[tuple[str, int, int]] = []
        fragment_anchor_pages: dict[str, list[dict[str, Any]]] = {}
        for start in range(0, len(fragments), SOURCE_FRAGMENT_SHARD_SIZE):
            shard = fragments[start : start + SOURCE_FRAGMENT_SHARD_SIZE]
            shard_ordinal = start // SOURCE_FRAGMENT_SHARD_SIZE + 1
            relative = (
                f"wiki/indexes/source-{source['source_revision_id']}-"
                f"fragments-{shard_ordinal:04d}.md"
            )
            shard_lines = _frontmatter(
                schema="deeplaw.living-wiki-source-fragment-index/v1",
                audit_head=audit_head,
                fields={"source_revision_id": source["source_revision_id"]},
            )
            shard_lines.extend(
                [
                    f"# Source fragments {start + 1}-{start + len(shard)}",
                    "",
                    (
                        "Exact read-only evidence identities. Retrieve content through "
                        "`knowledge_support`; this derived index is not evidence."
                    ),
                    "",
                ]
            )
            for fragment in shard:
                locator = str(fragment["locator"]).replace("`", "'")
                shard_lines.append(f'<a id="{_fragment_anchor(fragment["fragment_id"])}"></a>')
                shard_lines.append(
                    f"- {fragment['ordinal']}. `{fragment['fragment_id']}` · "
                    f"revision `{fragment['fragment_revision_id']}` · "
                    f"locator `{locator}` · quote SHA-256 "
                    f"`{fragment['text_sha256']}`"
                )
            fragment_anchor_pages[relative] = [
                anchor
                for fragment in shard
                for anchor in (
                    {
                        "anchor_id": f"fragment-{fragment['fragment_id']}",
                        "anchor": _fragment_anchor(fragment["fragment_id"]),
                        "kind": "source_fragment",
                        "source_fragment": {
                            "source_revision_id": source["source_revision_id"],
                            "fragment_id": fragment["fragment_id"],
                        },
                    },
                    {
                        "anchor_id": f"fragment-revision-{fragment['fragment_revision_id']}",
                        "anchor": _fragment_anchor(fragment["fragment_id"]),
                        "kind": "source_fragment",
                        "source_fragment": {
                            "source_revision_id": source["source_revision_id"],
                            "fragment_revision_id": fragment["fragment_revision_id"],
                        },
                    },
                )
            ]
            _write(
                output_root,
                relative=relative,
                content="\n".join(shard_lines),
                generated=generated,
            )
            fragment_shards.append((relative, start + 1, start + len(shard)))
        quality_flags = sorted(
            {
                flag
                for item in store.connection.execute(
                    """
                    SELECT quality_flags_json FROM source_ir_nodes_v2
                    WHERE source_revision_id = ?
                    """,
                    (source["source_revision_id"],),
                )
                for flag in strict_json_loads(item["quality_flags_json"])
                if isinstance(flag, str)
            }
        )
        related = [
            row
            for row in rows
            if any(
                reference.get("source_revision_id") == source["source_revision_id"]
                for reference in row["source_refs"]
            )
        ]
        related_by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in related:
            related_by_kind[row["kind"]].append(row)
        source_summary = next(
            (
                row
                for row in related_by_kind.get("synthesis", [])
                if row.get("semantic_key")
                == f"source-summary:{source['source_revision_id']}"
            ),
            None,
        )
        warnings = (
            strict_json_loads(source["warnings_json"]) if source["warnings_json"] else []
        )
        compiler = (
            strict_json_loads(source["compiler_json"]) if source["compiler_json"] else {}
        )
        freshness_order = {"fresh": 0, "unknown": 1, "stale": 2, "invalidated": 3}
        source_freshness = "unknown" if not dependencies else "fresh"
        for dependency in dependencies:
            candidate = dependency["freshness"]
            if candidate not in freshness_order:
                candidate = "unknown"
            if freshness_order[candidate] > freshness_order[source_freshness]:
                source_freshness = candidate
        title = source["title"] or source["source_revision_id"]
        lines = _frontmatter(
            schema="deeplaw.living-wiki-source/v1",
            audit_head=audit_head,
            fields={
                "source_revision_id": source["source_revision_id"],
                "revision": source["source_revision_id"],
                "lifecycle": source["status"] or "active",
                "freshness": source_freshness,
                "semantic_status": (
                    runs[0]["semantic_status"]
                    if runs and runs[0]["semantic_status"]
                    else "not_recorded"
                ),
            },
        )
        lines.extend(
            [
                f"# {title}",
                "",
                "## SOURCE EVIDENCE",
                "",
                "The following fields are immutable Source Revision identity and evidence "
                "coordinates. They are not an Agent-generated summary.",
                f"- Source Revision ID: `{source['source_revision_id']}`",
                f"- Canonical source identity: `{source['source_key']}`",
                f"- Original content SHA-256: `{source['content_sha256']}`",
                f"- Media identity: `{source['media_identity']}`",
                f"- Source format: `{source['kind'] or 'unknown'}` / "
                f"`{source['media_type'] or 'unknown'}`",
                f"- Bytes: `{source['byte_size']}`",
                f"- Lifecycle: `{source['status'] or 'unbound'}`",
                f"- Trust / sensitivity: `{source['trust'] or 'unknown'}` / "
                f"`{source['sensitivity'] or 'unknown'}`",
                "",
                "## Extraction quality",
                "",
                f"- Source IR compilation: "
                f"`{compilation['compilation_id'] if compilation else 'unavailable'}`",
                f"- Adapter: "
                f"`{compilation['adapter'] if compilation else 'unknown'}`"
                f"@`{compilation['adapter_version'] if compilation else 'unknown'}`",
                f"- Source IR nodes / fragments: "
                f"{compilation['node_count'] if compilation else 0} / "
                f"{compilation['fragment_count'] if compilation else 0}",
                f"- Instruction-risk fragments: "
                f"{compilation['risky_fragment_count'] if compilation else 0}",
                f"- Quality flags: "
                f"{', '.join(f'`{item}`' for item in quality_flags) or 'none'}",
                f"- Extractor: `{compiler.get('extractor', 'unknown')}`"
                f"@`{compiler.get('extractor_version', 'unknown')}`",
                "",
                "## Exact evidence drill-down",
                "",
                f"- Registered fragments: `{len(fragments)}`",
                *(
                    [
                        "- "
                        + _wiki_link(
                            relative,
                            f"Fragments {first}-{last}",
                        )
                        for relative, first, last in fragment_shards
                    ]
                    or ["- Explicit gap: no registered Source IR fragment."]
                ),
                "",
                "## Extraction warnings",
                "",
                *([f"- {item}" for item in warnings] or ["- No recorded extraction warning."]),
                "",
                "## Dependent knowledge freshness",
                "",
            ]
        )
        if dependencies:
            lines.extend(f"- `{item['freshness']}`: {item['count']}" for item in dependencies)
        else:
            lines.append("- Explicit gap: no compiled Knowledge dependency.")
        lines.extend(
            [
                "",
                "## AGENT-DERIVED SOURCE SUMMARY",
                "",
                "- origin=agent_derived",
                "- authority=none",
                "- legal_authority=false",
                "- This section is a governed derived view and never replaces SOURCE EVIDENCE.",
                "",
            ]
        )
        if source_summary is None:
            lines.append(
                "- Explicit gap: no canonical synthesis with semantic key "
                f"`source-summary:{source['source_revision_id']}`."
            )
        else:
            lines.extend(
                [
                    "- "
                    + _wiki_link(
                        page_paths[source_summary["knowledge_id"]],
                        source_summary["title"],
                    ),
                    "",
                    source_summary["body"],
                ]
            )
        lines.extend(["", "## Compiled knowledge", ""])
        remaining_inline_items = INDEX_SHARD_SIZE
        for kind in _KIND_DIRECTORIES:
            members = related_by_kind.get(kind, [])
            if not members:
                continue
            visible_members = members[:remaining_inline_items]
            lines.append(f"### {kind.title()}")
            lines.append("")
            lines.extend(
                f"- {_wiki_link(page_paths[item['knowledge_id']], item['title'])} "
                f"(`{item['knowledge_id']}`, `{item['freshness']}`)"
                for item in visible_members
            )
            remaining_inline_items -= len(visible_members)
            omitted_count = len(members) - len(visible_members)
            if omitted_count:
                lines.append(
                    f"- Explicit bounded projection: {omitted_count} additional "
                    f"source-bound {kind} revisions are not inlined; use bounded "
                    "query/context and exact Source Revision drill-down."
                )
            lines.append("")
        if not related:
            lines.append("- Explicit gap: no admitted source-bound Knowledge Revision.")
        lines.extend(["", "## Compilation runs", ""])
        if runs:
            for item in runs:
                lines.append(
                    f"- Run `{item['compilation_run_id']}` · "
                    f"`{item['compiler_profile']}@{item['compiler_profile_version']}`"
                )
                lines.append(
                    f"  - Compatibility: `{item['compilation_run_id']}` · "
                    f"`{item['status']}` · "
                    f"`{item['compiler_profile']}@{item['compiler_profile_version']}` · "
                    f"transaction `{item['status']}` · semantic "
                    f"`{item['semantic_status'] or 'not_recorded'}`"
                )
                lines.extend(
                    [
                        f"  - Run transaction status: `{item['status']}`",
                        f"  - Semantic status: `{item['semantic_status'] or 'not_recorded'}`",
                    ]
                )
                lines.extend(
                    [
                        f"  - Observed packets: `{item['observed_packet_count'] or 0}` / "
                        f"`{item['observation_packet_count'] or 0}`; observations: "
                        f"`{item['observation_count'] or 0}`",
                        f"  - Inventory: `{item['inventory_sha256'] or 'missing'}`",
                        f"  - Publication plan: "
                        f"`{item['publication_plan_sha256'] or 'missing'}`",
                        f"  - Quality receipt: "
                        f"`{item['quality_receipt_sha256'] or 'missing'}`",
                    ]
                )
                if item["duty_reports"]:
                    lines.append("  - Semantic duties:")
                    for report in item["duty_reports"]:
                        if not isinstance(report, dict):
                            lines.append("    - Explicit gap: malformed Duty Report.")
                            continue
                        applicability = report.get("applicability")
                        if not applicability and item["compiler_profile_version"] == "2":
                            applicability = "not_recorded_in_v2"
                        if not applicability:
                            applicability = "unknown"
                        lines.append(
                            f"    - Compatibility: `{report.get('duty_type', 'unknown')}`: "
                            f"`{report.get('status', 'unresolved')}`"
                            f"{' (required)' if report.get('required') else ''}"
                        )
                        lines.append(
                            f"    - `{report.get('duty_type', 'unknown')}`: "
                            f"applicability=`{applicability}` · "
                            f"status=`{report.get('status', 'unresolved')}`"
                            f"{' (required)' if report.get('required') else ''}"
                        )
                else:
                    lines.append("  - Explicit gap: no semantic Duty Report is registered.")
        else:
            lines.append("- Explicit gap: Source Revision has not been compiled.")
        _write(
            output_root,
            relative=f"wiki/sources/{source['source_revision_id']}.md",
            content="\n".join(lines),
            generated=generated,
        )
        source_result = dict(source)
        source_result["_fragment_anchor_pages"] = fragment_anchor_pages
        result.append(source_result)
    return result


def _community_views(
    *,
    store: AutonomousKnowledgeStore,
    output_root: Path,
    rows: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    page_paths: dict[str, str],
    titles: dict[str, str],
    audit_head: str,
    generated: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    communities = detect_communities(
        (row["knowledge_id"] for row in rows),
        relations,
        {row["knowledge_id"]: row.get("semantic_key") for row in rows},
    )
    rows_by_id = {row["knowledge_id"]: row for row in rows}
    members_by_community: dict[str, list[dict[str, Any]]] = {}
    for community in communities:
        community_id = community["community_id"]
        members = [
            rows_by_id[item]
            for item in community["knowledge_ids"]
            if item in rows_by_id
        ]
        members_by_community[community_id] = members
        member_ids = {row["knowledge_id"] for row in members}
        local_relations = [
            relation
            for relation in relations
            if relation["subject_knowledge_id"] in member_ids
            and relation["object_knowledge_id"] in member_ids
        ]
        bridge_nodes = sorted(
            {
                endpoint
                for relation in relations
                for endpoint in (
                    relation["subject_knowledge_id"],
                    relation["object_knowledge_id"],
                )
                if (
                    (
                        relation["subject_knowledge_id"] in member_ids
                        and relation["object_knowledge_id"] not in member_ids
                    )
                    or (
                        relation["object_knowledge_id"] in member_ids
                        and relation["subject_knowledge_id"] not in member_ids
                    )
                )
                and endpoint in member_ids
            }
        )
        shard_links: list[str] = []
        for shard_index, start in enumerate(
            range(0, len(members), INDEX_SHARD_SIZE),
            start=1,
        ):
            shard = members[start : start + INDEX_SHARD_SIZE]
            shard_path = f"wiki/communities/{community_id}-{shard_index:04d}.md"
            lines = _frontmatter(
                schema="deeplaw.living-wiki-community-shard/v1",
                audit_head=audit_head,
                fields={
                    "community_id": community_id,
                    "shard": str(shard_index),
                    "item_count": str(len(shard)),
                },
            )
            lines.extend([f"# Community members · {shard_index:04d}", ""])
            lines.extend(
                f"- {_wiki_link(page_paths[row['knowledge_id']], row['title'])} "
                f"(`{row['knowledge_id']}`)"
                for row in shard
            )
            _write(
                output_root,
                relative=shard_path,
                content="\n".join(lines),
                generated=generated,
            )
            shard_links.append(
                f"- {_wiki_link(shard_path, f'Members {shard_index:04d}')} "
                f"({len(shard)} objects)"
            )
        community_synthesis = next(
            (
                row
                for row in members
                if row["kind"] == "synthesis"
                and row.get("semantic_key") == f"community-summary:{community_id}"
            ),
            None,
        )
        lines = _frontmatter(
            schema="deeplaw.living-wiki-community/v2",
            audit_head=audit_head,
            fields={
                "community_id": community_id,
                "item_count": str(len(members)),
            },
        )
        lines.extend(
            [
                f"# Community {community_id}",
                "",
                f"- Algorithm: `{community['algorithm']}`",
                f"- Members: {len(members)}",
                "",
                "## Member shards",
                "",
                *shard_links,
                "",
                "## Key relations",
                "",
            ]
        )
        if local_relations:
            for item in local_relations[:200]:
                subject_id = item["subject_knowledge_id"]
                object_id = item["object_knowledge_id"]
                subject_link = _wiki_link(page_paths[subject_id], titles[subject_id])
                object_link = _wiki_link(page_paths[object_id], titles[object_id])
                lines.append(
                    f"- {subject_link} — `{item['predicate']}` → {object_link}"
                )
            if len(local_relations) > 200:
                lines.append(
                    f"- Pagination boundary: {len(local_relations) - 200} additional relations "
                    "remain available through the Graph interface."
                )
        else:
            lines.append("- No admitted internal relation.")
        lines.extend(["", "## Bridge nodes", ""])
        if bridge_nodes:
            lines.extend(
                f"- {_wiki_link(page_paths[item], titles[item])} (`{item}`)"
                for item in bridge_nodes
            )
        else:
            lines.append("- No deterministic bridge node.")
        lines.extend(["", "## Canonical community synthesis", ""])
        if community_synthesis is None:
            lines.append(
                "- Explicit gap: no canonical synthesis with semantic key "
                f"`community-summary:{community_id}`."
            )
        else:
            lines.extend(
                [
                    "- "
                    + _wiki_link(
                        page_paths[community_synthesis["knowledge_id"]],
                        community_synthesis["title"],
                    ),
                    "",
                    community_synthesis["body"],
                ]
            )
        _write(
            output_root,
            relative=f"wiki/communities/{community_id}.md",
            content="\n".join(lines),
            generated=generated,
        )
    return communities, members_by_community


def _canvas(
    *,
    rows: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    page_paths: dict[str, str],
    name: str,
) -> tuple[str, dict[str, Any]]:
    selected = rows[:CANVAS_NODE_LIMIT]
    selected_ids = {row["knowledge_id"] for row in selected}
    node_ids = {
        row["knowledge_id"]: stable_id("canvasnode", name, row["knowledge_id"]) for row in selected
    }
    nodes = [
        {
            "id": node_ids[row["knowledge_id"]],
            "type": "file",
            "file": page_paths[row["knowledge_id"]],
            "x": (index % 5) * 420,
            "y": (index // 5) * 260,
            "width": 360,
            "height": 200,
        }
        for index, row in enumerate(selected)
    ]
    edges = [
        {
            "id": relation["relation_revision_id"],
            "fromNode": node_ids[relation["subject_knowledge_id"]],
            "toNode": node_ids[relation["object_knowledge_id"]],
            "label": relation["predicate"],
        }
        for relation in relations
        if relation["subject_knowledge_id"] in selected_ids
        and relation["object_knowledge_id"] in selected_ids
    ][:CANVAS_EDGE_LIMIT]
    payload = json.dumps(
        {"nodes": nodes, "edges": edges},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return payload, {
        "name": name,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "truncated_nodes": max(0, len(rows) - len(nodes)),
        "truncated_edges": max(
            0,
            len(
                [
                    relation
                    for relation in relations
                    if relation["subject_knowledge_id"] in selected_ids
                    and relation["object_knowledge_id"] in selected_ids
                ]
            )
            - len(edges),
        ),
    }


def _recent_change_pages(
    store: AutonomousKnowledgeStore,
    *,
    output_root: Path,
    rows: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    page_paths: dict[str, str],
    audit_head: str,
    generated: list[dict[str, Any]],
) -> None:
    current_by_revision = {row["revision_id"]: row for row in rows}
    active_source_ids = {source["source_revision_id"] for source in sources}
    active_legacy_sources = {
        row["legacy_source_id"]: row["source_revision_id"]
        for row in store.connection.execute(
            """
            SELECT legacy_source_id, source_revision_id
            FROM source_revision_bindings_v2
            ORDER BY legacy_source_id
            """
        )
        if row["source_revision_id"] in active_source_ids
    }
    run_sources = {
        row["compilation_run_id"]: row["source_revision_id"]
        for row in store.connection.execute(
            """
            SELECT compilation_run_id, source_revision_id
            FROM source_compilation_runs_v1
            ORDER BY compilation_run_id
            """
        )
    }
    events = [
        {
            "ledger": "knowledge",
            "sequence": row["sequence"],
            "event_type": row["event_type"],
            "object_id": row["object_id"],
            "payload_json": row["payload_json"],
            "recorded_at": row["recorded_at"],
        }
        for row in store.connection.execute(
            """
            SELECT sequence, event_type, object_id, payload_json, recorded_at
            FROM autonomous_events_v3
            ORDER BY sequence DESC
            LIMIT 10001
            """
        )
    ]
    events.extend(
        {
            "ledger": "evidence",
            "sequence": row["sequence"],
            "event_type": row["event_type"],
            "object_id": row["object_id"],
            "payload_json": row["payload_json"],
            "recorded_at": row["created_at"],
        }
        for row in store.connection.execute(
            """
            SELECT sequence, event_type, object_id, payload_json, created_at
            FROM events
            ORDER BY sequence DESC
            LIMIT 10001
            """
        )
    )
    events.sort(
        key=lambda item: (
            item["recorded_at"],
            item["ledger"],
            item["sequence"],
        ),
        reverse=True,
    )
    history_truncated = len(events) > 10_000
    events = events[:10_000]

    def target(event: dict[str, Any]) -> str:
        event_type = event["event_type"]
        object_id = event["object_id"]
        if event_type == "knowledge_revision_committed":
            row = current_by_revision.get(object_id)
            if row is not None:
                return _wiki_link(
                    page_paths[row["knowledge_id"]],
                    row["title"],
                )
        source_revision_id: str | None = None
        if event["ledger"] == "evidence" and object_id is not None:
            source_revision_id = active_legacy_sources.get(object_id)
        elif event_type == "source_compilation_committed":
            source_revision_id = run_sources.get(object_id)
        elif event_type == "source_freshness_changed":
            payload = strict_json_loads(event["payload_json"])
            if isinstance(payload, dict):
                candidate = payload.get("replacement_source_revision_id")
                if isinstance(candidate, str):
                    source_revision_id = candidate
        if source_revision_id in active_source_ids:
            return _wiki_link(
                f"wiki/sources/{source_revision_id}.md",
                source_revision_id,
            )
        return "target unavailable or no longer admitted"

    shard_links: list[str] = []
    for shard_number, start in enumerate(range(0, len(events), 200), start=1):
        shard = events[start : start + 200]
        path = f"wiki/recent-changes/{shard_number:04d}.md"
        lines = _frontmatter(
            schema="deeplaw.living-wiki-recent-changes/v1",
            audit_head=audit_head,
            fields={
                "shard": str(shard_number),
                "event_count": str(len(shard)),
            },
        )
        lines.extend([f"# Recent changes · {shard_number:04d}", ""])
        lines.extend(
            f"- `{event['recorded_at']}` · `{event['event_type']}` · "
            f"{target(event)} · `{event['ledger']}:{event['sequence']}`"
            for event in shard
        )
        _write(
            output_root,
            relative=path,
            content="\n".join(lines),
            generated=generated,
        )
        shard_links.append(
            f"- {_wiki_link(path, f'Recent changes {shard_number:04d}')} "
            f"({len(shard)} events)"
        )
    index_lines = _frontmatter(
        schema="deeplaw.living-wiki-recent-changes-index/v1",
        audit_head=audit_head,
        fields={
            "event_count": str(len(events)),
            "history_truncated": str(history_truncated).lower(),
        },
    )
    index_lines.extend(["# Recent changes", "", *shard_links])
    if not events:
        index_lines.append("- Explicit gap: no Ledger event has been recorded.")
    if history_truncated:
        index_lines.extend(
            [
                "",
                "- Explicit pagination boundary: history before the oldest listed "
                "Ledger cursor is available through the Ledger/API, not silently omitted.",
            ]
        )
    _write(
        output_root,
        relative="wiki/recent-changes/index.md",
        content="\n".join(index_lines),
        generated=generated,
    )


def _navigation_page(
    *,
    output_root: Path,
    relative: str,
    title: str,
    audit_head: str,
    generated: list[dict[str, Any]],
    links: list[str],
    gap: str,
) -> None:
    lines = _frontmatter(
        schema="deeplaw.living-wiki-navigation/v1",
        audit_head=audit_head,
        fields={
            "semantic_status": "projection",
            "revision": "not_applicable",
        },
    )
    visible_links = links[:INDEX_SHARD_SIZE]
    lines.extend([f"# {title}", "", *visible_links])
    omitted_count = len(links) - len(visible_links)
    if omitted_count:
        lines.append(
            f"- Explicit bounded projection: {omitted_count} additional navigation "
            "entries are not inlined; use bounded query/context for discovery."
        )
    if not links:
        lines.append(f"- Explicit gap: {gap}")
    _write(
        output_root,
        relative=relative,
        content="\n".join(lines),
        generated=generated,
    )


def _generate_living_wiki(
    store: AutonomousKnowledgeStore,
    *,
    output_root: Path,
    input_audit_head: str | None = None,
    run_status_overrides: dict[str, str] | None = None,
    projection_profile: str = "standard",
    reference_time: str | None = None,
    lint: dict[str, Any] | None = None,
    gaps: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one profile-selected, sharded, deterministic Living Wiki projection."""

    store._require_write()
    profile = resolve_projection_profile(projection_profile)
    _validate_contract("projection-profile.v1.schema.json", profile)
    audit_head = input_audit_head or store.audit_head
    if audit_head != store.audit_head:
        raise RuntimeError("Living Wiki projection audit input changed")
    audit_event = store.connection.execute(
        "SELECT recorded_at FROM autonomous_events_v3 WHERE event_hash = ?",
        (audit_head,),
    ).fetchone()
    if audit_event is None:
        raise RuntimeError("Living Wiki projection audit input is not registered")
    selected_reference_time = reference_time or audit_event["recorded_at"]
    if selected_reference_time != audit_event["recorded_at"]:
        raise RuntimeError("Living Wiki projection reference time is not deterministic")
    effective_run_statuses = dict(run_status_overrides or {})
    known_run_statuses = {
        "planned",
        "staging",
        "validating",
        "ready_to_commit",
        "committed",
        "projection_pending",
        "succeeded",
        "failed",
        "aborted",
    }
    if any(
        not isinstance(run_id, str)
        or not run_id
        or status not in known_run_statuses
        for run_id, status in effective_run_statuses.items()
    ):
        raise ValueError("Living Wiki Run status override is invalid")
    stored_run_states = store.connection.execute(
        """
        SELECT compilation_run_id, source_revision_id, status,
               compiler_profile, compiler_profile_version
        FROM source_compilation_runs_v1
        ORDER BY compilation_run_id
        """
    ).fetchall()
    known_run_ids = {run["compilation_run_id"] for run in stored_run_states}
    if not set(effective_run_statuses).issubset(known_run_ids):
        raise ValueError("Living Wiki Run status override targets an unknown Run")
    compilation_state = [
        {
            "compilation_run_id": run["compilation_run_id"],
            "source_revision_id": run["source_revision_id"],
            "status": effective_run_statuses.get(
                run["compilation_run_id"],
                run["status"],
            ),
            "compiler_profile": run["compiler_profile"],
            "compiler_profile_version": run["compiler_profile_version"],
        }
        for run in stored_run_states
    ]
    rows = _current_rows(store, reference_time=selected_reference_time)
    admitted_ids = {row["knowledge_id"] for row in rows}
    relations = _current_relations(
        store,
        admitted_ids=admitted_ids,
        reference_time=selected_reference_time,
    )
    generated: list[dict[str, Any]] = []
    generated_object_pages = profile["name"] == "full"
    page_paths = {
        row["knowledge_id"]: (
            f"wiki/{_KIND_DIRECTORIES[row['kind']]}/{row['knowledge_id']}.md"
            if generated_object_pages
            else row["workspace_path"]
        )
        for row in rows
    }
    titles = {row["knowledge_id"]: row["title"] for row in rows}
    sources: list[dict[str, Any]] = []
    source_index = "wiki/sources/index.md"
    if profile["source_pages"]:
        # Source pages are generated before Knowledge pages so exact fragment anchors can be
        # wired into Statement Evidence drill-down links without rereading source bytes.
        sources = _source_pages(
            store,
            output_root=output_root,
            audit_head=audit_head,
            generated=generated,
            rows=rows,
            page_paths=page_paths,
            run_status_overrides=effective_run_statuses,
        )
    source_fragment_links: dict[tuple[str, str], tuple[str, str]] = {}
    for source in sources:
        for path, anchors in source.get("_fragment_anchor_pages", {}).items():
            for anchor in anchors:
                fragment = anchor.get("source_fragment", {})
                source_revision_id = fragment.get("source_revision_id")
                fragment_id = fragment.get("fragment_id", fragment.get("fragment_revision_id"))
                if isinstance(source_revision_id, str) and isinstance(fragment_id, str):
                    source_fragment_links[(source_revision_id, fragment_id)] = (
                        path,
                        anchor["anchor"],
                    )
    statement_shard_by_path: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for row in rows:
        statements = _current_statements(store, knowledge_revision_id=row["revision_id"])
        row["_statements"] = statements
        row["_page_path"] = page_paths[row["knowledge_id"]]
        statement_shards: list[dict[str, Any]] = []
        if len(statements) > STATEMENT_EVIDENCE_SHARD_SIZE:
            shard_count = (
                len(statements) + STATEMENT_EVIDENCE_SHARD_SIZE - 1
            ) // STATEMENT_EVIDENCE_SHARD_SIZE
            for shard_index, start in enumerate(
                range(0, len(statements), STATEMENT_EVIDENCE_SHARD_SIZE), start=1
            ):
                shard_statements = statements[
                    start : start + STATEMENT_EVIDENCE_SHARD_SIZE
                ]
                shard_path = (
                    f"wiki/statements/{row['knowledge_id']}-{shard_index:04d}.md"
                )
                shard = {
                    "path": shard_path,
                    "index": shard_index,
                    "count": len(shard_statements),
                    "first_ordinal": shard_statements[0]["ordinal"],
                    "last_ordinal": shard_statements[-1]["ordinal"],
                    "statements": shard_statements,
                }
                _write(
                    output_root,
                    relative=shard_path,
                    content=_statement_evidence_page(
                        row=row,
                        audit_head=audit_head,
                        statements=shard_statements,
                        shard_index=shard_index,
                        shard_count=shard_count,
                        source_fragment_links=source_fragment_links,
                    ),
                    generated=generated,
                )
                statement_shards.append(shard)
                statement_shard_by_path[shard_path] = (row, shard)
        row["_statement_shards"] = statement_shards
        if generated_object_pages:
            _write(
                output_root,
                relative=page_paths[row["knowledge_id"]],
                content=_object_page(
                    row=row,
                    audit_head=audit_head,
                    page_paths=page_paths,
                    titles=titles,
                    relations=relations,
                    statements=statements,
                    statement_shards=statement_shards,
                    source_fragment_links=source_fragment_links,
                ),
                generated=generated,
            )
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_kind[row["kind"]].append(row)
    index_links: list[str] = []
    if profile["kind_shards"] and profile["kind_indexes"]:
        for kind in _KIND_DIRECTORIES:
            members = by_kind.get(kind, [])
            shard_links: list[str] = []
            for shard_index, start in enumerate(
                range(0, len(members), INDEX_SHARD_SIZE), start=1
            ):
                shard = members[start : start + INDEX_SHARD_SIZE]
                shard_path = f"wiki/indexes/{kind}-{shard_index:04d}.md"
                lines = _frontmatter(
                    schema="deeplaw.living-wiki-index-shard/v1",
                    audit_head=audit_head,
                    fields={
                        "kind": kind,
                        "shard": str(shard_index),
                        "item_count": str(len(shard)),
                    },
                )
                lines.extend([f"# {kind.title()} index · {shard_index:04d}", ""])
                lines.extend(
                    f"- {_wiki_link(page_paths[row['knowledge_id']], row['title'])} "
                    f"(`{row['knowledge_id']}`, `{row['freshness']}`)"
                    for row in shard
                )
                _write(
                    output_root,
                    relative=shard_path,
                    content="\n".join(lines),
                    generated=generated,
                )
                shard_links.append(
                    f"- {_wiki_link(shard_path, f'{kind.title()} {shard_index:04d}')} "
                    f"({len(shard)} objects)"
                )
            kind_index = f"wiki/indexes/{kind}.md"
            lines = _frontmatter(
                schema="deeplaw.living-wiki-kind-index/v1",
                audit_head=audit_head,
                fields={"kind": kind, "item_count": str(len(members))},
            )
            lines.extend([f"# {kind.title()} index", "", *shard_links])
            if not shard_links:
                lines.append("- Explicit gap: no admitted current object.")
            _write(
                output_root,
                relative=kind_index,
                content="\n".join(lines),
                generated=generated,
            )
            index_links.append(f"- {_wiki_link(kind_index, kind.title())}: {len(members)}")
    if profile["source_pages"]:
        source_lines = _frontmatter(
            schema="deeplaw.living-wiki-source-index/v1",
            audit_head=audit_head,
            fields={"item_count": str(len(sources))},
        )
        source_lines.extend(["# Source Revision index", ""])
        source_lines.extend(
            f"- [[wiki/sources/{source['source_revision_id']}|"
            f"{source['title'] or source['source_revision_id']}]] "
            f"(`{source['source_revision_id']}`, `{source['status'] or 'unbound'}`)"
            for source in sources
        )
        if not sources:
            source_lines.append("- Explicit gap: no admitted Source Revision is available.")
        _write(
            output_root,
            relative=source_index,
            content="\n".join(source_lines),
            generated=generated,
        )
    else:
        _navigation_page(
            output_root=output_root,
            relative=source_index,
            title="Source Revision index",
            audit_head=audit_head,
            generated=generated,
            links=[],
            gap="Source pages are disabled by the selected projection profile.",
        )
    _navigation_page(
        output_root=output_root,
        relative="wiki/guides/index.md",
        title="Guides",
        audit_head=audit_head,
        generated=generated,
        links=[],
        gap="no governed guide coverage is registered for this projection.",
    )
    lint_report = (
        lint
        if isinstance(lint, dict)
        else store.semantic_lint(reference_time=selected_reference_time)
    )
    gaps_report = (
        gaps
        if isinstance(gaps, dict)
        else store.discover_gaps(reference_time=selected_reference_time)
    )
    if profile["gaps"]:
        lint_json = json.dumps(lint_report, ensure_ascii=False, indent=2, sort_keys=True)
        lint_lines = _frontmatter(
            schema="deeplaw.semantic-lint-view/v1",
            audit_head=audit_head,
            fields={"semantic_status": "projection"},
        )
        lint_lines.extend(["# Semantic Lint", "", "```json", lint_json, "```"])
        _write(
            output_root,
            relative="wiki/gaps/semantic-lint.md",
            content="\n".join(lint_lines),
            generated=generated,
        )
        gaps_json = json.dumps(gaps_report, ensure_ascii=False, indent=2, sort_keys=True)
        gaps_lines = _frontmatter(
            schema="deeplaw.knowledge-gap-view/v1",
            audit_head=audit_head,
            fields={"semantic_status": "projection"},
        )
        gaps_lines.extend(["# Knowledge Gaps", "", "```json", gaps_json, "```"])
        _write(
            output_root,
            relative="wiki/gaps/knowledge-gaps.md",
            content="\n".join(gaps_lines),
            generated=generated,
        )
    overview_synthesis = next(
        (
            row
            for row in rows
            if row["kind"] == "synthesis"
            and row.get("semantic_key") == f"overview:{store.vault_id}"
        ),
        None,
    )
    overview_lines = _frontmatter(
        schema="deeplaw.living-wiki-overview/v2",
        audit_head=audit_head,
    )
    freshness_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        freshness_counts[row["freshness"]] += 1
    overview_lines.extend(
        [
            "# Living Wiki overview",
            "",
            "## Canonical overview synthesis",
            "",
        ]
    )
    if overview_synthesis is None:
        overview_lines.append(
            "- Explicit gap: no canonical synthesis with semantic key "
            f"`overview:{store.vault_id}` has been compiled."
        )
    else:
        overview_lines.extend(
            [
                "- "
                + _wiki_link(
                    page_paths[overview_synthesis["knowledge_id"]],
                    overview_synthesis["title"],
                ),
                "",
                overview_synthesis["body"],
            ]
        )
    overview_lines.extend(
        [
            "",
            "## Projection status",
            "",
            "## Current state",
            "",
            f"- Admitted Knowledge Objects: {len(rows)}",
            f"- Admitted relations: {len(relations)}",
            f"- Source Revisions: {len(sources)}",
            *[
                f"- Freshness `{state}`: {count}"
                for state, count in sorted(freshness_counts.items())
            ],
            "",
            "## Navigate",
            "",
            *index_links,
            f"- {_wiki_link(source_index, 'Source Revisions')}: {len(sources)}",
            "- [[wiki/guides/index|Guides]]",
            "- [[wiki/gaps/index|Gaps]]",
            "- [[wiki/recent-changes/index|Recent changes]]",
            "- [[wiki/contradictions/index|Contradictions]]",
            "- [[wiki/gaps/compilation|Compilation gaps]]",
            "",
            "> This page renders only a designated canonical synthesis. The counts and "
            "links are deterministic projection status, not an inferred summary.",
        ]
    )
    if profile["communities"]:
        overview_lines.append("- [[wiki/communities/index|Communities]]")
    _write(
        output_root,
        relative="wiki/overview.md",
        content="\n".join(overview_lines),
        generated=generated,
    )
    if profile["communities"]:
        _, community_members = _community_views(
            store=store,
            output_root=output_root,
            rows=rows,
            relations=relations,
            page_paths=page_paths,
            titles=titles,
            audit_head=audit_head,
            generated=generated,
        )
    else:
        community_members = {}
    if profile["communities"]:
        community_links = [
            f"- {_wiki_link(f'wiki/communities/{community_id}.md', f'Community {community_id}')}"
            for community_id in sorted(community_members)
        ]
        _navigation_page(
            output_root=output_root,
            relative="wiki/communities/index.md",
            title="Communities",
            audit_head=audit_head,
            generated=generated,
            links=community_links,
            gap="no deterministic admitted community is available.",
        )
    root_index_lines = _frontmatter(
        schema="deeplaw.living-wiki-root-index/v1",
        audit_head=audit_head,
    )
    root_index_lines.extend(
        [
            "# Living Wiki index",
            "",
            *index_links,
            f"- {_wiki_link(source_index, 'Source Revisions')}",
            "- [[wiki/guides/index|Guides]]",
            "- [[wiki/gaps/index|Gaps]]",
            "- [[wiki/recent-changes/index|Recent changes]]",
            "- [[wiki/contradictions/index|Contradictions]]",
        ]
    )
    if profile["communities"]:
        root_index_lines.append("- [[wiki/communities/index|Communities]]")
    _write(
        output_root,
        relative="wiki/index.md",
        content="\n".join(root_index_lines),
        generated=generated,
    )
    community_count = len(community_members)
    if profile["recent_changes"]:
        _recent_change_pages(
            store,
            output_root=output_root,
            rows=rows,
            sources=sources,
            page_paths=page_paths,
            audit_head=audit_head,
            generated=generated,
        )
    contradiction_relations = [
        relation for relation in relations if relation["predicate"] == "contradicts"
    ]
    contradiction_lines = _frontmatter(
        schema="deeplaw.living-wiki-contradictions/v1",
        audit_head=audit_head,
    )
    contradiction_lines.extend(["# Contradictions", ""])
    contradiction_lines.extend(
        "- "
        + _wiki_link(
            page_paths[item["subject_knowledge_id"]],
            titles[item["subject_knowledge_id"]],
        )
        + " ↔ "
        + _wiki_link(
            page_paths[item["object_knowledge_id"]],
            titles[item["object_knowledge_id"]],
        )
        + " "
        f"(`{item['relation_revision_id']}`)"
        for item in contradiction_relations
    )
    if not contradiction_relations:
        contradiction_lines.append("- Explicit gap: no admitted contradiction relation.")
    _write(
        output_root,
        relative="wiki/contradictions/index.md",
        content="\n".join(contradiction_lines),
        generated=generated,
    )
    uncompiled_sources = [
        source
        for source in sources
        if not store.connection.execute(
            """
            SELECT 1 FROM source_compilation_runs_v1
            WHERE source_revision_id = ?
              AND status IN ('projection_pending', 'succeeded')
            """,
            (source["source_revision_id"],),
        ).fetchone()
    ]
    unresolved = store.connection.execute(
        """
        SELECT COUNT(*) FROM source_compilation_identity_candidates_v1
        WHERE status IN ('proposed', 'ambiguous')
        """
    ).fetchone()[0]
    gap_lines = _frontmatter(
        schema="deeplaw.living-wiki-compilation-gaps/v1",
        audit_head=audit_head,
    )
    gap_lines.extend(
        [
            "# Compilation gaps",
            "",
            f"- Unresolved identity candidates: {unresolved}",
            f"- Source Revisions without a committed compilation: {len(uncompiled_sources)}",
        ]
    )
    gap_lines.extend(
        f"- [[wiki/sources/{source['source_revision_id']}|"
        f"{source['title'] or source['source_revision_id']}]]"
        for source in uncompiled_sources
    )
    if not unresolved and not uncompiled_sources:
        gap_lines.append("- Explicit gap: no unresolved compilation Gap is recorded.")
    _write(
        output_root,
        relative="wiki/gaps/compilation.md",
        content="\n".join(gap_lines),
        generated=generated,
    )
    gap_links = [
        "- [[wiki/gaps/compilation|Compilation gaps]]",
    ]
    if profile["gaps"]:
        gap_links.extend(
            [
                "- [[wiki/gaps/semantic-lint|Semantic lint]]",
                "- [[wiki/gaps/knowledge-gaps|Knowledge gaps]]",
            ]
        )
    _navigation_page(
        output_root=output_root,
        relative="wiki/gaps/index.md",
        title="Gaps",
        audit_head=audit_head,
        generated=generated,
        links=gap_links,
        gap="no governed Gap report is available.",
    )
    canvas_manifests: list[dict[str, Any]] = []
    canvas_groups: dict[str, list[dict[str, Any]]] = {}
    if profile["global_canvas"]:
        # This path is a long-standing public surface; keep it stable even
        # though the builder now owns the only write.
        canvas_groups["knowledge-graph"] = rows
    if profile["kind_canvas"]:
        canvas_groups.update(
            {f"knowledge-{kind}": members for kind, members in by_kind.items() if members}
        )
    if profile["community_canvas"]:
        canvas_groups.update(
            {
                f"community-{community_id}": members
                for community_id, members in community_members.items()
            }
        )
    if profile["per_object_canvas"]:
        relation_neighbors: dict[str, set[str]] = defaultdict(set)
        for relation in relations:
            relation_neighbors[relation["subject_knowledge_id"]].add(
                relation["object_knowledge_id"]
            )
            relation_neighbors[relation["object_knowledge_id"]].add(
                relation["subject_knowledge_id"]
            )
        rows_by_id = {row["knowledge_id"]: row for row in rows}
        for row in rows:
            neighbors = [
                rows_by_id[item]
                for item in sorted(relation_neighbors[row["knowledge_id"]])
                if item in rows_by_id
            ][: CANVAS_NODE_LIMIT - 1]
            canvas_groups[f"object-{row['knowledge_id']}"] = [row, *neighbors]
    for name, members in sorted(canvas_groups.items()):
        canvas_payload, canvas_manifest = _canvas(
            rows=members,
            relations=relations,
            page_paths=page_paths,
            name=name,
        )
        canvas_path = f"canvas/{name}.canvas"
        _write(
            output_root,
            relative=canvas_path,
            content=canvas_payload,
            generated=generated,
        )
        canvas_manifest["path"] = canvas_path
        canvas_manifest["sha256"] = sha256_file(output_root / canvas_path)
        canvas_manifests.append(canvas_manifest)
    configuration = {
        "projection_profile": profile,
        "projection_profile_sha256": sha256_bytes(
            canonical_json(profile).encode("utf-8")
        ),
        "index_shard_size": INDEX_SHARD_SIZE,
        "source_fragment_shard_size": SOURCE_FRAGMENT_SHARD_SIZE,
        "canvas_node_limit": CANVAS_NODE_LIMIT,
        "canvas_edge_limit": CANVAS_EDGE_LIMIT,
        "page_kinds": sorted(_KIND_DIRECTORIES),
        "community_algorithm": "weighted-label-propagation+semantic-bridges/1",
        "local_canvas_per_object": profile["local_canvas_per_object"],
        "compilation_state_sha256": sha256_bytes(
            canonical_json(compilation_state).encode("utf-8")
        ),
    }
    generated_at = audit_event["recorded_at"]
    manifest = {
        "schema_version": LIVING_WIKI_SCHEMA,
        "input_audit_head": audit_head,
        "legacy_audit_head": store.legacy_audit_head,
        "generator": LIVING_WIKI_GENERATOR,
        "generator_version": "2",
        "configuration": configuration,
        "configuration_sha256": sha256_bytes(canonical_json(configuration).encode("utf-8")),
        "knowledge_revision_count": len(rows),
        "knowledge_revision_ids_sha256": sha256_bytes(
            canonical_json([row["revision_id"] for row in rows]).encode("utf-8")
        ),
        "relation_revision_count": len(relations),
        "relation_revision_ids_sha256": sha256_bytes(
            canonical_json([relation["relation_revision_id"] for relation in relations]).encode(
                "utf-8"
            )
        ),
        "source_revision_count": len(sources),
        "community_count": community_count,
        "files": sorted(generated, key=lambda item: item["path"]),
        "canvas_manifests": canvas_manifests,
        "generated_at": generated_at,
    }
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(manifest).encode("utf-8"))
    _validate_contract("living-wiki-manifest.v2.schema.json", manifest)
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    # Build the additive v3 bundle from the exact finalized v2 bytes.  Identity and governance
    # inputs are supplied by the projection branches above; the registry never derives them from
    # a filename, title, or frontmatter.
    payload_by_path = {
        item["path"]: (output_root / item["path"]).read_bytes()
        for item in manifest["files"]
        if item["path"].endswith(".md")
    }
    page_records: list[dict[str, Any]] = []
    registered_page_inventory: list[dict[str, Any]] = []

    def knowledge_page_record(
        row: dict[str, Any],
        *,
        path: str,
        payload: bytes,
    ) -> dict[str, Any]:
        return _page_record(
            page_id=row["knowledge_id"],
            namespace="knowledge",
            path=path,
            kind=row["kind"],
            revision_id=row["revision_id"],
            audit_head=audit_head,
            payload=payload,
            scope=row["scope"],
            sensitivity=row["sensitivity"],
            lifecycle=row["lifecycle"],
            freshness=row["freshness"],
            input_refs=[
                row["revision_id"],
                *[
                    str(statement["statement_id"])
                    for statement in row.get("_statements", [])
                ],
                *[
                    str(reference.get("source_revision_id"))
                    for reference in row.get("source_refs", [])
                    if reference.get("source_revision_id")
                ],
            ],
            knowledge_id=row["knowledge_id"],
            semantic_key=row.get("semantic_key"),
            aliases=row.get("aliases", []),
            title=row.get("title"),
            anchors=[
                {
                    "anchor_id": f"statement-{statement['statement_id']}",
                    "anchor": f"statement-{statement['statement_id']}",
                    "kind": "statement_evidence",
                    "statement_target": {"statement_id": statement["statement_id"]},
                }
                for statement in (
                    [] if row.get("_statement_shards") else row.get("_statements", [])
                )
            ]
            if generated_object_pages
            else [],
        )

    if not generated_object_pages:
        for row in rows:
            path = page_paths[row["knowledge_id"]]
            payload = _read_object(store.root, row["markdown_sha256"])
            if len(payload) > MAX_DERIVED_FILE_BYTES:
                raise RuntimeError(f"registered Knowledge Markdown exceeds Wiki byte bound: {path}")
            payload_by_path[path] = payload
            registered_page_inventory.append(
                {
                    "path": path,
                    "byte_size": len(payload),
                    "sha256": row["markdown_sha256"],
                }
            )
            page_records.append(knowledge_page_record(row, path=path, payload=payload))

    rows_by_path = (
        {page_paths[row["knowledge_id"]]: row for row in rows}
        if generated_object_pages
        else {}
    )
    source_by_path = {
        f"wiki/sources/{source['source_revision_id']}.md": source for source in sources
    }
    fragment_by_path = {
        path: anchors
        for source in sources
        for path, anchors in source.get("_fragment_anchor_pages", {}).items()
    }
    for item in manifest["files"]:
        path = item["path"]
        if not path.endswith(".md"):
            continue
        payload = payload_by_path[path]
        row = rows_by_path.get(path)
        if row is not None:
            page_records.append(knowledge_page_record(row, path=path, payload=payload))
            continue
        statement_shard = statement_shard_by_path.get(path)
        if statement_shard is not None:
            shard_row, shard = statement_shard
            shard_statements = shard["statements"]
            page_records.append(
                _page_record(
                    page_id=stable_id(
                        "statement-evidence-page",
                        shard_row["knowledge_id"],
                        str(shard["index"]),
                    ),
                    namespace="aggregate",
                    path=path,
                    kind="aggregate",
                    revision_id=stable_id(
                        "statement-evidence-page-revision",
                        shard_row["revision_id"],
                        str(shard["index"]),
                    ),
                    audit_head=audit_head,
                    payload=payload,
                    scope=shard_row["scope"],
                    sensitivity=shard_row["sensitivity"],
                    lifecycle=shard_row["lifecycle"],
                    freshness=shard_row["freshness"],
                    input_refs=[
                        shard_row["revision_id"],
                        *[
                            str(statement["statement_id"])
                            for statement in shard_statements
                        ],
                    ],
                    anchors=[
                        {
                            "anchor_id": f"statement-{statement['statement_id']}",
                            "anchor": f"statement-{statement['statement_id']}",
                            "kind": "statement_evidence",
                            "statement_target": {
                                "statement_id": statement["statement_id"]
                            },
                        }
                        for statement in shard_statements
                    ],
                    title=(
                        f"{shard_row['title']} · Statement Evidence "
                        f"{shard['index']:04d}"
                    ),
                )
            )
            continue
        source = source_by_path.get(path)
        if source is not None:
            source_sensitivity = source["sensitivity"] if source["sensitivity"] in {
                "public", "internal", "private", "restricted"
            } else "private"
            page_records.append(
                _page_record(
                    page_id=stable_id("source-page", source["source_revision_id"]),
                    namespace="source",
                    path=path,
                    kind="source",
                    revision_id=source["source_revision_id"],
                    audit_head=audit_head,
                    payload=payload,
                    scope=store.vault_scope,
                    sensitivity=source_sensitivity,
                    input_refs=[source["source_revision_id"]],
                    title=source.get("title") or source["source_revision_id"],
                )
            )
            continue
        anchors = fragment_by_path.get(path)
        if anchors is not None:
            source_revision_id = next(
                anchor["source_fragment"]["source_revision_id"] for anchor in anchors
            )
            shard_revision = stable_id("source-fragment-index-revision", path, audit_head)
            page_records.append(
                _page_record(
                    page_id=stable_id("source-fragment-index", path),
                    namespace="aggregate",
                    path=path,
                    kind="aggregate",
                    revision_id=shard_revision,
                    audit_head=audit_head,
                    payload=payload,
                    scope=store.vault_scope,
                    sensitivity="private",
                    input_refs=[
                        source_revision_id,
                        *[
                            anchor["source_fragment"].get(
                                "fragment_id", anchor["source_fragment"].get("fragment_revision_id")
                            )
                            for anchor in anchors
                        ],
                    ],
                    anchors=anchors,
                    title="Source fragments",
                )
            )
            continue
        # Every remaining Markdown page is an explicit aggregate/system projection.  Its stable
        # identity is bound to the declared projection role (path) and the audit input, not user
        # editable text or frontmatter.
        page_records.append(
            _page_record(
                page_id=stable_id("projection-page", path),
                namespace="aggregate",
                path=path,
                kind="aggregate",
                revision_id=stable_id("projection-page-revision", path, audit_head),
                audit_head=audit_head,
                payload=payload,
                scope=store.vault_scope,
                sensitivity="private",
                input_refs=[audit_head],
                title=Path(path).stem.replace("-", " ").title(),
            )
        )
    page_registry = build_page_registry(
        page_records,
        v2_file_inventory=manifest["files"],
        registered_page_inventory=registered_page_inventory,
        input_audit_head=audit_head,
        legacy_audit_head=store.legacy_audit_head,
        v2_manifest_sha256=manifest["manifest_sha256"],
        generated_at=generated_at,
    )
    resolver_artifact = build_resolver_index(page_registry)
    governed_links: list[dict[str, str]] = []
    if not generated_object_pages:
        for relation in relations:
            governed_links.append(
                {
                    "source_page_id": relation["subject_knowledge_id"],
                    "target_page_id": relation["object_knowledge_id"],
                    "reference_id": relation["relation_revision_id"],
                    "reason": f"relation:{relation['predicate']}",
                }
            )
        source_page_ids = {
            source["source_revision_id"]: stable_id(
                "source-page", source["source_revision_id"]
            )
            for source in sources
        }
        for row in rows:
            for reference in row.get("source_refs", []):
                source_revision_id = reference.get("source_revision_id")
                target_page_id = source_page_ids.get(source_revision_id)
                if target_page_id is None:
                    continue
                governed_links.append(
                    {
                        "source_page_id": row["knowledge_id"],
                        "target_page_id": target_page_id,
                        "reference_id": stable_id(
                            "source-reference-link",
                            row["revision_id"],
                            canonical_json(reference),
                        ),
                        "reason": "source_reference",
                    }
                )
    link_artifact = build_link_index(
        page_registry,
        payload_by_path,
        governed_links=governed_links,
        v2_manifest_sha256=manifest["manifest_sha256"],
        input_audit_head=audit_head,
        legacy_audit_head=store.legacy_audit_head,
        generated_at=generated_at,
        resolver=resolver_artifact["resolver"],
    )
    v3_artifact = build_living_wiki_manifest_v3(
        input_audit_head=audit_head,
        legacy_audit_head=store.legacy_audit_head,
        generated_at=generated_at,
        v2_manifest_sha256=manifest["manifest_sha256"],
        configuration={"profile": profile["name"]},
        page_registry=page_registry,
        link_index=link_artifact,
        resolver=resolver_artifact,
    )
    v3_payloads: dict[str, bytes] = {}
    for artifact in (page_registry, link_artifact, resolver_artifact):
        v3_payloads.update({path: bytes(payload) for path, payload in artifact["payloads"].items()})
    v3_payloads[V3_MANIFEST_PATH] = bytes(v3_artifact["manifest_bytes"])
    for relative, payload in v3_payloads.items():
        _atomic_owner_write(output_root / relative, payload)
    _atomic_owner_write(
        output_root / "living-wiki-manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return {
        "schema_version": LIVING_WIKI_SCHEMA,
        "manifest_sha256": manifest["manifest_sha256"],
        "knowledge_count": len(rows),
        "relation_count": len(relations),
        "source_count": len(sources),
        "file_count": len(generated),
        "index_shard_count": sum(
            math_count
            for math_count in [
                (len(members) + INDEX_SHARD_SIZE - 1) // INDEX_SHARD_SIZE
                for members in by_kind.values()
            ]
        )
        if profile["kind_shards"]
        else 0,
        "canvas_count": len(canvas_manifests),
        "community_count": community_count,
        "input_audit_head": audit_head,
        "projection_profile_name": profile["name"],
        "projection_profile_version": profile["version"],
        "files": manifest["files"],
        "v3_manifest_sha256": v3_artifact["manifest_sha256"],
        "v3_page_count": page_registry["component"]["page_count"],
        "v3_edge_count": link_artifact["component"]["edge_count"],
        "v3_candidate_count": resolver_artifact["component"]["candidate_count"],
        "v3_inventory": {
            "manifest": v3_artifact["manifest"],
            "manifest_sha256": v3_artifact["manifest_sha256"],
            "files": [
                {
                    "path": path,
                    "byte_size": len(payload),
                    "sha256": sha256_bytes(payload),
                }
                for path, payload in sorted(v3_payloads.items())
            ],
        },
    }


def rebuild_living_wiki(
    store: AutonomousKnowledgeStore,
    *,
    input_audit_head: str | None = None,
    run_status_overrides: dict[str, str] | None = None,
    projection_profile: str = "standard",
    reference_time: str | None = None,
    lint: dict[str, Any] | None = None,
    gaps: dict[str, Any] | None = None,
    dry_run: bool = False,
    _fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Recompute a projection and atomically activate only changed bytes.

    ``dry_run`` performs the same generation and hash diff in a temporary
    directory outside the Vault.  It intentionally refuses to recover an
    existing transaction because recovery itself is a Vault mutation.
    """

    store._require_write()
    if not isinstance(dry_run, bool):
        raise TypeError("dry_run must be a bool")
    journal_path = store.root / ".deeplaw/derived/tree/living-wiki-projection.journal.json"
    if dry_run:
        if journal_path.exists() or journal_path.is_symlink():
            raise RuntimeError("Living Wiki projection has an unresolved transaction")
    else:
        recover_projection(store.root)
    previous = read_previous_manifest(store.root)
    previous_v3 = read_previous_v3(store.root)

    if dry_run:
        with tempfile.TemporaryDirectory(prefix="deeplaw-projection-") as temporary:
            result = _generate_living_wiki(
                store,
                output_root=Path(temporary),
                input_audit_head=input_audit_head,
                run_status_overrides=run_status_overrides,
                projection_profile=projection_profile,
                reference_time=reference_time,
                lint=lint,
                gaps=gaps,
            )
            staged_manifest = strict_json_loads(
                (Path(temporary) / "living-wiki-manifest.json").read_bytes()
            )
            if not isinstance(staged_manifest, dict):
                raise RuntimeError("Living Wiki generated manifest is invalid")
            staged_v3 = read_previous_v3(Path(temporary))
            change_set = build_change_set(
                previous,
                staged_manifest,
                previous_v3=previous_v3,
                current_v3=staged_v3,
            )
            return {**result, "dry_run": True, "change_set": change_set}

    txn_id, staging, backup = begin_transaction(store.root)
    prepared = False
    try:
        result = _generate_living_wiki(
            store,
            output_root=staging,
            input_audit_head=input_audit_head,
            run_status_overrides=run_status_overrides,
            projection_profile=projection_profile,
            reference_time=reference_time,
            lint=lint,
            gaps=gaps,
        )
        staged_manifest = strict_json_loads((staging / "living-wiki-manifest.json").read_bytes())
        if not isinstance(staged_manifest, dict):
            raise RuntimeError("Living Wiki generated manifest is invalid")
        staged_v3 = read_previous_v3(staging)
        change_set = build_change_set(
            previous,
            staged_manifest,
            previous_v3=previous_v3,
            current_v3=staged_v3,
        )
        journal = prepare_activation(
            store.root,
            txn_id=txn_id,
            staging=staging,
            backup=backup,
            previous=previous,
            current=staged_manifest,
            change_set=change_set,
            previous_v3=previous_v3,
            current_v3=staged_v3,
        )
        prepared = True
        if _fault_hook is not None:
            _fault_hook("after_prepare")
        activate_projection(
            store.root,
            journal=journal,
            current=staged_manifest,
            fault_hook=_fault_hook,
        )
        return {**result, "dry_run": False, "change_set": change_set}
    except BaseException:
        if not prepared:
            discard_transaction(store.root, staging=staging, backup=backup)
        raise
