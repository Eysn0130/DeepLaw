from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from .knowledge_identity import record_governance_revision, record_relation_revision
from .knowledge_models import utc_now
from .knowledge_store import KnowledgeVault
from .util import strict_json_loads

RELATION_CARRY_FORWARD_PLAN_SCHEMA = "deeplaw.relation-carry-forward-plan/v1"
RELATION_CARRY_FORWARD_RESULT_SCHEMA = "deeplaw.relation-carry-forward-result/v1"
RELATION_CARRY_FORWARD_REVIEW_SCHEMA = "deeplaw.relation-carry-forward-review/v1"

_POLICY_PREFIX = "deeplaw.relation-carry-forward/"
_MAX_RELATIONS = 500


def _timestamp_after(*values: str) -> str:
    selected = utc_now()
    selected_time = datetime.fromisoformat(selected.replace("Z", "+00:00"))
    for value in values:
        value_time = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if selected_time <= value_time:
            selected_time = value_time + timedelta(seconds=1)
    return selected_time.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _latest_relation_rows(vault: KnowledgeVault, *, limit: int) -> tuple[list[Any], bool]:
    rows = vault.connection.execute(
        """
        WITH latest AS (
            SELECT relation_revisions_v2.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY relation_key
                       ORDER BY observed_at DESC, relation_revision_id DESC
                   ) AS relation_rank
            FROM relation_revisions_v2
        )
        SELECT * FROM latest
        WHERE relation_rank = 1
        ORDER BY observed_at DESC, relation_revision_id
        LIMIT ?
        """,
        (limit + 1,),
    ).fetchall()
    return list(rows[:limit]), len(rows) > limit


def _active_endpoint_revisions(vault: KnowledgeVault, knowledge_key: str) -> list[str]:
    rows = vault.connection.execute(
        """
        SELECT DISTINCT knowledge_revisions_v2.asset_revision_id
        FROM knowledge_revisions_v2
        JOIN asset_revision_bindings_v2 USING(asset_revision_id)
        JOIN assets
          ON assets.asset_id = asset_revision_bindings_v2.legacy_asset_id
        WHERE knowledge_revisions_v2.knowledge_key = ?
          AND assets.status = 'active'
          AND (assets.expires_at IS NULL OR assets.expires_at > ?)
        ORDER BY knowledge_revisions_v2.asset_revision_id
        """,
        (knowledge_key, utc_now()),
    ).fetchall()
    return [row[0] for row in rows]


def _reviewed_cross_key_successors(
    vault: KnowledgeVault,
    previous_revision_id: str,
) -> tuple[str, list[str]] | None:
    rows = vault.connection.execute(
        """
        SELECT DISTINCT knowledge_lineage_v2.status,
                        knowledge_lineage_v2.from_asset_revision_ids_json,
                        knowledge_lineage_v2.to_asset_revision_ids_json,
                        knowledge_lineage_v2.created_at,
                        knowledge_lineage_v2.lineage_id
        FROM knowledge_lineage_v2,
             json_each(knowledge_lineage_v2.from_asset_revision_ids_json) AS predecessor
        WHERE knowledge_lineage_v2.status IN ('split', 'merged', 'ambiguous')
          AND predecessor.value = ?
        ORDER BY created_at DESC, lineage_id DESC
        LIMIT 100
        """,
        (previous_revision_id,),
    ).fetchall()
    for row in rows:
        previous = strict_json_loads(row["from_asset_revision_ids_json"])
        successors = strict_json_loads(row["to_asset_revision_ids_json"])
        if previous_revision_id not in previous:
            continue
        active: list[str] = []
        for revision_id in successors:
            candidate = vault.connection.execute(
                """
                SELECT 1
                FROM asset_revision_bindings_v2
                JOIN assets
                  ON assets.asset_id = asset_revision_bindings_v2.legacy_asset_id
                WHERE asset_revision_bindings_v2.asset_revision_id = ?
                  AND assets.status = 'active'
                  AND (assets.expires_at IS NULL OR assets.expires_at > ?)
                LIMIT 1
                """,
                (revision_id, utc_now()),
            ).fetchone()
            if candidate is not None:
                active.append(revision_id)
        return str(row["status"]), sorted(set(active))
    return None


