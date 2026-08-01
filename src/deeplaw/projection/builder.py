from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from ..knowledge_autonomy import (
    AutonomousKnowledgeStore,
    _atomic_owner_write,
    _validate_contract,
)
from ..knowledge_intelligence import detect_communities
from ..util import canonical_json, sha256_bytes, sha256_file, stable_id, strict_json_loads

LIVING_WIKI_SCHEMA = "deeplaw.living-wiki-manifest/v1"
LIVING_WIKI_GENERATOR = "deeplaw.living-wiki-projector/1"
INDEX_SHARD_SIZE = 200
SOURCE_FRAGMENT_SHARD_SIZE = 64
CANVAS_NODE_LIMIT = 200
CANVAS_EDGE_LIMIT = 400
MAX_DERIVED_FILE_BYTES = 256 * 1024

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
    fields: dict[str, str] | None = None,
) -> list[str]:
    lines = [
        "---",
        f"schema: {schema}",
        "derived_view: true",
        f"audit_head: {audit_head}",
        "authority: none",
        "legal_authority: false",
    ]
    for key, value in (fields or {}).items():
        lines.append(f"{key}: {value}")
    lines.extend(["---", ""])
    return lines


def _wiki_link(path: str, title: str) -> str:
    target = PurePosixPath(path).with_suffix("").as_posix()
    safe_title = title.replace("|", "¦").replace("]", "）")
    return f"[[{target}|{safe_title}]]"


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


def _current_rows(store: AutonomousKnowledgeStore) -> list[dict[str, Any]]:
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
        revision = store._revision_row(row, include_body=True)
        if not store.revision_provenance_admitted(revision):
            continue
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
        rows.append(revision)
    return rows


def _current_relations(
    store: AutonomousKnowledgeStore,
    *,
    admitted_ids: set[str],
) -> list[dict[str, Any]]:
    return [
        relation
        for relation in store._current_relations()
        if relation["subject_knowledge_id"] in admitted_ids
        and relation["object_knowledge_id"] in admitted_ids
        and store.relation_provenance_admitted(relation)
    ]


