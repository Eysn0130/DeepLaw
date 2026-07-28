from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Literal

from .knowledge_inbox import list_inbox_artifacts
from .knowledge_store import KnowledgeVault
from .lineage_workflow import review_lineage_mapping
from .relation_workflow import (
    pending_relation_carry_forward,
    review_relation_carry_forward,
)
from .retrieval_fabric import recall
from .retrieval_profiles import load_active_retrieval_profile
from .review_workflow import transform_review_proposals
from .util import canonical_json, sha256_bytes, strict_json_loads

WORKBENCH_SCHEMA = "deeplaw.operator-workbench/v1"
ReviewAction = Literal["approve", "reject", "edit", "split", "merge"]


def _source_diff_pair(
    versions: list[dict[str, Any]],
) -> tuple[str, str] | None:
    pending = [source for source in versions if source["status"] == "pending"]
    if len(pending) > 1:
        return None
    active = [source for source in versions if source["status"] == "active"]
    if len(pending) == 1:
        latest = pending[0]
    elif len(active) == 1:
        latest = active[0]
    else:
        predecessor_ids = {
            source["previous_source_id"]
            for source in versions
            if source["previous_source_id"] is not None
        }
        heads = [
            source for source in versions if source["source_id"] not in predecessor_ids
        ]
        if len(heads) != 1:
            return None
        latest = heads[0]
    previous_source_id = latest["previous_source_id"]
    if previous_source_id is None:
        return None
    return str(previous_source_id), str(latest["source_id"])