def _lineage_status(
    vault: KnowledgeVault,
    *,
    knowledge_key: str,
    previous_revision_id: str,
    current_revision_id: str,
) -> str | None:
    if previous_revision_id == current_revision_id:
        return "retained"
    rows = vault.connection.execute(
        """
        SELECT status, from_asset_revision_ids_json, to_asset_revision_ids_json
        FROM knowledge_lineage_v2
        WHERE knowledge_key = ?
        ORDER BY created_at DESC, lineage_id DESC
        """,
        (knowledge_key,),
    ).fetchall()
    for row in rows:
        previous = strict_json_loads(row["from_asset_revision_ids_json"])
        current = strict_json_loads(row["to_asset_revision_ids_json"])
        if previous_revision_id in previous and current_revision_id in current:
            return str(row["status"])
    return None


def _fragment_logical_keys(vault: KnowledgeVault, fragment_revision_id: str) -> tuple[str, ...]:
    rows = vault.connection.execute(
        """
        SELECT source_ir_nodes_v2.logical_node_key
        FROM fragment_node_membership_v2
        JOIN source_ir_nodes_v2 USING(node_id)
        WHERE fragment_node_membership_v2.fragment_revision_id = ?
        ORDER BY fragment_node_membership_v2.node_ordinal
        """,
        (fragment_revision_id,),
    ).fetchall()
    return tuple(row[0] for row in rows)


def _active_source_successor(
    vault: KnowledgeVault,
    source_revision_id: str,
) -> tuple[str, str] | None:
    source = vault.connection.execute(
        """
        SELECT source_revisions_v2.source_key
        FROM source_revisions_v2
        WHERE source_revision_id = ?
        """,
        (source_revision_id,),
    ).fetchone()
    if source is None:
        return None
    rows = vault.connection.execute(
        """
        SELECT source_revision_bindings_v2.source_revision_id,
               source_revision_bindings_v2.legacy_source_id
        FROM source_lifecycle
        JOIN source_revision_bindings_v2
          ON source_revision_bindings_v2.legacy_source_id = source_lifecycle.source_id
        JOIN source_revisions_v2 USING(source_revision_id)
        WHERE source_revisions_v2.source_key = ?
          AND source_lifecycle.status = 'active'
        ORDER BY source_lifecycle.activated_at DESC, source_lifecycle.source_id
        """,
        (source["source_key"],),
    ).fetchall()
    if len(rows) != 1:
        return None
    return rows[0]["source_revision_id"], rows[0]["legacy_source_id"]


