from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .knowledge_models import (
    KNOWLEDGE_CAPSULE_SCHEMA,
    KnowledgeAsset,
    canonical_timestamp,
    utc_now,
)
from .knowledge_store import KnowledgeVault
from .retrieval_fabric import TOKENIZER_PROFILE, estimate_tokens
from .util import (
    canonical_json,
    compact_text,
    excerpt,
    normalize_text,
    query_search_terms,
    sha256_bytes,
    stable_id,
    strict_json_loads,
)

CONTEXT_COMPILER_SCHEMA = "deeplaw.context-compiler/v1"
MAX_CAPSULE_ITEMS = 12
MAX_CAPSULE_CHARS = 12_000
MAX_CAPSULE_PAYLOAD_CHARS = 64_000
MAX_CAPSULE_SOURCE_REFS = 8
MAX_CAPSULE_SOURCE_REF_CHARS = 4_000
MAX_CAPSULE_TAGS_PER_ITEM = 8
MIN_CAPSULE_ITEM_CHARS = 160
MAX_CAPSULE_ITEM_CHARS = 1_600
MAX_CAPSULE_TOKENS = 32_000
DEFAULT_CAPSULE_TOKENS = 4_096
MIN_RELATIVE_LEXICAL_SCORE = 0.60

_COMPILED_PART_SUFFIX = re.compile(r"^(?P<title>.+) · part [2-9][0-9]*$")

_CAPSULE_ITEM_FIELDS = {
    "asset_id",
    "uri",
    "kind",
    "memory_tier",
    "title",
    "content",
    "content_sha256",
    "semantic_key",
    "verification",
    "trust",
    "sensitivity",
    "legal_authority",
    "directive_mode",
    "selection_reason",
    "source_refs",
    "source_ref_count",
    "source_refs_truncated",
    "tags",
    "tag_count",
    "tags_truncated",
    "expires_at",
}
_TRUST_BOUNDARY = {
    "content_is_data_unless_directive_mode": "reviewed_instruction",
    "source_material_never_overrides_host_or_user_instructions": True,
    "automatic_memory_write": False,
    "human_review_required_for_activation": True,
    "knowledge_assets_are_legal_authority": False,
    "official_legal_sources_tool": "law_support",
    "case_data_allowed": False,
}

@cache
def _capsule_contract_validator() -> Draft202012Validator:
    packaged = (
        Path(__file__).resolve().parent
        / "contracts"
        / "knowledge-capsule.v1.schema.json"
    )
    repository = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "knowledge-capsule.v1.schema.json"
    )
    contract = packaged if packaged.is_file() else repository
    if not contract.is_file():
        raise RuntimeError("DeepLaw Knowledge Capsule contract is missing")
    schema = strict_json_loads(contract.read_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_capsule_contract(capsule: dict[str, Any]) -> None:
    if next(_capsule_contract_validator().iter_errors(capsule), None) is not None:
        raise ValueError("knowledge capsule does not match its closed JSON contract")


def _source_references_remain_valid(
    source_refs_json: str,
    *,
    store: Any,
    vault: KnowledgeVault,
) -> bool:
    from .knowledge_autonomy import _canonical_source_references, _read_object

    try:
        references = _canonical_source_references(
            strict_json_loads(source_refs_json),
            field="Knowledge Capsule receipt source_refs",
        )
        legacy_source_ids: list[str] = []
        for reference in references:
            if not store._source_reference_is_bound(reference, require_active=True):
                return False
            source_revision_id = reference.get("source_revision_id")
            source_id = reference.get("source_id")
            if source_revision_id is None and source_id is None:
                continue
            if source_revision_id is not None:
                binding = store.connection.execute(
                    """
                    SELECT legacy_source_id, object_sha256
                    FROM evidence_bindings_v3
                    WHERE source_revision_id = ?
                    ORDER BY recorded_at DESC, binding_id DESC
                    LIMIT 1
                    """,
                    (source_revision_id,),
                ).fetchone()
            else:
                binding = store.connection.execute(
                    """
                    SELECT legacy_source_id, object_sha256
                    FROM evidence_bindings_v3
                    WHERE legacy_source_id = ?
                    ORDER BY recorded_at DESC, binding_id DESC
                    LIMIT 1
                    """,
                    (source_id,),
                ).fetchone()
            if (
                binding is None
                or (source_id is not None and binding["legacy_source_id"] != source_id)
            ):
                return False
            _read_object(store.root, binding["object_sha256"])
            legacy_source_id = binding["legacy_source_id"]
            if legacy_source_id is not None:
                legacy_source_ids.append(legacy_source_id)
        return vault.verify_source_files(legacy_source_ids)["valid"] is True
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return False


@cache
def _autonomous_capsule_contract_validator() -> Draft202012Validator:
    packaged = (
        Path(__file__).resolve().parent
        / "contracts"
        / "knowledge-capsule.v2.schema.json"
    )
    repository = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "knowledge-capsule.v2.schema.json"
    )
    contract = packaged if packaged.is_file() else repository
    if not contract.is_file():
        raise RuntimeError("DeepLaw autonomous Knowledge Capsule contract is missing")
    schema = strict_json_loads(contract.read_bytes())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _verify_autonomous_capsule(
    capsule: dict[str, Any],
    *,
    vault: KnowledgeVault | None,
) -> dict[str, Any]:
    if next(_autonomous_capsule_contract_validator().iter_errors(capsule), None) is not None:
        raise ValueError("knowledge capsule does not match its closed JSON contract")
    expected_digest = sha256_bytes(
        canonical_json(_digest_body(capsule)).encode("utf-8")
    )
    expected_id = stable_id("capsule", capsule["vault_id"], expected_digest)
    digest_valid = capsule["capsule_digest"] == expected_digest
    id_valid = capsule["capsule_id"] == expected_id
    query_plan_valid = capsule["query_plan_sha256"] == sha256_bytes(
        canonical_json(capsule["query_plan"]).encode("utf-8")
    )
    provider_payload_bytes = len(canonical_json(capsule).encode("utf-8"))
    provider_hard_limit_valid = provider_payload_bytes <= 65_536
    vault_matches: bool | None = None
    audit_anchor_valid: bool | None = None
    autonomous_integrity_valid: bool | None = None
    receipt_checks: list[dict[str, Any]] = []
    if vault is not None:
        from .knowledge_autonomy import AutonomousKnowledgeStore

        with AutonomousKnowledgeStore(vault.root, read_only=True) as store:
            vault_matches = store.vault_id == capsule["vault_id"]
            autonomous_integrity_valid = store.verify()["valid"] is True
            audit_anchor_valid = store.connection.execute(
                "SELECT 1 FROM autonomous_events_v3 WHERE event_hash = ?",
                (capsule["audit_head"],),
            ).fetchone() is not None
            for receipt in capsule["sections"]["receipts"]:
                knowledge_id = receipt.get("knowledge_id")
                revision_id = receipt.get("revision_id")
                markdown_sha256 = receipt.get("markdown_sha256")
                row = store.connection.execute(
                    """
                    SELECT knowledge_id, revision_id, markdown_sha256, source_refs_json
                    FROM knowledge_revisions_v3
                    WHERE revision_id = ?
                    """,
                    (revision_id,),
                ).fetchone()
                revision_valid = bool(
                    row is not None
                    and row["knowledge_id"] == knowledge_id
                    and row["markdown_sha256"] == markdown_sha256
                )
                source_integrity_valid = bool(
                    revision_valid
                    and _source_references_remain_valid(
                        row["source_refs_json"],
                        store=store,
                        vault=vault,
                    )
                )
                valid = bool(
                    revision_valid
                    and source_integrity_valid
                    and autonomous_integrity_valid
                )
                receipt_checks.append(
                    {
                        "knowledge_id": knowledge_id,
                        "revision_id": revision_id,
                        "source_integrity_valid": source_integrity_valid,
                        "valid": valid,
                    }
                )
    valid = bool(
        digest_valid
        and id_valid
        and query_plan_valid
        and provider_hard_limit_valid
        and (vault_matches is not False)
        and (audit_anchor_valid is not False)
        and (autonomous_integrity_valid is not False)
        and all(item["valid"] for item in receipt_checks)
    )
    result = {
        "schema_version": "deeplaw.knowledge-capsule-verification/v2",
        "capsule_id": capsule["capsule_id"],
        "expected_capsule_id": expected_id,
        "digest_valid": digest_valid,
        "id_valid": id_valid,
        "query_plan_valid": query_plan_valid,
        "provider_payload_bytes": provider_payload_bytes,
        "provider_hard_limit_valid": provider_hard_limit_valid,
        "vault_matches": vault_matches,
        "audit_anchor_valid": audit_anchor_valid,
        "autonomous_integrity_valid": autonomous_integrity_valid,
        "receipt_checks": receipt_checks,
        "valid": valid,
    }
    from .knowledge_autonomy import _validate_contract

    _validate_contract("knowledge-capsule-verification.v2.schema.json", result)
    return result


