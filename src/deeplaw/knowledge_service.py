from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from .compilation.status import (
    ADMISSIBLE_SOURCE_LIFECYCLES,
    summarize_source_compilation,
)
from .knowledge_autonomy import (
    AutonomousKnowledgeStore,
    _validate_contract,
    autonomous_core_installed,
    initialize_autonomous_core,
)
from .knowledge_store import (
    KnowledgeVault,
    VaultScope,
    initialize_knowledge_vault,
)


def initialize_default_knowledge_vault(
    path: str | Path,
    *,
    name: str,
    scope: VaultScope,
) -> dict[str, Any]:
    """Initialize the shared legacy evidence and autonomous knowledge planes."""

    legacy = initialize_knowledge_vault(path, name=name, scope=scope)
    autonomous = initialize_autonomous_core(path, migration_source="new-vault")
    return {
        "schema_version": "deeplaw.knowledge-vault-initialization/v2",
        "vault_id": legacy["vault_id"],
        "legacy_compatibility": legacy,
        "autonomous_core": autonomous,
        "active_write_policy": "agent_derived_autonomous",
    }


@contextmanager
def auto_aware_knowledge_vault(
    path: str | Path,
    *,
    read_only: bool,
) -> Iterator[KnowledgeVault]:
    """Open legacy evidence and reconcile it into an installed autonomous core."""

    with KnowledgeVault(path, read_only=read_only) as vault:
        yield vault
    if not read_only and autonomous_core_installed(path):
        with AutonomousKnowledgeStore(path, read_only=False):
            pass