def _lineage_mapping_inventory(
    vault: KnowledgeVault,
    *,
    limit: int = 200,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ValueError("lineage mapping inventory limit must be between 1 and 500")
    rows = vault.connection.execute(
        """
        SELECT assets.asset_id, assets.title, assets.status,
               knowledge_revisions_v2.knowledge_key,
               asset_revision_bindings_v2.asset_revision_id,
               sources.title AS source_title
        FROM asset_revision_bindings_v2
        JOIN assets
          ON assets.asset_id = asset_revision_bindings_v2.legacy_asset_id
        JOIN knowledge_revisions_v2 USING(asset_revision_id)
        JOIN sources
          ON sources.source_id = asset_revision_bindings_v2.legacy_source_id
        WHERE json_array_length(assets.source_refs_json) > 0
        ORDER BY assets.created_at, assets.asset_id
        LIMIT ?
        """,
        (limit + 1,),
    ).fetchall()
    items = [
        {
            "position": position,
            "asset_id": row["asset_id"],
            "asset_revision_id": row["asset_revision_id"],
            "knowledge_key": row["knowledge_key"],
            "title": row["title"],
            "status": row["status"],
            "source_title": row["source_title"],
        }
        for position, row in enumerate(rows[:limit], start=1)
    ]
    return {
        "items": items,
        "count": len(items),
        "truncated": len(rows) > limit,
    }


def operator_review_lineage_by_selection(
    vault_path: str | Path,
    *,
    status: Literal["split", "merged", "ambiguous"],
    from_positions: tuple[int, ...],
    to_positions: tuple[int, ...],
    reviewer_id: str,
    reason: str,
    confirm_reviewed: bool,
) -> dict[str, Any]:
    """Review a lineage mapping by bounded Workbench positions, not internal IDs."""
    if (
        not from_positions
        or not to_positions
        or len(from_positions) > 20
        or len(to_positions) > 20
        or any(isinstance(value, bool) or value < 1 for value in (*from_positions, *to_positions))
    ):
        raise ValueError("Workbench lineage selection is invalid or exceeds its bound")
    with KnowledgeVault(vault_path, read_only=False) as vault:
        inventory = _lineage_mapping_inventory(vault, limit=500)
        by_position = {item["position"]: item for item in inventory["items"]}
        try:
            from_asset_ids = tuple(by_position[value]["asset_id"] for value in from_positions)
            to_asset_ids = tuple(by_position[value]["asset_id"] for value in to_positions)
        except KeyError as error:
            raise ValueError("Workbench lineage selection is no longer available") from error
        result = review_lineage_mapping(
            vault,
            status=status,
            from_asset_ids=from_asset_ids,
            to_asset_ids=to_asset_ids,
            reviewer_id=reviewer_id,
            reason=reason,
            confirm_reviewed=confirm_reviewed,
        )
    return {
        **result,
        "selection": {
            "from_positions": list(from_positions),
            "to_positions": list(to_positions),
            "internal_ids_copied_by_operator": False,
        },
    }


def review_side_by_side(vault_path: str | Path, asset_id: str) -> dict[str, Any]:
    with KnowledgeVault(vault_path, read_only=True) as vault:
        asset = vault.get_asset(asset_id, include_inactive=True)
        evidence = []
        for reference in asset.source_refs[:20]:
            fragment = vault.get_fragment(reference.fragment_id)
            evidence.append(
                {
                    "source_id": reference.source_id,
                    "source_title": fragment["source_title"],
                    "fragment_id": reference.fragment_id,
                    "locator": reference.locator,
                    "quote_sha256": reference.quote_sha256,
                    "text": fragment["text"][:4_000],
                    "text_truncated": len(fragment["text"]) > 4_000,
                }
            )
        return {
            "schema_version": "deeplaw.operator-review-detail/v1",
            "vault_id": vault.vault_id,
            "asset": asset.to_dict(),
            "evidence": evidence,
            "evidence_truncated": len(asset.source_refs) > 20,
            "approval_inherited": False,
        }


def operator_review_action(
    vault_path: str | Path,
    *,
    action: ReviewAction,
    asset_ids: tuple[str, ...],
    reviewer_id: str,
    reason: str,
    confirm_reviewed: bool,
    confirm_quarantined: bool = False,
    title: str | None = None,
    statement: str | None = None,
    split_items: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    if action not in {"approve", "reject", "edit", "split", "merge"}:
        raise ValueError("operator review action is unsupported")
    if not confirm_reviewed:
        raise ValueError("operator review action requires explicit review confirmation")
    if not asset_ids or len(asset_ids) > 100:
        raise ValueError("operator review action requires one to 100 asset IDs")
    if len(set(asset_ids)) != len(asset_ids):
        raise ValueError("operator review action contains duplicate asset IDs")
    reason = reason.strip()
    reviewer_id = reviewer_id.strip()
    if not 1 <= len(reviewer_id) <= 200 or not 1 <= len(reason) <= 2_000:
        raise ValueError("operator review action requires bounded reviewer and reason")
    with KnowledgeVault(vault_path, read_only=False) as vault:
        created: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        lineage_review: dict[str, Any] | None = None
        atomic = True
        if action in {"approve", "reject"}:
            decisions = _review_assets_atomically(
                vault,
                action=action,
                asset_ids=asset_ids,
                reviewer_id=reviewer_id,
                reason=reason,
                confirm_quarantined=confirm_quarantined,
            )
        else:
            transformed = transform_review_proposals(
                vault,
                action=action,
                asset_ids=asset_ids,
                reviewer_id=reviewer_id,
                reason=reason,
                confirm_reviewed=True,
                title=title,
                statement=statement,
                split_items=split_items,
            )
            created = transformed["created_proposals"]
            decisions = transformed["decisions"]
            lineage_review = transformed["lineage_review"]
            atomic = transformed["atomic"]
        return {
            "schema_version": "deeplaw.operator-review-action/v1",
            "vault_id": vault.vault_id,
            "action": action,
            "input_asset_ids": list(asset_ids),
            "decisions": decisions,
            "created_proposals": created,
            "created_proposal_count": len(created),
            "review_required": bool(created),
            "approval_inherited": False,
            "lineage_review": lineage_review,
            "atomic": atomic,
        }


def _review_assets_atomically(
    vault: KnowledgeVault,
    *,
    action: Literal["approve", "reject"],
    asset_ids: tuple[str, ...],
    reviewer_id: str,
    reason: str,
    confirm_quarantined: bool,
) -> list[dict[str, Any]]:
    """Apply a bounded Workbench decision without leaving a partial batch."""
    try:
        vault.connection.execute("BEGIN IMMEDIATE")
        vault._require_healthy_integrity()
        proposals = [
            vault.get_asset(asset_id, include_inactive=True) for asset_id in asset_ids
        ]
        source_revision_ids: set[str] = set()
        decisions: list[dict[str, Any]] = []
        source_file_cache: dict[str, dict[str, Any]] = {}
        for proposal in proposals:
            identity = vault._asset_revision_identity(proposal.asset_id)
            if identity is not None:
                source_revision_ids.update(identity["source_revision_ids"])
            if action == "reject":
                rejected = vault._reject_asset_in_transaction(
                    proposal.asset_id,
                    reason=reason,
                    reviewer_id=reviewer_id,
                    policy_id="deeplaw.operator-workbench/v1",
                )
                decisions.append(
                    {
                        "schema_version": "deeplaw.knowledge-review-decision/v1",
                        "asset_id": proposal.asset_id,
                        "decision": "reject",
                        "status": "revoked",
                        "review_receipt": rejected["review_receipt"],
                    }
                )
                continue
            manifest_body = {
                "schema_version": "deeplaw.knowledge-review-manifest/v1",
                "vault_id": vault.vault_id,
                "source_id": None,
                "asset_id": proposal.asset_id,
                "content_sha256": proposal.content_sha256,
                "status": proposal.status,
            }
            manifest_sha256 = sha256_bytes(
                canonical_json(manifest_body).encode("utf-8")
            )
            approved = vault._approve_asset_in_transaction(
                proposal.asset_id,
                confirm_quarantined=confirm_quarantined,
                source_file_cache=source_file_cache,
                reviewer_id=reviewer_id,
                policy_id="deeplaw.operator-workbench/v1",
            )
            receipt = vault._record_review_receipt(
                assets=[proposal],
                source_id=None,
                reviewer_id=reviewer_id,
                policy_id="deeplaw.operator-workbench/v1",
                reason=reason,
                review_manifest_sha256=manifest_sha256,
            )
            decisions.append(
                {
                    "decision": "approve",
                    "asset": approved.to_dict(),
                    "review_receipt": receipt,
                }
            )
        if source_revision_ids:
            vault._append_identity_snapshot(
                reason="governance_recorded",
                source_revision_id=(
                    next(iter(source_revision_ids))
                    if len(source_revision_ids) == 1
                    else None
                ),
            )
        vault.connection.commit()
    except BaseException:
        vault.connection.rollback()
        raise
    return decisions


def operator_snapshot(vault_path: str | Path) -> dict[str, Any]:
    with KnowledgeVault(vault_path, read_only=True) as vault:
        inspection = vault.inspect()
        queue = vault.review_queue(limit=25)
        all_sources = vault.all_sources()
        sources = [
            {
                "source_id": source["source_id"],
                "title": source["title"],
                "logical_path": source.get("logical_path"),
                "status": source.get("status"),
                "source_revision_id": source.get("source_revision_id"),
            }
            for source in all_sources[-100:]
        ]
        relations = vault.temporal_relations(mode="current", limit=100)
        relation_review_queue = pending_relation_carry_forward(vault, limit=100)
        lineage_mapping_inventory = _lineage_mapping_inventory(vault, limit=200)
        source_tree: list[dict[str, Any]] = []
        for source in sources[-25:]:
            if source["status"] == "removed":
                continue
            try:
                tree = vault.structure_list(source_id=source["source_id"], limit=25)
            except (KeyError, RuntimeError, ValueError):
                continue
            source_tree.extend(
                {
                    "source_id": source["source_id"],
                    "logical_path": source["logical_path"],
                    **node,
                }
                for node in tree["nodes"]
            )
        source_diffs: list[dict[str, Any]] = []
        versions_by_key: dict[str, list[dict[str, Any]]] = {}
        for source in all_sources:
            key = source.get("canonical_source_key") or source.get("source_key")
            if key is not None:
                versions_by_key.setdefault(key, []).append(source)
        for versions in versions_by_key.values():
            pair = _source_diff_pair(versions)
            if pair is None:
                continue
            source_diffs.append(vault.source_diff(*pair))
        lineages: list[dict[str, Any]] = []
        for asset in vault.all_assets(statuses=("active",))[:50]:
            try:
                lineage = vault.knowledge_lineage(asset_id=asset.asset_id)
            except (KeyError, RuntimeError, ValueError):
                continue
            lineages.append(
                {
                    "asset_id": asset.asset_id,
                    "title": asset.title,
                    "knowledge_key": lineage["knowledge_key"],
                    "revision_count": len(lineage["revisions"]),
                    "transitions": lineage["transitions"],
                }
            )
        feedback = vault.list_feedback(limit=100)["feedback"] if vault.control_enabled else []
        active_profile = load_active_retrieval_profile(vault)
        inbox = (
            list_inbox_artifacts(vault, state="pending", limit=100)
            if (vault.root / "inbox").exists()
            else {"artifacts": [], "invalid_artifact_count": 0}
        )
        last_capsule: dict[str, Any] | None = None
        capsule_path = vault.root / "derived" / "retrieval" / "last-capsule.json"
        if (
            capsule_path.is_file()
            and not capsule_path.is_symlink()
            and capsule_path.stat().st_size <= 512 * 1024
        ):
            value = strict_json_loads(capsule_path.read_bytes())
            if isinstance(value, dict):
                try:
                    valid = vault.verify_capsule(value)["valid"]
                except (KeyError, RuntimeError, TypeError, ValueError):
                    valid = False
                last_capsule = value if valid else None
        return {
            "schema_version": WORKBENCH_SCHEMA,
            "vault_id": vault.vault_id,
            "vault_name": vault.manifest["name"],
            "revision": vault.revision,
            "audit_head": vault.audit_head,
            "health": {
                "agent_ready": inspection["agent_ready"],
                "integrity_valid": inspection["integrity"]["valid"],
                "source_integrity_valid": inspection["source_integrity"]["valid"],
                "usable_active_count": inspection["usable_active_count"],
                "review_backlog": queue["total"] + relation_review_queue["total"],
            },
            "sources": sources,
            "source_tree": source_tree[:500],
            "source_diffs": source_diffs[:100],
            "review_queue": queue["items"],
            "relation_review_queue": relation_review_queue["items"],
            "relations": relations["relations"],
            "lineages": lineages,
            "lineage_mapping_inventory": lineage_mapping_inventory,
            "feedback": feedback,
            "pending_inbox": inbox["artifacts"],
            "invalid_inbox_artifact_count": inbox["invalid_artifact_count"],
            "active_retrieval_profile": (
                active_profile["profile_id"] if active_profile is not None else None
            ),
            "last_capsule": last_capsule,
            "panels": [
                "sources",
                "source-tree",
                "source-diff",
                "review",
                "search-recall",
                "explain",
                "lineage",
                "lineage-mapping",
                "relations",
                "current-history",
                "capsule",
                "feedback",
                "health",
                "benchmark",
            ],
            "canonical_write_boundary": "review service only",
        }


def _clip(value: str, width: int) -> str:
    value = " ".join(value.splitlines())
    return value if len(value) <= width else value[: max(0, width - 1)] + "…"


def _draw_header(screen: Any, snapshot: dict[str, Any], title: str) -> tuple[int, int]:
    height, width = screen.getmaxyx()
    screen.erase()
    screen.addnstr(0, 0, f"DeepLaw Operator Workbench · {title}", width - 1)
    screen.addnstr(
        1,
        0,
        (
            f"{snapshot['vault_name']} · rev {snapshot['revision']} · "
            f"active {snapshot['health']['usable_active_count']} · "
            f"review {snapshot['health']['review_backlog']}"
        ),
        width - 1,
    )
    screen.hline(2, 0, "─", max(1, width - 1))
    return height, width


def _draw_dashboard(screen: Any, snapshot: dict[str, Any]) -> None:
    height, width = _draw_header(screen, snapshot, "Dashboard")
    rows = [
        ("Health", "ready" if snapshot["health"]["agent_ready"] else "attention required"),
        ("Sources", str(len(snapshot["sources"]))),
        ("Review queue", str(snapshot["health"]["review_backlog"])),
        ("Relations", str(len(snapshot["relations"]))),
        ("Inbox", str(len(snapshot["pending_inbox"]))),
        ("Ranking profile", snapshot["active_retrieval_profile"] or "default"),
        (
            "Last Capsule",
            snapshot["last_capsule"]["capsule_id"] if snapshot["last_capsule"] else "none",
        ),
    ]
    for index, (label, value) in enumerate(rows, start=4):
        if index >= height - 3:
            break
        screen.addnstr(index, 2, f"{label:<18} {value}", width - 4)
    screen.addnstr(
        height - 2,
        0,
        "[s] sources [t] tree [d] diff [r] review [/] recall [g] graph "
        "[l] lineage [m] map [c] capsule [f] feedback [h] health [q] quit",
        width - 1,
    )
    screen.refresh()


def _list_panel(
    screen: Any,
    snapshot: dict[str, Any],
    *,
    title: str,
    lines: list[str],
) -> None:
    height, width = _draw_header(screen, snapshot, title)
    for index, line in enumerate(lines[: max(0, height - 6)], start=4):
        screen.addnstr(index, 1, _clip(line, width - 2), width - 2)
    screen.addnstr(height - 2, 0, "Press any key to return", width - 1)
    screen.refresh()
    screen.getch()


def _prompt(screen: Any, prompt: str) -> str:
    import curses

    height, width = screen.getmaxyx()
    screen.move(height - 2, 0)
    screen.clrtoeol()
    screen.addnstr(height - 2, 0, prompt, width - 1)
    screen.refresh()
    curses.echo()
    try:
        raw = screen.getstr(height - 1, 0, max(1, width - 1))
    finally:
        curses.noecho()
    return raw.decode("utf-8", errors="replace").strip()


def _asset_ids_from_visible_rows(
    visible: list[dict[str, Any]],
    positions: tuple[int, ...],
) -> tuple[str, ...]:
    if (
        not positions
        or len(positions) > 20
        or len(set(positions)) != len(positions)
        or any(isinstance(position, bool) or position < 1 for position in positions)
    ):
        raise ValueError("Workbench review row selection is invalid")
    selected: list[str] = []
    for position in positions:
        try:
            item = visible[position - 1]
        except IndexError as error:
            raise ValueError("Workbench review row is no longer visible") from error
        if item.get("review_kind") != "asset":
            raise ValueError("Workbench review row does not identify an Asset proposal")
        asset_id = item.get("asset_id")
        if not isinstance(asset_id, str):
            raise ValueError("Workbench review row has no Asset identity")
        selected.append(asset_id)
    return tuple(selected)


def _recall_panel(screen: Any, vault_path: Path, snapshot: dict[str, Any]) -> None:
    query = _prompt(screen, "Recall query: ")
    if not query:
        return
    with KnowledgeVault(vault_path, read_only=True) as vault:
        result = recall(
            vault,
            query,
            confirm_no_case_data=True,
            mode="auto",
            max_items=8,
            max_chars=6_000,
            max_tokens=4_096,
        )
    capsule = result["capsule"]
    lines = [
        f"Intent: {result['query_plan']['intent']}",
        f"Channels: {', '.join(result['query_plan']['channels'])}",
        f"Capsule: {capsule['capsule_id']}",
    ]
    for group in ("constraints", "decisions", "knowledge_assets", "experiences"):
        lines.extend(f"[{item['kind']}] {item['title']}" for item in capsule[group])
    lines.extend(f"Gap: {gap}" for gap in capsule["gaps"])
    _list_panel(screen, snapshot, title="Recall + Explain", lines=lines)


def _review_panel(screen: Any, vault_path: Path, snapshot: dict[str, Any]) -> None:
    queue = snapshot["review_queue"]
    relation_queue = snapshot["relation_review_queue"]
    if not queue and not relation_queue:
        _list_panel(screen, snapshot, title="Review Queue", lines=["Review queue is empty."])
        return
    height, width = _draw_header(screen, snapshot, "Review Queue")
    combined = [
        {"review_kind": "asset", **item} for item in queue
    ] + [
        {"review_kind": "relation", **item} for item in relation_queue
    ]
    visible = combined[: max(1, height - 9)]
    for index, item in enumerate(visible, start=1):
        if item["review_kind"] == "asset":
            label = f"{item['status']:<11} [{item['kind']}] {item['title']}"
        else:
            label = (
                f"relation/{item['review_mode']} {item['subject_knowledge_key']} "
                f"--{item['predicate']}--> {item['object_knowledge_key']}"
            )
        screen.addnstr(
            index + 3,
            1,
            _clip(f"{index:>2}. {label}", width - 2),
            width - 2,
        )
    selected = _prompt(screen, "Select proposal number (blank returns): ")
    if not selected:
        return
    try:
        selected_position = int(selected)
        if not 1 <= selected_position <= len(visible):
            return
        item = visible[selected_position - 1]
    except (ValueError, IndexError):
        return
    if item["review_kind"] == "relation":
        lines = [
            f"Relation: {item['subject_knowledge_key']} --{item['predicate']}--> "
            f"{item['object_knowledge_key']}",
            f"Review mode: {item['review_mode']}",
            "Approval inherited: no",
            "",
            "Evidence:",
            *(
                f"{reference['source_revision_id']} · {reference['locator']} · "
                f"{reference['quote_sha256']}"
                for reference in item["evidence_refs"]
            ),
        ]
        _list_panel(screen, snapshot, title="Relation Successor", lines=lines)
        action = _prompt(screen, "[a] approve [x] reject [blank] return: ").lower()
        if action not in {"a", "x"}:
            return
        reason = _prompt(screen, "Review reason: ") or "Reviewed in local workbench."
        with KnowledgeVault(vault_path, read_only=False) as vault:
            result = review_relation_carry_forward(
                vault,
                relation_revision_id=item["relation_revision_id"],
                decision="approve" if action == "a" else "reject",
                confirm_reviewed=True,
                reviewer_id="local-operator",
                reason=reason,
            )
        _list_panel(
            screen,
            snapshot,
            title="Review Result",
            lines=json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True).splitlines(),
        )
        return
    detail = review_side_by_side(vault_path, item["asset_id"])
    lines = [
        f"Proposal: [{detail['asset']['kind']}] {detail['asset']['title']}",
        detail["asset"]["statement"],
        "",
        "Evidence:",
    ]
    lines.extend(
        f"{evidence['source_title']} · {evidence['locator']} · {evidence['text']}"
        for evidence in detail["evidence"]
    )
    _list_panel(screen, snapshot, title="Source ↔ Proposal", lines=lines)
    action = _prompt(
        screen,
        "[a] approve [x] reject [e] edit [s] split [m] merge [blank] return: ",
    ).lower()
    if action not in {"a", "x", "e", "s", "m"}:
        return
    reason = _prompt(screen, "Review reason: ") or "Reviewed in local workbench."
    common: dict[str, Any] = {
        "vault_path": vault_path,
        "reviewer_id": "local-operator",
        "reason": reason,
        "confirm_reviewed": True,
    }
    if action == "a":
        confirm_quarantined = False
        if item["status"] == "quarantined":
            confirm_quarantined = (
                _prompt(
                    screen,
                    "Quarantined proposal: type APPROVE to accept the risk: ",
                )
                == "APPROVE"
            )
            if not confirm_quarantined:
                return
        result = operator_review_action(
            **common,
            action="approve",
            asset_ids=(item["asset_id"],),
            confirm_quarantined=confirm_quarantined,
        )
    elif action == "x":
        result = operator_review_action(
            **common,
            action="reject",
            asset_ids=(item["asset_id"],),
        )
    elif action == "e":
        title = _prompt(screen, "Edited title: ") or detail["asset"]["title"]
        statement = _prompt(screen, "Edited statement: ")
        if not statement:
            return
        result = operator_review_action(
            **common,
            action="edit",
            asset_ids=(item["asset_id"],),
            title=title,
            statement=statement,
        )
    elif action == "s":
        split_items: list[tuple[str, str]] = []
        for number in (1, 2):
            title = _prompt(screen, f"Split {number} title: ")
            statement = _prompt(screen, f"Split {number} statement: ")
            if not title or not statement:
                return
            split_items.append((title, statement))
        result = operator_review_action(
            **common,
            action="split",
            asset_ids=(item["asset_id"],),
            split_items=tuple(split_items),
        )
    else:
        other = _prompt(screen, "Other proposal row numbers (comma-separated): ")
        try:
            other_positions = tuple(
                int(value.strip()) for value in other.split(",") if value.strip()
            )
            merge_positions = tuple(
                dict.fromkeys((selected_position, *other_positions))
            )
            merged_ids = _asset_ids_from_visible_rows(visible, merge_positions)
        except ValueError:
            return
        title = _prompt(screen, "Merged title: ")
        statement = _prompt(screen, "Merged statement: ")
        if len(merged_ids) < 2 or not title or not statement:
            return
        result = operator_review_action(
            **common,
            action="merge",
            asset_ids=merged_ids,
            title=title,
            statement=statement,
        )
    _list_panel(
        screen,
        snapshot,
        title="Review Result",
        lines=json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True).splitlines(),
    )


