"""Deterministic Semantic Compiler v3 duty applicability.

This module is intentionally model-free.  It derives a bounded, canonical set of
runtime facts from the already registered Source Revision/IR, observations and
governed Knowledge state.  A host may propose a duty report, but it cannot change
the applicability decision or its evidence-bound basis.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from ..knowledge_autonomy import (
    SENSITIVITY_ORDER,
    AutonomousKnowledgeStore,
    strict_json_loads,
)
from ..util import canonical_json, sha256_bytes
from .profiles import (
    SEMANTIC_APPLICABILITY_POLICY,
    SEMANTIC_APPLICABILITY_POLICY_SHA256,
    SEMANTIC_DUTIES,
)

MAX_RUNTIME_NODES = 10_000
MAX_RUNTIME_FRAGMENTS = 10_000
MAX_RUNTIME_RELATIONS = 10_000
MAX_RUNTIME_REFS = 256
MAX_RUNTIME_SOURCE_TEXT_CHARS = 4 * 1024 * 1024

_DATE_SIGNAL = re.compile(r"\b(?:19|20)\d{2}[-/.]\d{1,2}(?:[-/.]\d{1,2})?\b")
_QUESTION_SIGNAL = re.compile(r"(?:\?|\bwho\b|\bwhat\b|\bwhen\b|\bwhere\b|\bwhy\b|\bhow\b)", re.I)
_CODE_SIGNAL = re.compile(
    r"(?:```|\b(?:def|class|function|return|SELECT|INSERT|for|while)\b)", re.I
)
_TABLE_SIGNAL = re.compile(r"(?:\|[^\n|]+\|[^\n|]+\||\btable\b|\bcolumn\b|\brow\b)", re.I)


def _sha(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _bounded_strings(
    values: list[Any], *, maximum: int = MAX_RUNTIME_REFS
) -> tuple[list[str], bool]:
    result: list[str] = []
    truncated = False
    for value in values:
        if not isinstance(value, str) or not value or "\x00" in value:
            continue
        if len(result) >= maximum:
            truncated = True
            break
        result.append(value)
    return result, truncated


def _source_facts(
    store: AutonomousKnowledgeStore, run: Any
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Read the registered source and Source IR using a bounded query set."""
    source_row = store.connection.execute(
        """
        SELECT revisions.source_revision_id, revisions.source_key,
               revisions.content_sha256, revisions.media_identity,
               revisions.byte_size, sources.kind, sources.title,
               sources.media_type, sources.byte_size AS legacy_byte_size,
               sources.sensitivity, lifecycle.status AS lifecycle_status,
               bindings.observed_at
        FROM source_revisions_v2 AS revisions
        LEFT JOIN source_revision_bindings_v2 AS bindings
          ON bindings.source_revision_id = revisions.source_revision_id
        LEFT JOIN sources ON sources.source_id = bindings.legacy_source_id
        LEFT JOIN source_lifecycle AS lifecycle
          ON lifecycle.source_id = bindings.legacy_source_id
        WHERE revisions.source_revision_id = ?
        ORDER BY bindings.observed_at DESC
        LIMIT 1
        """,
        (run["source_revision_id"],),
    ).fetchone()
    source = {
        "source_revision_id": run["source_revision_id"],
        "present": source_row is not None,
        "admitted": False,
        "nonempty": False,
        "media_type": None,
        "kind": None,
        "byte_size": 0,
        "content_sha256": None,
        "lifecycle": None,
        "scope": None,
        "sensitivity": None,
    }
    if source_row is not None:
        source.update(
            {
                "admitted": True,
                "media_type": source_row["media_type"] or source_row["media_identity"],
                "kind": source_row["kind"],
                "byte_size": int(source_row["byte_size"] or source_row["legacy_byte_size"] or 0),
                "content_sha256": source_row["content_sha256"],
                "lifecycle": source_row["lifecycle_status"] or "registered",
                "sensitivity": source_row["sensitivity"],
            }
        )
        binding = store._source_reference_binding({"source_revision_id": run["source_revision_id"]})
        if binding is not None:
            source["admitted"] = bool(binding["active"])
            source["scope"] = binding["scope"]
            source["sensitivity"] = binding["sensitivity"]
        source["nonempty"] = source["byte_size"] > 0

    nodes_cursor = store.connection.execute(
        """
        SELECT node_id, node_type, title, text, locator, content_sha256,
               quality_flags_json, instruction_risk
        FROM source_ir_nodes_v2
        WHERE compilation_id = ?
        ORDER BY ordinal, node_id
        LIMIT ?
        """,
        (run["source_ir_compilation_id"], MAX_RUNTIME_NODES + 1),
    )
    nodes: list[dict[str, Any]] = []
    node_text_characters = 0
    nodes_truncated = False
    for row in nodes_cursor:
        row_characters = len(row["title"] or "") + len(row["text"] or "")
        if (
            len(nodes) >= MAX_RUNTIME_NODES
            or node_text_characters + row_characters > MAX_RUNTIME_SOURCE_TEXT_CHARS
        ):
            nodes_truncated = True
            break
        nodes.append(dict(row))
        node_text_characters += row_characters
    fragment_rows = store.connection.execute(
        """
        SELECT fragments.fragment_revision_id, fragments.ordinal,
               fragments.locator, fragments.text_sha256, fragments.instruction_risk
        FROM fragments_v2 AS fragments
        WHERE fragments.compilation_id = ?
        ORDER BY fragments.ordinal, fragments.fragment_revision_id
        LIMIT ?
        """,
        (run["source_ir_compilation_id"], MAX_RUNTIME_FRAGMENTS + 1),
    ).fetchall()
    fragments_truncated = len(fragment_rows) > MAX_RUNTIME_FRAGMENTS
    fragments = [dict(row) for row in fragment_rows[:MAX_RUNTIME_FRAGMENTS]]
    return source, nodes, fragments, nodes_truncated or fragments_truncated