def _mapped_evidence_reference(
    vault: KnowledgeVault,
    reference: dict[str, Any],
) -> tuple[dict[str, str] | None, str | None]:
    if set(reference) != {
        "source_revision_id",
        "fragment_revision_id",
        "locator",
        "quote_sha256",
    } or not all(isinstance(value, str) for value in reference.values()):
        return None, "relation_evidence_invalid"
    source_revision_id = reference.get("source_revision_id")
    fragment_revision_id = reference.get("fragment_revision_id")
    if not isinstance(source_revision_id, str) or not isinstance(fragment_revision_id, str):
        return None, "relation_evidence_invalid"
    successor = _active_source_successor(vault, source_revision_id)
    if successor is None:
        return None, f"relation_evidence_has_no_unique_active_source:{source_revision_id}"
    current_source_revision_id, current_source_id = successor
    if current_source_revision_id == source_revision_id:
        return {
            "source_revision_id": source_revision_id,
            "fragment_revision_id": fragment_revision_id,
            "locator": reference["locator"],
            "quote_sha256": reference["quote_sha256"],
        }, None

    exact = vault.connection.execute(
        """
        SELECT legacy_fragment_bindings_v2.fragment_revision_id,
               source_fragments.locator, source_fragments.text_sha256
        FROM legacy_fragment_bindings_v2
        JOIN source_fragments USING(fragment_id)
        WHERE legacy_fragment_bindings_v2.legacy_source_id = ?
          AND source_fragments.locator = ?
          AND source_fragments.text_sha256 = ?
        ORDER BY legacy_fragment_bindings_v2.fragment_revision_id
        """,
        (current_source_id, reference.get("locator"), reference.get("quote_sha256")),
    ).fetchall()
    candidates = list(exact)
    if len(candidates) != 1:
        prior_keys = _fragment_logical_keys(vault, fragment_revision_id)
        content_matches = vault.connection.execute(
            """
            SELECT legacy_fragment_bindings_v2.fragment_revision_id,
                   source_fragments.locator, source_fragments.text_sha256
            FROM legacy_fragment_bindings_v2
            JOIN source_fragments USING(fragment_id)
            WHERE legacy_fragment_bindings_v2.legacy_source_id = ?
              AND source_fragments.text_sha256 = ?
            ORDER BY legacy_fragment_bindings_v2.fragment_revision_id
            LIMIT 101
            """,
            (current_source_id, reference.get("quote_sha256")),
        ).fetchall()
        candidates = [
            row
            for row in content_matches
            if prior_keys
            and _fragment_logical_keys(vault, row["fragment_revision_id"]) == prior_keys
        ]
    if len(candidates) != 1:
        return None, f"relation_evidence_mapping_ambiguous:{fragment_revision_id}"
    selected = candidates[0]
    return {
        "source_revision_id": current_source_revision_id,
        "fragment_revision_id": selected["fragment_revision_id"],
        "locator": selected["locator"],
        "quote_sha256": selected["text_sha256"],
    }, None


def _candidate_for_row(vault: KnowledgeVault, row: Any) -> dict[str, Any]:
    endpoint_specs = (
        (
            "subject",
            row["subject_knowledge_key"],
            row["subject_asset_revision_id"],
        ),
        (
            "object",
            row["object_knowledge_key"],
            row["object_asset_revision_id"],
        ),
    )
    endpoint_lineage: dict[str, dict[str, Any]] = {}
    blocked_reasons: list[str] = []
    current_endpoint_ids: dict[str, str] = {}
    for role, knowledge_key, previous_revision_id in endpoint_specs:
        cross_key = _reviewed_cross_key_successors(vault, previous_revision_id)
        if cross_key is not None:
            lineage_status, active_revisions = cross_key
            blocked_reasons.append(f"{role}_endpoint_{lineage_status}:{knowledge_key}")
            endpoint_lineage[role] = {
                "knowledge_key": knowledge_key,
                "previous_asset_revision_id": previous_revision_id,
                "current_asset_revision_ids": active_revisions,
                "lineage_status": lineage_status,
            }
            continue
        active_revisions = _active_endpoint_revisions(vault, knowledge_key)
        if len(active_revisions) != 1:
            reason = "deleted" if not active_revisions else "ambiguous"
            blocked_reasons.append(f"{role}_endpoint_{reason}:{knowledge_key}")
            endpoint_lineage[role] = {
                "knowledge_key": knowledge_key,
                "previous_asset_revision_id": previous_revision_id,
                "current_asset_revision_ids": active_revisions,
                "lineage_status": reason,
            }
            continue
        current_revision_id = active_revisions[0]
        lineage_status = _lineage_status(
            vault,
            knowledge_key=knowledge_key,
            previous_revision_id=previous_revision_id,
            current_revision_id=current_revision_id,
        )
        if lineage_status is None:
            blocked_reasons.append(f"{role}_endpoint_lineage_missing:{knowledge_key}")
        elif lineage_status in {"split", "merged", "ambiguous", "deleted"}:
            blocked_reasons.append(f"{role}_endpoint_{lineage_status}:{knowledge_key}")
        current_endpoint_ids[role] = current_revision_id
        endpoint_lineage[role] = {
            "knowledge_key": knowledge_key,
            "previous_asset_revision_id": previous_revision_id,
            "current_asset_revision_ids": active_revisions,
            "lineage_status": lineage_status,
        }

    evidence_refs = strict_json_loads(row["evidence_refs_json"])
    mapped_evidence: list[dict[str, str]] = []
    if not isinstance(evidence_refs, list):
        blocked_reasons.append("relation_evidence_invalid")
    else:
        for reference in evidence_refs:
            if not isinstance(reference, dict):
                blocked_reasons.append("relation_evidence_invalid")
                continue
            mapped, reason = _mapped_evidence_reference(vault, reference)
            if reason is not None:
                blocked_reasons.append(reason)
            elif mapped is not None:
                mapped_evidence.append(mapped)

    lineage_statuses = {
        details["lineage_status"]
        for details in endpoint_lineage.values()
        if details["lineage_status"] != "retained"
    }
    changed = any(
        details["previous_asset_revision_id"]
        not in details["current_asset_revision_ids"]
        for details in endpoint_lineage.values()
    )
    evidence_changed = (
        isinstance(evidence_refs, list)
        and len(mapped_evidence) == len(evidence_refs)
        and mapped_evidence != evidence_refs
    )
    review_mode = (
        "carry_forward"
        if lineage_statuses.issubset({"unchanged"})
        else "full_review"
    )
    return {
        "relation_key": row["relation_key"],
        "previous_relation_revision_id": row["relation_revision_id"],
        "predicate": row["predicate"],
        "subject_knowledge_key": row["subject_knowledge_key"],
        "object_knowledge_key": row["object_knowledge_key"],
        "subject_asset_revision_id": current_endpoint_ids.get("subject"),
        "object_asset_revision_id": current_endpoint_ids.get("object"),
        "endpoint_lineage": endpoint_lineage,
        "evidence_refs": mapped_evidence,
        "event_time": row["event_time"],
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "review_mode": review_mode,
        "changed": changed or evidence_changed,
        "blocked_reasons": list(dict.fromkeys(blocked_reasons)),
    }


