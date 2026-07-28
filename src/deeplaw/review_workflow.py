from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from .knowledge_identity import record_lineage_transition
from .knowledge_models import KnowledgeAsset, SourceReference, utc_now
from .knowledge_store import KnowledgeVault
from .util import canonical_json, has_instruction_risk, normalize_text, stable_id

REVIEW_TRANSFORM_SCHEMA = "deeplaw.knowledge-review-transform/v1"
ReviewTransformAction = Literal["edit", "split", "merge"]

_MAX_TRANSFORM_INPUTS = 20
_MAX_SPLIT_OUTPUTS = 20


def _next_lineage_timestamp(vault: KnowledgeVault) -> str:
    selected = datetime.fromisoformat(utc_now().replace("Z", "+00:00"))
    latest = vault.connection.execute(
        "SELECT MAX(created_at) FROM knowledge_lineage_v2"
    ).fetchone()[0]
    if latest is not None:
        previous = datetime.fromisoformat(str(latest).replace("Z", "+00:00"))
        if selected <= previous:
            selected = previous + timedelta(seconds=1)
    return selected.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _union_source_refs(assets: tuple[KnowledgeAsset, ...]) -> tuple[SourceReference, ...]:
    references: dict[tuple[str, str], SourceReference] = {}
    for asset in assets:
        for reference in asset.source_refs:
            key = (reference.source_id, reference.fragment_id)
            previous = references.get(key)
            if previous is not None and previous != reference:
                raise RuntimeError("Asset source references disagree for the same fragment")
            references[key] = reference
    if len(references) > 100:
        raise ValueError("workbench transformation exceeds 100 exact source references")
    return tuple(references[key] for key in sorted(references))


def _lineage_asset_view(
    asset: KnowledgeAsset,
    identity: dict[str, Any],
) -> dict[str, Any]:
    return {
        "asset_id": asset.asset_id,
        "asset_revision_id": identity["asset_revision_id"],
        "knowledge_key": identity["knowledge_key"],
        "title": asset.title,
        "status": asset.status,
        "source_revision_ids": list(identity["source_revision_ids"]),
        "source_refs": list(identity["source_refs"]),
    }


def _output_warnings(
    assets: tuple[KnowledgeAsset, ...],
    *,
    action: ReviewTransformAction,
    title: str,
    statement: str,
) -> tuple[str, ...]:
    required = [
        f"workbench {action} output requires independent approval",
    ]
    if has_instruction_risk(f"{title}\n{statement}"):
        required.append(
            "instruction-like or invisible control content detected; "
            "proposal requires explicit review"
        )
    inherited = [warning for asset in assets for warning in asset.warnings]
    return tuple(dict.fromkeys((*required, *inherited)))[:64]


def _generated_semantic_key(
    vault: KnowledgeVault,
    *,
    action: ReviewTransformAction,
    assets: tuple[KnowledgeAsset, ...],
    ordinal: int,
    source_bound: bool,
) -> str:
    prefix = "knowledge" if source_bound else "semantic"
    if action == "split":
        identity = (
            vault._asset_revision_identity(assets[0].asset_id)
            if source_bound
            else None
        )
        predecessor_key = (
            identity["knowledge_key"]
            if identity is not None
            else assets[0].semantic_key or assets[0].asset_id
        )
        return stable_id(
            prefix,
            vault.vault_id,
            "workbench-split/v1",
            predecessor_key,
            str(ordinal),
        )
    identities = (
        [vault._asset_revision_identity(asset.asset_id) for asset in assets]
        if source_bound
        else []
    )
    if source_bound and any(identity is None for identity in identities):
        raise RuntimeError("source-bound merge input has no Identity v2 binding")
    predecessor_keys = sorted(
        (
            identity["knowledge_key"]
            for identity in identities
            if identity is not None
        )
        if source_bound
        else (asset.semantic_key or asset.asset_id for asset in assets)
    )
    return stable_id(
        prefix,
        vault.vault_id,
        "workbench-merge/v1",
        canonical_json(predecessor_keys),
    )