def _grant_allows(grant: Any, *, scope: Any, sensitivity: Any) -> bool:
    """Apply the run grant boundary before exposing any existing identity."""
    if grant is None or not isinstance(scope, str) or not isinstance(sensitivity, str):
        return False
    if scope != grant["allowed_scope"] or sensitivity not in SENSITIVITY_ORDER:
        return False
    try:
        return SENSITIVITY_ORDER.index(sensitivity) <= SENSITIVITY_ORDER.index(
            grant["max_sensitivity"]
        )
    except (ValueError, TypeError):
        return False


def _interval_allows(row: Any, *, reference_time: str) -> bool:
    try:
        return bool(
            (row["valid_from"] is None or row["valid_from"] <= reference_time)
            and (row["valid_to"] is None or row["valid_to"] > reference_time)
            and (row["expires_at"] is None or row["expires_at"] > reference_time)
        )
    except (KeyError, TypeError):
        return False


def run_reference_time(store: AutonomousKnowledgeStore, run: Any) -> str:
    """Resolve the immutable run input audit event to a canonical time."""
    row = store.connection.execute(
        "SELECT recorded_at FROM autonomous_events_v3 WHERE event_hash = ?",
        (run["input_audit_head"],),
    ).fetchone()
    if row is None or not isinstance(row["recorded_at"], str):
        raise RuntimeError("semantic run input audit head has no canonical reference time")
    return row["recorded_at"]


