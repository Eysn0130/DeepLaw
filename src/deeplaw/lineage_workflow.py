from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from .knowledge_identity import record_lineage_transition
from .knowledge_models import utc_now
from .knowledge_store import KnowledgeVault
from .util import canonical_json, normalize_text, stable_id, strict_json_loads

LINEAGE_REVIEW_SCHEMA = "deeplaw.knowledge-lineage-review/v1"
LineageReviewStatus = Literal["split", "merged", "ambiguous"]

_MAX_MAPPING_ASSETS = 20


def _timestamp_after(value: str | None) -> str:
    selected = datetime.fromisoformat(utc_now().replace("Z", "+00:00"))
    if value is not None:
        previous = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if selected <= previous:
            selected = previous + timedelta(seconds=1)
    return selected.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _asset_identity(vault: KnowledgeVault, asset_id: str) -> dict[str, Any]:
    asset = vault.get_asset(asset_id, include_inactive=True)
    row = vault.connection.execute(
        """
        SELECT asset_revision_bindings_v2.asset_revision_id,
               knowledge_revisions_v2.knowledge_key
        FROM asset_revision_bindings_v2
        JOIN knowledge_revisions_v2 USING(asset_revision_id)
        WHERE asset_revision_bindings_v2.legacy_asset_id = ?
        """,
        (asset_id,),
    ).fetchone()
    if row is None or not asset.source_refs:
        raise ValueError(
            "reviewed lineage mapping requires source-bound Identity v2 assets"
        )
    source_rows = vault.connection.execute(
        """
        SELECT source_revision_id, fragment_revision_id, locator, quote_sha256
        FROM proposal_source_refs_v2
        WHERE asset_revision_id = ?
        ORDER BY ref_ordinal
        """,
        (row["asset_revision_id"],),
    ).fetchall()
    if not source_rows:
        raise ValueError(
            "reviewed lineage mapping requires exact Identity v2 source references"
        )
    return {
        "asset_id": asset.asset_id,
        "asset_revision_id": row["asset_revision_id"],
        "knowledge_key": row["knowledge_key"],
        "title": asset.title,
        "status": asset.status,
        "source_revision_ids": sorted({item["source_revision_id"] for item in source_rows}),
        "source_refs": [dict(item) for item in source_rows],
    }


def _validate_shape(
    status: LineageReviewStatus,
    from_assets: tuple[str, ...],
    to_assets: tuple[str, ...],
) -> None:
    if status == "split" and not (len(from_assets) == 1 and len(to_assets) > 1):
        raise ValueError("split lineage review requires one predecessor and multiple successors")
    if status == "merged" and not (len(from_assets) > 1 and len(to_assets) == 1):
        raise ValueError("merged lineage review requires multiple predecessors and one successor")
    if status == "ambiguous" and not (from_assets and to_assets):
        raise ValueError("ambiguous lineage review requires both predecessor and successor sets")


