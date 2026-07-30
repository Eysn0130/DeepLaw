from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, cast

from ..knowledge_autonomy import (
    SENSITIVITIES,
    AutonomousKnowledgeStore,
    _validate_contract,
)
from ..knowledge_intelligence import estimate_tokens
from ..knowledge_models import canonical_timestamp, utc_now
from ..knowledge_store import KnowledgeVault
from ..retrieval_fabric import retrieve
from ..util import canonical_json, sha256_bytes

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
    ) -> dict[str, Any]:
        selected_query = self._bounded_query(query)
        if purpose not in QUERY_PURPOSES:
            raise ValueError("query purpose is invalid")
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

        with (
            KnowledgeVault(self.root, read_only=True) as evidence_store,
            AutonomousKnowledgeStore(self.root, read_only=True) as knowledge_store,
        ):
            selected_scope = scope or knowledge_store.vault_scope
            if selected_scope not in {"personal", "project", "domain"}:
                raise ValueError("purpose-aware query scope is invalid")
            if evidence_store.audit_head != knowledge_store.legacy_audit_head:
                raise RuntimeError("knowledge read planes changed while opening a snapshot")
            if not evidence_store.verify_integrity()["valid"] or not knowledge_store.verify()[
                "valid"
            ]:
                raise RuntimeError("knowledge vault integrity is invalid; query stopped")

            if purpose == "legal":
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
                _validate_contract("purpose-aware-retrieval.v1.schema.json", result)
                return result

            compiled_budget, evidence_budget = self._partition_budget(
                selected_policy,
                limit=limit,
                max_chars=max_chars,
            )
            compiled = self._compiled(
                knowledge_store,
                query=selected_query,
                purpose=purpose,
                scope=selected_scope,
                max_sensitivity=max_sensitivity,
                limit=compiled_budget["items"],
                max_chars=compiled_budget["characters"],
                max_tokens=max_tokens,
                max_sources=max_sources,
                graph_hops=graph_hops,
                retrieval_mode=retrieval_mode,
                as_of=selected_as_of,
                kinds=kinds,
            )
            fallback_reason: str | None = None
            evidence_requested = evidence_budget["items"] > 0
            if (
                selected_policy == "compiled-first-v1"
                and not compiled["results"]
                and purpose != "freshness_check"
            ):
                evidence_requested = True
                evidence_budget = {
                    "items": min(5, limit),
                    "characters": min(6_000, max_chars),
                }
                fallback_reason = "no_fresh_compiled_match"
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
                )
                if evidence_requested
                else _EvidenceSelection([], [], 0, [], [])
            )
            gaps = [
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
            evidence_attachment_count = self._evidence_attachment_count(
                compiled["results"]
            )
            stale_selection_prevented_count = sum(
                1 for gap in gaps if gap.get("code") == "stale_knowledge"
            )
            selected_items = len(compiled["results"]) + len(evidence.cards)
            selected_chars = compiled["selected_characters"] + evidence.selected_characters
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
                    "reason": fallback_reason,
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
            if (
                len(canonical_json(result).encode("utf-8"))
                > _MAX_PROVIDER_CHARS
            ):
                raise RuntimeError("purpose-aware retrieval exceeds its hard 64 KiB budget")
            _validate_contract("purpose-aware-retrieval.v1.schema.json", result)
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
            compiled_items = max(1, limit - evidence_items)
        else:
            compiled_items = max(1, (limit + 1) // 2)
            evidence_items = min(5, max(1, limit - compiled_items))
        evidence_chars = min(
            6_000,
            max(200, max_chars * evidence_items // (compiled_items + evidence_items)),
        )
        compiled_chars = max(200, max_chars - evidence_chars)
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
        max_chars: int,
        max_tokens: int,
        max_sources: int,
        graph_hops: int,
        retrieval_mode: str,
        as_of: str | None,
        kinds: tuple[str, ...],
    ) -> dict[str, Any]:
        raw = store.recall(
            query,
            scope=cast(Any, scope),
            max_sensitivity=cast(Any, max_sensitivity),
            limit=limit,
            max_chars=max_chars,
            max_tokens=max_tokens,
            max_sources=max_sources,
            graph_hops=graph_hops,
            retrieval_mode=retrieval_mode,
            as_of=as_of,
            kinds=kinds,
        )
        accepted: list[dict[str, Any]] = []
        freshness_gaps: list[dict[str, Any]] = []
        stale_prevented = 0
        for item in raw["results"]:
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
        accepted.sort(
            key=lambda item: (
                _KIND_PRIORITY.get(str(item.get("kind")), 99),
                str(item.get("knowledge_id")),
            )
        )
        accepted = accepted[:limit]
        selected_ids = {item["knowledge_id"] for item in accepted}
        contradictions = [
            item
            for item in raw["contradictions"]
            if item.get("knowledge_id") in selected_ids
        ]
        return {
            "results": accepted,
            "contradictions": contradictions,
            "gaps": list(raw["gaps"]),
            "freshness_gaps": freshness_gaps,
            "candidate_count": raw["query_plan"]["candidate_count"],
            "selected_characters": sum(
                len(str(item.get("content", ""))) for item in accepted
            ),
            "stale_prevented_count": stale_prevented,
        }

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
        terms = tuple(
            term.casefold()
            for term in query.replace("/", " ").replace("\\", " ").split()
            if len(term) >= 2
        )[:16]
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
    ) -> _EvidenceSelection:
        if as_of is not None:
            return _EvidenceSelection(
                [],
                [
                    {
                        "code": "historical_evidence_unavailable",
                        "message": (
                            "The legacy evidence-card plane has no historical snapshot; "
                            "current evidence was not substituted."
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
        )
        sensitivity_order = ("public", "internal", "private", "restricted")
        cards = [
            self._evidence_card(item)
            for item in raw.get("results", [])
            if isinstance(item, dict)
            and item.get("sensitivity") in sensitivity_order
            and sensitivity_order.index(item["sensitivity"])
            <= sensitivity_order.index(max_sensitivity)
            and item.get("sensitivity") != "restricted"
        ]
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
            ],
            sum(len(str(item.get("excerpt", ""))) for item in cards),
            source_revision_ids,
            fragment_ids,
        )

    @staticmethod
    def _evidence_card(value: dict[str, Any]) -> dict[str, Any]:
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
            "selection_reason",
            "channels",
        }
        card = {key: item for key, item in value.items() if key in allowed}
        references = card.get("source_refs")
        if isinstance(references, list):
            card["source_refs"] = [
                {
                    key: reference.get(key)
                    for key in (
                        "source_revision_id",
                        "fragment_revision_id",
                        "fragment_id",
                        "locator",
                        "quote_sha256",
                    )
                    if reference.get(key) is not None
                }
                for reference in references[:2]
                if isinstance(reference, dict)
            ]
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
        terms = tuple(
            term.casefold()
            for term in query.replace("/", " ").replace("\\", " ").split()
            if len(term) >= 2
        )[:16]
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
                node = store.connection.execute(
                    """
                    SELECT 1 FROM source_ir_nodes_v2
                    JOIN compilations_v2 USING(compilation_id)
                    WHERE compilations_v2.source_revision_id = ?
                      AND (
                        instr(lower(COALESCE(source_ir_nodes_v2.title, '')), ?) > 0
                        OR instr(lower(source_ir_nodes_v2.text), ?) > 0
                      )
                    LIMIT 1
                    """,
                    (row["source_revision_id"], terms[0], terms[0]),
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