def pending_relation_carry_forward(
    vault: KnowledgeVault,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not 1 <= limit <= _MAX_RELATIONS:
        raise ValueError("relation carry-forward limit must be between 1 and 500")
    count = vault.connection.execute(
        """
        WITH latest AS (
            SELECT relation_revisions_v2.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY relation_key
                       ORDER BY observed_at DESC, relation_revision_id DESC
                   ) AS relation_rank
            FROM relation_revisions_v2
        ), latest_governance AS (
            SELECT governance_revisions_v2.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY subject_id
                       ORDER BY recorded_at DESC, governance_revision DESC
                   ) AS governance_rank
            FROM governance_revisions_v2
            WHERE subject_kind = 'relation_revision'
        )
        SELECT COUNT(*)
        FROM latest
        JOIN latest_governance
          ON latest_governance.subject_id = latest.relation_revision_id
         AND latest_governance.governance_rank = 1
        WHERE latest.relation_rank = 1
          AND latest.status = 'proposed'
          AND latest_governance.policy_id LIKE ?
        """,
        (f"{_POLICY_PREFIX}%",),
    ).fetchone()[0]
    rows = vault.connection.execute(
        """
        WITH latest AS (
            SELECT relation_revisions_v2.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY relation_key
                       ORDER BY observed_at DESC, relation_revision_id DESC
                   ) AS relation_rank
            FROM relation_revisions_v2
        ), latest_governance AS (
            SELECT governance_revisions_v2.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY subject_id
                       ORDER BY recorded_at DESC, governance_revision DESC
                   ) AS governance_rank
            FROM governance_revisions_v2
            WHERE subject_kind = 'relation_revision'
        )
        SELECT latest.*, latest_governance.policy_id
        FROM latest
        JOIN latest_governance
          ON latest_governance.subject_id = latest.relation_revision_id
         AND latest_governance.governance_rank = 1
        WHERE latest.relation_rank = 1
          AND latest.status = 'proposed'
          AND latest_governance.policy_id LIKE ?
        ORDER BY latest.observed_at, latest.relation_revision_id
        LIMIT ?
        """,
        (f"{_POLICY_PREFIX}%", limit),
    ).fetchall()
    items = [
        {
            "relation_key": row["relation_key"],
            "relation_revision_id": row["relation_revision_id"],
            "subject_knowledge_key": row["subject_knowledge_key"],
            "object_knowledge_key": row["object_knowledge_key"],
            "subject_asset_revision_id": row["subject_asset_revision_id"],
            "object_asset_revision_id": row["object_asset_revision_id"],
            "predicate": row["predicate"],
            "evidence_refs": strict_json_loads(row["evidence_refs_json"]),
            "review_mode": row["policy_id"].removeprefix(_POLICY_PREFIX).removesuffix("/v1"),
            "observed_at": row["observed_at"],
            "approval_inherited": False,
        }
        for row in rows
    ]
    return {
        "schema_version": "deeplaw.relation-carry-forward-queue/v1",
        "vault_id": vault.vault_id,
        "total": count,
        "items": items,
        "truncated": count > len(items),
    }


def plan_relation_carry_forward(
    vault: KnowledgeVault,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    if not vault.identity_v2_enabled:
        raise RuntimeError("Knowledge Identity v2 is not installed")
    if isinstance(limit, bool) or not 1 <= limit <= _MAX_RELATIONS:
        raise ValueError("relation carry-forward limit must be between 1 and 500")
    rows, truncated = _latest_relation_rows(vault, limit=limit)
    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    already_current_count = 0
    for row in rows:
        if row["status"] == "proposed":
            continue
        if row["status"] != "active":
            continue
        candidate = _candidate_for_row(vault, row)
        if not candidate["changed"] and not candidate["blocked_reasons"]:
            already_current_count += 1
        elif candidate["blocked_reasons"]:
            blocked.append(candidate)
        else:
            candidates.append(candidate)
    pending = pending_relation_carry_forward(vault, limit=limit)
    return {
        "schema_version": RELATION_CARRY_FORWARD_PLAN_SCHEMA,
        "vault_id": vault.vault_id,
        "candidate_count": len(candidates),
        "carry_forward_candidate_count": sum(
            item["review_mode"] == "carry_forward" for item in candidates
        ),
        "full_review_candidate_count": sum(
            item["review_mode"] == "full_review" for item in candidates
        ),
        "blocked_count": len(blocked),
        "already_current_count": already_current_count,
        "pending_candidate_count": pending["total"],
        "candidates": candidates,
        "blocked": blocked,
        "pending_candidates": pending["items"],
        "truncated": truncated or pending["truncated"],
        "automatic_activation": False,
    }


def propose_relation_carry_forward(
    vault: KnowledgeVault,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    if vault.read_only:
        raise RuntimeError("relation carry-forward proposal requires a writable vault")
    try:
        vault.connection.execute("BEGIN IMMEDIATE")
        vault._require_healthy_integrity()
        plan = plan_relation_carry_forward(vault, limit=limit)
        if not plan["candidates"]:
            vault.connection.rollback()
            return {
                "schema_version": RELATION_CARRY_FORWARD_RESULT_SCHEMA,
                "vault_id": vault.vault_id,
                "created_count": 0,
                "created_candidates": [],
                "blocked": plan["blocked"],
                "pending": pending_relation_carry_forward(vault, limit=limit),
                "revision": vault.revision,
                "audit_head": vault.audit_head,
            }
        created: list[dict[str, Any]] = []
        source_revision_ids: set[str] = set()
        for candidate in plan["candidates"]:
            previous = vault.connection.execute(
                "SELECT observed_at FROM relation_revisions_v2 WHERE relation_revision_id = ?",
                (candidate["previous_relation_revision_id"],),
            ).fetchone()
            if previous is None:
                raise RuntimeError("relation changed while carry-forward was being planned")
            observed_at = _timestamp_after(previous["observed_at"])
            relation = record_relation_revision(
                vault.connection,
                vault_id=vault.vault_id,
                legacy_relation_id=None,
                subject_knowledge_key=candidate["subject_knowledge_key"],
                object_knowledge_key=candidate["object_knowledge_key"],
                subject_asset_revision_id=candidate["subject_asset_revision_id"],
                object_asset_revision_id=candidate["object_asset_revision_id"],
                predicate=candidate["predicate"],
                evidence_refs=candidate["evidence_refs"],
                status="proposed",
                event_time=candidate["event_time"],
                valid_from=candidate["valid_from"],
                valid_to=candidate["valid_to"],
                observed_at=observed_at,
                reviewed_at=None,
                ingest_time=observed_at,
            )
            prior_governance = vault.connection.execute(
                """
                SELECT sensitivity FROM governance_revisions_v2
                WHERE subject_kind = 'relation_revision' AND subject_id = ?
                ORDER BY recorded_at DESC, governance_revision DESC LIMIT 1
                """,
                (candidate["previous_relation_revision_id"],),
            ).fetchone()
            record_governance_revision(
                vault.connection,
                subject_kind="relation_revision",
                subject_id=relation["relation_revision_id"],
                trust="user_provided",
                sensitivity=(
                    prior_governance["sensitivity"]
                    if prior_governance is not None
                    else "private"
                ),
                policy_id=f"{_POLICY_PREFIX}{candidate['review_mode']}/v1",
                review_status="unreviewed",
                lifecycle_status="proposed",
                activation_status="inactive",
                reviewer_id=None,
                recorded_at=observed_at,
            )
            source_revision_ids.update(
                reference["source_revision_id"] for reference in candidate["evidence_refs"]
            )
            created.append(
                {
                    **relation,
                    "previous_relation_revision_id": candidate[
                        "previous_relation_revision_id"
                    ],
                    "review_mode": candidate["review_mode"],
                    "status": "proposed",
                    "approval_inherited": False,
                }
            )
        revision, audit_head = vault._append_identity_snapshot(
            reason="relation_recorded",
            source_revision_id=(
                next(iter(source_revision_ids)) if len(source_revision_ids) == 1 else None
            ),
        )
        vault.connection.commit()
    except BaseException:
        vault.connection.rollback()
        raise
    return {
        "schema_version": RELATION_CARRY_FORWARD_RESULT_SCHEMA,
        "vault_id": vault.vault_id,
        "created_count": len(created),
        "created_candidates": created,
        "blocked": plan["blocked"],
        "pending": pending_relation_carry_forward(vault, limit=limit),
        "revision": revision,
        "audit_head": audit_head,
    }


def review_relation_carry_forward(
    vault: KnowledgeVault,
    *,
    relation_revision_id: str,
    decision: Literal["approve", "reject"],
    confirm_reviewed: bool,
    reviewer_id: str = "local-operator",
    reason: str = "Reviewed the relation carry-forward candidate.",
) -> dict[str, Any]:
    if vault.read_only:
        raise RuntimeError("relation carry-forward review requires a writable vault")
    if decision not in {"approve", "reject"}:
        raise ValueError("relation carry-forward decision is invalid")
    if not confirm_reviewed:
        raise ValueError("relation carry-forward review requires explicit confirmation")
    if not reviewer_id.strip() or not reason.strip():
        raise ValueError("relation carry-forward review requires reviewer and reason")
    try:
        vault.connection.execute("BEGIN IMMEDIATE")
        vault._require_healthy_integrity()
        candidate = vault.connection.execute(
            "SELECT * FROM relation_revisions_v2 WHERE relation_revision_id = ?",
            (relation_revision_id,),
        ).fetchone()
        if candidate is None or candidate["status"] != "proposed":
            raise KeyError("relation carry-forward candidate is unavailable")
        latest = vault.connection.execute(
            """
            SELECT relation_revision_id FROM relation_revisions_v2
            WHERE relation_key = ?
            ORDER BY observed_at DESC, relation_revision_id DESC LIMIT 1
            """,
            (candidate["relation_key"],),
        ).fetchone()
        if latest is None or latest["relation_revision_id"] != relation_revision_id:
            raise RuntimeError("relation carry-forward candidate is stale")
        candidate_governance = vault.connection.execute(
            """
            SELECT * FROM governance_revisions_v2
            WHERE subject_kind = 'relation_revision' AND subject_id = ?
            ORDER BY recorded_at DESC, governance_revision DESC LIMIT 1
            """,
            (relation_revision_id,),
        ).fetchone()
        if (
            candidate_governance is None
            or not candidate_governance["policy_id"].startswith(_POLICY_PREFIX)
            or candidate_governance["review_status"] != "unreviewed"
        ):
            raise RuntimeError("relation proposal is not a carry-forward candidate")
        if decision == "approve":
            for role in ("subject", "object"):
                knowledge_key = candidate[f"{role}_knowledge_key"]
                expected_revision = candidate[f"{role}_asset_revision_id"]
                if _active_endpoint_revisions(vault, knowledge_key) != [expected_revision]:
                    raise RuntimeError("relation carry-forward endpoint changed during review")
            evidence_refs = strict_json_loads(candidate["evidence_refs_json"])
            for reference in evidence_refs:
                successor = _active_source_successor(vault, reference["source_revision_id"])
                if successor is None or successor[0] != reference["source_revision_id"]:
                    raise RuntimeError("relation carry-forward evidence changed during review")
        else:
            evidence_refs = strict_json_loads(candidate["evidence_refs_json"])
        observed_at = _timestamp_after(
            candidate["observed_at"],
            candidate_governance["recorded_at"],
        )
        status = "active" if decision == "approve" else "revoked"
        reviewed = record_relation_revision(
            vault.connection,
            vault_id=vault.vault_id,
            legacy_relation_id=None,
            subject_knowledge_key=candidate["subject_knowledge_key"],
            object_knowledge_key=candidate["object_knowledge_key"],
            subject_asset_revision_id=candidate["subject_asset_revision_id"],
            object_asset_revision_id=candidate["object_asset_revision_id"],
            predicate=candidate["predicate"],
            evidence_refs=evidence_refs,
            status=status,
            event_time=candidate["event_time"],
            valid_from=candidate["valid_from"],
            valid_to=candidate["valid_to"],
            observed_at=observed_at,
            reviewed_at=observed_at,
            ingest_time=observed_at,
        )
        record_governance_revision(
            vault.connection,
            subject_kind="relation_revision",
            subject_id=reviewed["relation_revision_id"],
            trust="user_provided",
            sensitivity=candidate_governance["sensitivity"],
            policy_id="deeplaw.local-relation-review/v2",
            review_status="human_verified",
            lifecycle_status=status,
            activation_status="active" if decision == "approve" else "inactive",
            revoked_at=observed_at if decision == "reject" else None,
            export_allowed=False,
            reviewer_id=reviewer_id.strip(),
            recorded_at=observed_at,
        )
        source_revision_ids = {
            reference["source_revision_id"] for reference in evidence_refs
        }
        revision, audit_head = vault._append_identity_snapshot(
            reason="relation_recorded",
            source_revision_id=(
                next(iter(source_revision_ids)) if len(source_revision_ids) == 1 else None
            ),
        )
        vault.connection.commit()
    except BaseException:
        vault.connection.rollback()
        raise
    return {
        "schema_version": RELATION_CARRY_FORWARD_REVIEW_SCHEMA,
        "vault_id": vault.vault_id,
        "relation_key": candidate["relation_key"],
        "candidate_relation_revision_id": relation_revision_id,
        "relation_revision_id": reviewed["relation_revision_id"],
        "decision": decision,
        "status": status,
        "reviewer_id": reviewer_id.strip(),
        "reason": reason.strip(),
        "approval_inherited": False,
        "revision": revision,
        "audit_head": audit_head,
    }


def review_all_relation_carry_forward(
    vault: KnowledgeVault,
    *,
    decision: Literal["approve", "reject"],
    confirm_reviewed: bool,
    reviewer_id: str,
    reason: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    queue = pending_relation_carry_forward(vault, limit=limit)
    return [
        review_relation_carry_forward(
            vault,
            relation_revision_id=item["relation_revision_id"],
            decision=decision,
            confirm_reviewed=confirm_reviewed,
            reviewer_id=reviewer_id,
            reason=reason,
        )
        for item in queue["items"]
    ]