def review_lineage_mapping(
    vault: KnowledgeVault,
    *,
    status: LineageReviewStatus,
    from_asset_ids: tuple[str, ...],
    to_asset_ids: tuple[str, ...],
    confirm_reviewed: bool,
    reviewer_id: str = "local-operator",
    reason: str,
) -> dict[str, Any]:
    """Record one explicit, source-bound split/merge/ambiguity review.

    This workflow maps revisions that already exist; it never creates knowledge,
    activates proposals, or inherits approval. The same transition is indexed under
    every involved Knowledge Key so either side can discover the complete mapping.
    """
    vault._require_write()
    if not vault.identity_v2_enabled:
        raise RuntimeError("Knowledge Identity v2 is not installed")
    if status not in {"split", "merged", "ambiguous"}:
        raise ValueError("lineage review status is invalid")
    if not confirm_reviewed:
        raise ValueError("lineage mapping requires explicit review confirmation")
    reviewer_id = normalize_text(reviewer_id)
    reason = normalize_text(reason)
    if not 1 <= len(reviewer_id) <= 200 or not 1 <= len(reason) <= 2_000:
        raise ValueError("lineage review requires bounded reviewer and reason")
    from_asset_ids = tuple(dict.fromkeys(from_asset_ids))
    to_asset_ids = tuple(dict.fromkeys(to_asset_ids))
    if (
        len(from_asset_ids) > _MAX_MAPPING_ASSETS
        or len(to_asset_ids) > _MAX_MAPPING_ASSETS
        or set(from_asset_ids) & set(to_asset_ids)
    ):
        raise ValueError("lineage review asset inventory is invalid or exceeds its bound")
    _validate_shape(status, from_asset_ids, to_asset_ids)

    try:
        vault.connection.execute("BEGIN IMMEDIATE")
        vault._require_healthy_integrity()
        from_assets = [_asset_identity(vault, asset_id) for asset_id in from_asset_ids]
        to_assets = [_asset_identity(vault, asset_id) for asset_id in to_asset_ids]
        from_revision_ids = tuple(item["asset_revision_id"] for item in from_assets)
        to_revision_ids = tuple(item["asset_revision_id"] for item in to_assets)
        involved_keys = tuple(
            sorted(
                {
                    item["knowledge_key"]
                    for item in (*from_assets, *to_assets)
                }
            )
        )
        source_revision_ids = tuple(
            sorted(
                {
                    source_revision_id
                    for item in (*from_assets, *to_assets)
                    for source_revision_id in item["source_revision_ids"]
                }
            )
        )
        review_id = stable_id(
            "lineagereview",
            vault.vault_id,
            status,
            canonical_json(list(from_revision_ids)),
            canonical_json(list(to_revision_ids)),
            reviewer_id,
            reason,
        )
        existing = vault.connection.execute(
            """
            SELECT lineage_id, knowledge_key, mapping_evidence_json, created_at
            FROM knowledge_lineage_v2
            WHERE status = ?
              AND from_asset_revision_ids_json = ?
              AND to_asset_revision_ids_json = ?
            ORDER BY knowledge_key, lineage_id
            """,
            (
                status,
                canonical_json(list(from_revision_ids)),
                canonical_json(list(to_revision_ids)),
            ),
        ).fetchall()
        replayed_rows = [
            row
            for row in existing
            if strict_json_loads(row["mapping_evidence_json"]).get("review_id")
            == review_id
        ]
        if replayed_rows:
            if {row["knowledge_key"] for row in replayed_rows} != set(involved_keys):
                raise RuntimeError("reviewed lineage mapping is only partially recorded")
            reviewed_at = replayed_rows[0]["created_at"]
            transition_ids = [row["lineage_id"] for row in replayed_rows]
            vault.connection.rollback()
            return {
                "schema_version": LINEAGE_REVIEW_SCHEMA,
                "vault_id": vault.vault_id,
                "review_id": review_id,
                "status": status,
                "from_assets": from_assets,
                "to_assets": to_assets,
                "knowledge_keys": list(involved_keys),
                "source_revision_ids": list(source_revision_ids),
                "transition_ids": transition_ids,
                "reviewer_id": reviewer_id,
                "reason": reason,
                "reviewed_at": reviewed_at,
                "approval_inherited": False,
                "replayed": True,
            }

        latest = vault.connection.execute(
            "SELECT MAX(created_at) FROM knowledge_lineage_v2"
        ).fetchone()[0]
        reviewed_at = _timestamp_after(latest)
        mapping_evidence = {
            "method": "explicit-human-lineage-mapping",
            "review_id": review_id,
            "reviewer_id": reviewer_id,
            "reason": reason,
            "approval_inherited": False,
            "from_asset_ids": list(from_asset_ids),
            "to_asset_ids": list(to_asset_ids),
            "from_knowledge_keys": [item["knowledge_key"] for item in from_assets],
            "to_knowledge_keys": [item["knowledge_key"] for item in to_assets],
            "source_revision_ids": list(source_revision_ids),
        }
        transition_ids = [
            record_lineage_transition(
                vault.connection,
                knowledge_key=knowledge_key,
                from_asset_revision_ids=from_revision_ids,
                to_asset_revision_ids=to_revision_ids,
                status=status,
                source_revision_id=source_revision_ids[0],
                mapping_evidence=mapping_evidence,
                created_at=reviewed_at,
            )
            for knowledge_key in involved_keys
        ]
        vault._append_identity_snapshot(
            reason="governance_recorded",
            source_revision_id=source_revision_ids[0],
        )
        vault.connection.commit()
    except BaseException:
        vault.connection.rollback()
        raise

    return {
        "schema_version": LINEAGE_REVIEW_SCHEMA,
        "vault_id": vault.vault_id,
        "review_id": review_id,
        "status": status,
        "from_assets": from_assets,
        "to_assets": to_assets,
        "knowledge_keys": list(involved_keys),
        "source_revision_ids": list(source_revision_ids),
        "transition_ids": transition_ids,
        "reviewer_id": reviewer_id,
        "reason": reason,
        "reviewed_at": reviewed_at,
        "approval_inherited": False,
        "replayed": False,
    }