def _lineage_mapping_panel(
    screen: Any,
    vault_path: Path,
    snapshot: dict[str, Any],
) -> None:
    inventory = snapshot["lineage_mapping_inventory"]
    if not inventory["items"]:
        _list_panel(
            screen,
            snapshot,
            title="Reviewed Lineage Mapping",
            lines=["No source-bound Identity v2 revisions are available."],
        )
        return
    lines = [
        f"{item['position']:>3}. {item['status']:<11} {item['title']} · "
        f"source {item['source_title']}"
        for item in inventory["items"]
    ]
    if inventory["truncated"]:
        lines.append("Inventory truncated; use the advanced CLI for an exact larger selection.")
    _list_panel(screen, snapshot, title="Reviewed Lineage Mapping", lines=lines)
    action = _prompt(screen, "Mapping [s] split [m] merged [a] ambiguous [blank] return: ")
    status_by_key = {"s": "split", "m": "merged", "a": "ambiguous"}
    status = status_by_key.get(action.lower())
    if status is None:
        return

    def positions(prompt: str) -> tuple[int, ...]:
        raw = _prompt(screen, prompt)
        try:
            return tuple(int(value.strip()) for value in raw.split(",") if value.strip())
        except ValueError:
            return ()

    from_positions = positions("Predecessor row numbers (comma-separated): ")
    to_positions = positions("Successor row numbers (comma-separated): ")
    reason = _prompt(screen, "Review reason: ")
    if not from_positions or not to_positions or not reason:
        return
    result = operator_review_lineage_by_selection(
        vault_path,
        status=status,
        from_positions=from_positions,
        to_positions=to_positions,
        reviewer_id="local-operator",
        reason=reason,
        confirm_reviewed=True,
    )
    _list_panel(
        screen,
        snapshot,
        title="Lineage Review Result",
        lines=json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True).splitlines(),
    )