def admitted_knowledge_candidates(
    store: AutonomousKnowledgeStore,
    *,
    grant: Any,
    reference_time: str,
    limit: int = MAX_RUNTIME_NODES,
) -> tuple[list[dict[str, Any]], bool]:
    """Return only current Knowledge Revisions admitted to a run grant.

    The query is deliberately followed by deterministic provenance checks. SQL
    scope/sensitivity/lifecycle predicates are necessary but cannot establish
    that source bindings and dependency freshness are currently admissible.
    """
    try:
        admitted_sensitivities = SENSITIVITY_ORDER[
            : SENSITIVITY_ORDER.index(grant["max_sensitivity"]) + 1
        ]
    except (KeyError, TypeError, ValueError):
        return [], False
    sensitivity_placeholders = ",".join("?" for _ in admitted_sensitivities)
    rows = store.connection.execute(
        f"""
        SELECT knowledge_objects_v3.workspace_path AS current_workspace_path,
               knowledge_objects_v3.knowledge_id,
               knowledge_objects_v3.kind AS object_kind,
               knowledge_objects_v3.semantic_key AS object_semantic_key,
               knowledge_objects_v3.current_revision_id,
               knowledge_revisions_v3.*
        FROM knowledge_objects_v3
        JOIN knowledge_revisions_v3
          ON knowledge_revisions_v3.revision_id = knowledge_objects_v3.current_revision_id
        WHERE knowledge_revisions_v3.lifecycle = 'active'
          AND knowledge_revisions_v3.scope = ?
          AND knowledge_revisions_v3.sensitivity IN ({sensitivity_placeholders})
        ORDER BY knowledge_objects_v3.knowledge_id
        LIMIT ?
        """,
        (grant["allowed_scope"], *admitted_sensitivities, limit + 1),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if not _grant_allows(
            grant,
            scope=row["scope"],
            sensitivity=row["sensitivity"],
        ) or not _interval_allows(row, reference_time=reference_time):
            continue
        try:
            revision = store._revision_row(row, include_body=False)
            admitted = store.revision_provenance_admitted(revision)
        except (KeyError, TypeError, ValueError):
            admitted = False
        if not admitted:
            continue
        candidates.append(
            {
                "knowledge_id": row["knowledge_id"],
                "kind": row["kind"],
                "semantic_key": row["semantic_key"],
                "current_revision_id": row["current_revision_id"],
            }
        )
    return candidates, len(rows) > limit


def _observation_facts(observations: list[dict[str, Any]]) -> dict[str, Any]:
    kinds = Counter(item.get("kind") for item in observations if isinstance(item.get("kind"), str))
    refs: list[str] = []
    for item in observations:
        refs.append(item.get("observation_id"))
    stable_refs, refs_truncated = _bounded_strings(refs)
    return {
        "count": len(observations),
        "kinds": {key: kinds[key] for key in sorted(kinds)},
        "observation_ids": sorted(stable_refs),
        "source_ref_count": sum(
            len(item.get("source_refs", []))
            for item in observations
            if isinstance(item.get("source_refs"), list)
        ),
        "truncated": refs_truncated,
    }


def _existing_facts(
    store: AutonomousKnowledgeStore,
    *,
    grant: Any,
    reference_time: str,
) -> dict[str, Any]:
    candidates, candidates_truncated = admitted_knowledge_candidates(
        store,
        grant=grant,
        reference_time=reference_time,
    )
    by_kind = Counter(row["kind"] for row in candidates)
    ids, ids_truncated = _bounded_strings([row["knowledge_id"] for row in candidates])
    visible_ids = set(ids)
    relation_rows = store.connection.execute(
        """
        SELECT relations.current_revision_id,
               revisions.*
        FROM knowledge_relations_v3 AS relations
        JOIN knowledge_relation_revisions_v3 AS revisions
          ON revisions.relation_revision_id = relations.current_revision_id
        WHERE revisions.lifecycle = 'active'
        ORDER BY relations.relation_key
        LIMIT ?
        """,
        (MAX_RUNTIME_RELATIONS + 1,),
    ).fetchall()
    relation_count = 0
    for row in relation_rows[:MAX_RUNTIME_RELATIONS]:
        if (
            row["subject_knowledge_id"] not in visible_ids
            or row["object_knowledge_id"] not in visible_ids
            or not _grant_allows(
                grant,
                scope=row["scope"],
                sensitivity=row["sensitivity"],
            )
            or not _interval_allows(row, reference_time=reference_time)
        ):
            continue
        try:
            relation = {
                **dict(row),
                "evidence_refs": strict_json_loads(row["evidence_refs_json"]),
                "source_free": bool(row["source_free"]),
            }
            if store.relation_provenance_admitted(relation):
                relation_count += 1
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "count": len(candidates),
        "kinds": {key: by_kind[key] for key in sorted(by_kind)},
        "knowledge_ids": sorted(ids),
        "relation_count": relation_count,
        "truncated": (
            candidates_truncated
            or len(relation_rows) > MAX_RUNTIME_RELATIONS
            or ids_truncated
        ),
    }


