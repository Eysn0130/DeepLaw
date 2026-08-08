from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, cast

from ..knowledge_autonomy import (
    SENSITIVITIES,
    AutonomousKnowledgeStore,
    _validate_contract,
    parse_knowledge_markdown,
)
from ..knowledge_intelligence import (
    estimate_tokens,
    normalize_identity_text,
    rerank_candidates,
)
from ..knowledge_models import canonical_timestamp, utc_now
from ..knowledge_store import KnowledgeVault
from ..retrieval_fabric import retrieve
from ..task_context import normalize_task_context_binding
from ..util import (
    QUERY_EXPANSION_PROFILE,
    canonical_json,
    query_discovery_text,
    query_expansion_terms,
    query_search_terms,
    search_terms,
    sha256_bytes,
    strict_json_loads,
)

QueryPurpose = Literal[
    "answer",
    "verify",
    "quote",
    "historical",
    "legal",
    "debug",
    "freshness_check",
]
QueryPolicy = Literal["compiled-first-v1", "evidence-first-v1", "balanced-v1"]
QueryPlanVersion = Literal["4", "5", "6"]

QUERY_PURPOSES: Final = frozenset(
    {
        "answer",
        "verify",
        "quote",
        "historical",
        "legal",
        "debug",
        "freshness_check",
    }
)
QUERY_POLICIES: Final = frozenset(
    {"compiled-first-v1", "evidence-first-v1", "balanced-v1"}
)
_DEFAULT_POLICY: Final[dict[str, QueryPolicy]] = {
    "answer": "compiled-first-v1",
    "verify": "evidence-first-v1",
    "quote": "evidence-first-v1",
    "historical": "evidence-first-v1",
    "legal": "evidence-first-v1",
    "debug": "balanced-v1",
    "freshness_check": "compiled-first-v1",
}


@contextmanager
def _purpose_read_stores(
    root: Path,
    runtime_snapshot: Any | None,
) -> Iterator[tuple[KnowledgeVault, AutonomousKnowledgeStore]]:
    """Yield one verified snapshot without re-verifying a warm MCP lifespan."""

    if runtime_snapshot is not None:
        if bool(getattr(runtime_snapshot, "closed", True)):
            raise RuntimeError("persistent knowledge read snapshot is closed")
        evidence_store = getattr(runtime_snapshot, "legacy", None)
        knowledge_store = getattr(runtime_snapshot, "store", None)
        if not isinstance(evidence_store, KnowledgeVault) or not isinstance(
            knowledge_store, AutonomousKnowledgeStore
        ):
            raise RuntimeError("persistent knowledge read snapshot is invalid")
        if evidence_store.root != root or knowledge_store.root != root:
            raise RuntimeError("persistent knowledge read snapshot belongs to another Vault")
        if evidence_store.audit_head != knowledge_store.legacy_audit_head:
            raise RuntimeError("knowledge read planes changed while opening a snapshot")
        yield evidence_store, knowledge_store
        return

    with (
        KnowledgeVault(root, read_only=True) as evidence_store,
        AutonomousKnowledgeStore(
            root,
            read_only=True,
            legacy_snapshot=evidence_store,
        ) as knowledge_store,
    ):
        legacy_integrity = evidence_store.verify_integrity()
        if not legacy_integrity["valid"]:
            raise RuntimeError("knowledge vault integrity is invalid; query stopped")
        if evidence_store.audit_head != knowledge_store.legacy_audit_head:
            raise RuntimeError("knowledge read planes changed while opening a snapshot")
        if not knowledge_store.verify(
            preverified_legacy_integrity=legacy_integrity,
            preverified_legacy_audit_head=evidence_store.audit_head,
        )["valid"]:
            raise RuntimeError("knowledge vault integrity is invalid; query stopped")
        yield evidence_store, knowledge_store
_POLICY_ORDER: Final[dict[QueryPolicy, tuple[str, ...]]] = {
    "compiled-first-v1": (
        "exact_identity",
        "fresh_synthesis",
        "concept",
        "entity",
        "claim",
        "decision",
        "procedure",
        "experience_memory",
        "typed_relations",
        "contradictions",
        "gaps_freshness",
        "source_evidence",
        "raw_fragment_fallback",
    ),
    "evidence-first-v1": (
        "source_evidence",
        "raw_fragment_fallback",
        "fresh_compiled_knowledge",
        "typed_relations",
        "contradictions",
        "gaps_freshness",
    ),
    "balanced-v1": (
        "exact_identity",
        "fresh_compiled_knowledge",
        "source_evidence",
        "typed_relations",
        "contradictions",
        "gaps_freshness",
        "raw_fragment_fallback",
    ),
}
_KIND_PRIORITY: Final = {
    "synthesis": 0,
    "concept": 1,
    "entity": 2,
    "claim": 3,
    "decision": 4,
    "procedure": 5,
    "experience": 6,
    "memory": 7,
    "event": 8,
    "comparison": 9,
    "preference": 10,
    "skill": 11,
}
_MAX_PROVIDER_CHARS: Final = 65_536
_MIN_COMPILED_RERANKER_SCORE: Final = 0.20
_MIN_TARGET_SYNTHESIS_RERANKER_SCORE: Final = 0.15
_MIN_GENERIC_SUMMARY_RERANKER_SCORE: Final = 0.25
_MIN_EVIDENCE_RERANKER_SCORE: Final = 0.10
_POLICY_DESIGNATOR: Final = re.compile(
    r"\bpolicy[\s:_-]+([a-z](?![a-z0-9])|[0-9][a-z0-9._-]*)",
    re.IGNORECASE,
)
_ZH_POLICY_DESIGNATOR: Final = re.compile(r"政策\s*([甲乙丙丁]|[A-Za-z0-9]+)")
_ZH_POLICY_KEYS: Final = {"甲": "a", "乙": "b", "丙": "c", "丁": "d"}
_ISO_DATE_ANCHOR: Final = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")
_KNOWLEDGE_DUTIES: Final = (
    "primary_answer",
    "definition",
    "temporal_freshness",
    "contradiction_or_counterevidence",
    "limitation",
    "source_evidence",
    "applicability",
    "unresolved_gap",
)


def _policy_designators(value: str) -> set[str]:
    values = {match.group(1).casefold() for match in _POLICY_DESIGNATOR.finditer(value)}
    values.update(
        _ZH_POLICY_KEYS.get(match.group(1), match.group(1).casefold())
        for match in _ZH_POLICY_DESIGNATOR.finditer(value)
    )
    return values


def _matches_structured_query_anchor(
    query_anchors: set[str], item: dict[str, Any]
) -> bool:
    if not query_anchors:
        return False
    values = [
        item.get("title"),
        item.get("semantic_key"),
        item.get("content"),
        item.get("valid_from"),
        item.get("valid_to"),
    ]
    candidate_anchors = {
        anchor
        for value in values
        if isinstance(value, str)
        for anchor in _ISO_DATE_ANCHOR.findall(value)
    }
    return bool(query_anchors.intersection(candidate_anchors))


def _is_comparison_query(normalized_query: str, query: str) -> bool:
    return any(term in normalized_query for term in ("compare", "conflict")) or any(
        term in query for term in ("比较", "对照", "冲突", "矛盾")
    )


def _policy_designator_conflicts(
    query_designators: set[str], item: dict[str, Any]
) -> bool:
    """Reject a different named policy as an answer to an exact policy query."""

    if not query_designators:
        return False
    aliases = item.get("metadata", {}).get("aliases", [])
    values = [
        item.get("title"),
        item.get("semantic_key"),
        item.get("content"),
        item.get("excerpt"),
    ]
    if isinstance(aliases, list):
        values.extend(aliases)
    candidate_designators = {
        designator
        for value in values
        if isinstance(value, str)
        for designator in _policy_designators(value)
    }
    return bool(candidate_designators and query_designators.isdisjoint(candidate_designators))


def _has_exact_identifier_overlap(query: str, item: dict[str, Any]) -> bool:
    query_identifiers = {
        term.casefold()
        for term in search_terms(query, limit=256, cover_tail=True)
        if len(term) >= 6 and any(character.isdigit() for character in term)
    }
    if not query_identifiers:
        return False
    candidate_text = " ".join(
        str(value)
        for value in (
            item.get("title"),
            item.get("semantic_key"),
            item.get("content"),
            item.get("excerpt"),
        )
        if isinstance(value, str)
    )
    candidate_terms = {
        term.casefold()
        for term in search_terms(candidate_text, limit=256, cover_tail=True)
    }
    return bool(query_identifiers.intersection(candidate_terms))


@dataclass(frozen=True)
class _EvidenceSelection:
    cards: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    selected_characters: int
    selected_source_revision_ids: list[str]
    selected_fragment_ids: list[str]