def source_knowledge_status(
    vault: KnowledgeVault,
    *,
    source_ids: tuple[str, ...] = (),
    source_revision_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return one closed status for newly registered Source Revisions."""

    selected_revision_ids = {
        str(value) for value in source_revision_ids if isinstance(value, str)
    }
    selected_source_ids = {str(value) for value in source_ids if isinstance(value, str)}
    if selected_source_ids:
        placeholders = ", ".join("?" for _ in selected_source_ids)
        rows = vault.connection.execute(
            f"""
            SELECT legacy_source_id, source_revision_id
            FROM source_revision_bindings_v2
            WHERE legacy_source_id IN ({placeholders})
            """,
            tuple(sorted(selected_source_ids)),
        ).fetchall()
        selected_revision_ids.update(str(row["source_revision_id"]) for row in rows)

    lifecycle_rows: list[Any] = []
    if selected_revision_ids:
        placeholders = ", ".join("?" for _ in selected_revision_ids)
        lifecycle_rows = vault.connection.execute(
            f"""
            SELECT source_revisions_v2.source_revision_id,
                   source_lifecycle.status
            FROM source_revisions_v2
            JOIN source_revision_bindings_v2 USING(source_revision_id)
            JOIN source_lifecycle
              ON source_lifecycle.source_id =
                 source_revision_bindings_v2.legacy_source_id
            WHERE source_revisions_v2.source_revision_id IN ({placeholders})
            ORDER BY source_revisions_v2.source_revision_id
            """,
            tuple(sorted(selected_revision_ids)),
        ).fetchall()

    run_rows: list[Any] = []
    if selected_revision_ids and autonomous_core_installed(vault.root):
        placeholders = ", ".join("?" for _ in selected_revision_ids)
        run_rows = vault.connection.execute(
            f"""
            SELECT compilation_run_id, source_revision_id, status, updated_at
            FROM source_compilation_runs_v1
            WHERE source_revision_id IN ({placeholders})
            ORDER BY updated_at, compilation_run_id
            """,
            tuple(sorted(selected_revision_ids)),
        ).fetchall()

    lifecycle_by_source: dict[str, list[str]] = {}
    for row in lifecycle_rows:
        lifecycle_by_source.setdefault(str(row["source_revision_id"]), []).append(
            str(row["status"])
        )
    runs_by_source: dict[str, list[dict[str, Any]]] = {}
    for row in run_rows:
        runs_by_source.setdefault(str(row["source_revision_id"]), []).append(
            dict(row)
        )
    registered = bool(selected_revision_ids) and selected_revision_ids <= set(
        lifecycle_by_source
    )
    summaries = []
    for source_revision_id in sorted(selected_revision_ids):
        lifecycle_values = lifecycle_by_source.get(source_revision_id, [])
        lifecycle_status = next(
            (
                value
                for value in lifecycle_values
                if value not in ADMISSIBLE_SOURCE_LIFECYCLES
            ),
            lifecycle_values[0] if lifecycle_values else "active",
        )
        summaries.append(
            summarize_source_compilation(
                source_revision_id=source_revision_id,
                lifecycle_status=lifecycle_status,
                runs=runs_by_source.get(source_revision_id, []),
            )
        )
    canonical_knowledge_committed = bool(summaries) and all(
        summary["canonical_knowledge_committed"] for summary in summaries
    )
    canonical_knowledge_admissible = registered and bool(summaries) and all(
        summary["canonical_knowledge_admissible"] for summary in summaries
    )
    compiled = canonical_knowledge_committed
    stale_or_blocked = any(summary["stale_or_blocked"] for summary in summaries)
    compilation_required = registered and not compiled and not stale_or_blocked
    gap = not registered or (not compiled and not compilation_required and not stale_or_blocked)
    successful_run_ids = sorted(
        run_id
        for summary in summaries
        for run_id in summary["successful_compilation_run_ids"]
    )
    latest_compilation_attempts = [
        attempt
        for summary in summaries
        for attempt in summary["latest_compilation_attempts"]
    ]
    projection_pending_run_ids = sorted(
        run_id
        for summary in summaries
        for run_id in summary["projection_pending_compilation_run_ids"]
    )
    wiki_projection_ready = bool(
        canonical_knowledge_committed
        and summaries
        and all(summary["wiki_projection_ready"] for summary in summaries)
    )
    wiki_projection_pending = bool(
        canonical_knowledge_committed
        and not wiki_projection_ready
        and summaries
        and all(
            summary["wiki_projection_status"] in {"ready", "pending"}
            for summary in summaries
        )
        and any(summary["wiki_projection_pending"] for summary in summaries)
    )
    wiki_projection_status = (
        "ready"
        if wiki_projection_ready
        else (
            "pending"
            if wiki_projection_pending
            else (
                "blocked"
                if any(
                    summary["wiki_projection_status"] == "blocked"
                    for summary in summaries
                )
                else ("not_started" if registered else "unavailable")
            )
        )
    )
    state = (
        "stale_or_blocked"
        if stale_or_blocked
        else (
            "compiled"
            if compiled
            else ("compilation_required" if compilation_required else "gap")
        )
    )
    result = {
        "schema_version": "deeplaw.source-knowledge-status/v1",
        "state": state,
        "source_registered": registered,
        "compilation_required": compilation_required,
        "compiled": compiled,
        "canonical_knowledge_committed": canonical_knowledge_committed,
        "canonical_knowledge_admissible": canonical_knowledge_admissible,
        "wiki_projection_status": wiki_projection_status,
        "wiki_projection_pending": wiki_projection_pending,
        "wiki_projection_ready": wiki_projection_ready,
        "stale_or_blocked": stale_or_blocked,
        "gap": gap,
        "source_revision_ids": sorted(selected_revision_ids),
        "latest_compilation_attempts": latest_compilation_attempts,
        "successful_compilation_run_ids": successful_run_ids,
        "projection_pending_compilation_run_ids": projection_pending_run_ids,
    }
    _validate_contract("source-knowledge-status.v1.schema.json", result)
    return result


def source_knowledge_status_for_result(
    vault: KnowledgeVault,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Attach Source-to-Knowledge state to a direct compiler or ingest-job receipt."""

    source_ids: list[str] = []
    source_revision_ids: list[str] = []
    source = result.get("source")
    identity = result.get("identity")
    if isinstance(source, dict) and isinstance(source.get("source_id"), str):
        source_ids.append(cast(str, source["source_id"]))
    if isinstance(identity, dict) and isinstance(identity.get("source_revision_id"), str):
        source_revision_ids.append(cast(str, identity["source_revision_id"]))
    items = result.get("items")
    if isinstance(items, list):
        source_ids.extend(
            str(item["source_id"])
            for item in items
            if isinstance(item, dict) and isinstance(item.get("source_id"), str)
        )
    return {
        **result,
        "source_knowledge_status": source_knowledge_status(
            vault,
            source_ids=tuple(source_ids),
            source_revision_ids=tuple(source_revision_ids),
        ),
    }