def _run_curses(screen: Any, vault_path: Path) -> None:
    import curses

    curses.curs_set(0)
    screen.keypad(True)
    while True:
        snapshot = operator_snapshot(vault_path)
        _draw_dashboard(screen, snapshot)
        key = screen.getkey()
        if key in {"q", "Q"}:
            return
        if key in {"s", "S"}:
            _list_panel(
                screen,
                snapshot,
                title="Sources / Current and Historical",
                lines=[
                    f"{source['status']:<10} {source['logical_path'] or source['title']} · "
                    f"{source['source_revision_id'] or 'legacy'}"
                    for source in snapshot["sources"]
                ],
            )
        elif key in {"t", "T"}:
            _list_panel(
                screen,
                snapshot,
                title="Source Tree",
                lines=[
                    f"{item['logical_path'] or item['source_id']} · "
                    f"{item['node_type']} · "
                    f"{item.get('title') or item['logical_node_key']}"
                    for item in snapshot["source_tree"]
                ]
                or ["No Source IR roots."],
            )
        elif key in {"d", "D"}:
            _list_panel(
                screen,
                snapshot,
                title="Source Diff",
                lines=[
                    f"{item['source_key']} · changed {item['changed_count']} · "
                    f"added {item['added_count']} · removed {item['removed_count']}"
                    for item in snapshot["source_diffs"]
                ]
                or ["No source revision pairs."],
            )
        elif key in {"r", "R"}:
            _review_panel(screen, vault_path, snapshot)
        elif key == "/":
            _recall_panel(screen, vault_path, snapshot)
        elif key in {"g", "G"}:
            _list_panel(
                screen,
                snapshot,
                title="Reviewed Temporal Relations",
                lines=[
                    f"{item['subject_knowledge_key']} --{item['predicate']}--> "
                    f"{item['object_knowledge_key']} [{item['status']}]"
                    for item in snapshot["relations"]
                ]
                or ["No reviewed current relations."],
            )
        elif key in {"l", "L"}:
            _list_panel(
                screen,
                snapshot,
                title="Knowledge Lineage",
                lines=[
                    f"{item['title']} · {item['knowledge_key']} · "
                    f"{item['revision_count']} revisions"
                    for item in snapshot["lineages"]
                ],
            )
        elif key in {"m", "M"}:
            _lineage_mapping_panel(screen, vault_path, snapshot)
        elif key in {"c", "C"}:
            capsule = snapshot["last_capsule"]
            _list_panel(
                screen,
                snapshot,
                title="Capsule Viewer",
                lines=(
                    json.dumps(capsule, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
                    if capsule
                    else ["No Golden Path Capsule has been created yet."]
                ),
            )
        elif key in {"f", "F"}:
            _list_panel(
                screen,
                snapshot,
                title="Structured Feedback",
                lines=[
                    f"{item['feedback_id']} · {item.get('outcome', 'unknown')} · "
                    f"run {item.get('run_id', 'unknown')}"
                    for item in snapshot["feedback"]
                ]
                or ["No structured feedback."],
            )
        elif key in {"h", "H"}:
            _list_panel(
                screen,
                snapshot,
                title="Health / Benchmark Boundary",
                lines=[
                    f"Canonical integrity: {snapshot['health']['integrity_valid']}",
                    f"Source integrity: {snapshot['health']['source_integrity_valid']}",
                    f"Agent ready: {snapshot['health']['agent_ready']}",
                    "Benchmark: local diagnostics only; external verification remains pending.",
                ],
            )


def run_operator_workbench(vault_path: str | Path) -> dict[str, Any]:
    path = Path(vault_path).expanduser().absolute()
    snapshot = operator_snapshot(path)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
        return snapshot
    if os.environ.get("TERM", "").lower() in {"", "dumb"}:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
        return snapshot
    import curses

    curses.wrapper(_run_curses, path)
    return operator_snapshot(path)
