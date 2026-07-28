from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .knowledge_models import (
    ASSET_KINDS,
    MEMORY_TIERS,
    KnowledgeAsset,
    canonical_timestamp,
    utc_now,
)
from .util import (
    canonical_json,
    compact_text,
    excerpt,
    fts_query,
    normalize_query_text,
    query_phrases,
    search_terms,
    sha256_bytes,
    stable_id,
    strict_json_loads,
)

if TYPE_CHECKING:
    from .knowledge_store import KnowledgeVault

RetrievalMode = Literal[
    "auto",
    "exact",
    "lexical",
    "semantic",
    "tree",
    "graph",
    "temporal",
    "hybrid",
    "global",
]

RETRIEVAL_MODES = frozenset(RetrievalMode.__args__)
QUERY_PLAN_SCHEMA = "deeplaw.knowledge-query-plan/v1"
RETRIEVAL_TRACE_SCHEMA = "deeplaw.knowledge-retrieval-trace/v1"
RETRIEVAL_RESULT_SCHEMA = "deeplaw.knowledge-retrieval/v1"
RETRIEVAL_IMPLEMENTATION_REVISION = "retrieval-fabric/2"
TOKENIZER_PROFILE = "deeplaw-mixed-cjk-code/2"
FUSION_PROFILE = "rrf-duty-diversity/1"
RERANKER_PROFILE = "off"

_ASSET_ID = re.compile(r"asset_[0-9a-f]{24}")
_KNOWLEDGE_KEY = re.compile(r"knowledge_[0-9a-f]{24}")
_DEEPLAW_ASSET_URI = re.compile(
    r"deeplaw://vault_[0-9a-f]{24}/assets/(asset_[0-9a-f]{24})"
)
_DATE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_TYPO_WORD = re.compile(r"[A-Za-z]{5,32}")

_DUTIES = (
    "constraints",
    "current_decisions",
    "required_procedures",
    "definitions",
    "known_lessons",
    "recent_changes",
    "applicability",
    "exceptions",
    "conflicts",
    "open_questions",
    "missing_evidence",
    "counterevidence",
)

_DUTY_KINDS: dict[str, frozenset[str]] = {
    "constraints": frozenset({"constraint", "rule", "requirement"}),
    "current_decisions": frozenset({"decision"}),
    "required_procedures": frozenset({"procedure"}),
    "definitions": frozenset({"definition", "fact", "reference"}),
    "known_lessons": frozenset({"lesson", "experience"}),
    "recent_changes": frozenset({"decision", "fact", "reference"}),
    "applicability": frozenset({"constraint", "rule", "exception", "requirement"}),
    "exceptions": frozenset({"exception", "risk"}),
    "conflicts": frozenset({"risk", "question", "exception"}),
    "open_questions": frozenset({"question"}),
    "missing_evidence": frozenset(),
    "counterevidence": frozenset({"exception", "risk", "question"}),
}

_BASE_CHANNEL_WEIGHTS = {
    "exact_id": 4.0,
    "knowledge_key": 4.0,
    "semantic_key": 3.2,
    "exact_phrase": 2.6,
    "lexical": 1.0,
    "dense": 0.9,
    "tree": 1.15,
    "graph": 0.8,
    "temporal": 1.2,
    "feedback": 0.45,
}

_CHANNEL_ORDER = tuple(_BASE_CHANNEL_WEIGHTS)
_RRF_K = 60
_MAX_CANDIDATES_PER_CHANNEL = 64
_MAX_TRACE_CANDIDATES = 100


@dataclass(slots=True)
class _Candidate:
    asset_id: str
    ranks: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    raw_scores: dict[str, float] = field(default_factory=dict)
    fusion_score: float = 0.0
    selection_score: float = 0.0
    reranker_rank: int | None = None
    knowledge_key: str | None = None
    source_ids: tuple[str, ...] = ()

    def add(
        self,
        channel: str,
        rank: int,
        reason: str,
        *,
        raw_score: float | None = None,
    ) -> None:
        previous = self.ranks.get(channel)
        if previous is None or rank < previous:
            self.ranks[channel] = rank
            self.reasons[channel] = reason
            if raw_score is not None:
                self.raw_scores[channel] = raw_score


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(value in lowered for value in values)


def _query_intents(query: str) -> tuple[str, ...]:
    intents: list[str] = []
    if _ASSET_ID.search(query) or _KNOWLEDGE_KEY.search(query) or query_phrases(query):
        intents.append("exact_lookup")
    if _contains_any(query, ("当前", "最新", "现行", "as-of", "as of", "截至", "过去", "历史")):
        intents.append("temporal")
    if _contains_any(query, ("为什么", "为何", "依赖", "关系", "关联", "多跳", "why", "depend")):
        intents.append("relational")
    if _contains_any(query, ("章节", "目录", "第几页", "页面", "section", "chapter", "page")):
        intents.append("structure_navigation")
    if _contains_any(
        query,
        ("总结", "概览", "全局", "跨文档", "所有", "summary", "overview", "global"),
    ):
        intents.append("global_synthesis")
    if _contains_any(query, ("如何", "怎样", "步骤", "流程", "how to", "procedure")):
        intents.append("procedure")
    if _contains_any(query, ("比较", "区别", "差异", "versus", " vs ", "compare")):
        intents.append("comparison")
    if _contains_any(query, ("冲突", "矛盾", "反例", "例外", "contradict", "counter")):
        intents.append("contradiction")
    if _contains_any(query, ("定义", "是什么", "含义", "define", "meaning")):
        intents.append("definition")
    if not intents:
        intents.append("focused_recall")
    return tuple(dict.fromkeys(intents))


def _query_duties(query: str, intents: tuple[str, ...]) -> tuple[str, ...]:
    duties: list[str] = ["constraints", "applicability"]
    if "procedure" in intents:
        duties.extend(("required_procedures", "known_lessons"))
    if "definition" in intents:
        duties.append("definitions")
    if "temporal" in intents:
        duties.extend(("current_decisions", "recent_changes"))
    if "comparison" in intents or "contradiction" in intents:
        duties.extend(("exceptions", "conflicts", "counterevidence"))
    if "relational" in intents:
        duties.extend(("current_decisions", "counterevidence"))
    if "global_synthesis" in intents:
        duties.extend(
            (
                "current_decisions",
                "definitions",
                "known_lessons",
                "recent_changes",
                "exceptions",
                "conflicts",
                "open_questions",
                "counterevidence",
            )
        )
    if _contains_any(query, ("问题", "未决", "未知", "question", "unknown")):
        duties.append("open_questions")
    duties.append("missing_evidence")
    return tuple(duty for duty in _DUTIES if duty in set(duties))


def _channels_for_mode(
    mode: RetrievalMode,
    intents: tuple[str, ...],
    *,
    dense_available: bool,
) -> tuple[str, ...]:
    if mode == "exact":
        return ("exact_id", "knowledge_key", "semantic_key", "exact_phrase")
    if mode == "lexical":
        return ("exact_id", "knowledge_key", "semantic_key", "exact_phrase", "lexical")
    if mode == "semantic":
        return ("dense",) if dense_available else ()
    if mode == "tree":
        return ("exact_id", "exact_phrase", "tree", "lexical")
    if mode == "graph":
        return ("exact_id", "knowledge_key", "lexical", "graph")
    if mode == "temporal":
        return ("exact_id", "knowledge_key", "lexical", "temporal")
    if mode == "global":
        channels = ["exact_phrase", "lexical", "tree", "graph", "temporal"]
        if dense_available:
            channels.append("dense")
        return tuple(channels)
    if mode == "hybrid":
        channels = [
            "exact_id",
            "knowledge_key",
            "semantic_key",
            "exact_phrase",
            "lexical",
            "tree",
            "graph",
            "temporal",
            "feedback",
        ]
        if dense_available:
            channels.insert(5, "dense")
        return tuple(channels)

    channels = ["exact_id", "knowledge_key", "semantic_key", "exact_phrase", "lexical"]
    if "structure_navigation" in intents or "global_synthesis" in intents:
        channels.append("tree")
    if "relational" in intents or "contradiction" in intents or "global_synthesis" in intents:
        channels.append("graph")
    if "temporal" in intents or "global_synthesis" in intents:
        channels.append("temporal")
    if "procedure" in intents:
        channels.append("feedback")
    # The optional Discovery index remains outside default Context compilation
    # until its frozen held-out gate passes.  It is used only by an explicit
    # semantic/hybrid/global request with an explicitly supplied index.
    return tuple(dict.fromkeys(channels))