def collect_runtime_facts(
    store: AutonomousKnowledgeStore,
    run: Any,
    *,
    observations: list[dict[str, Any]],
    previous_outputs: list[dict[str, Any]],
    affected_syntheses: list[dict[str, Any]],
    reference_time: str | None = None,
) -> dict[str, Any]:
    """Collect canonical facts.  Missing/inconsistent/truncated data is explicit."""
    reference_time = reference_time or run_reference_time(store, run)
    source, nodes, fragments, source_truncated = _source_facts(store, run)
    if source["present"] and not fragments:
        # A non-empty byte blob without its registered fragment inventory is an
        # inconsistent Source Revision, not proof that content duties apply.
        source["nonempty"] = False
    node_types = Counter(
        node.get("node_type") for node in nodes if isinstance(node.get("node_type"), str)
    )
    texts = "\n".join(f"{node.get('title') or ''}\n{node.get('text') or ''}" for node in nodes)
    folded_types = {key.casefold() for key in node_types}
    signals = {
        "code": bool(_CODE_SIGNAL.search(texts))
        or bool(folded_types & {"code", "listing", "program", "source-code"}),
        "table": bool(_TABLE_SIGNAL.search(texts))
        or bool(folded_types & {"table", "tabular", "row", "column"}),
        "list": bool(folded_types & {"list", "item", "enumeration"})
        or bool(re.search(r"(?m)^\s*(?:[-*+] |\d+[.)] )", texts)),
        "timeline": bool(_DATE_SIGNAL.search(texts))
        or bool(folded_types & {"timeline", "date", "chronology", "event"}),
        "question": bool(_QUESTION_SIGNAL.search(texts))
        or bool(folded_types & {"question", "faq", "unresolved-question"}),
        "procedure": bool(folded_types & {"procedure", "step", "code", "listing"}),
    }
    media_type = str(source.get("media_type") or "").casefold()
    if (
        media_type.startswith("text/x-")
        or media_type
        in {
            "application/javascript",
            "application/x-sh",
            "application/x-python",
            "text/javascript",
        }
        or source.get("kind") in {"code", "source_code"}
    ):
        signals["procedure"] = True
    grant = store.connection.execute(
        """
        SELECT writer_id, allowed_scope, max_sensitivity
        FROM knowledge_sink_grants_v3 WHERE grant_id = ?
        """,
        (run["grant_id"],),
    ).fetchone()
    existing = _existing_facts(store, grant=grant, reference_time=reference_time)
    observation_facts = _observation_facts(observations)
    facts = {
        "schema_version": "deeplaw.semantic-runtime-facts/v1",
        "reference_time": reference_time,
        "source": source,
        "source_ir": {
            "compilation_id": run["source_ir_compilation_id"],
            "node_count": len(nodes),
            "node_types": {key: node_types[key] for key in sorted(node_types)},
            "signals": signals,
            "node_ids": sorted(
                item for item in (node.get("node_id") for node in nodes) if isinstance(item, str)
            )[:MAX_RUNTIME_REFS],
        },
        "fragments": {
            "count": len(fragments),
            "fragment_ids": sorted(
                item
                for item in (fragment.get("fragment_revision_id") for fragment in fragments)
                if isinstance(item, str)
            )[:MAX_RUNTIME_REFS],
            "locators": sorted(
                item
                for item in (fragment.get("locator") for fragment in fragments)
                if isinstance(item, str)
            )[:MAX_RUNTIME_REFS],
        },
        "observations": observation_facts,
        "existing": existing,
        "previous_outputs": {
            "count": len(previous_outputs),
            "kinds": dict(
                sorted(Counter(item.get("output_kind") for item in previous_outputs).items())
            ),
        },
        "affected_syntheses": {
            "count": len(affected_syntheses),
            "ids": sorted(
                item.get("synthesis_revision_id")
                for item in affected_syntheses
                if isinstance(item.get("synthesis_revision_id"), str)
            )[:MAX_RUNTIME_REFS],
        },
        "grant": {
            "writer_id": grant["writer_id"] if grant is not None else None,
            "scope": grant["allowed_scope"] if grant is not None else None,
            "max_sensitivity": grant["max_sensitivity"] if grant is not None else None,
        },
        "truncated": bool(
            source_truncated or observation_facts["truncated"] or existing["truncated"]
        ),
    }
    facts["facts_sha256"] = _sha(
        {key: value for key, value in facts.items() if key != "facts_sha256"}
    )
    return facts