class PurposeAwareRetrievalService:
    """Read-only, purpose-aware retrieval over governed knowledge and evidence."""

    def __init__(self, path: str | Path) -> None:
        self.root = Path(path).expanduser().absolute()

    def query(
        self,
        query: str,
        *,
        purpose: QueryPurpose = "answer",
        policy: QueryPolicy | None = None,
        scope: str | None = None,
        max_sensitivity: str = "private",
        limit: int = 8,
        max_chars: int = 8_000,
        max_tokens: int = 6_000,
        max_sources: int = 12,
        graph_hops: int = 1,
        retrieval_mode: str = "hybrid",
        as_of: str | None = None,
        kinds: tuple[str, ...] = (),
        query_plan_version: QueryPlanVersion = "6",
        force_canonical_lexical: bool = False,
        query_target: str | dict[str, Any] | None = None,
        applicable_duties: tuple[str, ...] | list[str] | None = None,
        projection: str = "standard",
        task_binding: dict[str, Any] | None = None,
        _runtime_snapshot: Any | None = None,
    ) -> dict[str, Any]:
        selected_query = self._bounded_query(query)
        if purpose not in QUERY_PURPOSES:
            raise ValueError("query purpose is invalid")
        if query_plan_version not in {"4", "5", "6"}:
            raise ValueError("query plan version is invalid")
        normalized_task_binding = normalize_task_context_binding(
            task_binding,
            allow_none=True,
        )
        if query_plan_version != "6" and normalized_task_binding is not None:
            raise ValueError("task_binding requires query_plan_version=6")
        if query_plan_version == "6" and projection not in {
            "compact",
            "standard",
            "audit",
        }:
            raise ValueError("query projection is invalid")
        selected_policy = policy or _DEFAULT_POLICY[purpose]
        if selected_policy not in QUERY_POLICIES:
            raise ValueError("query policy is invalid")
        if purpose == "legal" and selected_policy != "evidence-first-v1":
            raise ValueError("legal queries require evidence-first-v1")
        if purpose == "historical" and as_of is None:
            raise ValueError("historical query purpose requires as_of")
        selected_as_of = (
            canonical_timestamp(as_of, field="purpose-aware query as_of")
            if as_of is not None
            else None
        )
        if max_sensitivity not in SENSITIVITIES or max_sensitivity == "restricted":
            raise ValueError("purpose-aware query sensitivity is invalid")
        if not 1 <= limit <= 20 or not 200 <= max_chars <= 20_000:
            raise ValueError("purpose-aware query item or character budget is invalid")
        if not 128 <= max_tokens <= 32_000 or not 1 <= max_sources <= 32:
            raise ValueError("purpose-aware query token or source budget is invalid")
        if graph_hops not in {0, 1, 2}:
            raise ValueError("purpose-aware query graph-hop budget is invalid")
        if retrieval_mode not in {"exact", "lexical", "dense", "graph", "hybrid"}:
            raise ValueError("purpose-aware query retrieval mode is invalid")
        if not isinstance(force_canonical_lexical, bool):
            raise ValueError("purpose-aware canonical lexical control is invalid")

        with _purpose_read_stores(self.root, _runtime_snapshot) as (
            evidence_store,
            knowledge_store,
        ):
            selected_scope = scope or knowledge_store.vault_scope
            if selected_scope not in {"personal", "project", "domain"}:
                raise ValueError("purpose-aware query scope is invalid")

            if purpose == "legal":
                if query_plan_version == "6":
                    from .query_v6 import execute_v6

                    return execute_v6(
                        self,
                        evidence_store=evidence_store,
                        knowledge_store=knowledge_store,
                        query=selected_query,
                        purpose=purpose,
                        policy=selected_policy,
                        scope=selected_scope,
                        max_sensitivity=max_sensitivity,
                        limit=limit,
                        max_chars=max_chars,
                        max_tokens=max_tokens,
                        max_sources=max_sources,
                        graph_hops=graph_hops,
                        retrieval_mode=retrieval_mode,
                        as_of=selected_as_of,
                        kinds=kinds,
                        force_canonical_lexical=force_canonical_lexical,
                        query_target=query_target,
                        applicable_duties=applicable_duties,
                        projection=projection,
                        task_binding=normalized_task_binding,
                    )
                result = self._legal_boundary_result(
                    query=selected_query,
                    policy=selected_policy,
                    scope=selected_scope,
                    max_sensitivity=max_sensitivity,
                    as_of=selected_as_of,
                    limit=limit,
                    max_chars=max_chars,
                    max_tokens=max_tokens,
                    max_sources=max_sources,
                    graph_hops=graph_hops,
                    retrieval_mode=retrieval_mode,
                    audit_head=knowledge_store.audit_head,
                    legacy_audit_head=knowledge_store.legacy_audit_head,
                )
                if query_plan_version == "5":
                    result = self._upgrade_to_v5(
                        store=knowledge_store,
                        result=result,
                    )
                    _validate_contract("purpose-aware-retrieval.v2.schema.json", result)
                else:
                    _validate_contract("purpose-aware-retrieval.v1.schema.json", result)
                return result

            if query_plan_version == "6":
                from .query_v6 import execute_v6

                return execute_v6(
                    self,
                    evidence_store=evidence_store,
                    knowledge_store=knowledge_store,
                    query=selected_query,
                    purpose=purpose,
                    policy=selected_policy,
                    scope=selected_scope,
                    max_sensitivity=max_sensitivity,
                    limit=limit,
                    max_chars=max_chars,
                    max_tokens=max_tokens,
                    max_sources=max_sources,
                    graph_hops=graph_hops,
                    retrieval_mode=retrieval_mode,
                    as_of=selected_as_of,
                    kinds=kinds,
                    force_canonical_lexical=force_canonical_lexical,
                    query_target=query_target,
                    applicable_duties=applicable_duties,
                    projection=projection,
                    task_binding=normalized_task_binding,
                )

            compiled_budget, evidence_budget = self._partition_budget(
                selected_policy,
                limit=limit,
                max_chars=max_chars,
            )
            compiled_graph_hops = 0 if purpose == "freshness_check" else graph_hops
            compiled = self._compiled(
                knowledge_store,
                query=selected_query,
                purpose=purpose,
                scope=selected_scope,
                max_sensitivity=max_sensitivity,
                limit=(
                    min(20, max(compiled_budget["items"], compiled_budget["items"] * 3))
                    if query_plan_version == "5"
                    else compiled_budget["items"]
                ),
                selection_limit=compiled_budget["items"],
                duty_aware=query_plan_version == "5",
                max_chars=compiled_budget["characters"],
                max_tokens=max_tokens,
                max_sources=max_sources,
                graph_hops=compiled_graph_hops,
                retrieval_mode=retrieval_mode,
                as_of=selected_as_of,
                kinds=kinds,
                force_canonical_lexical=force_canonical_lexical,
            )
            compiled["results"], integrity_gaps = self._admit_compiled_source_bytes(
                evidence_store,
                knowledge_store,
                compiled=compiled["results"],
            )
            selected_knowledge_ids = {
                str(item.get("knowledge_id")) for item in compiled["results"]
            }
            compiled["contradictions"] = [
                item
                for item in compiled["contradictions"]
                if (
                    item.get("knowledge_id") in selected_knowledge_ids
                    or (
                        item.get("subject_knowledge_id") in selected_knowledge_ids
                        and item.get("object_knowledge_id") in selected_knowledge_ids
                    )
                )
            ]
            boundary_target_blocked = self._query_targets_outside_admission(
                knowledge_store,
                query=selected_query,
                scope=selected_scope,
                max_sensitivity=max_sensitivity,
                admitted=compiled["results"],
            )
            if boundary_target_blocked:
                compiled["results"] = []
                compiled["contradictions"] = []
            fallback_requested = False
            evidence_requested = evidence_budget["items"] > 0
            if (
                selected_policy == "compiled-first-v1"
                and not compiled["results"]
                and purpose != "freshness_check"
            ):
                fallback_requested = True
                evidence_requested = True
                evidence_budget = {
                    "items": min(5, limit),
                    "characters": min(6_000, max_chars),
                }
            evidence = (
                self._evidence(
                    evidence_store,
                    knowledge_store,
                    query=selected_query,
                    scope=selected_scope,
                    max_sensitivity=max_sensitivity,
                    limit=evidence_budget["items"],
                    max_chars=evidence_budget["characters"],
                    as_of=selected_as_of,
                    kinds=kinds,
                    compiled=compiled["results"],
                )
                if evidence_requested and not boundary_target_blocked
                else _EvidenceSelection([], [], 0, [], [])
            )
            fallback_reason = (
                "no_fresh_compiled_match"
                if fallback_requested and evidence.cards
                else None
            )
            fallback_unavailable_reason = (
                "no_admitted_target_evidence"
                if fallback_requested and not evidence.cards
                else None
            )
            gaps = [
                *integrity_gaps,
                *(
                    [
                        {
                            "code": "retrieval_gap",
                            "message": (
                                "No content admitted by the requested scope and "
                                "sensitivity matched the query."
                            ),
                        }
                    ]
                    if boundary_target_blocked
                    else []
                ),
                *compiled["freshness_gaps"],
                *self._stale_knowledge_gaps(
                    knowledge_store,
                    query=selected_query,
                    scope=selected_scope,
                    max_sensitivity=max_sensitivity,
                    limit=16,
                ),
                *evidence.gaps,
            ]
            uncompiled = self._uncompiled_sources(
                knowledge_store,
                query=selected_query,
                scope=selected_scope,
                max_sensitivity=max_sensitivity,
                limit=16,
            )
            if uncompiled:
                gaps.append(
                    {
                        "code": "uncompiled_source",
                        "message": (
                            "Relevant admitted Source Revisions have no successful compilation."
                        ),
                        "count": len(uncompiled),
                        "source_revision_ids": [
                            item["source_revision_id"] for item in uncompiled
                        ],
                    }
                )
            if fallback_reason is not None:
                gaps.append(
                    {
                        "code": "source_fallback",
                        "message": (
                            "Evidence fallback was used because no fresh compiled match "
                            "was admitted."
                        ),
                        "count": len(evidence.cards),
                        "source_revision_ids": evidence.selected_source_revision_ids,
                    }
                )
            gaps.extend(
                {"code": "retrieval_gap", "message": message}
                for message in compiled["gaps"]
            )
            if query_plan_version == "5":
                gaps.extend(
                    self._partial_compilation_gaps(
                        knowledge_store,
                        compiled=compiled["results"],
                        evidence_source_revision_ids=evidence.selected_source_revision_ids,
                    )
                )
            evidence_attachment_count = self._evidence_attachment_count(
                compiled["results"]
            )
            stale_selection_prevented_count = sum(
                1 for gap in gaps if gap.get("code") == "stale_knowledge"
            )
            selected_items = len(compiled["results"]) + len(evidence.cards)
            selected_chars = sum(
                len(str(item.get("content", ""))) for item in compiled["results"]
            ) + evidence.selected_characters
            plan = {
                "schema_version": "deeplaw.knowledge-query-plan/v4",
                "intent": "purpose_aware_knowledge_retrieval",
                "purpose": purpose,
                "policy_id": selected_policy,
                "query_sha256": sha256_bytes(selected_query.encode("utf-8")),
                "channel_order": list(_POLICY_ORDER[selected_policy]),
                "used_channels": self._used_channels(
                    compiled=compiled["results"],
                    evidence=evidence.cards,
                    fallback=fallback_reason is not None,
                ),
                "scope": selected_scope,
                "max_sensitivity": max_sensitivity,
                "as_of": selected_as_of,
                "filters": {"kinds": sorted(kinds)},
                "budget": {
                    "items": limit,
                    "characters": max_chars,
                    "tokens": max_tokens,
                    "sources": max_sources,
                    "graph_hops": graph_hops,
                    "provider_characters": 65_536,
                },
                "input_audit_head": knowledge_store.audit_head,
                "input_legacy_audit_head": knowledge_store.legacy_audit_head,
                "compiled_candidate_count": compiled["candidate_count"],
                "compiled_selected_count": len(compiled["results"]),
                "evidence_selected_count": len(evidence.cards),
                "uncompiled_source_count": len(uncompiled),
                "stale_selection_prevented_count": stale_selection_prevented_count,
                "evidence_attachment_count": evidence_attachment_count,
                "fallback": {
                    "used": fallback_reason is not None,
                    "reason": fallback_reason or fallback_unavailable_reason,
                    "source_revision_ids": evidence.selected_source_revision_ids,
                    "selected_fragment_ids": evidence.selected_fragment_ids,
                    "characters": (
                        evidence.selected_characters if fallback_reason is not None else 0
                    ),
                    "tokens": (
                        estimate_tokens(
                            " ".join(
                                str(item.get("excerpt", "")) for item in evidence.cards
                            )
                        )
                        if fallback_reason is not None
                        else 0
                    ),
                    "new_synthesis_created": False,
                },
                "created_at": utc_now(),
            }
            _validate_contract("knowledge-query-plan.v4.schema.json", plan)
            result = {
                "schema_version": "deeplaw.purpose-aware-retrieval/v1",
                "vault_id": knowledge_store.vault_id,
                "purpose": purpose,
                "policy_id": selected_policy,
                "query": selected_query,
                "query_plan": plan,
                "query_plan_sha256": sha256_bytes(
                    canonical_json(plan).encode("utf-8")
                ),
                "compiled": compiled["results"],
                "evidence": evidence.cards,
                "contradictions": compiled["contradictions"],
                "gaps": self._bounded_gaps(gaps),
                "metrics": {
                    "compiled_hit": bool(compiled["results"]),
                    "source_fallback_used": fallback_reason is not None,
                    "uncompiled_source_count": len(uncompiled),
                    "stale_selection_prevented_count": (
                        stale_selection_prevented_count
                    ),
                    "evidence_attachment_count": evidence_attachment_count,
                    "repeated_query_reused_compilation": bool(
                        compiled["results"] and not fallback_reason
                    ),
                },
                "budget": {
                    "max_items": limit,
                    "selected_items": selected_items,
                    "max_characters": max_chars,
                    "selected_characters": selected_chars,
                    "max_tokens": max_tokens,
                    "max_provider_characters": _MAX_PROVIDER_CHARS,
                },
                "audit_head": knowledge_store.audit_head,
                "authority_changed_by_ranking": False,
                "write_performed": False,
            }
            if query_plan_version == "5":
                result = self._upgrade_to_v5(store=knowledge_store, result=result)
            if (
                len(canonical_json(result).encode("utf-8"))
                > _MAX_PROVIDER_CHARS
            ):
                raise RuntimeError("purpose-aware retrieval exceeds its hard 64 KiB budget")
            _validate_contract(
                (
                    "purpose-aware-retrieval.v2.schema.json"
                    if query_plan_version == "5"
                    else "purpose-aware-retrieval.v1.schema.json"
                ),
                result,
            )
            return result

    @staticmethod
    def _bounded_query(value: str) -> str:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or not value
            or len(value) > 5_000
        ):
            raise ValueError("purpose-aware query must be bounded canonical text")
        return value

    @staticmethod
    def _partition_budget(
        policy: QueryPolicy,
        *,
        limit: int,
        max_chars: int,
    ) -> tuple[dict[str, int], dict[str, int]]:
        if policy == "compiled-first-v1":
            return (
                {"items": limit, "characters": max_chars},
                {"items": 0, "characters": 0},
            )
        if policy == "evidence-first-v1":
            evidence_items = min(5, max(1, (limit + 1) // 2))
            compiled_items = limit - evidence_items
        else:
            compiled_items = max(1, (limit + 1) // 2)
            evidence_items = min(5, limit - compiled_items)
        if compiled_items == 0:
            return (
                {"items": 0, "characters": 0},
                {"items": evidence_items, "characters": min(6_000, max_chars)},
            )
        if evidence_items == 0:
            return (
                {"items": compiled_items, "characters": max_chars},
                {"items": 0, "characters": 0},
            )
        if max_chars < 400:
            if policy == "evidence-first-v1":
                return (
                    {"items": 0, "characters": 0},
                    {
                        "items": min(5, limit),
                        "characters": min(6_000, max_chars),
                    },
                )
            return (
                {"items": limit, "characters": max_chars},
                {"items": 0, "characters": 0},
            )
        evidence_chars = min(
            6_000,
            max(
                200,
                min(
                    max_chars - 200,
                    max_chars * evidence_items // (compiled_items + evidence_items),
                ),
            ),
        )
        compiled_chars = max_chars - evidence_chars
        return (
            {"items": compiled_items, "characters": compiled_chars},
            {"items": evidence_items, "characters": evidence_chars},
        )

    def _compiled(
        self,
        store: AutonomousKnowledgeStore,
        *,
        query: str,
        purpose: QueryPurpose,
        scope: str,
        max_sensitivity: str,
        limit: int,
        selection_limit: int,
        duty_aware: bool,
        max_chars: int,
        max_tokens: int,
        max_sources: int,
        graph_hops: int,
        retrieval_mode: str,
        as_of: str | None,
        kinds: tuple[str, ...],
        force_canonical_lexical: bool,
    ) -> dict[str, Any]:
        if limit == 0 or max_chars == 0:
            return {
                "results": [],
                "contradictions": [],
                "gaps": [],
                "freshness_gaps": [],
                "candidate_count": 0,
                "selected_characters": 0,
                "stale_prevented_count": 0,
            }
        def recall(selected_mode: str) -> dict[str, Any]:
            return store.recall(
                query,
                scope=cast(Any, scope),
                max_sensitivity=cast(Any, max_sensitivity),
                limit=limit,
                max_chars=max_chars,
                max_tokens=max_tokens,
                max_sources=max_sources,
                graph_hops=graph_hops,
                retrieval_mode=selected_mode,
                as_of=as_of,
                kinds=kinds,
                force_canonical_lexical=force_canonical_lexical,
            )

        raw = recall("exact") if purpose == "freshness_check" else recall(retrieval_mode)
        if purpose == "freshness_check" and not raw["results"] and retrieval_mode != "exact":
            raw = recall(retrieval_mode)
        accepted: list[dict[str, Any]] = []
        freshness_gaps: list[dict[str, Any]] = []
        stale_prevented = 0
        low_relevance_prevented = 0
        exact_identity_discovery = any(
            {"exact", "identity_alias"}.intersection(item.get("channels", []))
            for item in raw["results"]
        )
        normalized_query = normalize_identity_text(query)
        structured_query_anchors = set(_ISO_DATE_ANCHOR.findall(query))
        comparison_query = _is_comparison_query(normalized_query, query)
        query_policy_designators = _policy_designators(query)
        for item in raw["results"]:
            if _policy_designator_conflicts(query_policy_designators, item):
                low_relevance_prevented += 1
                continue
            channels = set(item.get("channels", []))
            reranker = item.get("reranker")
            reranker_score = (
                float(reranker["score"])
                if isinstance(reranker, dict)
                and isinstance(reranker.get("score"), (int, float))
                and not isinstance(reranker.get("score"), bool)
                else None
            )
            aliases = item.get("metadata", {}).get("aliases", [])
            identity_values = [item.get("title"), item.get("semantic_key")]
            if isinstance(aliases, list):
                identity_values.extend(aliases)
            exact_identity_phrase = any(
                isinstance(value, str)
                and len(normalized := normalize_identity_text(value)) >= 3
                and normalized in normalized_query
                for value in identity_values
            )
            exact_structured_anchor = _matches_structured_query_anchor(
                structured_query_anchors, item
            )
            exact_identity_graph_neighbor = (
                exact_identity_discovery and "graph" in channels
            )
            minimum_reranker_score = (
                _MIN_GENERIC_SUMMARY_RERANKER_SCORE
                if str(item.get("semantic_key", "")).startswith("source-summary:")
                else _MIN_TARGET_SYNTHESIS_RERANKER_SCORE
                if comparison_query and item.get("kind") == "synthesis"
                else _MIN_COMPILED_RERANKER_SCORE
            )
            if (
                not {"exact", "identity_alias"}.intersection(channels)
                and not exact_identity_phrase
                and not exact_structured_anchor
                and not exact_identity_graph_neighbor
                and (
                    reranker_score is None
                    or reranker_score < minimum_reranker_score
                )
            ):
                low_relevance_prevented += 1
                continue
            freshness = self._revision_freshness(store, item["revision_id"])
            projected = dict(item)
            projected["freshness"] = freshness
            if freshness["state"] in {"stale", "invalidated", "unknown"} and purpose not in {
                "debug",
                "freshness_check",
            }:
                stale_prevented += 1
                freshness_gaps.append(
                    {
                        "code": "stale_knowledge",
                        "message": "A compiled revision was excluded by dependency freshness.",
                        "knowledge_revision_id": item["revision_id"],
                        "freshness": freshness["state"],
                    }
                )
                continue
            accepted.append(projected)
        recall_rank = {
            str(item["knowledge_id"]): index
            for index, item in enumerate(raw["results"])
        }
        accepted.sort(
            key=lambda item: (
                0
                if {"exact", "identity_alias"}.intersection(
                    item.get("channels", [])
                )
                else 1,
                recall_rank.get(str(item.get("knowledge_id")), len(recall_rank)),
                _KIND_PRIORITY.get(str(item.get("kind")), 99),
                str(item.get("knowledge_id")),
            )
        )
        if duty_aware:
            accepted = self._duty_aware_selection(
                accepted,
                query=query,
                purpose=purpose,
                limit=selection_limit,
            )
        else:
            accepted = accepted[:selection_limit]
        selected_ids = {item["knowledge_id"] for item in accepted}
        contradictions = [
            item
            for item in raw["contradictions"]
            if item.get("knowledge_id") in selected_ids
            or (
                item.get("subject_knowledge_id") in selected_ids
                and item.get("object_knowledge_id") in selected_ids
            )
        ]
        discovery_gaps = list(raw["gaps"])
        if duty_aware:
            discovery_gaps = [
                message
                for message in discovery_gaps
                if message
                != "some candidates were rejected by admission or selection budgets"
            ]
        return {
            "results": accepted,
            "contradictions": contradictions,
            "gaps": [
                *discovery_gaps,
                *(
                    [
                        (
                            f"{low_relevance_prevented} compiled "
                            "candidate(s) were below the deterministic relevance floor"
                        )
                    ]
                    if low_relevance_prevented and not duty_aware
                    else []
                ),
            ],
            "freshness_gaps": freshness_gaps,
            "candidate_count": raw["query_plan"]["candidate_count"],
            "selected_characters": sum(
                len(str(item.get("content", ""))) for item in accepted
            ),
            "stale_prevented_count": stale_prevented,
        }

    @staticmethod
    def _admit_compiled_source_bytes(
        evidence_store: KnowledgeVault,
        knowledge_store: AutonomousKnowledgeStore,
        *,
        compiled: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fail closed when a selected compiled revision depends on changed source bytes."""

        if not compiled:
            return [], []
        source_cache: dict[str, dict[str, Any]] = {}
        admitted: list[dict[str, Any]] = []
        excluded = 0
        for item in compiled:
            revision_id = item.get("revision_id")
            if not isinstance(revision_id, str):
                excluded += 1
                continue
            row = knowledge_store.connection.execute(
                "SELECT source_refs_json FROM knowledge_revisions_v3 WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()
            if row is None:
                excluded += 1
                continue
            references = strict_json_loads(row["source_refs_json"])
            if not isinstance(references, list):
                excluded += 1
                continue
            source_revision_ids = {
                str(reference["source_revision_id"])
                for reference in references
                if isinstance(reference, dict)
                and isinstance(reference.get("source_revision_id"), str)
            }
            source_revision_ids.update(
                str(dependency["source_revision_id"])
                for dependency in knowledge_store.connection.execute(
                    """
                    SELECT DISTINCT source_revision_id
                    FROM knowledge_dependencies_v1
                    WHERE consumer_kind = 'knowledge_revision'
                      AND consumer_revision_id = ?
                    """,
                    (revision_id,),
                )
            )
            legacy_source_ids = {
                str(reference["source_id"])
                for reference in references
                if isinstance(reference, dict)
                and isinstance(reference.get("source_id"), str)
            }
            if source_revision_ids:
                placeholders = ",".join("?" for _ in source_revision_ids)
                legacy_source_ids.update(
                    str(binding["legacy_source_id"])
                    for binding in knowledge_store.connection.execute(
                        f"""
                        SELECT legacy_source_id
                        FROM source_revision_bindings_v2
                        WHERE source_revision_id IN ({placeholders})
                        """,
                        tuple(sorted(source_revision_ids)),
                    )
                )
            if any(
                not evidence_store._source_file_check(
                    source_id,
                    cache=source_cache,
                )["valid"]
                for source_id in sorted(legacy_source_ids)
            ):
                excluded += 1
                continue
            admitted.append(item)
        gaps = (
            [
                {
                    "code": "source_integrity",
                    "message": (
                        "Compiled knowledge depending on source bytes that failed "
                        "current integrity verification was excluded."
                    ),
                    "count": excluded,
                }
            ]
            if excluded
            else []
        )
        return admitted, gaps

    @staticmethod
    def _query_targets_outside_admission(
        store: AutonomousKnowledgeStore,
        *,
        query: str,
        scope: str,
        max_sensitivity: str,
        admitted: list[dict[str, Any]],
    ) -> bool:
        """Detect a strong target outside the boundary without exposing its identity."""

        if any(
            {"exact", "identity_alias"}.intersection(item.get("channels", []))
            for item in admitted
        ):
            return False

        ignored = {
            "reveal",
            "show",
            "quote",
            "exact",
            "what",
            "which",
            "does",
            "the",
            "is",
            "an",
            "a",
        }
        terms = [
            term.casefold()
            for term in query_search_terms(query, limit=32, cover_tail=True)
            if term.casefold() not in ignored
            and ((term.isascii() and len(term) >= 4) or (not term.isascii() and len(term) >= 2))
        ]
        if len(terms) < 2:
            return False
        sensitivity_order = ("public", "internal", "private", "restricted")
        admitted_sensitivities = sensitivity_order[
            : sensitivity_order.index(max_sensitivity) + 1
        ]
        rows = store.connection.execute(
            """
            SELECT revisions.title, revisions.semantic_key, revisions.markdown_sha256
            FROM knowledge_objects_v3 AS objects
            JOIN knowledge_revisions_v3 AS revisions
              ON revisions.revision_id = objects.current_revision_id
            WHERE revisions.lifecycle = 'active'
              AND (revisions.scope <> ? OR revisions.sensitivity NOT IN (
                    SELECT value FROM json_each(?)
                  ))
            ORDER BY revisions.knowledge_id
            LIMIT 501
            """,
            (scope, canonical_json(list(admitted_sensitivities))),
        ).fetchall()
        for row in rows[:500]:
            parsed = parse_knowledge_markdown(
                (store.root / ".deeplaw" / "objects" / "sha256" /
                 row["markdown_sha256"][:2] / row["markdown_sha256"][2:]).read_bytes()
            )
            haystack = " ".join(
                (
                    str(row["title"]),
                    str(row["semantic_key"] or ""),
                    str(parsed["body"]),
                )
            ).casefold()
            matches = {term for term in terms if term in haystack}
            required = min(3, len(set(terms)))
            if len(matches) >= required:
                return True
        return False

    @staticmethod
    def _duty_aware_selection(
        candidates: list[dict[str, Any]],
        *,
        query: str,
        purpose: QueryPurpose,
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        normalized = normalize_identity_text(query)
        source_summary_only = (
            "summarize" in normalized and "source" in normalized
        ) or any(
            term in query
            for term in (
                "概述来源",
                "概括材料",
                "概述材料",
                "概括来源",
                "摘要说明来源",
            )
        ) or ("摘要" in query and "来源" in query)
        if source_summary_only:
            selected = [
                item
                for item in candidates
                if str(item.get("semantic_key", "")).startswith("source-summary:")
            ]
            return selected[:1]
        date_count = len(re.findall(r"20\d{2}(?:[-年]\d{1,2})", query))
        if any(term in normalized for term in ("orderedsteps", "procedure", "workflow")) or any(
            term in query for term in ("有序步骤", "流程", "依次", "怎么做")
        ):
            target_kinds = {"procedure"}
        elif date_count >= 2 and (
            "protocol" in normalized or "协议" in query
        ):
            target_kinds = {"event", "concept"}
        elif (
            "timeline" in normalized
            or date_count >= 2
            or any(term in query for term in ("时间线", "按时间", "按先后"))
        ):
            target_kinds = {"event"}
        elif _is_comparison_query(normalized, query):
            target_kinds = {"synthesis", "comparison", "claim"}
        elif any(term in normalized for term in ("accordingtoeachpolicy", "howlong")) or any(
            term in query for term in ("多久", "多少天", "保留期", "留存")
        ):
            target_kinds = {"claim"}
        elif "overview" in normalized or any(term in query for term in ("概览", "概况")):
            target_kinds = {"synthesis"}
        elif "protocolrevision" in normalized or purpose == "quote" or any(
            term in query for term in ("逐字", "确切", "原文", "协议修订版")
        ):
            target_kinds = {"claim"}
        elif normalized.startswith(("whatis", "whatdoes", "who", "whatorganization")) or any(
            term in query for term in ("什么", "谁", "哪个组织", "何谓", "指什么")
        ):
            target_kinds = {"concept", "entity"}
        else:
            target_kinds = set()
        exact = [
            item
            for item in candidates
            if {"exact", "identity_alias"}.intersection(item.get("channels", []))
        ]
        scoped = [item for item in candidates if item.get("kind") in target_kinds]
        if target_kinds == {"event"}:
            scoped.sort(
                key=lambda item: (
                    str(item.get("valid_from") or "9999-12-31T23:59:59Z"),
                    str(item.get("knowledge_id")),
                )
            )
        if scoped:
            exact_scoped = [
                item for item in exact if item.get("kind") in target_kinds
            ]
            multi_target_query = bool(
                _is_comparison_query(normalized, query)
                or target_kinds == {"event"}
                or (target_kinds == {"event", "concept"} and date_count >= 2)
            )
            if exact_scoped and not multi_target_query:
                return exact_scoped[:limit]
            exact_ids = {str(item.get("knowledge_id")) for item in exact_scoped}
            target_scoped = [
                item
                for item in scoped
                if str(item.get("knowledge_id")) not in exact_ids
            ]
            return [*exact_scoped, *target_scoped][:limit]
        if exact:
            exact_ids = {str(item.get("knowledge_id")) for item in exact}
            related = [
                item
                for item in candidates
                if str(item.get("knowledge_id")) not in exact_ids
            ]
            return [*exact, *related][: min(limit, 4)]
        return candidates[: min(limit, 4)]

    @staticmethod
    def _provider_selection_reason(item: dict[str, Any]) -> str:
        channels = set(item.get("channels", []))
        if {"exact", "identity_alias"}.intersection(channels):
            return "exact_identity"
        if item.get("kind") == "synthesis" and item.get("freshness", {}).get(
            "state"
        ) == "fresh":
            return "fresh_synthesis"
        kind = str(item.get("kind", "knowledge"))
        return f"target_scoped_{kind}"

    @classmethod
    def _provider_compiled_item(cls, item: dict[str, Any]) -> dict[str, Any]:
        """Project a selected revision into the provider-visible Knowledge Capsule."""

        allowed = {
            "knowledge_id",
            "revision_id",
            "title",
            "kind",
            "lifecycle",
            "epistemic_state",
            "origin",
            "authority",
            "verification",
            "scope",
            "sensitivity",
            "source_refs",
            "source_ref_count",
            "source_refs_truncated",
            "semantic_key",
            "valid_from",
            "valid_to",
            "expires_at",
            "legal_authority",
            "content",
            "content_truncated",
            "applicability",
            "synthesis_evidence_receipt",
        }
        projected = {key: value for key, value in item.items() if key in allowed}
        freshness = item.get("freshness")
        if isinstance(freshness, dict):
            projected["freshness"] = {
                "state": freshness.get("state"),
                "dependency_count": freshness.get("dependency_count", 0),
            }
        projected["selection_reason"] = cls._provider_selection_reason(item)
        references = [
            reference
            for reference in projected.get("source_refs", [])
            if isinstance(reference, dict)
        ]
        drill_down = []
        for reference in references[:4]:
            fragment_id = reference.get("fragment_revision_id") or reference.get(
                "fragment_id"
            )
            if not isinstance(fragment_id, str):
                continue
            drill_down.append(
                {
                    "operation": "source",
                    "source_action": "fragment",
                    "fragment_id": fragment_id,
                    "offset": 0,
                    "max_chars": 12_000,
                }
            )
        projected["evidence_drill_down"] = drill_down
        if projected.get("content_truncated"):
            projected["continuation"] = {
                "operation": "get",
                "knowledge_id": projected["knowledge_id"],
                "expected_revision_id": projected["revision_id"],
            }
        else:
            projected["continuation"] = None
        return projected

    @staticmethod
    def _provider_evidence_card(item: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "asset_id",
            "asset_revision_id",
            "title",
            "kind",
            "excerpt",
            "content_sha256",
            "status",
            "verification",
            "trust",
            "sensitivity",
            "legal_authority",
            "source_refs",
            "source_ref_count",
            "source_refs_truncated",
        }
        projected = {key: value for key, value in item.items() if key in allowed}
        projected["selection_reason"] = "exact_evidence"
        projected["evidence_drill_down"] = [
            {
                "operation": "source",
                "source_action": "fragment",
                "fragment_id": fragment_id,
                "offset": 0,
                "max_chars": 12_000,
            }
            for reference in projected.get("source_refs", [])[:4]
            if isinstance(reference, dict)
            and isinstance(
                fragment_id := (
                    reference.get("fragment_revision_id")
                    or reference.get("fragment_id")
                ),
                str,
            )
        ]
        return projected

    @staticmethod
    def provider_capsule(result: dict[str, Any]) -> dict[str, Any]:
        """Project a v5 owner/audit result onto the Agent provider surface."""

        if result.get("schema_version") != "deeplaw.purpose-aware-retrieval/v2":
            raise ValueError("provider capsule requires a v5 retrieval result")
        plan = result["query_plan"]
        capsule = {
            "schema_version": "deeplaw.provider-knowledge-capsule/v1",
            "purpose": result["purpose"],
            "policy_id": result["policy_id"],
            "query_sha256": plan["query_sha256"],
            "compiled": result["compiled"],
            "evidence": result["evidence"],
            "contradictions": result["contradictions"],
            "gaps": result["gaps"],
            "receipt": {
                "query_plan_sha256": result["query_plan_sha256"],
                "fallback_used": plan["fallback"]["used"],
                "fallback_reason": plan["fallback"]["reason"],
                "uncompiled_source_count": plan["uncompiled_source_count"],
                "stale_selection_prevented_count": plan[
                    "stale_selection_prevented_count"
                ],
                "suppressed_candidate_count": plan["suppressed_candidate_count"],
                "deduplicated_object_count": plan["deduplicated_object_count"],
                "internal_discovery_receipt_sha256": plan[
                    "internal_discovery_receipt_sha256"
                ],
                "audit_head": result["audit_head"],
            },
            "delivery": {
                "hard_limit_bytes": 65_536,
                "selected_object_count": len(result["compiled"])
                + len(result["evidence"]),
                "provider_content_bytes": int(
                    result.get("delivery", {}).get("provider_content_bytes", 0)
                ),
                "content_truncated": any(
                    bool(item.get("content_truncated"))
                    for item in result["compiled"]
                ),
                "continuation_available": any(
                    bool(item.get("continuation") or item.get("evidence_drill_down"))
                    for item in [*result["compiled"], *result["evidence"]]
                ),
            },
            "authority_changed_by_ranking": False,
            "write_performed": False,
        }
        _validate_contract("provider-knowledge-capsule.v1.schema.json", capsule)
        if len(canonical_json(capsule).encode("utf-8")) > _MAX_PROVIDER_CHARS:
            raise RuntimeError("provider Knowledge Capsule exceeds its hard 64 KiB budget")
        return capsule

    @staticmethod
    def _raw_fragment_baseline_bytes(
        store: AutonomousKnowledgeStore,
        *,
        compiled: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> int:
        fragment_ids = {
            str(fragment_id)
            for item in [*compiled, *evidence]
            for reference in item.get("source_refs", [])
            if isinstance(reference, dict)
            and isinstance(
                fragment_id := (
                    reference.get("fragment_revision_id")
                    or reference.get("fragment_id")
                ),
                str,
            )
        }
        total = 0
        for fragment_id in sorted(fragment_ids):
            row = store.connection.execute(
                """
                SELECT source_fragments.text
                FROM legacy_fragment_bindings_v2
                JOIN source_fragments USING(fragment_id)
                WHERE legacy_fragment_bindings_v2.fragment_id = ?
                   OR legacy_fragment_bindings_v2.fragment_revision_id = ?
                LIMIT 1
                """,
                (fragment_id, fragment_id),
            ).fetchone()
            if row is not None:
                total += len(str(row["text"]).encode("utf-8"))
        return total

    @staticmethod
    def _partial_compilation_gaps(
        store: AutonomousKnowledgeStore,
        *,
        compiled: list[dict[str, Any]],
        evidence_source_revision_ids: list[str],
    ) -> list[dict[str, Any]]:
        source_revision_ids = set(evidence_source_revision_ids)
        for item in compiled:
            for reference in item.get("source_refs", []):
                source_revision_id = reference.get("source_revision_id")
                if isinstance(source_revision_id, str):
                    source_revision_ids.add(source_revision_id)
        partial = []
        for source_revision_id in sorted(source_revision_ids):
            row = store.connection.execute(
                """
                SELECT semantic.semantic_status
                FROM source_compilation_runs_v1 AS runs
                JOIN semantic_compilation_runs_v2 AS semantic
                  ON semantic.compilation_run_id = runs.compilation_run_id
                WHERE runs.source_revision_id = ?
                  AND runs.status IN ('committed', 'projection_pending', 'succeeded')
                ORDER BY runs.created_at DESC, runs.compilation_run_id DESC
                LIMIT 1
                """,
                (source_revision_id,),
            ).fetchone()
            if row is not None and row["semantic_status"] in {
                "partial",
                "blocked",
                "unknown",
            }:
                partial.append(source_revision_id)
        if not partial:
            return []
        return [
            {
                "code": "partial_compilation",
                "message": (
                    "Selected evidence depends on a semantically incomplete Compilation Run."
                ),
                "count": len(partial),
                "source_revision_ids": partial[:16],
            }
        ]

    @staticmethod
    def _knowledge_partitions(
        store: AutonomousKnowledgeStore,
        *,
        compiled: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        partitions = {
            "source_bound_compiled": [],
            "revision_bound_synthesis": [],
            "run_bound_knowledge": [],
            "source_free": [],
            "exact_evidence": [],
            "agent_interpretation": [],
        }
        for item in compiled:
            revision_id = str(item.get("revision_id", ""))
            if not revision_id:
                continue
            synthesis = store.connection.execute(
                """
                SELECT 1 FROM synthesis_input_sets_v1
                WHERE synthesis_revision_id = ?
                """,
                (revision_id,),
            ).fetchone()
            if synthesis is not None:
                partitions["revision_bound_synthesis"].append(revision_id)
            elif item.get("verification") == "run_bound":
                partitions["run_bound_knowledge"].append(revision_id)
            elif item.get("source_refs"):
                partitions["source_bound_compiled"].append(revision_id)
            if item.get("source_free", False):
                partitions["source_free"].append(revision_id)
                partitions["agent_interpretation"].append(revision_id)
        partitions["exact_evidence"] = sorted(
            {
                str(item["fragment_id"])
                for item in evidence
                if isinstance(item.get("fragment_id"), str)
            }
        )
        return {key: list(dict.fromkeys(values))[:20] for key, values in partitions.items()}

    @staticmethod
    def _synthesis_evidence_receipts(
        store: AutonomousKnowledgeStore,
        *,
        compiled: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        evidence_references = [
            reference
            for item in evidence
            for reference in item.get("source_refs", [])
            if isinstance(reference, dict)
        ]
        synthesis_revision_ids = [
            item["revision_id"]
            for item in compiled
            if item.get("kind") == "synthesis"
            and not str(item.get("semantic_key", "")).startswith("source-summary:")
        ]
        rows = {}
        if synthesis_revision_ids:
            placeholders = ",".join("?" for _ in synthesis_revision_ids)
            rows = {
                row["synthesis_revision_id"]: row
                for row in store.connection.execute(
                    f"""
                    SELECT synthesis_revision_id, input_set_sha256,
                           source_revision_ids_json
                    FROM synthesis_input_sets_v1
                    WHERE synthesis_revision_id IN ({placeholders})
                    """,
                    synthesis_revision_ids,
                )
            }
        projected: list[dict[str, Any]] = []
        for item in compiled:
            current = dict(item)
            if item.get("kind") != "synthesis" or str(
                item.get("semantic_key", "")
            ).startswith("source-summary:"):
                projected.append(current)
                continue
            row = rows.get(item["revision_id"])
            if row is None:
                projected.append(current)
                continue
            loaded_sources = strict_json_loads(row["source_revision_ids_json"])
            if not isinstance(loaded_sources, list):
                raise RuntimeError("Synthesis input Source Revision set is invalid")
            expected_sources = sorted(
                source_revision_id
                for source_revision_id in loaded_sources
                if isinstance(source_revision_id, str)
            )
            if len(expected_sources) <= 1:
                projected.append(current)
                continue
            available_references = [
                reference
                for reference in [*evidence_references, *item.get("source_refs", [])]
                if isinstance(reference, dict)
                and reference.get("source_revision_id") in expected_sources
            ]
            references: list[dict[str, Any]] = []
            seen_sources: set[str] = set()
            for reference in available_references:
                source_revision_id = reference.get("source_revision_id")
                fragment_identity = reference.get("fragment_id") or reference.get(
                    "fragment_revision_id"
                )
                if not isinstance(source_revision_id, str) or not isinstance(
                    fragment_identity, str
                ):
                    continue
                if source_revision_id in seen_sources:
                    continue
                seen_sources.add(source_revision_id)
                references.append(reference)
                if len(references) >= 8:
                    break
            actual_sources = {
                str(reference["source_revision_id"]) for reference in references
            }
            receipt = {
                "schema_version": "deeplaw.synthesis-query-evidence-receipt/v1",
                "synthesis_revision_id": item["revision_id"],
                "input_set_sha256": row["input_set_sha256"],
                "source_revision_ids": expected_sources,
                "source_refs": references,
                "complete": set(expected_sources).issubset(actual_sources),
            }
            receipt["receipt_sha256"] = sha256_bytes(
                canonical_json(receipt).encode("utf-8")
            )
            _validate_contract("synthesis-query-evidence-receipt.v1.schema.json", receipt)
            current["synthesis_evidence_receipt"] = receipt
            projected.append(current)
        return projected

    @staticmethod
    def _hydrate_selected_source_refs(
        store: AutonomousKnowledgeStore,
        *,
        compiled: list[dict[str, Any]],
        query: str,
    ) -> list[dict[str, Any]]:
        """Hydrate only selected revisions with bounded claim-evidence references."""

        revision_ids = [
            str(item["revision_id"])
            for item in compiled
            if isinstance(item.get("revision_id"), str)
        ]
        if not revision_ids:
            return [dict(item) for item in compiled]
        placeholders = ",".join("?" for _ in revision_ids)
        source_refs_by_revision = {
            row["revision_id"]: strict_json_loads(row["source_refs_json"])
            for row in store.connection.execute(
                f"""
                SELECT revision_id, source_refs_json
                FROM knowledge_revisions_v3
                WHERE revision_id IN ({placeholders})
                """,
                revision_ids,
            )
        }
        hydrated = []
        for item in compiled:
            current = dict(item)
            references = source_refs_by_revision.get(item.get("revision_id"))
            if not isinstance(references, list):
                hydrated.append(current)
                continue
            valid_references = [
                dict(reference) for reference in references if isinstance(reference, dict)
            ]
            target_text = "\n".join(
                str(value)
                for value in (
                    query,
                    item.get("title", ""),
                    item.get("content", ""),
                    item.get("body", ""),
                    item.get("semantic_key", ""),
                )
                if value
            )
            target_terms = set(
                query_search_terms(target_text, limit=128, cover_tail=True)
            )
            fragment_ids = [
                fragment_id
                for reference in valid_references
                if isinstance(
                    fragment_id := (
                        reference.get("fragment_id")
                        or reference.get("fragment_revision_id")
                    ),
                    str,
                )
            ]
            fragment_rows: dict[str, Any] = {}
            if fragment_ids:
                fragment_placeholders = ",".join("?" for _ in fragment_ids)
                fragment_rows = {
                    row["fragment_id"]: row
                    for row in store.connection.execute(
                        f"""
                        SELECT fragment_id, text, ordinal
                        FROM source_fragments
                        WHERE fragment_id IN ({fragment_placeholders})
                        """,
                        fragment_ids,
                    )
                }
            scored: list[tuple[int, int, dict[str, Any]]] = []
            for index, reference in enumerate(valid_references):
                fragment_id = reference.get("fragment_id") or reference.get(
                    "fragment_revision_id"
                )
                row = fragment_rows.get(fragment_id)
                overlap = 0
                if row is not None and target_terms:
                    overlap = len(
                        target_terms.intersection(
                            search_terms(str(row["text"]), limit=256, cover_tail=True)
                        )
                    )
                scored.append((overlap, index, reference))

            ranked = sorted(scored, key=lambda value: (-value[0], value[1]))
            selected_indexes: set[int] = set()
            best_by_source: dict[str, tuple[int, int, dict[str, Any]]] = {}
            for candidate in ranked:
                reference = candidate[2]
                source_key = reference.get("source_revision_id") or reference.get(
                    "source_id"
                )
                if isinstance(source_key, str) and source_key not in best_by_source:
                    best_by_source[source_key] = candidate
            for candidate in sorted(
                best_by_source.values(), key=lambda value: (-value[0], value[1])
            )[:4]:
                selected_indexes.add(candidate[1])
            for candidate in ranked:
                if len(selected_indexes) >= 4:
                    break
                selected_indexes.add(candidate[1])
            bounded = [
                reference
                for index, reference in enumerate(valid_references)
                if index in selected_indexes
            ]
            current["source_refs"] = bounded
            current["source_ref_count"] = len(valid_references)
            current["source_refs_truncated"] = len(valid_references) > len(bounded)
            hydrated.append(current)
        return hydrated

    @staticmethod
    def _knowledge_duty_reports(
        *,
        purpose: str,
        compiled: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        contradictions: list[dict[str, Any]],
        gaps: list[dict[str, Any]],
        partitions: dict[str, list[str]],
    ) -> tuple[list[dict[str, Any]], float]:
        compiled_refs = [str(item["revision_id"]) for item in compiled]
        evidence_refs = [
            str(item["fragment_id"])
            for item in evidence
            if isinstance(item.get("fragment_id"), str)
        ]
        by_kind: dict[str, list[str]] = {}
        for item in compiled:
            by_kind.setdefault(str(item.get("kind")), []).append(
                str(item.get("revision_id"))
            )
        definitions = [*by_kind.get("concept", []), *by_kind.get("entity", [])]
        applicability_refs = [
            str(item["revision_id"])
            for item in compiled
            if item.get("applicability")
        ]
        unresolved_refs = [str(item.get("code", "retrieval_gap")) for item in gaps]
        specifications = {
            "primary_answer": (
                purpose == "answer",
                [
                    *partitions["revision_bound_synthesis"],
                    *partitions["source_bound_compiled"],
                    *partitions["run_bound_knowledge"],
                ],
                "Admitted compiled knowledge covers the primary answer duty.",
            ),
            "definition": (
                purpose == "answer",
                definitions,
                "Concept or Entity knowledge covers the definition duty.",
            ),
            "temporal_freshness": (
                purpose in {"answer", "historical", "freshness_check"},
                compiled_refs,
                "Selected compiled revisions passed deterministic freshness admission.",
            ),
            "contradiction_or_counterevidence": (
                purpose in {"answer", "verify"},
                [str(item.get("relation_revision_id", "contradiction")) for item in contradictions],
                "Contradiction discovery was included in the query plan.",
            ),
            "limitation": (
                True,
                unresolved_refs,
                "Visible gaps and bounded result limits carry limitations.",
            ),
            "source_evidence": (
                purpose in {"verify", "quote", "historical"}
                or bool(partitions["source_bound_compiled"])
                or bool(partitions["revision_bound_synthesis"]),
                [*evidence_refs, *partitions["source_bound_compiled"]],
                "Exact evidence or evidence-bound compiled knowledge is visible.",
            ),
            "applicability": (
                purpose == "answer",
                applicability_refs,
                "Selected Knowledge applicability metadata is preserved.",
            ),
            "unresolved_gap": (
                True,
                unresolved_refs,
                "Unresolved retrieval and compilation gaps remain explicit.",
            ),
        }
        reports = []
        applicable_count = 0
        satisfied_count = 0
        for duty in _KNOWLEDGE_DUTIES:
            applicable, refs, reason = specifications[duty]
            if applicable:
                applicable_count += 1
            status = (
                "not_applicable"
                if not applicable
                else "satisfied"
                if refs
                or duty in {
                    "contradiction_or_counterevidence",
                    "limitation",
                    "unresolved_gap",
                }
                else "unresolved"
            )
            if applicable and status == "satisfied":
                satisfied_count += 1
            reports.append(
                {
                    "duty": duty,
                    "applicable": applicable,
                    "status": status,
                    "selected_refs": list(dict.fromkeys(refs))[:20],
                    "reason": reason,
                }
            )
        coverage = satisfied_count / applicable_count if applicable_count else 1.0
        return reports, coverage

    @classmethod
    def _upgrade_to_v5(
        cls,
        *,
        store: AutonomousKnowledgeStore,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        distinct_compiled: list[dict[str, Any]] = []
        seen_knowledge_ids: set[str] = set()
        for item in result["compiled"]:
            knowledge_id = str(item.get("knowledge_id", ""))
            if knowledge_id in seen_knowledge_ids:
                continue
            seen_knowledge_ids.add(knowledge_id)
            distinct_compiled.append(item)
        deduplicated_object_count = len(result["compiled"]) - len(distinct_compiled)
        distinct_compiled = cls._hydrate_selected_source_refs(
            store,
            compiled=distinct_compiled,
            query=str(result["query"]),
        )
        compiled_with_receipts = cls._synthesis_evidence_receipts(
            store,
            compiled=distinct_compiled,
            evidence=result["evidence"],
        )
        incomplete_syntheses = [
            item["revision_id"]
            for item in compiled_with_receipts
            if isinstance(item.get("synthesis_evidence_receipt"), dict)
            and not item["synthesis_evidence_receipt"]["complete"]
        ]
        gaps = cls._bounded_gaps(
            [
                *result["gaps"],
                *(
                    {
                        "code": "evidence_gap",
                        "message": (
                            "A selected Synthesis lacks a complete provider-visible "
                            "evidence receipt for its input Source Revisions."
                        ),
                        "knowledge_revision_id": revision_id,
                    }
                    for revision_id in incomplete_syntheses
                ),
            ]
        )
        partitions = cls._knowledge_partitions(
            store,
            compiled=compiled_with_receipts,
            evidence=result["evidence"],
        )
        duties, duty_coverage = cls._knowledge_duty_reports(
            purpose=result["purpose"],
            compiled=compiled_with_receipts,
            evidence=result["evidence"],
            contradictions=result["contradictions"],
            gaps=gaps,
            partitions=partitions,
        )
        compiled = [
            cls._provider_compiled_item(item) for item in compiled_with_receipts
        ]
        evidence = [cls._provider_evidence_card(item) for item in result["evidence"]]
        candidate_count = int(result["query_plan"]["compiled_candidate_count"])
        suppressed_candidate_count = max(0, candidate_count - len(compiled))
        internal_discovery_receipt_sha256 = sha256_bytes(
            canonical_json(
                {
                    "candidate_count": candidate_count,
                    "selected_knowledge_ids": [
                        item["knowledge_id"] for item in compiled
                    ],
                    "input_audit_head": result["query_plan"]["input_audit_head"],
                    "input_legacy_audit_head": result["query_plan"][
                        "input_legacy_audit_head"
                    ],
                }
            ).encode("utf-8")
        )
        plan = dict(result["query_plan"])
        plan["schema_version"] = "deeplaw.knowledge-query-plan/v5"
        plan["knowledge_duties"] = duties
        plan["knowledge_partitions"] = partitions
        plan["duty_coverage"] = duty_coverage
        plan["compiled_selected_count"] = len(compiled)
        plan["evidence_selected_count"] = len(evidence)
        plan["provider_surface"] = "knowledge_capsule"
        plan["suppressed_candidate_count"] = suppressed_candidate_count
        plan["deduplicated_object_count"] = deduplicated_object_count
        plan["internal_discovery_receipt_sha256"] = internal_discovery_receipt_sha256
        expansion_terms = query_expansion_terms(str(result["query"]))
        plan["query_expansion"] = {
            "profile": QUERY_EXPANSION_PROFILE,
            "applied": bool(expansion_terms),
            "term_count": len(expansion_terms),
            "terms_sha256": sha256_bytes(
                canonical_json(expansion_terms).encode("utf-8")
            ),
            "authority_changed": False,
            "stored_evidence_changed": False,
        }
        _validate_contract("knowledge-query-plan.v5.schema.json", plan)
        upgraded = dict(result)
        upgraded["schema_version"] = "deeplaw.purpose-aware-retrieval/v2"
        upgraded["compiled"] = compiled
        upgraded["evidence"] = evidence
        upgraded["gaps"] = gaps
        upgraded["query_plan"] = plan
        upgraded["query_plan_sha256"] = sha256_bytes(
            canonical_json(plan).encode("utf-8")
        )
        selected_count = len(compiled)
        raw_fragment_baseline_bytes = cls._raw_fragment_baseline_bytes(
            store,
            compiled=compiled_with_receipts,
            evidence=result["evidence"],
        )
        selected_content_bytes = sum(
            len(str(item.get("content", item.get("excerpt", ""))).encode("utf-8"))
            for item in [*compiled, *evidence]
        )
        bytes_saved = max(0, raw_fragment_baseline_bytes - selected_content_bytes)
        metrics = dict(result["metrics"])
        metrics.update(
            {
                "compiled_hit_ratio": 1.0 if selected_count else 0.0,
                "repeated_compiled_reuse_rate": (
                    1.0 if metrics["repeated_query_reused_compilation"] else 0.0
                ),
                "source_fallback_ratio": (
                    1.0 if metrics["source_fallback_used"] else 0.0
                ),
                "source_free_selection_rate": (
                    len(partitions["source_free"]) / selected_count
                    if selected_count
                    else 0.0
                ),
                "evidence_attachment_rate": (
                    metrics["evidence_attachment_count"] / selected_count
                    if selected_count
                    else 0.0
                ),
                "stale_selection_prevention": metrics[
                    "stale_selection_prevented_count"
                ],
                "partial_compilation_gap_rate": (
                    1.0
                    if any(item.get("code") == "partial_compilation" for item in result["gaps"])
                    else 0.0
                ),
                "duty_coverage": duty_coverage,
                "provider_payload_bytes": 0,
                "provider_content_bytes": selected_content_bytes,
                "provider_visible_object_count": selected_count + len(evidence),
                "deduplicated_object_count": deduplicated_object_count,
                "suppressed_candidate_count": suppressed_candidate_count,
                "raw_fragment_baseline_bytes": raw_fragment_baseline_bytes,
                "context_bytes_saved_vs_raw_fragment_baseline": bytes_saved,
                "query_token_savings_vs_raw_fallback": bytes_saved // 4,
            }
        )
        upgraded["metrics"] = metrics
        upgraded["delivery"] = {
            "surface": "provider_visible_knowledge_capsule",
            "provider_visible_bytes": 0,
            "provider_content_bytes": selected_content_bytes,
            "hard_limit_bytes": _MAX_PROVIDER_CHARS,
            "selected_object_count": selected_count + len(evidence),
            "deduplicated_object_count": deduplicated_object_count,
            "suppressed_candidate_count": suppressed_candidate_count,
            "raw_fragment_baseline_bytes": raw_fragment_baseline_bytes,
            "context_bytes_saved": bytes_saved,
            "estimated_tokens_saved": bytes_saved // 4,
            "content_truncated": any(
                bool(item.get("content_truncated")) for item in compiled
            ),
            "continuation_available": any(
                bool(item.get("continuation") or item.get("evidence_drill_down"))
                for item in [*compiled, *evidence]
            ),
            "internal_discovery_receipt_sha256": internal_discovery_receipt_sha256,
        }
        upgraded["budget"] = dict(upgraded["budget"])
        upgraded["budget"]["selected_characters"] = sum(
            len(str(item.get("content", item.get("excerpt", ""))))
            for item in [*compiled, *evidence]
        )
        provider_payload_bytes = len(
            canonical_json(cls.provider_capsule(upgraded)).encode("utf-8")
        )
        metrics["provider_payload_bytes"] = provider_payload_bytes
        upgraded["delivery"]["provider_visible_bytes"] = provider_payload_bytes
        return upgraded

    @staticmethod
    def _revision_freshness(
        store: AutonomousKnowledgeStore,
        revision_id: str,
    ) -> dict[str, Any]:
        source_rows = store.connection.execute(
            """
            SELECT source_revision_id, fragment_id, freshness, reason
            FROM knowledge_dependencies_v1
            WHERE consumer_kind = 'knowledge_revision'
              AND consumer_revision_id = ?
            ORDER BY source_revision_id, fragment_id
            """,
            (revision_id,),
        ).fetchall()
        revision_rows = store.connection.execute(
            """
            SELECT input_kind, input_id, freshness, reason
            FROM revision_dependencies_v1
            WHERE consumer_kind = 'knowledge_revision'
              AND consumer_revision_id = ?
            ORDER BY input_kind, input_id
            """,
            (revision_id,),
        ).fetchall()
        if not source_rows and not revision_rows:
            return {"state": "fresh", "dependencies": [], "source_free": True}
        order = {"fresh": 0, "unknown": 1, "stale": 2, "invalidated": 3}
        state = max(
            (row["freshness"] for row in [*source_rows, *revision_rows]),
            key=order.__getitem__,
        )
        dependencies = [
            {
                "input_kind": "source_fragment",
                "source_revision_id": row["source_revision_id"],
                "fragment_id": row["fragment_id"],
                "freshness": row["freshness"],
                "reason": row["reason"],
            }
            for row in source_rows
        ]
        dependencies.extend(
            {
                "input_kind": row["input_kind"],
                "input_id": row["input_id"],
                "freshness": row["freshness"],
                "reason": row["reason"],
            }
            for row in revision_rows
        )
        return {
            "state": state,
            "dependencies": dependencies[:8],
            "dependency_count": len(dependencies),
            "dependencies_truncated": len(dependencies) > 8,
            "source_free": not source_rows,
        }

    @classmethod
    def _stale_knowledge_gaps(
        cls,
        store: AutonomousKnowledgeStore,
        *,
        query: str,
        scope: str,
        max_sensitivity: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        terms = tuple(query_search_terms(query, limit=16, cover_tail=True))
        if not terms or scope != store.vault_scope:
            return []
        rows = store.connection.execute(
            """
            SELECT knowledge_objects_v3.workspace_path AS current_workspace_path,
                   knowledge_revisions_v3.*
            FROM knowledge_objects_v3
            JOIN knowledge_revisions_v3
              ON knowledge_revisions_v3.revision_id =
                 knowledge_objects_v3.current_revision_id
            WHERE knowledge_revisions_v3.lifecycle = 'active'
              AND knowledge_revisions_v3.scope = ?
              AND (
                  EXISTS (
                      SELECT 1 FROM knowledge_dependencies_v1
                      WHERE consumer_kind = 'knowledge_revision'
                        AND consumer_revision_id =
                            knowledge_revisions_v3.revision_id
                        AND freshness <> 'fresh'
                  )
                  OR EXISTS (
                      SELECT 1 FROM revision_dependencies_v1
                      WHERE consumer_kind = 'knowledge_revision'
                        AND consumer_revision_id =
                            knowledge_revisions_v3.revision_id
                        AND freshness <> 'fresh'
                  )
              )
            ORDER BY knowledge_revisions_v3.recorded_at DESC,
                     knowledge_revisions_v3.revision_id
            LIMIT 501
            """,
            (scope,),
        ).fetchall()
        sensitivity_order = ("public", "internal", "private", "restricted")
        selected: list[dict[str, Any]] = []
        for row in rows[:500]:
            if (
                row["sensitivity"] not in sensitivity_order
                or row["sensitivity"] == "restricted"
                or sensitivity_order.index(row["sensitivity"])
                > sensitivity_order.index(max_sensitivity)
            ):
                continue
            dependency_sources = store.connection.execute(
                """
                SELECT DISTINCT source_revision_id
                FROM knowledge_dependencies_v1
                WHERE consumer_kind = 'knowledge_revision'
                  AND consumer_revision_id = ?
                """,
                (row["revision_id"],),
            ).fetchall()
            source_bindings = [
                store._source_reference_binding(
                    {"source_revision_id": item["source_revision_id"]}
                )
                for item in dependency_sources
            ]
            if any(
                binding is None
                or binding["scope"] != scope
                or binding["sensitivity"] not in sensitivity_order
                or binding["sensitivity"] == "restricted"
                or sensitivity_order.index(binding["sensitivity"])
                > sensitivity_order.index(max_sensitivity)
                for binding in source_bindings
            ):
                continue
            revision = store._revision_row(row, include_body=True)
            haystack = " ".join(
                (
                    str(revision.get("title", "")),
                    str(revision.get("semantic_key", "")),
                    str(revision.get("body", "")),
                )
            ).casefold()
            if not any(term in haystack for term in terms):
                continue
            freshness = cls._revision_freshness(
                store,
                revision["revision_id"],
            )
            selected.append(
                {
                    "code": "stale_knowledge",
                    "message": (
                        "A relevant compiled revision was excluded by "
                        "dependency freshness."
                    ),
                    "knowledge_revision_id": revision["revision_id"],
                    "freshness": freshness["state"],
                }
            )
            if len(selected) >= limit:
                break
        if len(rows) > 500 and len(selected) < limit:
            selected.append(
                {
                    "code": "stale_scan_truncated",
                    "message": (
                        "Stale-knowledge gap discovery reached its bounded "
                        "500-revision scan."
                    ),
                }
            )
        return selected

    def _evidence(
        self,
        evidence_store: KnowledgeVault,
        knowledge_store: AutonomousKnowledgeStore,
        *,
        query: str,
        scope: str,
        max_sensitivity: str,
        limit: int,
        max_chars: int,
        as_of: str | None,
        kinds: tuple[str, ...],
        compiled: list[dict[str, Any]],
    ) -> _EvidenceSelection:
        if as_of is not None:
            historical = self._historical_evidence_from_compiled(
                evidence_store,
                knowledge_store,
                compiled=compiled,
                scope=scope,
                max_sensitivity=max_sensitivity,
                as_of=as_of,
                limit=limit,
                max_chars=max_chars,
            )
            if historical.cards:
                return historical
            return _EvidenceSelection(
                [],
                [
                    {
                        "code": "historical_evidence_unavailable",
                        "message": (
                            "No exact immutable evidence admitted at the requested "
                            "transaction time matched the query."
                        ),
                    }
                ],
                0,
                [],
                [],
            )
        if scope != knowledge_store.vault_scope:
            return _EvidenceSelection([], [], 0, [], [])
        source_kinds = tuple(
            kind
            for kind in kinds
            if kind
            in {
                "assumption",
                "constraint",
                "decision",
                "definition",
                "exception",
                "experience",
                "fact",
                "lesson",
                "procedure",
                "question",
                "reference",
                "requirement",
                "risk",
                "rule",
            }
        )
        raw = retrieve(
            evidence_store,
            query,
            mode="auto",
            limit=min(limit, 5),
            max_chars=min(max_chars, 6_000),
            kinds=source_kinds,
            memory_tiers=(),
            include_restricted=False,
            include_inactive=False,
            explain=False,
            _preverified_audit_head=evidence_store.audit_head,
        )
        sensitivity_order = ("public", "internal", "private", "restricted")
        query_policy_designators = _policy_designators(query)
        boundary_candidates = [
            item
            for item in raw.get("results", [])
            if isinstance(item, dict)
            and item.get("sensitivity") in sensitivity_order
            and sensitivity_order.index(item["sensitivity"])
            <= sensitivity_order.index(max_sensitivity)
            and item.get("sensitivity") != "restricted"
            and isinstance(item.get("asset_id"), str)
            and not _policy_designator_conflicts(
                query_policy_designators, item
            )
        ]
        discovery_query = query_discovery_text(query)
        evidence_scores = {
            item["knowledge_id"]: float(item["reranker_score"])
            for item in rerank_candidates(
                discovery_query,
                [
                    {
                        "knowledge_id": item["asset_id"],
                        "title": item.get("title", ""),
                        "body": item.get("excerpt", ""),
                        "semantic_key": item.get("semantic_key"),
                        "epistemic_state": "supported",
                        "feedback_utility": 0.0,
                    }
                    for item in boundary_candidates
                ],
            )
        }
        cards: list[dict[str, Any]] = []
        unbound_evidence_count = 0
        low_relevance_evidence_count = 0
        for item in boundary_candidates:
            if (
                evidence_scores.get(item["asset_id"], 0.0)
                < _MIN_EVIDENCE_RERANKER_SCORE
                and not _has_exact_identifier_overlap(query, item)
            ):
                low_relevance_evidence_count += 1
                continue
            card = self._evidence_card(item, evidence_store=evidence_store)
            if card is None:
                unbound_evidence_count += 1
                continue
            cards.append(card)
        source_revision_ids = sorted(
            {
                reference["source_revision_id"]
                for card in cards
                for reference in card.get("source_refs", [])
                if isinstance(reference, dict)
                and isinstance(reference.get("source_revision_id"), str)
            }
        )
        fragment_ids = sorted(
            {
                str(reference.get("fragment_revision_id") or reference.get("fragment_id"))
                for card in cards
                for reference in card.get("source_refs", [])
                if isinstance(reference, dict)
                and (
                    isinstance(reference.get("fragment_revision_id"), str)
                    or isinstance(reference.get("fragment_id"), str)
                )
            }
        )
        return _EvidenceSelection(
            cards,
            [
                {"code": "evidence_gap", "message": str(message)}
                for message in raw.get("gaps", [])
                if isinstance(message, str)
            ]
            + (
                [
                    {
                        "code": "evidence_gap",
                        "message": (
                            "Evidence candidates without exact Source Revision "
                            "bindings were excluded."
                        ),
                    }
                ]
                if unbound_evidence_count
                else []
            )
            + (
                [
                    {
                        "code": "evidence_gap",
                        "message": (
                            "Source evidence candidates below the deterministic "
                            "relevance floor were excluded."
                        ),
                    }
                ]
                if low_relevance_evidence_count
                else []
            ),
            sum(len(str(item.get("excerpt", ""))) for item in cards),
            source_revision_ids,
            fragment_ids,
        )

    @staticmethod
    def _historical_evidence_from_compiled(
        evidence_store: KnowledgeVault,
        knowledge_store: AutonomousKnowledgeStore,
        *,
        compiled: list[dict[str, Any]],
        scope: str,
        max_sensitivity: str,
        as_of: str,
        limit: int,
        max_chars: int,
    ) -> _EvidenceSelection:
        """Project exact immutable fragments referenced by admitted historical revisions."""

        if not compiled or limit <= 0 or max_chars <= 0:
            return _EvidenceSelection([], [], 0, [], [])
        sensitivity_order = ("public", "internal", "private", "restricted")
        cards: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        selected_chars = 0
        source_cache: dict[str, dict[str, Any]] = {}
        for item in compiled:
            revision_id = item.get("revision_id")
            if not isinstance(revision_id, str):
                continue
            revision = knowledge_store.connection.execute(
                "SELECT source_refs_json FROM knowledge_revisions_v3 WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()
            if revision is None:
                continue
            references = strict_json_loads(revision["source_refs_json"])
            if not isinstance(references, list):
                continue
            for reference in references:
                if not isinstance(reference, dict):
                    continue
                source_revision_id = reference.get("source_revision_id")
                fragment_identity = reference.get("fragment_id") or reference.get(
                    "fragment_revision_id"
                )
                if not isinstance(source_revision_id, str) or not isinstance(
                    fragment_identity, str
                ):
                    continue
                identity = (source_revision_id, fragment_identity)
                if identity in seen:
                    continue
                binding = knowledge_store.connection.execute(
                    """
                    SELECT bindings.legacy_source_id,
                           sources.imported_at,
                           source_lifecycle.activated_at,
                           source_lifecycle.superseded_at,
                           source_lifecycle.removed_at,
                           evidence.scope,
                           evidence.lifecycle AS evidence_lifecycle,
                           governance.sensitivity,
                           governance.review_status,
                           governance.lifecycle_status,
                           governance.activation_status
                    FROM source_revision_bindings_v2 AS bindings
                    JOIN sources ON sources.source_id = bindings.legacy_source_id
                    JOIN source_lifecycle
                      ON source_lifecycle.source_id = bindings.legacy_source_id
                    JOIN evidence_bindings_v3 AS evidence
                      ON evidence.source_revision_id = bindings.source_revision_id
                    JOIN governance_revisions_v2 AS governance
                      ON governance.subject_kind = 'source_revision'
                     AND governance.subject_id = bindings.source_revision_id
                    WHERE bindings.source_revision_id = ?
                      AND sources.imported_at <= ?
                      AND governance.recorded_at <= ?
                    ORDER BY governance.recorded_at DESC,
                             CASE
                                 WHEN governance.activation_status = 'inactive'
                                  AND governance.lifecycle_status NOT IN (
                                      'pending', 'proposed', 'quarantined'
                                  ) THEN 2
                                 WHEN governance.activation_status = 'active' THEN 1
                                 ELSE 0
                             END DESC,
                             governance.governance_revision DESC
                    LIMIT 1
                    """,
                    (source_revision_id, as_of, as_of),
                ).fetchone()
                if (
                    binding is None
                    or binding["activated_at"] is None
                    or binding["activated_at"] > as_of
                    or (
                        binding["superseded_at"] is not None
                        and binding["superseded_at"] <= as_of
                    )
                    or (
                        binding["removed_at"] is not None
                        and binding["removed_at"] <= as_of
                    )
                    or binding["scope"] != scope
                    or binding["evidence_lifecycle"] != "active"
                    or binding["review_status"] != "human_verified"
                    or binding["lifecycle_status"] != "active"
                    or binding["activation_status"] != "active"
                    or binding["sensitivity"] not in sensitivity_order
                    or binding["sensitivity"] == "restricted"
                    or sensitivity_order.index(binding["sensitivity"])
                    > sensitivity_order.index(max_sensitivity)
                    or not evidence_store._source_file_check(
                        binding["legacy_source_id"], cache=source_cache
                    )["valid"]
                ):
                    continue
                fragment_binding = knowledge_store.connection.execute(
                    """
                    SELECT bindings.fragment_id, bindings.fragment_revision_id,
                           fragments.text, fragments.text_sha256, fragments.locator
                    FROM legacy_fragment_bindings_v2 AS bindings
                    JOIN source_revision_bindings_v2 AS source_binding
                      ON source_binding.legacy_source_id = bindings.legacy_source_id
                    JOIN source_fragments AS fragments
                      ON fragments.fragment_id = bindings.fragment_id
                    WHERE source_binding.source_revision_id = ?
                      AND (bindings.fragment_id = ? OR bindings.fragment_revision_id = ?)
                    LIMIT 1
                    """,
                    (source_revision_id, fragment_identity, fragment_identity),
                ).fetchone()
                if fragment_binding is None:
                    continue
                if (
                    reference.get("locator") not in {None, fragment_binding["locator"]}
                    or reference.get("quote_sha256")
                    not in {None, fragment_binding["text_sha256"]}
                ):
                    continue
                remaining = max_chars - selected_chars
                if remaining <= 0 or len(cards) >= limit:
                    break
                text = str(fragment_binding["text"])
                excerpt_value = text[:remaining]
                source_ref = {
                    "source_revision_id": source_revision_id,
                    "fragment_revision_id": fragment_binding["fragment_revision_id"],
                    "locator": fragment_binding["locator"],
                    "quote_sha256": fragment_binding["text_sha256"],
                }
                cards.append(
                    {
                        "title": str(item.get("title", "Historical source evidence")),
                        "kind": "reference",
                        "excerpt": excerpt_value,
                        "content_sha256": fragment_binding["text_sha256"],
                        "status": "active",
                        "verification": "verified_source",
                        "trust": "verified_source",
                        "sensitivity": binding["sensitivity"],
                        "legal_authority": False,
                        "fragment_id": fragment_binding["fragment_revision_id"],
                        "source_refs": [source_ref],
                        "source_ref_count": 1,
                        "source_refs_truncated": False,
                    }
                )
                selected_chars += len(excerpt_value)
                seen.add(identity)
            if len(cards) >= limit or selected_chars >= max_chars:
                break
        return _EvidenceSelection(
            cards,
            [],
            selected_chars,
            sorted(
                {
                    reference["source_revision_id"]
                    for card in cards
                    for reference in card["source_refs"]
                }
            ),
            sorted(str(card["fragment_id"]) for card in cards),
        )

    @staticmethod
    def _evidence_card(
        value: dict[str, Any],
        *,
        evidence_store: KnowledgeVault,
    ) -> dict[str, Any] | None:
        allowed = {
            "asset_id",
            "asset_revision_id",
            "title",
            "kind",
            "excerpt",
            "content_sha256",
            "status",
            "verification",
            "trust",
            "sensitivity",
            "legal_authority",
            "source_refs",
            "source_ref_count",
            "source_refs_truncated",
        }
        card = {key: item for key, item in value.items() if key in allowed}
        asset_id = card.get("asset_id")
        if not isinstance(asset_id, str):
            return None
        identity = evidence_store.connection.execute(
            """
            SELECT asset_revision_id
            FROM asset_revision_bindings_v2
            WHERE legacy_asset_id = ?
            """,
            (asset_id,),
        ).fetchone()
        if identity is None:
            return None
        references = evidence_store.connection.execute(
            """
            SELECT refs.source_revision_id, refs.fragment_revision_id,
                   refs.locator, refs.quote_sha256
            FROM proposal_source_refs_v2 AS refs
            WHERE refs.asset_revision_id = ?
            ORDER BY refs.ref_ordinal
            """,
            (identity["asset_revision_id"],),
        ).fetchall()
        if not references:
            return None
        card["source_refs"] = [dict(reference) for reference in references[:2]]
        card["source_ref_count"] = len(references)
        card["source_refs_truncated"] = len(references) > 2
        return card

    @staticmethod
    def _uncompiled_sources(
        store: AutonomousKnowledgeStore,
        *,
        query: str,
        scope: str,
        max_sensitivity: str,
        limit: int,
    ) -> list[dict[str, str]]:
        terms = tuple(query_search_terms(query, limit=16, cover_tail=True))
        if not terms:
            return []
        rows = store.connection.execute(
            """
            SELECT DISTINCT source_revisions_v2.source_revision_id,
                   source_identities_v2.logical_path,
                   sources.sensitivity,
                   source_lifecycle.status
            FROM source_revisions_v2
            JOIN source_identities_v2 USING(source_key)
            JOIN source_revision_bindings_v2 USING(source_revision_id)
            JOIN sources
              ON sources.source_id = source_revision_bindings_v2.legacy_source_id
            JOIN source_lifecycle
              ON source_lifecycle.source_id = sources.source_id
            JOIN compilations_v2 USING(source_revision_id)
            JOIN source_ir_nodes_v2 USING(compilation_id)
            WHERE source_lifecycle.status IN ('active', 'pending')
              AND NOT EXISTS (
                  SELECT 1 FROM source_compilation_runs_v1
                  WHERE source_compilation_runs_v1.source_revision_id =
                        source_revisions_v2.source_revision_id
                    AND source_compilation_runs_v1.status IN (
                        'committed', 'projection_pending', 'succeeded'
                    )
              )
            ORDER BY source_revisions_v2.source_revision_id
            LIMIT 501
            """
        ).fetchall()
        sensitivity_order = ("public", "internal", "private", "restricted")
        selected: list[dict[str, str]] = []
        for row in rows[:500]:
            if (
                scope != store.vault_scope
                or row["sensitivity"] not in sensitivity_order
                or row["sensitivity"] == "restricted"
                or sensitivity_order.index(row["sensitivity"])
                > sensitivity_order.index(max_sensitivity)
            ):
                continue
            haystack = f"{row['logical_path']}".casefold()
            if not any(term in haystack for term in terms):
                node_conditions = " OR ".join(
                    (
                        "instr(lower(COALESCE(source_ir_nodes_v2.title, '')), ?) > 0 "
                        "OR instr(lower(source_ir_nodes_v2.text), ?) > 0"
                    )
                    for _term in terms
                )
                node_parameters = tuple(
                    parameter
                    for term in terms
                    for parameter in (term, term)
                )
                node_query = (
                    """
                    SELECT 1 FROM source_ir_nodes_v2
                    JOIN compilations_v2 USING(compilation_id)
                    WHERE compilations_v2.source_revision_id = ?
                      AND (
                    """
                    + node_conditions
                    + """
                      )
                    LIMIT 1
                    """
                )
                node = store.connection.execute(
                    node_query,
                    (row["source_revision_id"], *node_parameters),
                ).fetchone()
                if node is None:
                    continue
            selected.append(
                {
                    "source_revision_id": row["source_revision_id"],
                    "status": row["status"],
                }
            )
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _evidence_attachment_count(items: list[dict[str, Any]]) -> int:
        return sum(
            1
            for item in items
            if isinstance(item.get("source_refs"), list) and item["source_refs"]
        )

    @staticmethod
    def _used_channels(
        *,
        compiled: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        fallback: bool,
    ) -> list[str]:
        channels: list[str] = []
        if compiled:
            channels.append("compiled_knowledge")
        if evidence:
            channels.append("source_evidence")
        if fallback:
            channels.append("raw_fragment_fallback")
        return channels

    @staticmethod
    def _bounded_gaps(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for value in values[:32]:
            gap = {
                key: item
                for key, item in value.items()
                if key
                in {
                    "code",
                    "message",
                    "count",
                    "knowledge_revision_id",
                    "freshness",
                    "source_revision_ids",
                }
            }
            if isinstance(gap.get("message"), str) and len(gap["message"]) > 500:
                gap["message"] = gap["message"][:499].rstrip() + "…"
            projected.append(gap)
        return projected

    @staticmethod
    def _legal_boundary_result(
        *,
        query: str,
        policy: QueryPolicy,
        scope: str,
        max_sensitivity: str,
        as_of: str | None,
        limit: int,
        max_chars: int,
        max_tokens: int,
        max_sources: int,
        graph_hops: int,
        retrieval_mode: str,
        audit_head: str,
        legacy_audit_head: str,
    ) -> dict[str, Any]:
        created_at = utc_now()
        plan = {
            "schema_version": "deeplaw.knowledge-query-plan/v4",
            "intent": "purpose_aware_knowledge_retrieval",
            "purpose": "legal",
            "policy_id": policy,
            "query_sha256": sha256_bytes(query.encode("utf-8")),
            "channel_order": list(_POLICY_ORDER[policy]),
            "used_channels": ["law_support_boundary"],
            "scope": scope,
            "max_sensitivity": max_sensitivity,
            "as_of": as_of,
            "filters": {"kinds": []},
            "budget": {
                "items": limit,
                "characters": max_chars,
                "tokens": max_tokens,
                "sources": max_sources,
                "graph_hops": graph_hops,
                "provider_characters": 65_536,
            },
            "input_audit_head": audit_head,
            "input_legacy_audit_head": legacy_audit_head,
            "compiled_candidate_count": 0,
            "compiled_selected_count": 0,
            "evidence_selected_count": 0,
            "uncompiled_source_count": 0,
            "stale_selection_prevented_count": 0,
            "evidence_attachment_count": 0,
            "fallback": {
                "used": False,
                "reason": "legal_queries_require_law_support",
                "source_revision_ids": [],
                "selected_fragment_ids": [],
                "characters": 0,
                "tokens": 0,
                "new_synthesis_created": False,
            },
            "created_at": created_at,
        }
        _validate_contract("knowledge-query-plan.v4.schema.json", plan)
        result = {
            "schema_version": "deeplaw.purpose-aware-retrieval/v1",
            "vault_id": "",
            "purpose": "legal",
            "policy_id": policy,
            "query": query,
            "query_plan": plan,
            "query_plan_sha256": sha256_bytes(canonical_json(plan).encode("utf-8")),
            "compiled": [],
            "evidence": [],
            "contradictions": [],
            "gaps": [
                {
                    "code": "law_support_required",
                    "message": (
                        "Legal purpose is fail-closed here; use law_support for authoritative "
                        "legal evidence."
                    ),
                }
            ],
            "metrics": {
                "compiled_hit": False,
                "source_fallback_used": False,
                "uncompiled_source_count": 0,
                "stale_selection_prevented_count": 0,
                "evidence_attachment_count": 0,
                "repeated_query_reused_compilation": False,
            },
            "budget": {
                "max_items": limit,
                "selected_items": 0,
                "max_characters": max_chars,
                "selected_characters": 0,
                "max_tokens": max_tokens,
                "max_provider_characters": _MAX_PROVIDER_CHARS,
            },
            "audit_head": audit_head,
            "authority_changed_by_ranking": False,
            "write_performed": False,
        }
        del retrieval_mode
        return result