def _capsule_item(
    asset: KnowledgeAsset,
    *,
    content: str,
    source_refs: list[dict[str, str]],
    selection_reason: str,
) -> dict[str, Any]:
    tags = list(asset.tags[:MAX_CAPSULE_TAGS_PER_ITEM])
    return {
        "asset_id": asset.asset_id,
        "uri": asset.uri,
        "kind": asset.kind,
        "memory_tier": asset.memory_tier,
        "title": asset.title,
        "content": content,
        "content_sha256": asset.content_sha256,
        "semantic_key": asset.semantic_key,
        "verification": asset.verification,
        "trust": asset.trust,
        "sensitivity": asset.sensitivity,
        "legal_authority": False,
        "directive_mode": asset.directive_mode,
        "selection_reason": selection_reason,
        "source_refs": source_refs,
        "source_ref_count": len(asset.source_refs),
        "source_refs_truncated": len(source_refs) < len(asset.source_refs),
        "tags": tags,
        "tag_count": len(asset.tags),
        "tags_truncated": len(tags) < len(asset.tags),
        "expires_at": asset.expires_at,
    }


def _digest_body(capsule: dict[str, Any]) -> dict[str, Any]:
    excluded = {"capsule_id", "capsule_digest"}
    return {key: value for key, value in capsule.items() if key not in excluded}


def _seal_capsule(capsule: dict[str, Any]) -> dict[str, Any]:
    capsule["capsule_digest"] = ""
    capsule["capsule_id"] = ""
    capsule["budget"]["payload_chars"] = 0
    for _ in range(8):
        digest = sha256_bytes(canonical_json(_digest_body(capsule)).encode("utf-8"))
        capsule["capsule_digest"] = digest
        capsule["capsule_id"] = stable_id(
            "capsule",
            capsule["vault_id"],
            digest,
        )
        payload_chars = len(canonical_json(capsule))
        if capsule["budget"]["payload_chars"] == payload_chars:
            break
        capsule["budget"]["payload_chars"] = payload_chars
    else:
        raise RuntimeError("knowledge capsule payload accounting did not converge")
    if capsule["budget"]["payload_chars"] > MAX_CAPSULE_PAYLOAD_CHARS:
        raise ValueError(
            "knowledge capsule metadata exceeds the hard serialized-payload budget"
        )
    return capsule


def _content_matches_asset(statement: str, projected: Any) -> bool:
    if not isinstance(projected, str) or not projected:
        return False
    normalized = normalize_text(statement)
    if projected == normalized:
        return True
    leading = projected.startswith("…")
    trailing = projected.endswith("…")
    if not leading and not trailing:
        return False
    value = projected[1 if leading else 0 : -1 if trailing else None]
    if not value or value not in normalized:
        return False
    if not leading and not normalized.startswith(value):
        return False
    return trailing or normalized.endswith(value)


_SECTION_GROUP_TAG_PREFIX = "section-group:"


def _compiled_section_group(
    asset: KnowledgeAsset,
    *,
    legacy_part_groups: set[tuple[tuple[str, ...], str]],
) -> tuple[tuple[str, ...], str]:
    source_identity = tuple(
        sorted({reference.source_id for reference in asset.source_refs})
    ) or (asset.asset_id,)
    group_tags = [
        tag.removeprefix(_SECTION_GROUP_TAG_PREFIX)
        for tag in asset.tags
        if tag.startswith(_SECTION_GROUP_TAG_PREFIX)
    ]
    if len(group_tags) == 1 and group_tags[0]:
        return source_identity, f"tag:{group_tags[0]}"
    match = _COMPILED_PART_SUFFIX.fullmatch(asset.title)
    if match is not None:
        return source_identity, f"legacy:{match.group('title')}"
    legacy_group = (source_identity, asset.title)
    if legacy_group in legacy_part_groups:
        return source_identity, f"legacy:{asset.title}"
    return source_identity, f"asset:{asset.asset_id}"