def build_query_plan(
    query: str,
    *,
    mode: RetrievalMode = "auto",
    limit: int = 5,
    kinds: tuple[str, ...] = (),
    memory_tiers: tuple[str, ...] = (),
    as_of: str | None = None,
    dense_available: bool = False,
    ranking_profile: dict[str, Any] | None = None,
    reranker_profile: str = RERANKER_PROFILE,
    restricted_allowed: bool = False,
    active_reviewed_only: bool = True,
) -> dict[str, Any]:
    normalized = normalize_query_text(query)
    if not normalized or len(normalized) > 4_000:
        raise ValueError("knowledge query must be between 1 and 4000 characters")
    if mode not in RETRIEVAL_MODES:
        raise ValueError("retrieval mode is invalid")
    if isinstance(limit, bool) or not 1 <= limit <= 20:
        raise ValueError("retrieval limit must be between 1 and 20")
    if any(kind not in ASSET_KINDS for kind in kinds):
        raise ValueError("retrieval contains an unsupported asset kind")
    if any(tier not in MEMORY_TIERS for tier in memory_tiers):
        raise ValueError("retrieval contains an unsupported memory tier")
    if not isinstance(restricted_allowed, bool) or not isinstance(active_reviewed_only, bool):
        raise ValueError("retrieval admission flags must be booleans")
    if reranker_profile != "off" and not re.fullmatch(
        r"rerankerprofile_[0-9a-f]{24}", reranker_profile
    ):
        raise ValueError("reranker profile identity is invalid")
    if as_of is not None:
        as_of = canonical_as_of(as_of)
    else:
        match = _DATE.search(normalized)
        if match and _contains_any(normalized, ("as-of", "as of", "截至")):
            as_of = f"{match.group(1)}T23:59:59Z"
    intents = _query_intents(normalized)
    duties = _query_duties(normalized, intents)
    channels = _channels_for_mode(mode, intents, dense_available=dense_available)
    weights = dict(
        ranking_profile["channel_weights"]
        if ranking_profile is not None
        else _BASE_CHANNEL_WEIGHTS
    )
    if "exact_lookup" in intents:
        for channel in ("exact_id", "knowledge_key", "semantic_key", "exact_phrase"):
            weights[channel] *= 1.5
    if "structure_navigation" in intents:
        weights["tree"] *= 1.6
    if "relational" in intents or "contradiction" in intents:
        weights["graph"] *= 1.5
    if "temporal" in intents:
        weights["temporal"] *= 1.6
    if "global_synthesis" in intents:
        weights["tree"] *= 1.25
        weights["graph"] *= 1.25
    terms = search_terms(normalized, limit=48, cover_tail=True)
    phrases = query_phrases(normalized)
    if not terms and not phrases and not _ASSET_ID.search(normalized):
        raise ValueError("knowledge query has no searchable terms")
    channel_budgets = {
        channel: min(_MAX_CANDIDATES_PER_CHANNEL, max(12, limit * 8))
        for channel in channels
    }
    plan_body = {
        "schema_version": QUERY_PLAN_SCHEMA,
        "normalized_query": normalized,
        "mode": mode,
        "intent": list(intents),
        "duties": list(duties),
        "channels": list(channels),
        "channel_budgets": channel_budgets,
        "filters": {
            "kinds": list(kinds),
            "memory_tiers": list(memory_tiers),
            "active_reviewed_only": active_reviewed_only,
            "restricted_allowed": restricted_allowed,
        },
        "temporal_scope": {"as_of": as_of, "mode": "as-of" if as_of else "current"},
        "search_terms": terms,
        "exact_phrases": phrases,
        "fusion_profile": FUSION_PROFILE,
        "channel_weights": {channel: weights[channel] for channel in channels},
        "reranker_profile": reranker_profile,
        "retrieval_profile": (
            {
                "profile_id": ranking_profile["profile_id"],
                "profile_sha256": ranking_profile["profile_sha256"],
                "authority_effect": "ranking-only",
            }
            if ranking_profile is not None
            else {
                "profile_id": "builtin",
                "profile_sha256": None,
                "authority_effect": "ranking-only",
            }
        ),
        "tokenizer_profile": TOKENIZER_PROFILE,
        "implementation_revision": RETRIEVAL_IMPLEMENTATION_REVISION,
    }
    return {
        **plan_body,
        "query_plan_id": stable_id(
            "queryplan",
            sha256_bytes(canonical_json(plan_body).encode("utf-8")),
        ),
    }


def _add_ranked(
    candidates: dict[str, _Candidate],
    channel: str,
    values: list[tuple[str, str, float | None]],
) -> None:
    seen: set[str] = set()
    rank = 0
    for asset_id, reason, raw_score in values:
        if asset_id in seen:
            continue
        seen.add(asset_id)
        rank += 1
        candidates.setdefault(asset_id, _Candidate(asset_id)).add(
            channel,
            rank,
            reason,
            raw_score=raw_score,
        )


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    left = left.casefold()
    right = right.casefold()
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        mismatches = [
            index for index, (first, second) in enumerate(zip(left, right, strict=True))
            if first != second
        ]
        if len(mismatches) == 1:
            return True
        return (
            len(mismatches) == 2
            and mismatches[1] == mismatches[0] + 1
            and left[mismatches[0]] == right[mismatches[1]]
            and left[mismatches[1]] == right[mismatches[0]]
        )
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_index = 0
    long_index = 0
    skipped = False
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
            continue
        if skipped:
            return False
        skipped = True
        long_index += 1
    return True


def _typo_candidates(
    vault: KnowledgeVault,
    plan: dict[str, Any],
    *,
    budget: int,
) -> list[tuple[str, str, float | None]]:
    typo_terms = list(
        dict.fromkeys(
            term.casefold()
            for term in plan["search_terms"]
            if _TYPO_WORD.fullmatch(term)
        )
    )[:4]
    if not typo_terms:
        return []
    prefixes = list(
        dict.fromkeys(
            term[:4] if len(term) >= 7 else term[:3]
            for term in typo_terms
        )
    )
    prefix_query = " OR ".join(f'"{prefix}"*' for prefix in prefixes)
    filters = plan["filters"]
    clauses = ["asset_search MATCH ?"]
    parameters: list[Any] = [prefix_query]
    if filters["kinds"]:
        clauses.append(f"assets.kind IN ({','.join('?' for _ in filters['kinds'])})")
        parameters.extend(filters["kinds"])
    if filters["memory_tiers"]:
        clauses.append(
            f"assets.memory_tier IN ({','.join('?' for _ in filters['memory_tiers'])})"
        )
        parameters.extend(filters["memory_tiers"])
    parameters.append(min(128, budget * 8))
    rows = vault.connection.execute(
        f"""
        SELECT assets.asset_id, assets.title, assets.statement,
               assets.semantic_key, assets.tags_json,
               bm25(asset_search, 0.0, 8.0, 3.0, 10.0, 2.0) AS lexical_rank
        FROM asset_search JOIN assets USING(asset_id)
        WHERE {' AND '.join(clauses)}
        ORDER BY lexical_rank, assets.asset_id
        LIMIT ?
        """,
        tuple(parameters),
    ).fetchall()
    values: list[tuple[str, str, float | None]] = []
    for row in rows:
        tags = strict_json_loads(row["tags_json"])
        candidate_words = list(
            dict.fromkeys(
                word.casefold()
                for word in _TYPO_WORD.findall(
                    " ".join(
                        (
                            row["title"],
                            row["statement"],
                            row["semantic_key"] or "",
                            *(tags if isinstance(tags, list) else ()),
                        )
                    )
                )
            )
        )[:200]
        match = next(
            (
                (query_term, candidate_word)
                for query_term in typo_terms
                for candidate_word in candidate_words
                if _edit_distance_at_most_one(query_term, candidate_word)
            ),
            None,
        )
        if match is None:
            continue
        values.append(
            (
                row["asset_id"],
                f"fielded_bm25_typo_repair:{match[0]}->{match[1]}",
                -float(row["lexical_rank"]),
            )
        )
        if len(values) == budget:
            break
    return values