def _validate_request(
    *,
    action: ReviewTransformAction,
    asset_ids: tuple[str, ...],
    reviewer_id: str,
    reason: str,
    confirm_reviewed: bool,
    title: str | None,
    statement: str | None,
    split_items: tuple[tuple[str, str], ...],
) -> tuple[str, str]:
    if action not in {"edit", "split", "merge"}:
        raise ValueError("review transformation is unsupported")
    if not confirm_reviewed:
        raise ValueError("review transformation requires explicit review confirmation")
    if (
        not asset_ids
        or len(asset_ids) > _MAX_TRANSFORM_INPUTS
        or len(set(asset_ids)) != len(asset_ids)
    ):
        raise ValueError("review transformation input inventory is invalid")
    normalized_reviewer = normalize_text(reviewer_id)
    normalized_reason = normalize_text(reason)
    if not 1 <= len(normalized_reviewer) <= 200:
        raise ValueError("review transformation requires a bounded reviewer")
    if not 1 <= len(normalized_reason) <= 2_000:
        raise ValueError("review transformation requires a bounded reason")
    if action == "edit" and (
        len(asset_ids) != 1 or title is None or statement is None or split_items
    ):
        raise ValueError("edit requires one input, title, and statement")
    if action == "split" and (
        len(asset_ids) != 1
        or title is not None
        or statement is not None
        or not 2 <= len(split_items) <= _MAX_SPLIT_OUTPUTS
    ):
        raise ValueError("split requires one input and two to 20 output items")
    if action == "merge" and (
        len(asset_ids) < 2 or title is None or statement is None or split_items
    ):
        raise ValueError("merge requires two to 20 inputs, title, and statement")
    output_values = (
        split_items if action == "split" else ((title or "", statement or ""),)
    )
    if any(
        not item_title.strip() or not item_statement.strip()
        for item_title, item_statement in output_values
    ):
        raise ValueError("review transformation output title and statement are required")
    return normalized_reviewer, normalized_reason