def _object_page(
    *,
    row: dict[str, Any],
    audit_head: str,
    page_paths: dict[str, str],
    titles: dict[str, str],
    relations: list[dict[str, Any]],
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
                   COUNT(DISTINCT source_ir_nodes_v2.node_id) AS node_count,
                   COUNT(DISTINCT fragments_v2.fragment_revision_id) AS fragment_count,
                   COUNT(
                       DISTINCT CASE
                           WHEN fragments_v2.instruction_risk = 1
                           THEN fragments_v2.fragment_revision_id
                       END
                   ) AS risky_fragment_count
            FROM compilations_v2
            LEFT JOIN source_ir_nodes_v2
              ON source_ir_nodes_v2.compilation_id = compilations_v2.compilation_id
            LEFT JOIN fragments_v2
              ON fragments_v2.compilation_id = compilations_v2.compilation_id
            WHERE compilations_v2.source_revision_id = ?
            GROUP BY compilations_v2.compilation_id
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
                shard_lines.append(
                    f"- {fragment['ordinal']}. `{fragment['fragment_id']}` · "
                    f"revision `{fragment['fragment_revision_id']}` · "
                    f"locator `{locator}` · quote SHA-256 "
                    f"`{fragment['text_sha256']}`"
                )
            _write(
                store.root,
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
        title = source["title"] or source["source_revision_id"]
        lines = _frontmatter(
            schema="deeplaw.living-wiki-source/v1",
            audit_head=audit_head,
            fields={"source_revision_id": source["source_revision_id"]},
        )
        lines.extend(
            [
                f"# {title}",
                "",
                "## Source Revision",
                "",
                f"- Source Revision: `{source['source_revision_id']}`",
                f"- Source identity: `{source['source_key']}`",
                f"- Content SHA-256: `{source['content_sha256']}`",
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
        lines.extend(["", "## Source summary synthesis", ""])
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
        for kind in _KIND_DIRECTORIES:
            members = related_by_kind.get(kind, [])
            if not members:
                continue
            lines.append(f"### {kind.title()}")
            lines.append("")
            lines.extend(
                f"- {_wiki_link(page_paths[item['knowledge_id']], item['title'])} "
                f"(`{item['knowledge_id']}`, `{item['freshness']}`)"
                for item in members
            )
            lines.append("")
        if not related:
            lines.append("- Explicit gap: no admitted source-bound Knowledge Revision.")
        lines.extend(["", "## Compilation runs", ""])
        if runs:
            for item in runs:
                lines.append(
                    f"- `{item['compilation_run_id']}` · `{item['status']}` · "
                    f"`{item['compiler_profile']}@{item['compiler_profile_version']}` · "
                    f"transaction `{item['status']}` · semantic "
                    f"`{item['semantic_status'] or 'not_recorded'}`"
                )
                if item["compiler_profile_version"] != "2":
                    continue
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
                    lines.extend(
                        f"    - `{report['duty_type']}`: `{report['status']}`"
                        f"{' (required)' if report['required'] else ''}"
                        for report in item["duty_reports"]
                    )
                else:
                    lines.append("  - Explicit gap: no semantic Duty Report is registered.")
        else:
            lines.append("- Explicit gap: Source Revision has not been compiled.")
        _write(
            store.root,
            relative=f"wiki/sources/{source['source_revision_id']}.md",
            content="\n".join(lines),
            generated=generated,
        )
        result.append(dict(source))
    return result


def _community_views(
    *,
    store: AutonomousKnowledgeStore,
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
                store.root,
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
            store.root,
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
            store.root,
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
        index_lines.append("- No Ledger event has been recorded.")
    if history_truncated:
        index_lines.extend(
            [
                "",
                "- Explicit pagination boundary: history before the oldest listed "
                "Ledger cursor is available through the Ledger/API, not silently omitted.",
            ]
        )
    _write(
        store.root,
        relative="wiki/recent-changes/index.md",
        content="\n".join(index_lines),
        generated=generated,
    )


def rebuild_living_wiki(
    store: AutonomousKnowledgeStore,
    *,
    input_audit_head: str | None = None,
    run_status_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build complete, sharded, deterministic views from admitted current revisions."""

    store._require_write()
    audit_head = input_audit_head or store.audit_head
    if audit_head != store.audit_head:
        raise RuntimeError("Living Wiki projection audit input changed")
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
    rows = _current_rows(store)
    admitted_ids = {row["knowledge_id"] for row in rows}
    relations = _current_relations(store, admitted_ids=admitted_ids)
    generated: list[dict[str, Any]] = []
    page_paths = {
        row["knowledge_id"]: (f"wiki/{_KIND_DIRECTORIES[row['kind']]}/{row['knowledge_id']}.md")
        for row in rows
    }
    titles = {row["knowledge_id"]: row["title"] for row in rows}
    for row in rows:
        _write(
            store.root,
            relative=page_paths[row["knowledge_id"]],
            content=_object_page(
                row=row,
                audit_head=audit_head,
                page_paths=page_paths,
                titles=titles,
                relations=relations,
            ),
            generated=generated,
        )
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_kind[row["kind"]].append(row)
    index_links: list[str] = []
    for kind in _KIND_DIRECTORIES:
        members = by_kind.get(kind, [])
        shard_links: list[str] = []
        for shard_index, start in enumerate(range(0, len(members), INDEX_SHARD_SIZE), start=1):
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
                store.root,
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
            lines.append("- No admitted current object.")
        _write(
            store.root,
            relative=kind_index,
            content="\n".join(lines),
            generated=generated,
        )
        index_links.append(f"- {_wiki_link(kind_index, kind.title())}: {len(members)}")
    sources = _source_pages(
        store,
        audit_head=audit_head,
        generated=generated,
        rows=rows,
        page_paths=page_paths,
        run_status_overrides=effective_run_statuses,
    )
    source_index = "wiki/indexes/sources.md"
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
    _write(
        store.root,
        relative=source_index,
        content="\n".join(source_lines),
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
            "- [[wiki/recent-changes/index|Recent changes]]",
            "- [[wiki/contradictions/index|Contradictions]]",
            "- [[wiki/gaps/compilation|Compilation gaps]]",
            "",
            "> This page renders only a designated canonical synthesis. The counts and "
            "links are deterministic projection status, not an inferred summary.",
        ]
    )
    _write(
        store.root,
        relative="wiki/overview.md",
        content="\n".join(overview_lines),
        generated=generated,
    )
    communities, community_members = _community_views(
        store=store,
        rows=rows,
        relations=relations,
        page_paths=page_paths,
        titles=titles,
        audit_head=audit_head,
        generated=generated,
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
        ]
    )
    _write(
        store.root,
        relative="wiki/index.md",
        content="\n".join(root_index_lines),
        generated=generated,
    )
    _recent_change_pages(
        store,
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
        contradiction_lines.append("- No admitted contradiction relation.")
    _write(
        store.root,
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
    _write(
        store.root,
        relative="wiki/gaps/compilation.md",
        content="\n".join(gap_lines),
        generated=generated,
    )
    canvas_manifests: list[dict[str, Any]] = []
    canvas_groups = {"knowledge-overview": rows}
    canvas_groups.update(
        {f"knowledge-{kind}": members for kind, members in by_kind.items() if members}
    )
    canvas_groups.update(
        {
            f"community-{community_id}": members
            for community_id, members in community_members.items()
        }
    )
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
            store.root,
            relative=canvas_path,
            content=canvas_payload,
            generated=generated,
        )
        canvas_manifest["path"] = canvas_path
        canvas_manifest["sha256"] = sha256_file(store.root / canvas_path)
        canvas_manifests.append(canvas_manifest)
    configuration = {
        "index_shard_size": INDEX_SHARD_SIZE,
        "source_fragment_shard_size": SOURCE_FRAGMENT_SHARD_SIZE,
        "canvas_node_limit": CANVAS_NODE_LIMIT,
        "canvas_edge_limit": CANVAS_EDGE_LIMIT,
        "page_kinds": sorted(_KIND_DIRECTORIES),
        "community_algorithm": "weighted-label-propagation+semantic-bridges/1",
        "local_canvas_per_object": True,
        "compilation_state_sha256": sha256_bytes(
            canonical_json(compilation_state).encode("utf-8")
        ),
    }
    audit_event = store.connection.execute(
        """
        SELECT recorded_at FROM autonomous_events_v3
        WHERE event_hash = ?
        """,
        (audit_head,),
    ).fetchone()
    if audit_event is None:
        raise RuntimeError("Living Wiki projection audit input is not registered")
    generated_at = audit_event["recorded_at"]
    manifest = {
        "schema_version": LIVING_WIKI_SCHEMA,
        "input_audit_head": audit_head,
        "legacy_audit_head": store.legacy_audit_head,
        "generator": LIVING_WIKI_GENERATOR,
        "generator_version": "1",
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
        "community_count": len(communities),
        "files": sorted(generated, key=lambda item: item["path"]),
        "canvas_manifests": canvas_manifests,
        "generated_at": generated_at,
    }
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(manifest).encode("utf-8"))
    _validate_contract("living-wiki-manifest.v1.schema.json", manifest)
    manifest_path = store.root / ".deeplaw" / "derived" / "tree" / "living-wiki-manifest.json"
    previous_paths: set[str] = set()
    if manifest_path.is_file() and not manifest_path.is_symlink():
        previous = strict_json_loads(manifest_path.read_bytes())
        if isinstance(previous, dict) and isinstance(previous.get("files"), list):
            previous_paths = {
                item["path"]
                for item in previous["files"]
                if isinstance(item, dict)
                and isinstance(item.get("path"), str)
                and item["path"].startswith(("wiki/", "canvas/"))
            }
    current_paths = {item["path"] for item in generated}
    for relative in sorted(previous_paths - current_paths):
        stale = store.root / relative
        if stale.is_symlink() or (stale.exists() and not stale.is_file()):
            raise RuntimeError("Living Wiki stale projection target is unsafe")
        stale.unlink(missing_ok=True)
    _atomic_owner_write(
        manifest_path,
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
        ),
        "canvas_count": len(canvas_manifests),
        "community_count": len(communities),
        "input_audit_head": audit_head,
        "files": manifest["files"],
    }