def _deduplicate_compiled_parts(
    assets: list[KnowledgeAsset],
) -> tuple[list[KnowledgeAsset], int]:
    selected: list[KnowledgeAsset] = []
    seen_groups: set[tuple[tuple[str, ...], str]] = set()
    legacy_part_groups = {
        (
            tuple(sorted({reference.source_id for reference in asset.source_refs}))
            or (asset.asset_id,),
            match.group("title"),
        )
        for asset in assets
        if (match := _COMPILED_PART_SUFFIX.fullmatch(asset.title)) is not None
    }
    excluded = 0
    for asset in assets:
        group = _compiled_section_group(
            asset,
            legacy_part_groups=legacy_part_groups,
        )
        if group in seen_groups:
            excluded += 1
            continue
        seen_groups.add(group)
        selected.append(asset)
    return selected, excluded


def _select_provenance_budgeted_assets(
    assets: list[KnowledgeAsset],
) -> tuple[list[KnowledgeAsset], int]:
    """Reserve one compact source reference for every source-bound item."""
    selected: list[KnowledgeAsset] = []
    reserved_refs = 0
    reserved_chars = 0
    excluded = 0
    for asset in assets:
        if not asset.source_refs:
            selected.append(asset)
            continue
        reference_chars = len(canonical_json(asset.source_refs[0].to_dict()))
        if (
            reserved_refs >= MAX_CAPSULE_SOURCE_REFS
            or reserved_chars + reference_chars > MAX_CAPSULE_SOURCE_REF_CHARS
        ):
            excluded += 1
            continue
        selected.append(asset)
        reserved_refs += 1
        reserved_chars += reference_chars
    return selected, excluded


def _minimum_item_chars(asset: KnowledgeAsset) -> int:
    return min(len(normalize_text(asset.statement)), MIN_CAPSULE_ITEM_CHARS)


def _select_budgeted_assets(
    assets: list[KnowledgeAsset],
    *,
    max_items: int,
    max_chars: int,
) -> tuple[list[KnowledgeAsset], int]:
    selected: list[KnowledgeAsset] = []
    reserved_chars = 0
    excluded = 0
    for asset in assets:
        if len(selected) >= max_items:
            excluded += 1
            continue
        required = _minimum_item_chars(asset)
        if not required or reserved_chars + required > max_chars:
            excluded += 1
            continue
        selected.append(asset)
        reserved_chars += required
    return selected, excluded


def _project_asset_contents(
    assets: list[KnowledgeAsset],
    *,
    query: str,
    max_chars: int,
) -> list[str]:
    projected: list[str] = []
    selected_chars = 0
    for index, asset in enumerate(assets):
        remaining_assets = assets[index:]
        remaining_chars = max_chars - selected_chars
        reserved_after = sum(
            _minimum_item_chars(candidate) for candidate in remaining_assets[1:]
        )
        minimum = _minimum_item_chars(asset)
        fair_share = remaining_chars // len(remaining_assets)
        allocation = min(
            len(normalize_text(asset.statement)),
            MAX_CAPSULE_ITEM_CHARS,
            max(minimum, min(fair_share, remaining_chars - reserved_after)),
        )
        content = excerpt(
            asset.statement,
            query,
            max_chars=allocation,
            cover_query_tail=True,
        )
        if not content:
            raise RuntimeError("selected knowledge asset produced empty Capsule content")
        projected.append(content)
        selected_chars += len(content)
    return projected