def _exact_candidates(
    vault: KnowledgeVault,
    plan: dict[str, Any],
    candidates: dict[str, _Candidate],
) -> None:
    query = plan["normalized_query"]
    budget = max(plan["channel_budgets"].values(), default=20)
    uri_matches = [match.group(1) for match in _DEEPLAW_ASSET_URI.finditer(query)]
    asset_matches = [*uri_matches, *_ASSET_ID.findall(query)]
    existing_assets: list[tuple[str, str, float | None]] = []
    for asset_id in dict.fromkeys(asset_matches):
        row = vault.connection.execute(
            "SELECT asset_id FROM assets WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if row is not None:
            existing_assets.append((asset_id, "exact_asset_id_or_uri", None))
    if "exact_id" in plan["channels"]:
        _add_ranked(candidates, "exact_id", existing_assets[:budget])

    knowledge_values: list[tuple[str, str, float | None]] = []
    if "knowledge_key" in plan["channels"]:
        for knowledge_key in dict.fromkeys(_KNOWLEDGE_KEY.findall(query)):
            rows = vault.connection.execute(
                """
                SELECT asset_revision_bindings_v2.legacy_asset_id
                FROM knowledge_revisions_v2
                JOIN asset_revision_bindings_v2 USING(asset_revision_id)
                WHERE knowledge_revisions_v2.knowledge_key = ?
                ORDER BY asset_revision_bindings_v2.observed_at DESC,
                         asset_revision_bindings_v2.legacy_asset_id
                LIMIT ?
                """,
                (knowledge_key, budget),
            ).fetchall()
            knowledge_values.extend(
                (row["legacy_asset_id"], "exact_knowledge_key", None) for row in rows
            )
        _add_ranked(candidates, "knowledge_key", knowledge_values)

    if "semantic_key" in plan["channels"]:
        semantic_values: list[tuple[str, str, float | None]] = []
        semantic_queries = [query]
        semantic_queries.extend(plan["exact_phrases"])
        active_only = plan["filters"]["active_reviewed_only"]
        for value in dict.fromkeys(semantic_queries):
            if active_only:
                rows = vault.connection.execute(
                    """
                    SELECT asset_id FROM assets
                    WHERE semantic_key = ? AND status = 'active'
                    ORDER BY asset_id LIMIT ?
                    """,
                    (value, budget),
                ).fetchall()
            else:
                rows = vault.connection.execute(
                    """
                    SELECT asset_id FROM assets
                    WHERE semantic_key = ?
                    ORDER BY asset_id LIMIT ?
                    """,
                    (value, budget),
                ).fetchall()
            semantic_values.extend(
                (row["asset_id"], "exact_semantic_key", None) for row in rows
            )
        _add_ranked(candidates, "semantic_key", semantic_values)

    if "exact_phrase" in plan["channels"]:
        phrases = list(plan["exact_phrases"])
        if not phrases and len(query) <= 100 and len(plan["search_terms"]) <= 8:
            phrases.append(query)
        phrase_values: list[tuple[str, str, float | None]] = []
        for phrase in phrases[:8]:
            phrase_terms = search_terms(phrase, limit=16, cover_tail=True)
            if not phrase_terms:
                continue
            safe_phrase_terms = (term.replace('"', '""') for term in phrase_terms)
            phrase_match = " AND ".join(
                f'"{term}"' for term in safe_phrase_terms
            )
            rows = vault.connection.execute(
                """
                SELECT assets.asset_id
                FROM asset_search JOIN assets USING(asset_id)
                WHERE asset_search MATCH ?
                  AND (
                    instr(lower(assets.title), lower(?)) > 0
                    OR instr(lower(assets.statement), lower(?)) > 0
                    OR instr(lower(COALESCE(assets.semantic_key, '')), lower(?)) > 0
                  )
                ORDER BY CASE WHEN lower(assets.title) = lower(?) THEN 0 ELSE 1 END,
                         assets.asset_id
                LIMIT ?
                """,
                (phrase_match, phrase, phrase, phrase, phrase, budget),
            ).fetchall()
            phrase_values.extend(
                (row["asset_id"], f"exact_phrase:{phrase[:80]}", None) for row in rows
            )
        _add_ranked(candidates, "exact_phrase", phrase_values[:budget])


def _lexical_candidates(
    vault: KnowledgeVault,
    plan: dict[str, Any],
    candidates: dict[str, _Candidate],
) -> None:
    if "lexical" not in plan["channels"]:
        return
    terms = plan["search_terms"]
    if not terms:
        return
    budget = plan["channel_budgets"]["lexical"]
    distinctive_terms = [
        term
        for term in terms
        if term.isascii()
        and (any(character.isdigit() for character in term) or len(term) >= 12)
    ][:8]
    selected_terms = distinctive_terms or terms
    clauses = ["asset_search MATCH ?"]
    parameters: list[Any] = [fts_query(selected_terms)]
    filters = plan["filters"]
    if filters["kinds"]:
        clauses.append(f"assets.kind IN ({','.join('?' for _ in filters['kinds'])})")
        parameters.extend(filters["kinds"])
    if filters["memory_tiers"]:
        clauses.append(
            f"assets.memory_tier IN ({','.join('?' for _ in filters['memory_tiers'])})"
        )
        parameters.extend(filters["memory_tiers"])
    parameters.append(budget)
    statement = f"""
        SELECT assets.asset_id,
               bm25(asset_search, 0.0, 8.0, 3.0, 10.0, 2.0) AS lexical_rank
        FROM asset_search JOIN assets USING(asset_id)
        WHERE {' AND '.join(clauses)}
        ORDER BY lexical_rank, assets.asset_id
        LIMIT ?
        """
    rows = vault.connection.execute(statement, tuple(parameters)).fetchall()
    if not rows and distinctive_terms:
        parameters[0] = fts_query(terms)
        rows = vault.connection.execute(statement, tuple(parameters)).fetchall()
    typo_values = _typo_candidates(vault, plan, budget=budget) if not rows else []
    _add_ranked(
        candidates,
        "lexical",
        (
            [
                (row["asset_id"], "fielded_bm25_cjk", -float(row["lexical_rank"]))
                for row in rows
            ]
            if rows
            else typo_values
        ),
    )


def _tree_candidates(
    vault: KnowledgeVault,
    plan: dict[str, Any],
    candidates: dict[str, _Candidate],
) -> None:
    if "tree" not in plan["channels"] or not vault.identity_v2_enabled:
        return
    anchors = [
        term
        for term in plan["search_terms"]
        if (term.isascii() and len(term) >= 3) or (not term.isascii() and len(term) >= 4)
    ][:8]
    if not anchors:
        anchors = plan["search_terms"][:4]
    if not anchors:
        return
    seeded = sorted(
        candidates.values(),
        key=lambda candidate: (
            min(candidate.ranks.values(), default=10_000),
            candidate.asset_id,
        ),
    )[: min(32, plan["channel_budgets"]["tree"] * 2)]
    if seeded:
        selected_rows = ",".join("(?, ?)" for _ in seeded)
        selected_parameters: list[Any] = []
        for rank, candidate in enumerate(seeded, start=1):
            selected_parameters.extend((candidate.asset_id, rank))
        selected_parameters.append(plan["channel_budgets"]["tree"] * 8)
        rows = vault.connection.execute(
            f"""
            WITH selected_assets(asset_id, seed_rank) AS (
                VALUES {selected_rows}
            )
            SELECT selected_assets.seed_rank,
                   asset_revision_bindings_v2.legacy_asset_id,
                   source_ir_nodes_v2.logical_node_key,
                   source_ir_nodes_v2.ordinal,
                   source_ir_nodes_v2.node_type,
                   source_ir_nodes_v2.title,
                   source_ir_nodes_v2.text
            FROM selected_assets
            JOIN asset_revision_bindings_v2
              ON asset_revision_bindings_v2.legacy_asset_id =
                 selected_assets.asset_id
            JOIN proposal_source_refs_v2 USING(asset_revision_id)
            JOIN fragment_node_membership_v2 USING(fragment_revision_id)
            JOIN source_ir_nodes_v2 USING(node_id)
            ORDER BY selected_assets.seed_rank,
                     source_ir_nodes_v2.ordinal,
                     source_ir_nodes_v2.node_id
            LIMIT ?
            """,
            tuple(selected_parameters),
        ).fetchall()
        seeded_values: list[tuple[int, int, str, str]] = []
        for row in rows:
            node_text = f"{row['title'] or ''}\n{row['text']}".casefold()
            anchor_score = sum(
                (
                    100
                    if any(character.isdigit() for character in anchor)
                    else (30 if len(anchor) >= 12 else 1)
                )
                for anchor in anchors
                if anchor.casefold() in node_text
            )
            seeded_values.append(
                (
                    -anchor_score,
                    row["seed_rank"],
                    row["legacy_asset_id"],
                    f"source_tree:{row['logical_node_key']}:{row['node_type']}",
                )
            )
        if seeded_values:
            seeded_values.sort()
            _add_ranked(
                candidates,
                "tree",
                [
                    (asset_id, reason, None)
                    for _, _, asset_id, reason in seeded_values[
                        : plan["channel_budgets"]["tree"]
                    ]
                ],
            )
            return
    conditions = " OR ".join(
        "instr(lower(COALESCE(source_ir_nodes_v2.title, '')), lower(?)) > 0 "
        "OR instr(lower(source_ir_nodes_v2.text), lower(?)) > 0"
        for _ in anchors
    )
    parameters: list[Any] = []
    for anchor in anchors:
        parameters.extend((anchor, anchor))
    score_expression = " + ".join(
        "CASE WHEN instr(lower(COALESCE(source_ir_nodes_v2.title, '')), lower(?)) > 0 "
        "OR instr(lower(source_ir_nodes_v2.text), lower(?)) > 0 THEN ? ELSE 0 END"
        for _ in anchors
    )
    score_parameters: list[Any] = []
    for anchor in anchors:
        identifier_weight = (
            100
            if any(character.isdigit() for character in anchor)
            else (30 if len(anchor) >= 12 else 1)
        )
        score_parameters.extend((anchor, anchor, identifier_weight))
    node_budget = min(128, plan["channel_budgets"]["tree"] * 3)
    query_parameters = [*score_parameters, *parameters, node_budget]
    nodes = vault.connection.execute(
        f"""
        SELECT node_id, logical_node_key, ordinal, node_type,
               ({score_expression}) AS tree_score
        FROM source_ir_nodes_v2
        WHERE {conditions}
        ORDER BY tree_score DESC, compilation_id, ordinal, node_id
        LIMIT ?
        """,
        tuple(query_parameters),
    ).fetchall()
    values: list[tuple[str, str, float | None]] = []
    for node in nodes:
        rows = vault.connection.execute(
            """
            SELECT DISTINCT asset_revision_bindings_v2.legacy_asset_id
            FROM fragment_node_membership_v2
            JOIN proposal_source_refs_v2 USING(fragment_revision_id)
            JOIN asset_revision_bindings_v2 USING(asset_revision_id)
            WHERE fragment_node_membership_v2.node_id = ?
            ORDER BY asset_revision_bindings_v2.legacy_asset_id
            LIMIT 16
            """,
            (node["node_id"],),
        ).fetchall()
        values.extend(
            (
                row["legacy_asset_id"],
                f"source_tree:{node['logical_node_key']}:{node['node_type']}",
                None,
            )
            for row in rows
        )
        if len(values) >= plan["channel_budgets"]["tree"] * 2:
            break
    _add_ranked(
        candidates,
        "tree",
        values[: plan["channel_budgets"]["tree"]],
    )


def _graph_candidates(
    vault: KnowledgeVault,
    plan: dict[str, Any],
    candidates: dict[str, _Candidate],
) -> None:
    if "graph" not in plan["channels"] or not vault.control_enabled:
        return
    seeds = [
        candidate.asset_id
        for candidate in sorted(
            candidates.values(),
            key=lambda candidate: (
                min(candidate.ranks.values(), default=10_000),
                candidate.asset_id,
            ),
        )[:12]
    ]
    if not seeds:
        return
    budget = plan["channel_budgets"]["graph"]
    visited = set(seeds)
    frontier = seeds
    values: list[tuple[str, str, float | None]] = []
    for hop in (1, 2):
        if not frontier or len(values) >= budget:
            break
        frontier = sorted(dict.fromkeys(frontier))[:20]
        frontier_set = set(frontier)
        rows = vault.relations_for_assets(
            frontier,
            limit=min(64, max(1, (budget - len(values)) * 2)),
            include_restricted=plan["filters"]["restricted_allowed"],
            require_evidence=True,
        )
        next_frontier: list[str] = []
        for row in rows:
            if row["subject_asset_id"] in frontier_set:
                neighbor = row["object_asset_id"]
                seed = row["subject_asset_id"]
            elif row["object_asset_id"] in frontier_set:
                neighbor = row["subject_asset_id"]
                seed = row["object_asset_id"]
            else:  # pragma: no cover - storage query is endpoint-bound
                continue
            if neighbor in visited:
                continue
            visited.add(neighbor)
            next_frontier.append(neighbor)
            reason = (
                f"reviewed_relation:{row['predicate']}:{seed}"
                if hop == 1
                else f"reviewed_relation:hop2:{row['predicate']}:{seed}"
            )
            values.append((neighbor, reason, None))
            if len(values) >= budget:
                break
        frontier = next_frontier
    _add_ranked(
        candidates,
        "graph",
        values,
    )


def _temporal_candidates(
    vault: KnowledgeVault,
    plan: dict[str, Any],
    candidates: dict[str, _Candidate],
) -> None:
    if "temporal" not in plan["channels"] or not vault.identity_v2_enabled:
        return
    seed_ids = [
        candidate.asset_id
        for candidate in sorted(
            candidates.values(),
            key=lambda candidate: (
                min(candidate.ranks.values(), default=10_000),
                candidate.asset_id,
            ),
        )[:16]
    ]
    values: list[tuple[str, str, float | None]] = [
        (asset_id, "temporal_scope_seed", None) for asset_id in seed_ids
    ]
    if seed_ids:
        placeholders = ",".join("?" for _ in seed_ids)
        selected_time = plan["temporal_scope"]["as_of"] or utc_now()
        if plan["temporal_scope"]["as_of"] is None:
            sql = f"""
            WITH eligible AS (
                SELECT relation_revisions_v2.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY relation_key
                           ORDER BY observed_at DESC, relation_revision_id DESC
                       ) AS relation_rank
                FROM relation_revisions_v2
            )
            SELECT eligible.relation_revision_id,
                   eligible.predicate,
                   eligible.evidence_refs_json,
                   subject_binding.legacy_asset_id AS subject_asset_id,
                   object_binding.legacy_asset_id AS object_asset_id
            FROM eligible
            JOIN asset_revision_bindings_v2 AS subject_binding
              ON subject_binding.asset_revision_id =
                 eligible.subject_asset_revision_id
            JOIN assets AS subject_asset
              ON subject_asset.asset_id = subject_binding.legacy_asset_id
            JOIN asset_revision_bindings_v2 AS object_binding
              ON object_binding.asset_revision_id =
                 eligible.object_asset_revision_id
            JOIN assets AS object_asset
              ON object_asset.asset_id = object_binding.legacy_asset_id
            WHERE eligible.relation_rank = 1
              AND eligible.status = 'active'
              AND subject_asset.status = 'active'
              AND object_asset.status = 'active'
              AND (eligible.valid_from IS NULL OR eligible.valid_from <= ?)
              AND (eligible.valid_to IS NULL OR eligible.valid_to > ?)
              AND (subject_binding.legacy_asset_id IN ({placeholders})
                   OR object_binding.legacy_asset_id IN ({placeholders}))
            ORDER BY eligible.observed_at DESC, eligible.relation_revision_id
            LIMIT ?
            """
            parameters: tuple[Any, ...] = (
                selected_time,
                selected_time,
                *seed_ids,
                *seed_ids,
                plan["channel_budgets"]["temporal"] * 2,
            )
        else:
            sql = f"""
            WITH eligible AS (
                SELECT relation_revisions_v2.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY relation_key
                           ORDER BY observed_at DESC, relation_revision_id DESC
                       ) AS relation_rank
                FROM relation_revisions_v2
                WHERE observed_at <= ?
            ), subject_governance AS (
                SELECT governance_revisions_v2.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY subject_id
                           ORDER BY recorded_at DESC, governance_revision DESC
                       ) AS governance_rank
                FROM governance_revisions_v2
                WHERE subject_kind = 'asset_revision' AND recorded_at <= ?
            ), object_governance AS (
                SELECT governance_revisions_v2.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY subject_id
                           ORDER BY recorded_at DESC, governance_revision DESC
                       ) AS governance_rank
                FROM governance_revisions_v2
                WHERE subject_kind = 'asset_revision' AND recorded_at <= ?
            )
            SELECT eligible.relation_revision_id,
                   eligible.predicate,
                   eligible.evidence_refs_json,
                   subject_binding.legacy_asset_id AS subject_asset_id,
                   object_binding.legacy_asset_id AS object_asset_id
            FROM eligible
            JOIN asset_revision_bindings_v2 AS subject_binding
              ON subject_binding.asset_revision_id = eligible.subject_asset_revision_id
            JOIN asset_revision_bindings_v2 AS object_binding
              ON object_binding.asset_revision_id = eligible.object_asset_revision_id
            JOIN subject_governance
              ON subject_governance.subject_id = eligible.subject_asset_revision_id
             AND subject_governance.governance_rank = 1
            JOIN object_governance
              ON object_governance.subject_id = eligible.object_asset_revision_id
             AND object_governance.governance_rank = 1
            WHERE eligible.relation_rank = 1
              AND eligible.status = 'active'
              AND subject_governance.review_status = 'human_verified'
              AND subject_governance.lifecycle_status = 'active'
              AND subject_governance.activation_status = 'active'
              AND object_governance.review_status = 'human_verified'
              AND object_governance.lifecycle_status = 'active'
              AND object_governance.activation_status = 'active'
              AND (eligible.valid_from IS NULL OR eligible.valid_from <= ?)
              AND (eligible.valid_to IS NULL OR eligible.valid_to > ?)
              AND (subject_binding.legacy_asset_id IN ({placeholders})
                   OR object_binding.legacy_asset_id IN ({placeholders}))
            ORDER BY eligible.observed_at DESC, eligible.relation_revision_id
            LIMIT ?
            """
            parameters = (
                selected_time,
                selected_time,
                selected_time,
                selected_time,
                selected_time,
                *seed_ids,
                *seed_ids,
                plan["channel_budgets"]["temporal"] * 2,
            )
        rows = vault.connection.execute(sql, parameters).fetchall()
        seed_set = set(seed_ids)
        for row in rows:
            if vault._relation_revision_admission_reasons(
                relation_revision_id=row["relation_revision_id"],
                evidence_refs_json=row["evidence_refs_json"],
                as_of=plan["temporal_scope"]["as_of"],
                include_restricted=plan["filters"]["restricted_allowed"],
            ):
                continue
            neighbor = (
                row["object_asset_id"]
                if row["subject_asset_id"] in seed_set
                else row["subject_asset_id"]
            )
            values.append(
                (
                    neighbor,
                    f"temporal_relation:{row['predicate']}:{row['relation_revision_id']}",
                    None,
                )
            )
    _add_ranked(
        candidates,
        "temporal",
        values[: plan["channel_budgets"]["temporal"]],
    )


def _feedback_candidates(
    vault: KnowledgeVault,
    plan: dict[str, Any],
    candidates: dict[str, _Candidate],
) -> None:
    if "feedback" not in plan["channels"] or not vault.control_enabled:
        return
    helpful_counts: defaultdict[str, int] = defaultdict(int)
    for row in vault.connection.execute(
        "SELECT payload_json FROM feedback_records ORDER BY created_at DESC LIMIT 500"
    ):
        try:
            payload = strict_json_loads(row["payload_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for asset_id in payload.get("helpful_asset_ids", []):
            if isinstance(asset_id, str):
                helpful_counts[asset_id] += 1
        for classification in (
            "irrelevant_asset_ids",
            "harmful_asset_ids",
            "stale_asset_ids",
        ):
            for asset_id in payload.get(classification, []):
                if isinstance(asset_id, str):
                    helpful_counts[asset_id] -= 1
    candidate_ids = set(candidates)
    ranked = sorted(
        (
            (count, asset_id)
            for asset_id, count in helpful_counts.items()
            if count > 0 and asset_id in candidate_ids
        ),
        key=lambda item: (-item[0], item[1]),
    )
    _add_ranked(
        candidates,
        "feedback",
        [
            (asset_id, f"reviewed_helpful_feedback:{count}", float(count))
            for count, asset_id in ranked[: plan["channel_budgets"]["feedback"]]
        ],
    )


def _dense_candidates(
    vault: KnowledgeVault,
    plan: dict[str, Any],
    candidates: dict[str, _Candidate],
    *,
    discovery_index_path: Path | None,
    model_root: Path | None,
    threads: int | None,
) -> None:
    if "dense" not in plan["channels"] or discovery_index_path is None:
        return
    from .knowledge_discovery import DiscoveryIndex

    discovery = DiscoveryIndex(
        discovery_index_path,
        vault=vault,
        model_root=model_root,
        threads=threads,
    )
    rows = discovery.search(
        plan["normalized_query"],
        limit=plan["channel_budgets"]["dense"],
    )
    _add_ranked(
        candidates,
        "dense",
        [
            (row["asset_id"], row["hit_reason"], None)
            for row in rows
        ],
    )


def _asset_identity(vault: KnowledgeVault, asset: KnowledgeAsset) -> tuple[str, tuple[str, ...]]:
    row = vault.connection.execute(
        """
        SELECT knowledge_revisions_v2.knowledge_key
        FROM asset_revision_bindings_v2
        JOIN knowledge_revisions_v2 USING(asset_revision_id)
        WHERE asset_revision_bindings_v2.legacy_asset_id = ?
        """,
        (asset.asset_id,),
    ).fetchone()
    knowledge_key = (
        row["knowledge_key"]
        if row is not None
        else asset.semantic_key or f"legacy:{asset.asset_id}"
    )
    source_ids = tuple(sorted({reference.source_id for reference in asset.source_refs}))
    return knowledge_key, source_ids


def _governance_at(
    vault: KnowledgeVault,
    asset_id: str,
    as_of: str,
) -> tuple[bool, str | None]:
    binding = vault.connection.execute(
        "SELECT asset_revision_id FROM asset_revision_bindings_v2 WHERE legacy_asset_id = ?",
        (asset_id,),
    ).fetchone()
    if binding is None:
        return False, "historical_governance_unavailable"
    row = vault.connection.execute(
        """
        SELECT lifecycle_status, activation_status, review_status, sensitivity
        FROM governance_revisions_v2
        WHERE subject_kind = 'asset_revision' AND subject_id = ? AND recorded_at <= ?
        ORDER BY recorded_at DESC, governance_revision DESC LIMIT 1
        """,
        (binding["asset_revision_id"], as_of),
    ).fetchone()
    if row is None:
        return False, "not_reviewed_at_requested_time"
    if (
        row["review_status"] != "human_verified"
        or row["lifecycle_status"] != "active"
        or row["activation_status"] != "active"
    ):
        return False, f"historical_governance:{row['lifecycle_status']}"
    return True, None


def _relevance_admitted(asset: KnowledgeAsset, plan: dict[str, Any], candidate: _Candidate) -> bool:
    if set(candidate.ranks) & {"exact_id", "knowledge_key", "semantic_key", "exact_phrase"}:
        return True
    if candidate.reasons.get("graph", "").startswith("reviewed_relation:"):
        return True
    if candidate.reasons.get("temporal", "").startswith("temporal_relation:"):
        return True
    if candidate.reasons.get("lexical", "").startswith(
        "fielded_bm25_typo_repair:"
    ):
        return True
    haystack = compact_text(
        " ".join((asset.title, asset.semantic_key or "", asset.statement, *asset.tags))
    )
    title = compact_text(" ".join((asset.title, asset.semantic_key or "", *asset.tags)))
    matches = [
        compact_text(term)
        for term in plan["search_terms"]
        if compact_text(term) and compact_text(term) in haystack
    ]
    if any(term in title for term in matches):
        return True
    if any(
        (term.isascii() and len(term) >= 8)
        or (not term.isascii() and len(term) >= 4)
        for term in matches
    ):
        return True
    meaningful = [
        term
        for term in plan["search_terms"]
        if (term.isascii() and len(term) >= 3) or (not term.isascii() and len(term) >= 2)
    ]
    required = 1 if len(meaningful) <= 2 else 2 if len(meaningful) <= 8 else 3
    return len(set(matches)) >= required


def _admit_candidate(
    vault: KnowledgeVault,
    asset: KnowledgeAsset,
    candidate: _Candidate,
    plan: dict[str, Any],
    *,
    include_restricted: bool,
    include_inactive: bool,
    source_cache: dict[str, dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    as_of = plan["temporal_scope"]["as_of"]
    if as_of is None:
        if not include_inactive and asset.status != "active":
            reasons.append(f"lifecycle_status:{asset.status}")
        if not include_inactive and asset.verification != "human_verified":
            reasons.append(f"review_status:{asset.verification}")
    elif not include_inactive:
        allowed, reason = _governance_at(vault, asset.asset_id, as_of)
        if not allowed and reason:
            reasons.append(reason)
    if not include_restricted and asset.sensitivity == "restricted":
        reasons.append("sensitivity:restricted")
    selected_time = as_of or utc_now()
    if asset.expires_at is not None and asset.expires_at <= selected_time:
        reasons.append("expired")
    if plan["filters"]["kinds"] and asset.kind not in plan["filters"]["kinds"]:
        reasons.append("kind_filter")
    if (
        plan["filters"]["memory_tiers"]
        and asset.memory_tier not in plan["filters"]["memory_tiers"]
    ):
        reasons.append("memory_tier_filter")
    if not _relevance_admitted(asset, plan, candidate):
        reasons.append("weak_query_match")
    for reference in asset.source_refs:
        check = vault._source_file_check(reference.source_id, cache=source_cache)
        if not check["valid"]:
            reasons.append(f"source_integrity:{reference.source_id}")
        source_governance = vault.connection.execute(
            """
            SELECT source_lifecycle.status,
                   governance_revisions_v2.sensitivity,
                   governance_revisions_v2.lifecycle_status AS governed_lifecycle
            FROM source_revision_bindings_v2
            JOIN source_lifecycle
              ON source_lifecycle.source_id =
                 source_revision_bindings_v2.legacy_source_id
            JOIN governance_revisions_v2
              ON governance_revisions_v2.subject_kind = 'source_revision'
             AND governance_revisions_v2.subject_id =
                 source_revision_bindings_v2.source_revision_id
            WHERE source_revision_bindings_v2.legacy_source_id = ?
              AND (? IS NULL OR governance_revisions_v2.recorded_at <= ?)
            ORDER BY governance_revisions_v2.recorded_at DESC,
                     governance_revisions_v2.governance_revision DESC
            LIMIT 1
            """,
            (reference.source_id, as_of, as_of),
        ).fetchone()
        if source_governance is None:
            reasons.append(f"source_governance_missing:{reference.source_id}")
        else:
            if (
                not include_restricted
                and source_governance["sensitivity"] == "restricted"
            ):
                reasons.append(f"source_sensitivity:restricted:{reference.source_id}")
            if as_of is None and source_governance["status"] == "removed":
                reasons.append(f"source_lifecycle:removed:{reference.source_id}")
            if source_governance["governed_lifecycle"] == "removed":
                reasons.append(f"source_governance:removed:{reference.source_id}")
    if vault.identity_v2_enabled:
        binding = vault.connection.execute(
            """
            SELECT knowledge_revisions_v2.knowledge_key,
                   asset_revision_bindings_v2.asset_revision_id
            FROM asset_revision_bindings_v2
            JOIN knowledge_revisions_v2 USING(asset_revision_id)
            WHERE asset_revision_bindings_v2.legacy_asset_id = ?
            """,
            (asset.asset_id,),
        ).fetchone()
        if binding is not None:
            deletion = vault.connection.execute(
                """
                SELECT created_at FROM knowledge_lineage_v2
                WHERE knowledge_key = ? AND status = 'deleted'
                  AND json_array_length(to_asset_revision_ids_json) = 0
                ORDER BY created_at DESC LIMIT 1
                """,
                (binding["knowledge_key"],),
            ).fetchone()
            if deletion is not None and deletion["created_at"] <= selected_time:
                reasons.append("lineage:deleted")
    return list(dict.fromkeys(reasons))


def _fusion_score(candidate: _Candidate, weights: dict[str, float]) -> float:
    return sum(weights[channel] / (_RRF_K + rank) for channel, rank in candidate.ranks.items())


def _duty_coverage(asset: KnowledgeAsset, duties: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(duty for duty in duties if asset.kind in _DUTY_KINDS[duty])


def _select_diverse(
    values: list[tuple[_Candidate, KnowledgeAsset]],
    *,
    duties: tuple[str, ...],
    limit: int,
    source_diversity_weight: float = 1.0,
    type_priority: dict[str, float] | None = None,
) -> list[tuple[_Candidate, KnowledgeAsset, tuple[str, ...]]]:
    remaining = list(values)
    selected: list[tuple[_Candidate, KnowledgeAsset, tuple[str, ...]]] = []
    covered_duties: set[str] = set()
    source_counts: defaultdict[str, int] = defaultdict(int)
    while remaining and len(selected) < limit:
        ranked: list[
            tuple[float, float, str, _Candidate, KnowledgeAsset, tuple[str, ...]]
        ] = []
        for candidate, asset in remaining:
            coverage = _duty_coverage(asset, duties)
            new_coverage = sum(duty not in covered_duties for duty in coverage)
            repeated_sources = sum(source_counts[source_id] for source_id in candidate.source_ids)
            diversity_bonus = (
                0.002 * source_diversity_weight
                if candidate.source_ids and not repeated_sources
                else 0.0
            )
            duty_bonus = min(0.004, new_coverage * 0.0015)
            repetition_penalty = min(
                0.004 * source_diversity_weight,
                repeated_sources * 0.001 * source_diversity_weight,
            )
            type_bonus = (
                (type_priority.get(asset.kind, 1.0) - 1.0) * 0.001
                if type_priority is not None
                else 0.0
            )
            objective = (
                candidate.selection_score
                + duty_bonus
                + diversity_bonus
                + type_bonus
                - repetition_penalty
            )
            ranked.append(
                (
                    objective,
                    candidate.selection_score,
                    candidate.asset_id,
                    candidate,
                    asset,
                    coverage,
                )
            )
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        _, _, _, candidate, asset, coverage = ranked[0]
        selected.append((candidate, asset, coverage))
        covered_duties.update(coverage)
        for source_id in candidate.source_ids:
            source_counts[source_id] += 1
        remaining = [item for item in remaining if item[0].asset_id != candidate.asset_id]
    return selected


def retrieve(
    vault: KnowledgeVault,
    query: str,
    *,
    mode: RetrievalMode = "auto",
    limit: int = 5,
    max_chars: int = 5_000,
    kinds: tuple[str, ...] = (),
    memory_tiers: tuple[str, ...] = (),
    include_restricted: bool = False,
    include_inactive: bool = False,
    as_of: str | None = None,
    discovery_index_path: str | Path | None = None,
    model_root: str | Path | None = None,
    threads: int | None = None,
    explain: bool = True,
    ranking_profile: dict[str, Any] | None = None,
    reranker_manifest: str | Path | None = None,
) -> dict[str, Any]:
    if isinstance(max_chars, bool) or not 1 <= max_chars <= 20_000:
        raise ValueError("retrieval max_chars must be between 1 and 20000")
    if not vault.verify_integrity()["valid"]:
        raise RuntimeError("knowledge vault integrity is invalid; retrieval stopped")
    selected_index = Path(discovery_index_path) if discovery_index_path is not None else None
    if ranking_profile is None:
        from .retrieval_profiles import load_active_retrieval_profile

        ranking_profile = load_active_retrieval_profile(vault)
    reranker_loaded: dict[str, Any] | None = None
    if reranker_manifest is not None:
        from .local_reranker import load_local_reranker_manifest

        reranker_loaded = load_local_reranker_manifest(reranker_manifest)
    plan = build_query_plan(
        query,
        mode=mode,
        limit=limit,
        kinds=kinds,
        memory_tiers=memory_tiers,
        as_of=as_of,
        dense_available=selected_index is not None,
        ranking_profile=ranking_profile,
        reranker_profile=(
            reranker_loaded["profile_id"] if reranker_loaded is not None else RERANKER_PROFILE
        ),
        restricted_allowed=include_restricted,
        active_reviewed_only=not include_inactive,
    )

    candidates: dict[str, _Candidate] = {}
    _exact_candidates(vault, plan, candidates)
    _lexical_candidates(vault, plan, candidates)
    _tree_candidates(vault, plan, candidates)
    _dense_candidates(
        vault,
        plan,
        candidates,
        discovery_index_path=selected_index,
        model_root=Path(model_root) if model_root is not None else None,
        threads=threads,
    )
    _graph_candidates(vault, plan, candidates)
    _temporal_candidates(vault, plan, candidates)
    _feedback_candidates(vault, plan, candidates)

    weights = plan["channel_weights"]
    channel_candidates: dict[str, list[dict[str, Any]]] = {}
    for channel in _CHANNEL_ORDER:
        if channel not in plan["channels"]:
            continue
        ranked = sorted(
            (
                candidate
                for candidate in candidates.values()
                if channel in candidate.ranks
            ),
            key=lambda candidate: (candidate.ranks[channel], candidate.asset_id),
        )
        channel_candidates[channel] = [
            {
                "rank": candidate.ranks[channel],
                "asset_id": candidate.asset_id,
                "reason": candidate.reasons[channel],
            }
            for candidate in ranked[:_MAX_TRACE_CANDIDATES]
        ]
    for candidate in candidates.values():
        candidate.fusion_score = _fusion_score(candidate, weights)
        candidate.selection_score = candidate.fusion_score

    fused = sorted(candidates.values(), key=lambda item: (-item.fusion_score, item.asset_id))
    admitted_by_key: dict[str, tuple[_Candidate, KnowledgeAsset]] = {}
    excluded: list[dict[str, Any]] = []
    source_cache: dict[str, dict[str, Any]] = {}
    for candidate in fused:
        try:
            asset = vault.get_asset(candidate.asset_id, include_inactive=True)
        except (KeyError, ValueError):
            excluded.append(
                {"asset_id": candidate.asset_id, "reasons": ["asset_unavailable"]}
            )
            continue
        knowledge_key, source_ids = _asset_identity(vault, asset)
        candidate.knowledge_key = knowledge_key
        candidate.source_ids = source_ids
        exclusion_reasons = _admit_candidate(
            vault,
            asset,
            candidate,
            plan,
            include_restricted=include_restricted,
            include_inactive=include_inactive,
            source_cache=source_cache,
        )
        if exclusion_reasons:
            excluded.append(
                {
                    "asset_id": asset.asset_id,
                    "knowledge_key": knowledge_key,
                    "reasons": exclusion_reasons,
                }
            )
            continue
        previous = admitted_by_key.get(knowledge_key)
        if previous is not None:
            previous_candidate, _ = previous
            if candidate.selection_score <= previous_candidate.selection_score:
                excluded.append(
                    {
                        "asset_id": asset.asset_id,
                        "knowledge_key": knowledge_key,
                        "reasons": ["knowledge_key_deduplicated"],
                    }
                )
                continue
            excluded.append(
                {
                    "asset_id": previous_candidate.asset_id,
                    "knowledge_key": knowledge_key,
                    "reasons": ["knowledge_key_deduplicated"],
                }
            )
        admitted_by_key[knowledge_key] = (candidate, asset)

    reranker_result: dict[str, Any] | None = None
    if reranker_loaded is not None and admitted_by_key:
        from .local_reranker import run_local_reranker

        manifest = reranker_loaded["manifest"]
        reranker_candidates = sorted(
            admitted_by_key.values(),
            key=lambda item: (-item[0].fusion_score, item[0].asset_id),
        )[: manifest["max_candidates"]]
        reranker_result = run_local_reranker(
            manifest_path=reranker_manifest,
            loaded_manifest=reranker_loaded,
            query=plan["normalized_query"],
            candidates=[
                {
                    "asset_id": asset.asset_id,
                    "title": asset.title,
                    "statement": asset.statement[:8_000],
                }
                for _candidate, asset in reranker_candidates
            ],
        )
        by_asset_id = {
            candidate.asset_id: candidate for candidate, _asset in reranker_candidates
        }
        for rank, asset_id in enumerate(reranker_result["ordered_asset_ids"], start=1):
            candidate = by_asset_id[asset_id]
            candidate.reranker_rank = rank
            candidate.selection_score += 8.0 / (_RRF_K + rank)

    selected = _select_diverse(
        list(admitted_by_key.values()),
        duties=tuple(plan["duties"]),
        limit=limit,
        source_diversity_weight=(
            float(ranking_profile["source_diversity_weight"])
            if ranking_profile is not None
            else 1.0
        ),
        type_priority=(
            ranking_profile["type_priority"] if ranking_profile is not None else None
        ),
    )
    results: list[dict[str, Any]] = []
    total_chars = 0
    covered_duties: set[str] = set()
    covered_sources: set[str] = set()
    for candidate, asset, coverage in selected:
        remaining = max_chars - total_chars
        if remaining <= 0:
            excluded.append(
                {"asset_id": asset.asset_id, "reasons": ["excerpt_budget"]}
            )
            continue
        content = excerpt(
            asset.statement,
            plan["normalized_query"],
            max_chars=min(700, remaining),
            cover_query_tail=True,
        )
        if not content:
            excluded.append(
                {"asset_id": asset.asset_id, "reasons": ["empty_excerpt"]}
            )
            continue
        primary_channel = min(
            candidate.ranks,
            key=lambda channel: (-weights[channel], candidate.ranks[channel], channel),
        )
        results.append(
            {
                "rank": len(results) + 1,
                "asset_id": asset.asset_id,
                "uri": asset.uri,
                "knowledge_key": candidate.knowledge_key,
                "kind": asset.kind,
                "memory_tier": asset.memory_tier,
                "title": asset.title,
                "excerpt": content,
                "semantic_key": asset.semantic_key,
                "verification": asset.verification,
                "trust": asset.trust,
                "sensitivity": asset.sensitivity,
                "directive_mode": asset.directive_mode,
                "source_refs": [reference.to_dict() for reference in asset.source_refs],
                "tags": list(asset.tags),
                "content_sha256": asset.content_sha256,
                "hit_reason": f"retrieval_fabric:{primary_channel}",
                "channels": [
                    {
                        "channel": channel,
                        "rank": candidate.ranks[channel],
                        "reason": candidate.reasons[channel],
                    }
                    for channel in _CHANNEL_ORDER
                    if channel in candidate.ranks
                ],
                "duty_coverage": list(coverage),
                "legal_authority": False,
            }
        )
        total_chars += len(content)
        covered_duties.update(coverage)
        covered_sources.update(candidate.source_ids)

    gaps: list[str] = []
    if not results:
        gaps.append("no active reviewed knowledge asset passed retrieval admission")
    uncovered = [
        duty
        for duty in plan["duties"]
        if duty not in covered_duties and duty != "missing_evidence"
    ]
    if uncovered:
        gaps.append(f"uncovered knowledge duties: {', '.join(uncovered)}")
    if "dense" not in plan["channels"] and mode == "semantic":
        gaps.append("semantic mode requires an explicitly prepared verified Discovery index")
    if selected_index is None and mode in {"hybrid", "global"}:
        gaps.append("dense discovery was unavailable; remaining configured channels were used")
    if any(
        any(reason.startswith("source_integrity:") for reason in item["reasons"])
        for item in excluded
    ):
        gaps.append("one or more candidates failed stored-source integrity verification")

    fusion_ranks = [
        {
            "rank": rank,
            "asset_id": candidate.asset_id,
            "channel_ranks": {
                channel: candidate.ranks[channel]
                for channel in _CHANNEL_ORDER
                if channel in candidate.ranks
            },
            "fusion_value": round(candidate.fusion_score, 9),
            "fusion_value_is_probability": False,
        }
        for rank, candidate in enumerate(fused[:_MAX_TRACE_CANDIDATES], start=1)
    ]
    reranker_ranks = (
        [
            {
                "rank": rank,
                "asset_id": asset_id,
                "profile_id": reranker_result["profile_id"],
                "manifest_sha256": reranker_result["manifest_sha256"],
                "model_identity": reranker_result["model_identity"],
                "model_revision": reranker_result["model_revision"],
                "numeric_confidence_exposed": False,
                "authority_effect": "ranking-only",
            }
            for rank, asset_id in enumerate(
                reranker_result["ordered_asset_ids"], start=1
            )
        ]
        if reranker_result is not None
        else []
    )
    trace_body = {
        "schema_version": RETRIEVAL_TRACE_SCHEMA,
        "vault_id": vault.vault_id,
        "vault_revision": vault.revision,
        "audit_head": vault.audit_head,
        "query_plan": plan,
        "channel_candidates": channel_candidates,
        "fusion_ranks": fusion_ranks,
        "reranker_ranks": reranker_ranks,
        "excluded_candidates": excluded[:_MAX_TRACE_CANDIDATES],
        "selected_asset_ids": [item["asset_id"] for item in results],
        "source_coverage": {
            "selected_source_count": len(covered_sources),
            "selected_source_ids": sorted(covered_sources),
            "all_source_bound_items_have_refs": all(item["source_refs"] for item in results),
        },
        "duty_coverage": {
            duty: ("covered" if duty in covered_duties else "gap")
            for duty in plan["duties"]
        },
        "budget": {
            "max_items": limit,
            "max_excerpt_chars": max_chars,
            "selected_items": len(results),
            "selected_excerpt_chars": total_chars,
        },
        "gaps": gaps,
        "numeric_confidence_exposed": False,
        "authority_changed_by_ranking": False,
    }
    trace = {
        **trace_body,
        "trace_id": stable_id(
            "retrievaltrace",
            sha256_bytes(canonical_json(trace_body).encode("utf-8")),
        ),
    }
    response: dict[str, Any] = {
        "schema_version": RETRIEVAL_RESULT_SCHEMA,
        "vault_id": vault.vault_id,
        "vault_revision": vault.revision,
        "query": query,
        "query_plan_id": plan["query_plan_id"],
        "mode": mode,
        "results": results,
        "ranking": {
            "method": (
                f"{FUSION_PROFILE}+local-reranker-rank/1"
                if reranker_result is not None
                else FUSION_PROFILE
            ),
            "candidate_only_channels": True,
            "central_admission_applied": True,
            "numeric_confidence_exposed": False,
            "authority_changed": False,
        },
        "gaps": gaps,
        "total_excerpt_chars": total_chars,
    }
    if explain:
        response["trace"] = trace
    return response


def compare_retrieval(
    vault: KnowledgeVault,
    query: str,
    *,
    modes: tuple[RetrievalMode, ...] = ("lexical", "hybrid", "global"),
    limit: int = 5,
    max_chars: int = 5_000,
    discovery_index_path: str | Path | None = None,
    as_of: str | None = None,
    reranker_manifest: str | Path | None = None,
) -> dict[str, Any]:
    if not modes or len(modes) > 5 or len(set(modes)) != len(modes):
        raise ValueError("retrieval comparison requires one to five distinct modes")
    runs = [
        retrieve(
            vault,
            query,
            mode=mode,
            limit=limit,
            max_chars=max_chars,
            discovery_index_path=discovery_index_path,
            as_of=as_of,
            reranker_manifest=reranker_manifest,
            explain=True,
        )
        for mode in modes
    ]
    inventories = {
        run["mode"]: [item["asset_id"] for item in run["results"]] for run in runs
    }
    return {
        "schema_version": "deeplaw.knowledge-retrieval-comparison/v1",
        "query": query,
        "vault_id": vault.vault_id,
        "vault_revision": vault.revision,
        "modes": list(modes),
        "inventories": inventories,
        "overlap": {
            f"{left}:{right}": sorted(set(inventories[left]) & set(inventories[right]))
            for index, left in enumerate(modes)
            for right in modes[index + 1 :]
        },
        "runs": runs,
        "comparison_is_performance_claim": False,
    }


def recall(
    vault: KnowledgeVault,
    task: str,
    *,
    confirm_no_case_data: bool,
    goal: str | None = None,
    mode: RetrievalMode = "auto",
    max_items: int = 8,
    max_chars: int = 6_000,
    max_tokens: int = 4_096,
    kinds: tuple[str, ...] = (),
    memory_tiers: tuple[str, ...] = (),
    include_restricted: bool = False,
    as_of: str | None = None,
    discovery_index_path: str | Path | None = None,
    model_root: str | Path | None = None,
    threads: int | None = None,
    reranker_manifest: str | Path | None = None,
) -> dict[str, Any]:
    if not confirm_no_case_data:
        raise ValueError(
            "knowledge recall requires confirmation that task and goal contain "
            "no Analytix case material"
        )
    normalized_task = task.strip()
    normalized_goal = goal.strip() if goal else None
    context_query = f"{normalized_task} {normalized_goal or ''}".strip()
    retrieval = retrieve(
        vault,
        context_query,
        mode=mode,
        limit=max_items,
        max_chars=max_chars,
        kinds=kinds,
        memory_tiers=memory_tiers,
        include_restricted=include_restricted,
        as_of=as_of,
        discovery_index_path=discovery_index_path,
        model_root=model_root,
        threads=threads,
        reranker_manifest=reranker_manifest,
        explain=True,
    )
    from .context_compiler import compile_context

    capsule = compile_context(
        vault,
        task=normalized_task,
        goal=normalized_goal,
        confirm_no_case_data=True,
        max_items=max_items,
        max_chars=max_chars,
        max_tokens=max_tokens,
        kinds=kinds,
        memory_tiers=memory_tiers,
        include_restricted=include_restricted,
        retrieval_result=retrieval,
    )
    return {
        "schema_version": "deeplaw.knowledge-recall/v1",
        "vault_id": vault.vault_id,
        "vault_revision": vault.revision,
        "query_plan": retrieval["trace"]["query_plan"],
        "retrieval": retrieval,
        "capsule": capsule,
        "authority_boundary": {
            "ranking_changes_authority": False,
            "automatic_memory_write": False,
            "human_review_required_for_activation": True,
            "legal_authority": False,
        },
    }


def verify_retrieval_trace(
    trace: dict[str, Any],
    *,
    vault: KnowledgeVault | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(trace, dict)
        or trace.get("schema_version") != RETRIEVAL_TRACE_SCHEMA
        or not isinstance(trace.get("trace_id"), str)
    ):
        return {"valid": False, "reason": "trace_contract_invalid"}
    body = {key: value for key, value in trace.items() if key != "trace_id"}
    expected = stable_id(
        "retrievaltrace",
        sha256_bytes(canonical_json(body).encode("utf-8")),
    )
    digest_valid = trace["trace_id"] == expected
    query_plan = trace.get("query_plan")
    query_plan_valid = False
    if isinstance(query_plan, dict) and isinstance(query_plan.get("query_plan_id"), str):
        query_plan_body = {
            key: value for key, value in query_plan.items() if key != "query_plan_id"
        }
        query_plan_valid = query_plan["query_plan_id"] == stable_id(
            "queryplan",
            sha256_bytes(canonical_json(query_plan_body).encode("utf-8")),
        )
    vault_binding_valid = True
    stale = False
    if vault is not None:
        vault_binding_valid = (
            trace.get("vault_id") == vault.vault_id
            and isinstance(trace.get("vault_revision"), int)
            and 0 <= trace["vault_revision"] <= vault.revision
            and isinstance(trace.get("audit_head"), str)
            and vault.audit_hash_at(trace["vault_revision"]) == trace["audit_head"]
        )
        stale = trace.get("vault_revision") != vault.revision
    return {
        "valid": bool(digest_valid and query_plan_valid and vault_binding_valid),
        "trace_id": trace.get("trace_id"),
        "digest_valid": digest_valid,
        "query_plan_valid": query_plan_valid,
        "vault_binding_valid": vault_binding_valid,
        "stale": stale,
    }


def estimate_tokens(text: str) -> int:
    """Conservative local estimate; callers must label it as estimated."""
    if not text:
        return 0
    cjk = sum("\u3400" <= character <= "\u9fff" for character in text)
    non_cjk = len(text) - cjk
    return cjk + (non_cjk + 3) // 4


def canonical_as_of(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) == 10:
        value = f"{value}T23:59:59Z"
    return canonical_timestamp(value, field="retrieval as_of")


def timestamp_epoch(value: str) -> float:
    canonical = canonical_timestamp(value, field="timestamp")
    return datetime.fromisoformat(canonical.replace("Z", "+00:00")).astimezone(UTC).timestamp()