def transform_review_proposals(
    vault: KnowledgeVault,
    *,
    action: ReviewTransformAction,
    asset_ids: tuple[str, ...],
    reviewer_id: str,
    reason: str,
    confirm_reviewed: bool,
    title: str | None = None,
    statement: str | None = None,
    split_items: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    """Atomically create review-gated edit/split/merge proposals.

    Exact Source References and Identity v2 bindings are retained. Split/merge
    transitions are recorded under every involved Knowledge Key, but output
    approval is never inherited.
    """
    vault._require_write()
    vault._require_control()
    reviewer_id, reason = _validate_request(
        action=action,
        asset_ids=asset_ids,
        reviewer_id=reviewer_id,
        reason=reason,
        confirm_reviewed=confirm_reviewed,
        title=title,
        statement=statement,
        split_items=split_items,
    )
    try:
        vault.connection.execute("BEGIN IMMEDIATE")
        vault._require_healthy_integrity()
        assets = tuple(
            vault.get_asset(asset_id, include_inactive=True) for asset_id in asset_ids
        )
        if action == "edit":
            if assets[0].status not in {"active", "proposed", "quarantined"}:
                raise ValueError("only active or pending Assets can be edited")
        elif any(asset.status not in {"proposed", "quarantined"} for asset in assets):
            raise ValueError("workbench split/merge requires pending proposals")
        if action == "merge":
            assets = tuple(sorted(assets, key=lambda asset: asset.asset_id))
            first = assets[0]
            if any(
                (asset.kind, asset.memory_tier, asset.trust, asset.sensitivity)
                != (first.kind, first.memory_tier, first.trust, first.sensitivity)
                for asset in assets[1:]
            ):
                raise ValueError(
                    "merged proposals must share kind, tier, trust, and sensitivity"
                )

        source_binding_flags = tuple(bool(asset.source_refs) for asset in assets)
        if action == "merge" and len(set(source_binding_flags)) != 1:
            raise ValueError("source-bound and source-free proposals cannot be merged")
        source_bound = any(source_binding_flags)
        if source_bound and not vault.identity_v2_enabled:
            raise RuntimeError("source-bound transformation requires Identity v2")
        predecessor_identities = tuple(
            vault._asset_revision_identity(asset.asset_id) for asset in assets
        )
        if source_bound and any(identity is None for identity in predecessor_identities):
            raise RuntimeError(
                "source-bound transformation input has no exact Identity v2 binding"
            )

        if action == "split":
            output_specs = split_items
        else:
            assert title is not None and statement is not None
            output_specs = ((title, statement),)
        combined_refs = _union_source_refs(assets) if source_bound else ()
        combined_node_keys = tuple(
            dict.fromkeys(
                node_key
                for identity in predecessor_identities
                if identity is not None
                for node_key in identity["logical_node_keys"]
            )
        )
        created_at = utc_now()
        created_assets: list[KnowledgeAsset] = []
        created_identities: list[dict[str, Any]] = []
        for ordinal, (item_title, item_statement) in enumerate(output_specs, start=1):
            item_title = item_title.strip()
            item_statement = item_statement.strip()
            if action == "edit":
                semantic_key = assets[0].semantic_key
                supersedes_asset_id = (
                    assets[0].asset_id
                    if assets[0].status == "active"
                    else assets[0].supersedes_asset_id
                )
            else:
                semantic_key = _generated_semantic_key(
                    vault,
                    action=action,
                    assets=assets,
                    ordinal=ordinal,
                    source_bound=source_bound,
                )
                supersedes_asset_id = None
            output_refs = assets[0].source_refs if action in {"edit", "split"} else combined_refs
            first = assets[0]
            output_tags = (
                first.tags
                if action != "merge"
                else tuple(sorted({tag for asset in assets for tag in asset.tags}))
            )
            expiries = sorted(
                asset.expires_at for asset in assets if asset.expires_at is not None
            )
            output_expires_at = expiries[0] if expiries else None
            origin_inputs = "+".join(asset.asset_id for asset in assets)
            origin_uri = (
                f"deeplaw-workbench://{action}/{origin_inputs}/{ordinal}"
            )
            proposal, inserted = vault._insert_asset(
                kind=first.kind,
                memory_tier=first.memory_tier,
                title=item_title,
                statement=item_statement,
                semantic_key=semantic_key,
                status="quarantined",
                verification="source_bound" if output_refs else "unverified",
                trust=first.trust,
                sensitivity=first.sensitivity,
                source_refs=output_refs,
                tags=output_tags,
                warnings=_output_warnings(
                    assets,
                    action=action,
                    title=item_title,
                    statement=item_statement,
                ),
                expires_at=output_expires_at,
                supersedes_asset_id=supersedes_asset_id,
                origin_uri=origin_uri,
                created_at=created_at,
            )
            if not inserted:
                raise RuntimeError("review transformation output already exists")
            created_assets.append(proposal)
            identity = None
            if source_bound:
                if action == "edit":
                    predecessor_identity = predecessor_identities[0]
                    assert predecessor_identity is not None
                    knowledge_key = predecessor_identity["knowledge_key"]
                    predecessor_revision_ids = (
                        predecessor_identity["asset_revision_id"],
                    )
                    lineage_status: Literal["modified", "split", "merged"] | None = (
                        "modified"
                    )
                else:
                    knowledge_key = semantic_key
                    predecessor_revision_ids = ()
                    lineage_status = None
                identity = vault._register_asset_revision_in_transaction(
                    proposal,
                    knowledge_key=knowledge_key,
                    logical_node_keys=combined_node_keys,
                    predecessor_revision_ids=predecessor_revision_ids,
                    lineage_status=lineage_status,
                    mapping_evidence=(
                        {
                            "method": "explicit-human-workbench-edit",
                            "reviewer_id": reviewer_id,
                            "reason": reason,
                            "predecessor_asset_id": assets[0].asset_id,
                            "approval_inherited": False,
                        }
                        if action == "edit"
                        else None
                    ),
                    policy_id="deeplaw.workbench-transform/v1",
                    created_at=created_at,
                )
                created_identities.append(identity)
            vault._append_event(
                event_type="asset_revision_proposed",
                object_id=proposal.asset_id,
                payload={
                    "content_sha256": proposal.content_sha256,
                    "status": proposal.status,
                    "verification": proposal.verification,
                    "predecessor_asset_ids": [asset.asset_id for asset in assets],
                    "source_ref_count": len(proposal.source_refs),
                    "lineage_status": (
                        "modified"
                        if action == "edit"
                        else "split" if action == "split" else "merged"
                    ),
                    "transformation": action,
                    "approval_inherited": False,
                },
            )

        lineage_review: dict[str, Any] | None = None
        if source_bound and action in {"split", "merge"}:
            from_revision_ids = tuple(
                identity["asset_revision_id"]
                for identity in predecessor_identities
                if identity is not None
            )
            to_revision_ids = tuple(
                identity["asset_revision_id"] for identity in created_identities
            )
            lineage_status = "split" if action == "split" else "merged"
            involved_keys = tuple(
                sorted(
                    {
                        identity["knowledge_key"]
                        for identity in (*predecessor_identities, *created_identities)
                        if identity is not None
                    }
                )
            )
            source_revision_ids = tuple(
                sorted(
                    {
                        source_revision_id
                        for identity in (*predecessor_identities, *created_identities)
                        if identity is not None
                        for source_revision_id in identity["source_revision_ids"]
                    }
                )
            )
            review_id = stable_id(
                "lineagereview",
                vault.vault_id,
                lineage_status,
                canonical_json(list(from_revision_ids)),
                canonical_json(list(to_revision_ids)),
                reviewer_id,
                reason,
            )
            reviewed_at = _next_lineage_timestamp(vault)
            mapping_evidence = {
                "method": "explicit-human-workbench-transform",
                "review_id": review_id,
                "reviewer_id": reviewer_id,
                "reason": reason,
                "approval_inherited": False,
                "from_asset_ids": [asset.asset_id for asset in assets],
                "to_asset_ids": [asset.asset_id for asset in created_assets],
                "from_knowledge_keys": [
                    identity["knowledge_key"]
                    for identity in predecessor_identities
                    if identity is not None
                ],
                "to_knowledge_keys": [
                    identity["knowledge_key"] for identity in created_identities
                ],
                "source_revision_ids": list(source_revision_ids),
            }
            transition_ids = [
                record_lineage_transition(
                    vault.connection,
                    knowledge_key=knowledge_key,
                    from_asset_revision_ids=from_revision_ids,
                    to_asset_revision_ids=to_revision_ids,
                    status=lineage_status,
                    source_revision_id=source_revision_ids[0],
                    mapping_evidence=mapping_evidence,
                    created_at=reviewed_at,
                )
                for knowledge_key in involved_keys
            ]
            lineage_review = {
                "schema_version": "deeplaw.knowledge-lineage-review/v1",
                "vault_id": vault.vault_id,
                "review_id": review_id,
                "status": lineage_status,
                "from_assets": [
                    _lineage_asset_view(asset, identity)
                    for asset, identity in zip(
                        assets, predecessor_identities, strict=True
                    )
                    if identity is not None
                ],
                "to_assets": [
                    _lineage_asset_view(asset, identity)
                    for asset, identity in zip(
                        created_assets, created_identities, strict=True
                    )
                ],
                "knowledge_keys": list(involved_keys),
                "source_revision_ids": list(source_revision_ids),
                "transition_ids": transition_ids,
                "reviewed_at": reviewed_at,
                "reviewer_id": reviewer_id,
                "reason": reason,
                "approval_inherited": False,
                "replayed": False,
            }

        rejected: list[dict[str, Any]] = []
        if action != "edit" or assets[0].status in {"proposed", "quarantined"}:
            for asset in assets:
                result = vault._reject_asset_in_transaction(
                    asset.asset_id,
                    reason=(
                        f"Replaced by reviewed workbench {action}: {reason}"
                    ),
                    reviewer_id=reviewer_id,
                    policy_id="deeplaw.workbench-transform/v1",
                )
                rejected.append(
                    {
                        "asset_id": asset.asset_id,
                        "decision": "reject",
                        "status": "revoked",
                        "review_receipt": result["review_receipt"],
                    }
                )

        identity_inventory = [
            identity
            for identity in (*predecessor_identities, *created_identities)
            if identity is not None
        ]
        if identity_inventory:
            source_revision_ids = sorted(
                {
                    source_revision_id
                    for identity in identity_inventory
                    for source_revision_id in identity["source_revision_ids"]
                }
            )
            vault._append_identity_snapshot(
                reason="asset_revision_proposed",
                source_revision_id=source_revision_ids[0],
            )
        vault.connection.commit()
    except BaseException:
        vault.connection.rollback()
        raise

    return {
        "schema_version": REVIEW_TRANSFORM_SCHEMA,
        "vault_id": vault.vault_id,
        "action": action,
        "input_asset_ids": [asset.asset_id for asset in assets],
        "created_proposals": [asset.to_dict() for asset in created_assets],
        "created_proposal_count": len(created_assets),
        "decisions": rejected,
        "lineage_review": lineage_review,
        "review_required": True,
        "approval_inherited": False,
        "atomic": True,
        "revision": vault.revision,
        "audit_head": vault.audit_head,
    }