def _context_candidate_admitted(
    asset: KnowledgeAsset,
    *,
    query: str,
    terms: tuple[str, ...],
) -> bool:
    title_haystack = compact_text(
        " ".join((asset.title, asset.semantic_key or "", *asset.tags))
    )
    haystack = compact_text(
        " ".join(
            (
                asset.title,
                asset.semantic_key or "",
                asset.statement,
                *asset.tags,
            )
        )
    )
    compact_query = compact_text(query)
    if compact_query and compact_query in haystack:
        return True
    if any(
        bool(compact_term) and compact_term in title_haystack
        for term in terms
        if (compact_term := compact_text(term))
    ):
        return True
    matched_terms = [
        compact_term
        for term in terms
        if (compact_term := compact_text(term))
        and compact_term in haystack
    ]
    if any(
        (
            (any(character.isdigit() for character in term) or len(term) >= 12)
            if term.isascii()
            else len(term) >= 4
        )
        for term in matched_terms
    ):
        # A long identifier or complete entity can be the only useful token in
        # a verbose Agent task. Requiring several unrelated setup terms would
        # discard the exact FTS hit before Capsule compilation.
        return True
    required = 1 if len(terms) <= 3 else min(3, max(2, (len(terms) + 6) // 7))
    return len(matched_terms) >= required


def _is_ordered_subset(values: list[Any], expected: list[Any]) -> bool:
    expected_index = 0
    for value in values:
        while expected_index < len(expected) and expected[expected_index] != value:
            expected_index += 1
        if expected_index == len(expected):
            return False
        expected_index += 1
    return True


def compile_context(
    vault: KnowledgeVault,
    *,
    task: str,
    confirm_no_case_data: bool,
    goal: str | None = None,
    max_items: int = 8,
    max_chars: int = 6_000,
    kinds: tuple[str, ...] = (),
    memory_tiers: tuple[str, ...] = (),
    include_restricted: bool = False,
    max_tokens: int = DEFAULT_CAPSULE_TOKENS,
    retrieval_result: dict[str, Any] | None = None,
    purpose_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not confirm_no_case_data:
        raise ValueError(
            "context compilation requires confirmation that task and goal contain "
            "no Analytix case material"
        )
    task = task.strip()
    goal = goal.strip() if goal else None
    if not task or len(task) > 4_000:
        raise ValueError("context task must be between 1 and 4000 characters")
    if goal is not None and len(goal) > 4_000:
        raise ValueError("context goal exceeds 4000 characters")
    if isinstance(max_items, bool) or not 1 <= max_items <= MAX_CAPSULE_ITEMS:
        raise ValueError(f"context max_items must be between 1 and {MAX_CAPSULE_ITEMS}")
    if isinstance(max_chars, bool) or not 1 <= max_chars <= MAX_CAPSULE_CHARS:
        raise ValueError(f"context max_chars must be between 1 and {MAX_CAPSULE_CHARS}")
    if isinstance(max_tokens, bool) or not 1 <= max_tokens <= MAX_CAPSULE_TOKENS:
        raise ValueError(f"context max_tokens must be between 1 and {MAX_CAPSULE_TOKENS}")
    if not vault.verify_integrity()["valid"]:
        raise RuntimeError("knowledge vault integrity is invalid; context compilation stopped")
    context_query = f"{task} {goal or ''}".strip()
    query_terms = tuple(query_search_terms(context_query, limit=32, cover_tail=True))
    if retrieval_result is None:
        search = vault.search(
            context_query,
            limit=min(20, max_items * 3),
            max_chars=MAX_CAPSULE_CHARS,
            kinds=kinds,
            memory_tiers=memory_tiers,
            include_restricted=include_restricted,
        )
        search_results = [
            {
                "asset_id": card.asset_id,
                "score": card.score,
                "hit_reason": card.hit_reason,
            }
            for card in search.results
        ]
        search_gaps = list(search.gaps)
        retrieval_fabric_selected = False
    else:
        if (
            not isinstance(retrieval_result, dict)
            or retrieval_result.get("schema_version") != "deeplaw.knowledge-retrieval/v1"
            or retrieval_result.get("vault_id") != vault.vault_id
            or retrieval_result.get("vault_revision") != vault.revision
            or retrieval_result.get("query") != context_query
            or not isinstance(retrieval_result.get("results"), list)
            or not isinstance(retrieval_result.get("gaps"), list)
        ):
            raise ValueError("context retrieval result does not match this vault and task")
        search_results = retrieval_result["results"]
        search_gaps = list(retrieval_result["gaps"])
        retrieval_fabric_selected = True
    if purpose_result is not None:
        if (
            not isinstance(purpose_result, dict)
            or purpose_result.get("schema_version")
            != "deeplaw.purpose-aware-retrieval/v2"
            or purpose_result.get("vault_id") != vault.vault_id
            or purpose_result.get("query") != context_query
            or not isinstance(purpose_result.get("compiled"), list)
            or not isinstance(purpose_result.get("evidence"), list)
            or not isinstance(purpose_result.get("gaps"), list)
        ):
            raise ValueError(
                "purpose-aware context result does not match this vault and task"
            )
        purpose_gaps = [
            f"{gap.get('code', 'retrieval_gap')}: {gap.get('message', '')}".rstrip()
            for gap in purpose_result["gaps"]
            if isinstance(gap, dict)
        ]
        search_gaps = [*purpose_gaps, *search_gaps]
        admitted_evidence = [
            item
            for item in purpose_result["evidence"]
            if isinstance(item, dict) and isinstance(item.get("asset_id"), str)
        ]
        if admitted_evidence:
            search_results = [
                {
                    "asset_id": item["asset_id"],
                    "hit_reason": "retrieval_fabric:lexical",
                }
                for item in admitted_evidence
            ]
            retrieval_fabric_selected = True
        elif purpose_result["compiled"]:
            search_results = []
            missing_compiled_assets = 0
            seen_asset_ids: set[str] = set()
            for item in purpose_result["compiled"]:
                semantic_key = (
                    item.get("semantic_key") if isinstance(item, dict) else None
                )
                if not isinstance(semantic_key, str):
                    missing_compiled_assets += 1
                    continue
                asset = vault.active_asset_for_semantic_key(semantic_key)
                if asset is None or asset.asset_id in seen_asset_ids:
                    missing_compiled_assets += 1
                    continue
                seen_asset_ids.add(asset.asset_id)
                search_results.append(
                    {
                        "asset_id": asset.asset_id,
                        "hit_reason": "retrieval_fabric:compiled_admission",
                    }
                )
            if missing_compiled_assets:
                search_gaps.append(
                    "compiled_admission_gap: "
                    f"{missing_compiled_assets} admitted compiled object(s) had no "
                    "active Knowledge Capsule v1 projection"
                )
            retrieval_fabric_selected = True
        elif not purpose_result["compiled"] and not any(
            isinstance(gap, dict)
            and gap.get("code") == "evidence_gap"
            and "without exact Source Revision bindings"
            in str(gap.get("message", ""))
            for gap in purpose_result["gaps"]
        ):
            search_results = []
            retrieval_fabric_selected = True
    ranked: list[KnowledgeAsset] = []
    selection_reasons: dict[str, str] = {}
    excluded_by_relevance = 0
    top_lexical_score = (
        search_results[0]["score"]
        if search_results and not retrieval_fabric_selected
        else None
    )
    from .retrieval.purpose import _policy_designator_conflicts, _policy_designators

    query_policy_designators = _policy_designators(context_query)
    for card in search_results:
        asset = vault.get_asset(card["asset_id"])
        if _policy_designator_conflicts(
            query_policy_designators,
            {
                "title": asset.title,
                "semantic_key": asset.semantic_key,
                "content": asset.statement,
            },
        ):
            excluded_by_relevance += 1
            continue
        if not _context_candidate_admitted(
            asset,
            query=context_query,
            terms=query_terms,
        ) and not retrieval_fabric_selected:
            excluded_by_relevance += 1
            continue
        if (
            top_lexical_score is not None
            and card["score"] < top_lexical_score * MIN_RELATIVE_LEXICAL_SCORE
        ):
            excluded_by_relevance += 1
            continue
        ranked.append(asset)
        if retrieval_fabric_selected:
            hit_reason = card.get("hit_reason", "retrieval_fabric:lexical")
            if not isinstance(hit_reason, str) or not hit_reason.startswith(
                "retrieval_fabric:"
            ):
                raise ValueError("context retrieval result contains an invalid hit reason")
            selection_reasons[asset.asset_id] = hit_reason
        else:
            selection_reasons[asset.asset_id] = "lexical_match"

    seed_ids = tuple(asset.asset_id for asset in ranked[:20])
    if seed_ids and not retrieval_fabric_selected:
        discovery_relations = vault.relations_for_assets(
            seed_ids,
            limit=min(64, max(16, max_items * 4)),
            include_restricted=include_restricted,
        )
        seed_id_set = set(seed_ids)
        for relation in discovery_relations:
            subject_id = relation["subject_asset_id"]
            object_id = relation["object_asset_id"]
            if subject_id in seed_id_set and object_id not in selection_reasons:
                neighbor_id = object_id
                seed_id = subject_id
            elif object_id in seed_id_set and subject_id not in selection_reasons:
                neighbor_id = subject_id
                seed_id = object_id
            else:
                continue
            neighbor = vault.get_asset(neighbor_id)
            if kinds and neighbor.kind not in kinds:
                continue
            if memory_tiers and neighbor.memory_tier not in memory_tiers:
                continue
            ranked.append(neighbor)
            selection_reasons[neighbor_id] = (
                f"reviewed_relation:{relation['predicate']}:{seed_id}"
            )
            if len(ranked) >= min(20, max_items * 3):
                break

    verified_ranked: list[KnowledgeAsset] = []
    excluded_by_integrity = 0
    for asset in ranked:
        if not vault.verify_asset(asset.asset_id)["valid"]:
            excluded_by_integrity += 1
            continue
        verified_ranked.append(asset)
    diversified, duplicate_parts = _deduplicate_compiled_parts(verified_ranked)
    excluded_by_relevance += duplicate_parts
    selected, excluded_by_budget = _select_budgeted_assets(
        diversified,
        max_items=max_items,
        max_chars=max_chars,
    )
    selected, excluded_by_provenance_budget = _select_provenance_budgeted_assets(
        selected
    )
    excluded_by_budget += excluded_by_provenance_budget
    projected_contents = _project_asset_contents(
        selected,
        query=context_query,
        max_chars=max_chars,
    )
    while selected and sum(estimate_tokens(content) for content in projected_contents) > max_tokens:
        selected.pop()
        projected_contents.pop()
        excluded_by_budget += 1

    source_refs_by_asset: dict[str, list[dict[str, str]]] = {
        asset.asset_id: [] for asset in selected
    }
    selected_source_refs = 0
    selected_source_ref_chars = 0
    excluded_source_refs = 0
    for asset in selected:
        if not asset.source_refs:
            continue
        reference_payload = asset.source_refs[0].to_dict()
        reference_chars = len(canonical_json(reference_payload))
        if (
            selected_source_refs >= MAX_CAPSULE_SOURCE_REFS
            or selected_source_ref_chars + reference_chars
            > MAX_CAPSULE_SOURCE_REF_CHARS
        ):
            raise RuntimeError(
                "selected source-bound knowledge lost its reserved provenance budget"
            )
        source_refs_by_asset[asset.asset_id].append(reference_payload)
        selected_source_refs += 1
        selected_source_ref_chars += reference_chars
    for asset in selected:
        for reference in asset.source_refs[1:]:
            reference_payload = reference.to_dict()
            reference_chars = len(canonical_json(reference_payload))
            if (
                selected_source_refs >= MAX_CAPSULE_SOURCE_REFS
                or selected_source_ref_chars + reference_chars
                > MAX_CAPSULE_SOURCE_REF_CHARS
            ):
                excluded_source_refs += 1
                continue
            source_refs_by_asset[asset.asset_id].append(reference_payload)
            selected_source_refs += 1
            selected_source_ref_chars += reference_chars

    item_payloads: list[dict[str, Any]] = []
    selected_chars = 0
    for asset, content in zip(selected, projected_contents, strict=True):
        item_payloads.append(
            _capsule_item(
                asset,
                content=content,
                source_refs=source_refs_by_asset[asset.asset_id],
                selection_reason=selection_reasons[asset.asset_id],
            )
        )
        selected_chars += len(content)

    constraints = [
        item
        for item in item_payloads
        if item["kind"] == "constraint" and item["directive_mode"] == "reviewed_instruction"
    ]
    decisions = [item for item in item_payloads if item["kind"] == "decision"]
    experiences = [
        item for item in item_payloads if item["kind"] in {"experience", "lesson"}
    ]
    open_questions = [item for item in item_payloads if item["kind"] == "question"]
    classified_ids = {
        item["asset_id"]
        for group in (constraints, decisions, experiences, open_questions)
        for item in group
    }
    knowledge_assets = [
        item for item in item_payloads if item["asset_id"] not in classified_ids
    ]
    evidence: list[dict[str, Any]] = []
    seen_fragments: set[str] = set()
    for item in item_payloads:
        for reference in item["source_refs"]:
            if reference["fragment_id"] in seen_fragments:
                continue
            seen_fragments.add(reference["fragment_id"])
            evidence.append(
                {
                    "asset_id": item["asset_id"],
                    **reference,
                }
            )
    selected_id_set = {asset.asset_id for asset in selected}
    relations = [
        relation
        for relation in vault.relations_for_assets(
            (asset.asset_id for asset in selected),
            limit=min(16, max_items * 2),
            include_restricted=include_restricted,
            require_evidence=retrieval_fabric_selected,
        )
        if relation["subject_asset_id"] in selected_id_set
        and relation["object_asset_id"] in selected_id_set
    ]
    gaps = search_gaps
    contradiction_count = sum(
        relation["predicate"] == "contradicts" for relation in relations
    )
    if contradiction_count:
        gaps.append(
            f"{contradiction_count} reviewed contradiction relation(s) require resolution"
        )
    if excluded_by_budget:
        gaps.append(
            f"{excluded_by_budget} relevant assets were excluded by the explicit context budget"
        )
    if excluded_source_refs:
        gaps.append(
            f"{excluded_source_refs} source reference(s) were excluded by the "
            "explicit Capsule metadata budget; use the asset URI for focused verification"
        )
    if excluded_by_relevance:
        gaps.append(
            f"{excluded_by_relevance} weak lexical candidate(s) were rejected by "
            "the deterministic context admission gate"
        )
    if excluded_by_integrity:
        gaps.append(
            f"{excluded_by_integrity} candidate asset(s) failed current source/integrity "
            "verification and were excluded"
        )
    if not constraints:
        gaps.append("no reviewed task constraint matched")
    capsule: dict[str, Any] = {
        "schema_version": KNOWLEDGE_CAPSULE_SCHEMA,
        "compiler_schema": CONTEXT_COMPILER_SCHEMA,
        "vault_id": vault.vault_id,
        "vault_revision": vault.revision,
        "audit_head": vault.audit_head,
        "generated_at": utc_now(),
        "task": task,
        "goal": goal,
        "budget": {
            "max_items": max_items,
            "max_chars": max_chars,
            "selected_items": len(selected),
            "selected_chars": selected_chars,
            "excluded_by_budget": excluded_by_budget,
            "max_source_refs": MAX_CAPSULE_SOURCE_REFS,
            "selected_source_refs": selected_source_refs,
            "excluded_source_refs": excluded_source_refs,
            "max_source_ref_chars": MAX_CAPSULE_SOURCE_REF_CHARS,
            "selected_source_ref_chars": selected_source_ref_chars,
            "max_payload_chars": MAX_CAPSULE_PAYLOAD_CHARS,
            "payload_chars": 0,
            "tokenizer_profile": TOKENIZER_PROFILE,
            "tokenizer_version": "2",
            "max_tokens": max_tokens,
            "selected_tokens": sum(estimate_tokens(content) for content in projected_contents),
            "token_count_mode": "estimated",
        },
        "trust_boundary": dict(_TRUST_BOUNDARY),
        "constraints": constraints,
        "decisions": decisions,
        "knowledge_assets": knowledge_assets,
        "experiences": experiences,
        "open_questions": open_questions,
        "relations": relations,
        "evidence": evidence,
        "gaps": gaps,
        "next_actions": [
            f"Review unresolved question asset {item['uri']}"
            for item in open_questions[:3]
        ],
    }
    return _seal_capsule(capsule)


def verify_capsule(
    capsule: dict[str, Any],
    *,
    vault: KnowledgeVault | None = None,
) -> dict[str, Any]:
    if not isinstance(capsule, dict):
        raise ValueError("knowledge capsule must be an object")
    if capsule.get("schema_version") == "deeplaw.knowledge-capsule/v2":
        return _verify_autonomous_capsule(capsule, vault=vault)
    _validate_capsule_contract(capsule)
    expected_fields = {
        "schema_version",
        "compiler_schema",
        "vault_id",
        "vault_revision",
        "audit_head",
        "generated_at",
        "task",
        "goal",
        "budget",
        "trust_boundary",
        "constraints",
        "decisions",
        "knowledge_assets",
        "experiences",
        "open_questions",
        "relations",
        "evidence",
        "gaps",
        "next_actions",
        "capsule_digest",
        "capsule_id",
    }
    if set(capsule) != expected_fields:
        raise ValueError("knowledge capsule does not match its closed contract")
    if capsule.get("schema_version") != KNOWLEDGE_CAPSULE_SCHEMA:
        raise ValueError("unsupported knowledge capsule schema")
    if capsule.get("compiler_schema") != CONTEXT_COMPILER_SCHEMA:
        raise ValueError("unsupported context compiler schema")
    try:
        canonical_timestamp(capsule.get("generated_at"), field="capsule generated_at")
    except (TypeError, ValueError) as error:
        raise ValueError("knowledge capsule generated_at is invalid") from error
    groups: dict[str, list[dict[str, Any]]] = {}
    all_items: list[dict[str, Any]] = []
    for group_name in (
        "constraints",
        "decisions",
        "knowledge_assets",
        "experiences",
        "open_questions",
    ):
        group = capsule.get(group_name)
        if (
            not isinstance(group, list)
            or len(group) > MAX_CAPSULE_ITEMS
            or any(not isinstance(item, dict) for item in group)
        ):
            raise ValueError(f"knowledge capsule {group_name} must be a bounded object array")
        groups[group_name] = group
        all_items.extend(group)
    for item in all_items:
        source_refs = item.get("source_refs")
        tags = item.get("tags")
        if (
            set(item) != _CAPSULE_ITEM_FIELDS
            or not isinstance(item.get("asset_id"), str)
            or not isinstance(item.get("content"), str)
            or not item["content"]
            or not isinstance(item.get("content_sha256"), str)
            or not isinstance(item.get("uri"), str)
            or item.get("legal_authority") is not False
            or not isinstance(item.get("selection_reason"), str)
            or not isinstance(source_refs, list)
            or len(source_refs) > MAX_CAPSULE_SOURCE_REFS
            or any(not isinstance(reference, dict) for reference in source_refs)
            or not isinstance(item.get("source_ref_count"), int)
            or isinstance(item.get("source_ref_count"), bool)
            or item["source_ref_count"] < len(source_refs)
            or (item["source_ref_count"] > 0 and not source_refs)
            or item.get("source_refs_truncated")
            is not (item["source_ref_count"] > len(source_refs))
            or not isinstance(tags, list)
            or len(tags) > MAX_CAPSULE_TAGS_PER_ITEM
            or any(not isinstance(tag, str) or not tag for tag in tags)
            or not isinstance(item.get("tag_count"), int)
            or isinstance(item.get("tag_count"), bool)
            or item["tag_count"] < len(tags)
            or item.get("tags_truncated") is not (item["tag_count"] > len(tags))
        ):
            if (
                isinstance(item.get("source_ref_count"), int)
                and not isinstance(item.get("source_ref_count"), bool)
                and item["source_ref_count"] > 0
                and source_refs == []
            ):
                raise ValueError(
                    "knowledge capsule contains a source-bound asset without embedded provenance"
                )
            raise ValueError("knowledge capsule contains an invalid asset item")
    asset_ids = [item["asset_id"] for item in all_items]
    if len(all_items) > MAX_CAPSULE_ITEMS or len(set(asset_ids)) != len(asset_ids):
        raise ValueError("knowledge capsule contains too many or duplicate assets")
    for item in all_items:
        if item["uri"] != (
            f"deeplaw://{capsule['vault_id']}/assets/{item['asset_id']}"
        ):
            raise ValueError("knowledge capsule contains an invalid asset URI")
    if (
        any(
            item["kind"] != "constraint"
            or item["directive_mode"] != "reviewed_instruction"
            for item in groups["constraints"]
        )
        or any(item["kind"] != "decision" for item in groups["decisions"])
        or any(
            item["kind"] not in {"experience", "lesson"}
            for item in groups["experiences"]
        )
        or any(item["kind"] != "question" for item in groups["open_questions"])
    ):
        raise ValueError("knowledge capsule asset classification is invalid")
    budget = capsule.get("budget")
    selected_chars = sum(
        len(item["content"]) for item in all_items
    )
    selected_source_refs = sum(len(item["source_refs"]) for item in all_items)
    selected_source_ref_chars = sum(
        len(canonical_json(reference))
        for item in all_items
        for reference in item["source_refs"]
    )
    legacy_budget_fields = {
        "max_items",
        "max_chars",
        "selected_items",
        "selected_chars",
        "excluded_by_budget",
        "max_source_refs",
        "selected_source_refs",
        "excluded_source_refs",
        "max_source_ref_chars",
        "selected_source_ref_chars",
        "max_payload_chars",
        "payload_chars",
    }
    token_budget_fields = {
        "tokenizer_profile",
        "tokenizer_version",
        "max_tokens",
        "selected_tokens",
        "token_count_mode",
    }
    expected_budget_fields = legacy_budget_fields | token_budget_fields
    if (
        not isinstance(budget, dict)
        or frozenset(budget)
        not in {frozenset(legacy_budget_fields), frozenset(expected_budget_fields)}
        or isinstance(budget.get("max_items"), bool)
        or not isinstance(budget.get("max_items"), int)
        or not 1 <= budget["max_items"] <= MAX_CAPSULE_ITEMS
        or isinstance(budget.get("max_chars"), bool)
        or not isinstance(budget.get("max_chars"), int)
        or not 1 <= budget["max_chars"] <= MAX_CAPSULE_CHARS
        or budget.get("selected_items") != len(all_items)
        or len(all_items) > budget["max_items"]
        or budget.get("selected_chars") != selected_chars
        or selected_chars > budget["max_chars"]
        or isinstance(budget.get("excluded_by_budget"), bool)
        or not isinstance(budget.get("excluded_by_budget"), int)
        or budget["excluded_by_budget"] < 0
        or budget.get("max_source_refs") != MAX_CAPSULE_SOURCE_REFS
        or budget.get("selected_source_refs") != selected_source_refs
        or selected_source_refs > MAX_CAPSULE_SOURCE_REFS
        or isinstance(budget.get("excluded_source_refs"), bool)
        or not isinstance(budget.get("excluded_source_refs"), int)
        or budget["excluded_source_refs"] < 0
        or budget.get("max_source_ref_chars") != MAX_CAPSULE_SOURCE_REF_CHARS
        or budget.get("selected_source_ref_chars") != selected_source_ref_chars
        or selected_source_ref_chars > MAX_CAPSULE_SOURCE_REF_CHARS
        or budget.get("max_payload_chars") != MAX_CAPSULE_PAYLOAD_CHARS
        or isinstance(budget.get("payload_chars"), bool)
        or not isinstance(budget.get("payload_chars"), int)
        or budget["payload_chars"] < 1
        or budget["payload_chars"] > MAX_CAPSULE_PAYLOAD_CHARS
    ):
        raise ValueError("knowledge capsule budget does not match its selected assets")
    if token_budget_fields.issubset(budget) and (
        budget["tokenizer_profile"] != TOKENIZER_PROFILE
        or budget["tokenizer_version"] != "2"
        or isinstance(budget["max_tokens"], bool)
        or not isinstance(budget["max_tokens"], int)
        or not 1 <= budget["max_tokens"] <= MAX_CAPSULE_TOKENS
        or isinstance(budget["selected_tokens"], bool)
        or not isinstance(budget["selected_tokens"], int)
        or budget["selected_tokens"] != sum(
            estimate_tokens(item["content"]) for item in all_items
        )
        or budget["selected_tokens"] > budget["max_tokens"]
        or budget["token_count_mode"] != "estimated"
    ):
        raise ValueError("knowledge capsule token budget is invalid")
    payload_accounting_valid = budget["payload_chars"] == len(canonical_json(capsule))
    if capsule.get("trust_boundary") != _TRUST_BOUNDARY:
        raise ValueError("knowledge capsule trust boundary is invalid")
    relations = capsule.get("relations")
    evidence = capsule.get("evidence")
    gaps = capsule.get("gaps")
    next_actions = capsule.get("next_actions")
    if (
        not isinstance(relations, list)
        or len(relations) > 16
        or any(not isinstance(relation, dict) for relation in relations)
        or not isinstance(evidence, list)
        or len(evidence) > MAX_CAPSULE_SOURCE_REFS
        or any(not isinstance(item, dict) for item in evidence)
        or not isinstance(gaps, list)
        or len(gaps) > 32
        or any(
            not isinstance(gap, str) or not gap or len(gap) > 1_000
            for gap in gaps
        )
        or not isinstance(next_actions, list)
        or len(next_actions) > 3
        or any(
            not isinstance(action, str) or not action or len(action) > 500
            for action in next_actions
        )
    ):
        raise ValueError("knowledge capsule metadata is invalid or unbounded")
    expected_next_actions = [
        f"Review unresolved question asset {item['uri']}"
        for item in groups["open_questions"][:3]
    ]
    if next_actions != expected_next_actions:
        raise ValueError("knowledge capsule next actions are not derived safely")
    relation_fields = {
        "relation_id",
        "subject_asset_id",
        "predicate",
        "object_asset_id",
        "evidence_fragment_id",
        "verification",
        "created_at",
    }
    if any(
        set(relation) != relation_fields
        or relation.get("verification") != "human_verified"
        for relation in relations
    ):
        raise ValueError("knowledge capsule relation is invalid")
    selected_id_set = set(asset_ids)
    for item in all_items:
        reason = item["selection_reason"]
        if reason == "lexical_match":
            continue
        if reason.startswith("retrieval_fabric:"):
            parts = reason.split(":")
            if len(parts) != 2 or parts[1] not in {
                "exact_id",
                "knowledge_key",
                "semantic_key",
                "exact_phrase",
                "lexical",
                "dense",
                "tree",
                "graph",
                "temporal",
                "feedback",
            }:
                raise ValueError("knowledge capsule retrieval selection reason is invalid")
            continue
        parts = reason.split(":")
        if (
            len(parts) != 3
            or parts[0] != "reviewed_relation"
            or parts[1] not in {
                "supports",
                "contradicts",
                "depends_on",
                "implements",
                "derived_from",
                "applies_to",
                "related_to",
            }
            or parts[2] not in selected_id_set
            or not any(
                relation["predicate"] == parts[1]
                and {
                    relation["subject_asset_id"],
                    relation["object_asset_id"],
                }
                == {item["asset_id"], parts[2]}
                for relation in relations
            )
        ):
            raise ValueError("knowledge capsule relation selection reason is invalid")
    source_reference_fields = {
        "source_id",
        "fragment_id",
        "locator",
        "quote_sha256",
    }
    expected_evidence: list[dict[str, Any]] = []
    seen_fragments: set[str] = set()
    for item in all_items:
        for reference in item["source_refs"]:
            if set(reference) != source_reference_fields:
                raise ValueError("knowledge capsule source reference is invalid")
            fragment_id = reference.get("fragment_id")
            if not isinstance(fragment_id, str):
                raise ValueError("knowledge capsule fragment ID is invalid")
            if fragment_id in seen_fragments:
                continue
            seen_fragments.add(fragment_id)
            expected_evidence.append({"asset_id": item["asset_id"], **reference})
    if evidence != expected_evidence:
        raise ValueError("knowledge capsule evidence does not match selected source references")
    expected_digest = sha256_bytes(canonical_json(_digest_body(capsule)).encode("utf-8"))
    expected_id = stable_id("capsule", str(capsule.get("vault_id")), expected_digest)
    digest_valid = capsule.get("capsule_digest") == expected_digest
    id_valid = capsule.get("capsule_id") == expected_id
    asset_checks: list[dict[str, Any]] = []
    relation_checks: list[dict[str, Any]] = []
    vault_matches = True
    audit_anchor_valid: bool | None = None
    if vault is not None:
        vault_matches = capsule.get("vault_id") == vault.vault_id
        revision = capsule.get("vault_revision")
        try:
            audit_anchor_valid = (
                vault_matches
                and vault.verify_integrity()["valid"]
                and vault.audit_hash_at(revision) == capsule.get("audit_head")
            )
        except (TypeError, ValueError):
            audit_anchor_valid = False
        retrieval_fabric_capsule = any(
            item.get("selection_reason", "").startswith("retrieval_fabric:")
            for item in all_items
        )
        current_relations = {
            relation["relation_id"]: relation
            for relation in vault.relations_for_assets(
                asset_ids,
                limit=64,
                include_restricted=True,
                require_evidence=retrieval_fabric_capsule,
            )
        }
        for relation in relations:
            relation_valid = (
                current_relations.get(relation.get("relation_id")) == relation
            )
            relation_checks.append(
                {
                    "relation_id": relation.get("relation_id"),
                    "valid": relation_valid,
                }
            )
        for group in groups.values():
            for item in group:
                try:
                    asset = vault.get_asset(item["asset_id"], include_inactive=True)
                    integrity = vault.verify_asset(asset.asset_id)
                    asset_source_refs = [
                        reference.to_dict() for reference in asset.source_refs
                    ]
                    item_source_refs = item["source_refs"]
                    asset_tags = list(asset.tags)
                    valid = (
                        asset.content_sha256 == item.get("content_sha256")
                        and asset.uri == item.get("uri")
                        and asset.kind == item.get("kind")
                        and asset.memory_tier == item.get("memory_tier")
                        and asset.title == item.get("title")
                        and _content_matches_asset(
                            asset.statement,
                            item.get("content"),
                        )
                        and asset.semantic_key == item.get("semantic_key")
                        and asset.verification == item.get("verification")
                        and asset.trust == item.get("trust")
                        and asset.sensitivity == item.get("sensitivity")
                        and asset.directive_mode == item.get("directive_mode")
                        and len(asset_source_refs) == item.get("source_ref_count")
                        and item.get("source_refs_truncated")
                        is (len(item_source_refs) < len(asset_source_refs))
                        and _is_ordered_subset(
                            item_source_refs,
                            asset_source_refs,
                        )
                        and len(asset_tags) == item.get("tag_count")
                        and item.get("tags_truncated")
                        is (len(item["tags"]) < len(asset_tags))
                        and item["tags"] == asset_tags[: len(item["tags"])]
                        and asset.expires_at == item.get("expires_at")
                        and asset.status == "active"
                        and asset.verification == "human_verified"
                        and (asset.expires_at is None or asset.expires_at > utc_now())
                        and integrity["integrity_valid"]
                    )
                    status = asset.status
                except (KeyError, TypeError, ValueError):
                    valid = False
                    status = "missing"
                    integrity = {"integrity_valid": False}
                asset_checks.append(
                    {
                        "asset_id": item.get("asset_id"),
                        "valid": valid,
                        "current_status": status,
                        "integrity_valid": integrity["integrity_valid"],
                    }
                )
    valid = (
        digest_valid
        and id_valid
        and payload_accounting_valid
        and vault_matches
        and audit_anchor_valid is not False
        and all(item["valid"] for item in asset_checks)
        and all(item["valid"] for item in relation_checks)
    )
    return {
        "schema_version": "deeplaw.knowledge-capsule-verification/v1",
        "capsule_id": capsule.get("capsule_id"),
        "expected_capsule_id": expected_id,
        "digest_valid": digest_valid,
        "id_valid": id_valid,
        "payload_accounting_valid": payload_accounting_valid,
        "vault_matches": vault_matches,
        "audit_anchor_valid": audit_anchor_valid,
        "asset_checks": asset_checks,
        "relation_checks": relation_checks,
        "vault_revision_at_compile": capsule.get("vault_revision"),
        "vault_revision_current": vault.revision if vault is not None else None,
        "stale": (
            vault is not None and capsule.get("vault_revision") != vault.revision
        ),
        "valid": valid,
    }


def verify_capsule_file(
    path: str | Path,
    *,
    vault: KnowledgeVault | None = None,
) -> dict[str, Any]:
    capsule_input = Path(path).expanduser().absolute()
    if capsule_input.is_symlink():
        raise ValueError("knowledge capsule file must be a regular non-symlink file")
    capsule_path = capsule_input.resolve(strict=True)
    if not capsule_path.is_file():
        raise ValueError("knowledge capsule file must be a regular non-symlink file")
    if capsule_path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("knowledge capsule exceeds the 4 MiB limit")
    try:
        value = strict_json_loads(capsule_path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("knowledge capsule is not valid UTF-8 JSON") from error
    return verify_capsule(value, vault=vault)