def _basis(rule_id: str, facts: dict[str, Any], refs: list[str], *, reason: str) -> dict[str, Any]:
    closed_facts = {
        "source_present": facts["source"]["present"],
        "source_admitted": facts["source"]["admitted"],
        "source_nonempty": facts["source"]["nonempty"],
        "media_type": facts["source"]["media_type"],
        "byte_size": facts["source"]["byte_size"],
        "lifecycle": facts["source"]["lifecycle"],
        "node_types": facts["source_ir"]["node_types"],
        "signals": facts["source_ir"]["signals"],
        "observation_kinds": facts["observations"]["kinds"],
        "observation_count": facts["observations"]["count"],
        "existing_kinds": facts["existing"]["kinds"],
        "existing_count": facts["existing"]["count"],
        "relation_count": facts["existing"]["relation_count"],
        "previous_output_count": facts["previous_outputs"]["count"],
        "affected_synthesis_count": facts["affected_syntheses"]["count"],
        "truncated": facts["truncated"],
    }
    stable_refs, _ = _bounded_strings(sorted(set(refs)))
    return {
        "rule_id": rule_id,
        "facts": closed_facts,
        "stable_refs": stable_refs,
        "facts_sha256": _sha(closed_facts),
        "reason": reason,
    }


def derive_applicability(facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the policy-controlled applicability and deterministic basis per duty."""
    source = facts["source"]
    signals = facts["source_ir"]["signals"]
    kinds = facts["observations"]["kinds"]
    existing = facts["existing"]["kinds"]
    refs = facts["observations"]["observation_ids"]
    source_refs = [source["source_revision_id"], *facts["fragments"]["fragment_ids"]]
    out: dict[str, dict[str, Any]] = {}
    integrity_unknown = bool(facts.get("truncated")) or not source["present"]

    def add(duty: str, value: str, rule: str, selected: list[str], reason: str) -> None:
        out[duty] = {
            "applicability": value,
            "deterministic_basis": _basis(rule, facts, selected, reason=reason),
        }

    if integrity_unknown:
        add(
            "source_summary",
            "unknown",
            "admitted-nonempty-source-v1",
            source_refs,
            "Registered source facts are missing or bounded facts are incomplete.",
        )
        add(
            "key_claims",
            "unknown",
            "admitted-nonempty-source-v1",
            source_refs,
            "Registered source facts are missing or bounded facts are incomplete.",
        )
    else:
        value = "applicable" if source["admitted"] and source["nonempty"] else "unknown"
        reason = (
            "The admitted Source Revision has non-empty registered bytes."
            if value == "applicable"
            else "The Source Revision is not proven admitted and non-empty by registered facts."
        )
        add("source_summary", value, "admitted-nonempty-source-v1", source_refs, reason)
        add("key_claims", value, "admitted-nonempty-source-v1", source_refs, reason)

    def signal_duty(
        duty: str, observation_kinds: set[str], existing_kinds: set[str], signal: str | None = None
    ) -> None:
        matched = sorted(
            item
            for item in refs
            if facts["observations"]["kinds"].get(
                next(
                    (
                        obs_kind
                        for obs_kind in observation_kinds
                        if obs_kind in facts["observations"]["kinds"]
                    ),
                    "",
                ),
                0,
            )
        )
        has_obs = any(k in kinds for k in observation_kinds)
        has_existing = any(k in existing for k in existing_kinds)
        has_signal = bool(signal and signals.get(signal))
        if integrity_unknown:
            value, why = "unknown", "Bounded runtime facts are incomplete."
        elif has_obs or has_existing or has_signal:
            value, why = (
                "applicable",
                "A registered observation, object, or fixed Source IR signal matches the duty.",
            )
        else:
            value, why = (
                "unknown",
                "No registered witness was found; absence cannot prove not-applicable.",
            )
        add(duty, value, SEMANTIC_APPLICABILITY_POLICY["rules"][duty], matched + source_refs, why)

    signal_duty("entities", {"entity", "identity", "entity_candidate"}, {"entity"})
    signal_duty("concepts", {"concept", "concept_candidate"}, {"concept"})
    signal_duty("events", {"event", "event_candidate", "timeline"}, {"event"}, "timeline")
    signal_duty("procedures", {"procedure", "procedure_candidate"}, {"procedure"}, "procedure")
    signal_duty("comparisons", {"comparison", "comparison_candidate"}, {"comparison"}, "table")

    identity_count = sum(
        kinds.get(kind, 0)
        for kind in {
            "entity",
            "identity",
            "identity_candidate",
            "entity_candidate",
            "concept",
            "concept_candidate",
        }
    ) + sum(existing.get(kind, 0) for kind in {"entity", "concept"})
    relation_obs = kinds.get("relation", 0) + kinds.get("relation_candidate", 0)
    if integrity_unknown:
        relation_value, relation_reason = "unknown", "Bounded runtime facts are incomplete."
    elif relation_obs or identity_count >= 2:
        relation_value, relation_reason = (
            "applicable",
            "A relation observation or at least two identity-bearing endpoints are registered.",
        )
    else:
        relation_value, relation_reason = (
            "unknown",
            "No relation or pair of identity-bearing endpoints is registered.",
        )
    add(
        "typed_relations",
        relation_value,
        SEMANTIC_APPLICABILITY_POLICY["rules"]["typed_relations"],
        refs + source_refs,
        relation_reason,
    )

    for duty in (
        "contradiction_scan",
        "identity_resolution",
        "source_coverage",
        "affected_synthesis_detection",
        "limitations_and_warnings",
    ):
        if integrity_unknown:
            value, why = (
                "unknown",
                "Bounded runtime facts are incomplete; deterministic scan cannot be closed.",
            )
        else:
            value, why = (
                "applicable",
                "Deterministic scan is always applicable to a complete registered run.",
            )
        add(duty, value, "deterministic-scan-v1", refs + source_refs, why)

    signal_duty(
        "unresolved_questions",
        {"question", "unresolved_question", "unresolved_item"},
        {"question", "unresolved_question"},
        "question",
    )
    overview_kinds = {"overview", "community", "synthesis"}
    previous_kinds = facts["previous_outputs"].get("kinds", {})
    has_overview = (
        any(kind in existing for kind in overview_kinds)
        or any(kind in previous_kinds for kind in overview_kinds)
        or bool(facts["affected_syntheses"]["count"])
    )
    if integrity_unknown:
        overview_value, overview_reason = "unknown", "Bounded runtime facts are incomplete."
    elif has_overview:
        overview_value, overview_reason = (
            "applicable",
            "Existing, previous, affected, or synthesis context requires impact review.",
        )
    else:
        overview_value, overview_reason = (
            "unknown",
            "No closed profile rule permits not-applicable; impact context is not proven absent.",
        )
    add(
        "overview_impact", overview_value, "overview-impact-signal-v1", source_refs, overview_reason
    )
    return out


def applicability_digest(applicability: dict[str, dict[str, Any]]) -> str:
    ordered = [
        {
            "duty_type": duty,
            "applicability": applicability[duty]["applicability"],
            "deterministic_basis": applicability[duty]["deterministic_basis"],
        }
        for duty in SEMANTIC_DUTIES
    ]
    return _sha(ordered)


def policy_digest() -> str:
    return SEMANTIC_APPLICABILITY_POLICY_SHA256
