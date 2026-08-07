from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..evidence.statements import validate_statement
from ..knowledge_autonomy import (
    AutonomousKnowledgeStore,
    _read_object,
    _validate_contract,
)
from ..knowledge_intelligence import normalize_identity_text
from ..knowledge_store import KnowledgeVault
from ..util import (
    canonical_json,
    query_search_terms,
    sha256_bytes,
    stable_id,
    strict_json_loads,
)

V6_DUTIES = (
    "primary_answer",
    "identity",
    "definition",
    "current_state",
    "temporal_freshness",
    "procedure",
    "exception",
    "contradiction",
    "applicability",
    "limitation",
    "source_evidence",
    "unresolved_gap",
)
V6_PROJECTIONS = frozenset({"compact", "standard", "audit"})
_SENSITIVITY_ORDER = ("public", "internal", "private", "restricted")
_FRESHNESS_ORDER = {"fresh": 0, "unknown": 1, "stale": 2, "invalidated": 3}
_MAX_STATEMENT_SCAN = 5_000
_MAX_STATEMENT_ARTIFACT_BYTES = 256 * 1024
_MAX_LOCAL_AUDIT_BYTES = 256 * 1024
_MAX_CANDIDATE_RECEIPTS = 512
_MAX_EVIDENCE_TEXT = 12_000
_MAX_PROJECTION_BYTES = 65_536
_MAX_EMBEDDED_PROJECTION_BYTES = 60_000


def _bounded(value: Any, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise ValueError(f"{field} is invalid or exceeds its bound")
    return value


def _source_key(reference: dict[str, Any]) -> str:
    return canonical_json(
        {
            "source_revision_id": reference.get("source_revision_id"),
            "fragment_id": reference.get("fragment_id")
            or reference.get("fragment_revision_id"),
            "locator": reference.get("locator"),
            "quote_sha256": reference.get("quote_sha256"),
        }
    )


def _artifact_value(store: AutonomousKnowledgeStore, digest: str, role: str) -> dict[str, Any]:
    row = store.connection.execute(
        """
        SELECT artifact_role, byte_size
        FROM source_compilation_artifacts_v1
        WHERE artifact_sha256 = ?
        """,
        (digest,),
    ).fetchone()
    if row is None or row["artifact_role"] != role:
        raise RuntimeError("query statement artifact binding is invalid")
    if row["byte_size"] > _MAX_STATEMENT_ARTIFACT_BYTES:
        raise RuntimeError("query statement artifact exceeds its read bound")
    payload = _read_object(store.root, digest)
    if len(payload) != row["byte_size"] or sha256_bytes(payload) != digest:
        raise RuntimeError("query statement artifact bytes are invalid")
    value = strict_json_loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError("query statement artifact is not an object")
    return value


def _statement_map_is_valid(
    store: AutonomousKnowledgeStore,
    *,
    statement_id: str,
    statement: dict[str, Any],
) -> bool:
    row = store.connection.execute(
        """
        SELECT map_artifact_sha256, map_sha256, map_json,
               knowledge_revision_id, ordinal, statement_sha256, input_set_sha256
        FROM statement_evidence_maps_v1
        WHERE statement_id = ?
        """,
        (statement_id,),
    ).fetchone()
    if row is None:
        return False
    value = _artifact_value(store, row["map_artifact_sha256"], "statement_map")
    _validate_contract("statement-evidence-map.v1.schema.json", value)
    if (
        row["map_sha256"] != row["map_artifact_sha256"]
        or canonical_json(value) != row["map_json"]
        or row["knowledge_revision_id"] != value.get("knowledge_revision_id")
        or row["ordinal"] != value.get("ordinal")
        or row["statement_sha256"] != value.get("statement_sha256")
        or row["input_set_sha256"] != value.get("input_set_sha256")
        or value.get("statement_id") != statement_id
        or value.get("knowledge_revision_id") != statement["knowledge_revision_id"]
        or value.get("ordinal") != statement["ordinal"]
        or value.get("statement_text") != statement["statement_text"]
        or value.get("statement_sha256") != statement["statement_sha256"]
        or value.get("statement_type") != statement["statement_type"]
        or value.get("support_status") != statement["support_status"]
        or value.get("valid_from") != statement.get("valid_from")
        or value.get("valid_to") != statement.get("valid_to")
        or value.get("limitation") != statement.get("limitation")
        or value.get("input_set_sha256") != statement["input_set_sha256"]
        or value.get("source_refs") != statement.get("source_refs")
        or value.get("knowledge_revision_refs") != statement.get("knowledge_revision_refs")
        or value.get("relation_revision_refs") != statement.get("relation_revision_refs")
        or value.get("gaps") != statement.get("gaps")
    ):
        raise RuntimeError("statement evidence map identity is inconsistent")
    return True


def _freshness(
    store: AutonomousKnowledgeStore,
    *,
    revision_id: str,
    source_refs: list[dict[str, Any]],
) -> str:
    state = "fresh"
    for reference in source_refs:
        row = store.connection.execute(
            """
            SELECT freshness
            FROM knowledge_dependencies_v1
            WHERE consumer_kind = 'knowledge_revision'
              AND consumer_revision_id = ?
              AND source_revision_id = ?
              AND fragment_id = ?
              AND dependency_kind = 'direct'
            LIMIT 1
            """,
            (
                revision_id,
                reference.get("source_revision_id"),
                reference.get("fragment_id") or reference.get("fragment_revision_id"),
            ),
        ).fetchone()
        candidate = row["freshness"] if row is not None else "unknown"
        if candidate not in _FRESHNESS_ORDER:
            candidate = "unknown"
        if _FRESHNESS_ORDER[candidate] > _FRESHNESS_ORDER[state]:
            state = candidate
    return state


def _target(query: str, value: str | dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        text = query
        extras: dict[str, Any] = {}
    elif isinstance(value, str):
        text = _bounded(value, field="query_target", maximum=5_000)
        extras = {}
    elif isinstance(value, dict):
        allowed = {"text", "semantic_key", "knowledge_id", "revision_id", "kind"}
        if set(value) - allowed:
            raise ValueError("query_target contains unknown fields")
        text = value.get("text", query)
        if not isinstance(text, str):
            raise ValueError("query_target.text is invalid")
        text = _bounded(text, field="query_target.text", maximum=5_000)
        extras = {
            key: item
            for key, item in value.items()
            if key != "text" and item is not None
        }
        for key, item in extras.items():
            if not isinstance(item, str):
                raise ValueError(f"query_target.{key} is invalid")
            _bounded(item, field=f"query_target.{key}", maximum=500)
    else:
        raise ValueError("query_target is invalid")
    normalized = normalize_identity_text(text) or text.casefold()
    terms = sorted(set(query_search_terms(text, limit=64, cover_tail=True)))
    return {
        "text": text,
        "normalized": normalized,
        "query_sha256": sha256_bytes(text.encode("utf-8")),
        "terms": terms,
        **extras,
    }


def _applicable_duties(
    *,
    query: str,
    purpose: str,
    requested: tuple[str, ...] | list[str] | None,
) -> list[str]:
    if requested is not None:
        if not isinstance(requested, (tuple, list)):
            raise ValueError("applicable_duties is invalid")
        selected = list(requested)
        if len(selected) != len(set(selected)) or any(
            duty not in V6_DUTIES for duty in selected
        ):
            raise ValueError("applicable_duties contains an invalid duty")
        return [duty for duty in V6_DUTIES if duty in selected]
    normalized = normalize_identity_text(query)
    procedure = any(
        token in normalized or token in query
        for token in ("procedure", "workflow", "how", "step", "流程", "步骤", "怎么")
    )
    exception = any(
        token in normalized or token in query
        for token in ("exception", "exclude", "limitation", "例外", "限制", "排除")
    )
    contradiction = purpose == "verify" or any(
        token in normalized or token in query
        for token in ("conflict", "contradiction", "compare", "冲突", "矛盾", "比较")
    )
    identity = any(
        token in normalized or token in query
        for token in ("identity", "who", "organization", "身份", "谁", "组织")
    )
    definition = any(
        token in normalized or token in query
        for token in ("what", "definition", "meaning", "什么", "定义", "含义")
    )
    temporal = purpose in {"historical", "freshness_check"} or any(
        token in normalized or token in query
        for token in ("current", "today", "date", "现在", "当前", "截至", "时间")
    )
    applicability = purpose in {"answer", "verify"} or any(
        token in normalized or token in query
        for token in ("applicable", "适用", "适用性")
    )
    source_evidence = purpose in {"verify", "quote", "historical", "legal"}
    return [
        duty
        for duty, enabled in (
            ("primary_answer", True),
            ("identity", identity),
            ("definition", definition),
            ("current_state", purpose in {"answer", "debug", "freshness_check"}),
            ("temporal_freshness", temporal),
            ("procedure", procedure),
            ("exception", exception),
            ("contradiction", contradiction),
            ("applicability", applicability),
            ("limitation", True),
            ("source_evidence", source_evidence),
            ("unresolved_gap", True),
        )
        if enabled
    ]


def _candidate_score(item: dict[str, Any], target: dict[str, Any]) -> tuple[int, int, int, str]:
    text = " ".join(
        str(item.get(field, ""))
        for field in ("statement_text", "title", "semantic_key", "kind")
    )
    normalized = normalize_identity_text(text)
    target_normalized = target["normalized"]
    exact = int(len(target_normalized) >= 3 and target_normalized in normalized)
    overlap = len(
        set(target["terms"]).intersection(
            query_search_terms(text, limit=128, cover_tail=True)
        )
    )
    factual = int(item.get("statement_type") == "factual")
    return (-exact, -overlap, -factual, str(item["statement_id"]))


def _load_statement_candidates(
    store: AutonomousKnowledgeStore,
    *,
    target: dict[str, Any],
    query: str,
    scope: str,
    max_sensitivity: str,
    purpose: str,
    as_of: str | None,
    kinds: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], int, bool]:
    rows = store.connection.execute(
        """
        SELECT statements.*, revisions.*, objects.current_revision_id,
               objects.workspace_path AS current_workspace_path
        FROM knowledge_statements_v1 AS statements
        JOIN knowledge_revisions_v3 AS revisions
          ON revisions.revision_id = statements.knowledge_revision_id
        JOIN knowledge_objects_v3 AS objects
          ON objects.knowledge_id = revisions.knowledge_id
        ORDER BY statements.knowledge_revision_id, statements.ordinal, statements.statement_id
        LIMIT ?
        """,
        (_MAX_STATEMENT_SCAN + 1,),
    ).fetchall()
    truncated = len(rows) > _MAX_STATEMENT_SCAN
    rows = rows[:_MAX_STATEMENT_SCAN]
    candidates: list[dict[str, Any]] = []
    rejections: list[dict[str, str]] = []
    query_terms = set(target["terms"])
    for row in rows:
        statement_id = str(row["statement_id"])
        item_ref = {"statement_id": statement_id}
        try:
            value = _artifact_value(store, row["statement_artifact_sha256"], "statement")
            _validate_contract("knowledge-statement.v1.schema.json", value)
            if canonical_json(value) != row["statement_json"]:
                raise ValueError("statement row/artifact mismatch")
            validate_statement(value, require_statement_id=True)
            references = value.get("source_refs", [])
            freshness = _freshness(
                store,
                revision_id=row["knowledge_revision_id"],
                source_refs=references,
            )
            is_current = row["current_revision_id"] == row["knowledge_revision_id"]
            if not is_current and purpose != "historical":
                rejections.append({**item_ref, "reason": "historical_statement"})
                continue
            if as_of is not None and row["recorded_at"] > as_of:
                rejections.append({**item_ref, "reason": "outside_as_of"})
                continue
            if row["lifecycle"] != "active":
                rejections.append({**item_ref, "reason": "withdrawn_or_inactive"})
                continue
            if row["scope"] != scope:
                rejections.append(
                    {
                        "candidate_id": sha256_bytes(statement_id.encode("utf-8")),
                        "reason": "denied_scope",
                    }
                )
                continue
            if (
                row["sensitivity"] not in _SENSITIVITY_ORDER
                or row["sensitivity"] == "restricted"
                or _SENSITIVITY_ORDER.index(row["sensitivity"])
                > _SENSITIVITY_ORDER.index(max_sensitivity)
            ):
                rejections.append(
                    {
                        "candidate_id": sha256_bytes(statement_id.encode("utf-8")),
                        "reason": "denied_sensitivity",
                    }
                )
                continue
            if kinds and row["kind"] not in kinds:
                rejections.append({**item_ref, "reason": "kind_filter"})
                continue
            target_values = {
                "semantic_key": row["semantic_key"],
                "knowledge_id": row["knowledge_id"],
                "revision_id": row["knowledge_revision_id"],
                "kind": row["kind"],
            }
            if any(
                target.get(field) is not None and target_values[field] != target[field]
                for field in target_values
            ):
                rejections.append({**item_ref, "reason": "query_target_mismatch"})
                continue
            if freshness != "fresh" and purpose != "historical":
                rejections.append({**item_ref, "reason": f"{freshness}_statement"})
                continue
            if value["support_status"] in {"unsupported", "not_applicable"}:
                rejections.append({**item_ref, "reason": "unsupported_statement"})
                continue
            if value["statement_type"] == "factual" and (
                not references
                or not _statement_map_is_valid(
                    store,
                    statement_id=statement_id,
                    statement=value,
                )
            ):
                rejections.append({**item_ref, "reason": "factual_statement_map_missing"})
                continue
            if bool(row["source_free"]) and value["statement_type"] != "interpretation":
                rejections.append({**item_ref, "reason": "source_free_factual"})
                continue
            revision = store._revision_row(row, include_body=False)
            if purpose != "historical" and not store.revision_provenance_admitted(revision):
                rejections.append({**item_ref, "reason": "provenance_not_admitted"})
                continue
            text = str(value["statement_text"])
            searchable = set(query_search_terms(
                " ".join((row["title"], row["semantic_key"] or "", text)),
                limit=128,
                cover_tail=True,
            ))
            has_identity_target = any(
                target.get(field) is not None
                for field in ("semantic_key", "knowledge_id", "revision_id", "kind")
            )
            if query_terms and not query_terms.intersection(searchable) and not has_identity_target:
                rejections.append({**item_ref, "reason": "query_mismatch"})
                continue
            metadata = strict_json_loads(row["metadata_json"])
            if not isinstance(metadata, dict):
                metadata = {}
            partition = (
                "source_free_interpretation"
                if bool(row["source_free"])
                else "interpretation"
                if value["statement_type"] == "interpretation"
                else "factual"
            )
            candidates.append(
                {
                    "statement_id": statement_id,
                    "knowledge_revision_id": row["knowledge_revision_id"],
                    "knowledge_id": row["knowledge_id"],
                    "ordinal": value["ordinal"],
                    "statement_text": text,
                    "statement_sha256": value["statement_sha256"],
                    "statement_type": value["statement_type"],
                    "support_status": value["support_status"],
                    "source_refs": references,
                    "knowledge_revision_refs": value["knowledge_revision_refs"],
                    "relation_revision_refs": value["relation_revision_refs"],
                    "valid_from": value["valid_from"],
                    "valid_to": value["valid_to"],
                    "limitation": value["limitation"],
                    "gaps": value["gaps"],
                    "input_set_sha256": value["input_set_sha256"],
                    "current_supported": bool(
                        is_current
                        and purpose != "historical"
                        and value["support_status"] == "supported"
                    ),
                    "freshness": freshness,
                    "partition": partition,
                    "title": row["title"],
                    "kind": row["kind"],
                    "semantic_key": row["semantic_key"],
                    "epistemic_state": row["epistemic_state"],
                    "origin": row["origin"],
                    "authority": row["authority"],
                    "verification": row["verification"],
                    "legal_authority": False,
                    "source_free": bool(row["source_free"]),
                    "applicability": metadata.get("applicability"),
                    "_score": _candidate_score(
                        {
                            "statement_id": statement_id,
                            "statement_text": text,
                            "title": row["title"],
                            "semantic_key": row["semantic_key"],
                            "kind": row["kind"],
                            "statement_type": value["statement_type"],
                        },
                        target,
                    ),
                }
            )
        except (TypeError, KeyError):
            rejections.append({**item_ref, "reason": "invalid_statement_evidence"})
    candidates.sort(key=lambda item: item["_score"])
    return candidates, rejections, len(rows), truncated


def _source_evidence(
    store: KnowledgeVault,
    knowledge_store: AutonomousKnowledgeStore,
    *,
    references: Iterable[dict[str, Any]],
    scope: str,
    max_sensitivity: str,
    max_sources: int,
    max_chars: int,
    reason: str,
    seen: dict[str, dict[str, Any]],
    represented_keys: set[str],
    deduplications: list[dict[str, str]],
    suppressions: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], int]:
    selected: list[dict[str, Any]] = []
    selected_chars = sum(len(str(item.get("excerpt", ""))) for item in seen.values())
    for reference in references:
        if not isinstance(reference, dict):
            suppressions.append({"candidate_id": "unknown", "reason": "invalid_source_ref"})
            continue
        source_revision_id = reference.get("source_revision_id")
        fragment_id = reference.get("fragment_id") or reference.get("fragment_revision_id")
        if not isinstance(source_revision_id, str) or not isinstance(fragment_id, str):
            suppressions.append({"candidate_id": "unknown", "reason": "invalid_source_ref"})
            continue
        normalized = dict(reference)
        if "fragment_id" not in normalized:
            binding = knowledge_store.connection.execute(
                """
                SELECT fragment_id
                FROM legacy_fragment_bindings_v2
                WHERE fragment_revision_id = ?
                LIMIT 1
                """,
                (fragment_id,),
            ).fetchone()
            if binding is None:
                suppressions.append({"candidate_id": fragment_id, "reason": "fragment_unavailable"})
                continue
            normalized["fragment_id"] = binding["fragment_id"]
        key = _source_key(normalized)
        if key in seen:
            deduplications.append({"source_key": key, "reason": "duplicate_source_reference"})
            continue
        if key in represented_keys:
            deduplications.append(
                {"source_key": key, "reason": "represented_source_reference"}
            )
            continue
        if len(seen) >= max_sources:
            suppressions.append({"candidate_id": key, "reason": "source_budget"})
            continue
        if not knowledge_store._source_reference_is_bound(
            normalized,
            scope=scope,
            max_sensitivity=max_sensitivity,
            require_active=True,
        ):
            suppressions.append({"candidate_id": key, "reason": "source_not_admitted"})
            continue
        row = knowledge_store.connection.execute(
            """
            SELECT fragments.text, fragments.text_sha256, fragments.locator,
                   source_binding.source_revision_id
            FROM legacy_fragment_bindings_v2 AS bindings
            JOIN source_fragments AS fragments USING(fragment_id)
            JOIN source_revision_bindings_v2 AS source_binding
              ON source_binding.legacy_source_id = bindings.legacy_source_id
            WHERE source_binding.source_revision_id = ?
              AND (bindings.fragment_id = ? OR bindings.fragment_revision_id = ?)
            LIMIT 1
            """,
            (source_revision_id, normalized["fragment_id"], fragment_id),
        ).fetchone()
        if row is None or row["text_sha256"] != normalized.get("quote_sha256"):
            raise RuntimeError("statement source reference does not match its fragment")
        if normalized.get("locator") not in {None, row["locator"]}:
            raise RuntimeError("statement source locator does not match its fragment")
        remaining = max_chars - selected_chars
        if remaining <= 0:
            suppressions.append({"candidate_id": key, "reason": "character_budget"})
            continue
        excerpt = str(row["text"])[: min(remaining, _MAX_EVIDENCE_TEXT)]
        item = {
            "evidence_id": stable_id(
                "queryevidence", source_revision_id, normalized["fragment_id"]
            ),
            "source_revision_id": source_revision_id,
            "fragment_id": normalized["fragment_id"],
            "excerpt": excerpt,
            "content_sha256": row["text_sha256"],
            "source_refs": [normalized],
            "selection_reason": reason,
            "verification": "verified_source",
        }
        seen[key] = item
        represented_keys.add(key)
        selected.append(item)
        selected_chars += len(excerpt)
    return selected, selected_chars


def _historical_evidence_cards(
    cards: Iterable[dict[str, Any]], *, reason: str
) -> list[dict[str, Any]]:
    """Normalize the already as-of-admitted cards without current-state rechecking."""
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        references = card.get("source_refs", [])
        if not isinstance(references, list) or not references:
            continue
        reference = references[0]
        if not isinstance(reference, dict):
            continue
        source_revision_id = reference.get("source_revision_id")
        fragment_id = reference.get("fragment_id") or reference.get(
            "fragment_revision_id"
        ) or card.get("fragment_id")
        if not isinstance(source_revision_id, str) or not isinstance(fragment_id, str):
            continue
        normalized = {
            "source_revision_id": source_revision_id,
            "fragment_id": fragment_id,
            "locator": reference.get("locator") or str(card.get("locator", "historical")),
            "quote_sha256": reference.get("quote_sha256") or card.get("content_sha256"),
        }
        key = _source_key(normalized)
        if key in seen:
            continue
        seen.add(key)
        selected.append(
            {
                "evidence_id": stable_id(
                    "queryevidence", source_revision_id, fragment_id
                ),
                "source_revision_id": source_revision_id,
                "fragment_id": fragment_id,
                "excerpt": str(card.get("excerpt", "")),
                "content_sha256": card.get("content_sha256"),
                "source_refs": [normalized],
                "selection_reason": reason,
                "verification": "verified_source",
            }
        )
    return selected


def _duty_reports(
    *,
    applicable: list[str],
    statements: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    contradictions: list[dict[str, Any]],
    residual_gaps: list[dict[str, Any]],
    before: bool,
) -> list[dict[str, Any]]:
    supported_factual_ids = [
        str(item["statement_id"])
        for item in statements
        if item.get("statement_type") == "factual"
        and item.get("support_status") in {"supported", "contested"}
    ]
    identity_ids = [
        str(item["statement_id"])
        for item in statements
        if item.get("kind") in {"entity", "event"}
        and item.get("statement_type") == "factual"
    ]
    definition_ids = [
        str(item["statement_id"])
        for item in statements
        if item.get("kind") in {"concept", "definition"}
        and item.get("statement_type") == "factual"
    ]
    current_state_ids = [
        str(item["statement_id"])
        for item in statements
        if item.get("statement_type") == "factual"
        and item.get("support_status") == "supported"
        and item.get("current_supported") is True
    ]
    temporal_ids = [
        str(item["statement_id"])
        for item in statements
        if item.get("freshness") == "fresh"
        and (item.get("valid_from") is not None or item.get("valid_to") is not None)
    ]
    procedure_ids = [
        str(item["statement_id"])
        for item in statements
        if item.get("kind") == "procedure"
    ]
    source_ids = [
        "source:"
        f"{reference.get('source_revision_id')}:"
        f"{reference.get('fragment_id') or reference.get('fragment_revision_id')}"
        for item in statements
        for reference in item.get("source_refs", [])
        if isinstance(reference, dict)
        and isinstance(reference.get("source_revision_id"), str)
        and isinstance(
            reference.get("fragment_id") or reference.get("fragment_revision_id"),
            str,
        )
    ]
    source_ids.extend(
        f"source:{item['source_revision_id']}:{item['fragment_id']}"
        for item in evidence
        if isinstance(item.get("source_revision_id"), str)
        and isinstance(item.get("fragment_id"), str)
    )
    source_ids = list(dict.fromkeys(source_ids))
    contested_ids = [
        str(item["statement_id"])
        for item in statements
        if item.get("support_status") == "contested"
    ]
    applicability_ids = [
        str(item["statement_id"])
        for item in statements
        if item.get("applicability")
    ]
    limitation_ids = [
        str(item["statement_id"])
        for item in statements
        if item.get("limitation") or item.get("statement_type") == "limitation"
    ]
    unresolved_ids = [str(item["gap_id"]) for item in residual_gaps]
    reports: list[dict[str, Any]] = []
    for duty in V6_DUTIES:
        is_applicable = duty in applicable
        refs: list[str]
        if duty == "primary_answer":
            refs = [*supported_factual_ids, *source_ids]
        elif duty == "identity":
            refs = identity_ids
        elif duty == "definition":
            refs = definition_ids
        elif duty == "current_state":
            refs = current_state_ids
        elif duty == "temporal_freshness":
            refs = temporal_ids
        elif duty == "procedure":
            refs = procedure_ids
        elif duty == "exception":
            refs = [*limitation_ids, *contested_ids]
        elif duty == "contradiction":
            refs = [str(item["statement_id"]) for item in contradictions]
        elif duty == "applicability":
            refs = applicability_ids
        elif duty == "limitation":
            refs = limitation_ids
        elif duty == "source_evidence":
            refs = source_ids
        else:
            refs = unresolved_ids
        refs = list(dict.fromkeys(refs))[:64]
        if not is_applicable:
            status = "not_applicable"
            reason = "The deterministic query-purpose matrix marked this duty not applicable."
        elif duty == "unresolved_gap":
            status = "unresolved" if before and not residual_gaps else "satisfied"
            reason = (
                "Residual gaps are represented explicitly in the bounded result."
                if residual_gaps
                else "No unresolved gap remains after bounded admission and selection."
            )
        elif refs:
            status = "satisfied"
            reason = "Selected statement or exact source references cover this duty."
        else:
            status = "unresolved"
            reason = "No admitted selected statement or exact source reference covers this duty."
        reports.append(
            {
                "duty": duty,
                "applicable": is_applicable,
                "status": status,
                "selected_refs": refs,
                "reason": reason,
            }
        )
    return reports


def _projection_item(item: dict[str, Any], *, compact: bool) -> dict[str, Any]:
    result = {
        "statement_id": item["statement_id"],
        "statement_text": item["statement_text"][:2_000],
        "statement_type": item["statement_type"],
        "support_status": item["support_status"],
        "current_supported": bool(item["current_supported"]),
        "freshness": item["freshness"],
        "origin": item["origin"],
        "authority": item["authority"],
        "verification": item["verification"],
        "legal_authority": False,
        "source_refs": item["source_refs"][:2],
    }
    if not compact:
        result.update(
            {
                "knowledge_revision_id": item["knowledge_revision_id"],
                "knowledge_id": item["knowledge_id"],
                "ordinal": item["ordinal"],
                "valid_from": item["valid_from"],
                "valid_to": item["valid_to"],
                "limitation": item["limitation"],
                "partition": item["partition"],
                "object_summary": {
                    "knowledge_id": item["knowledge_id"],
                    "revision_id": item["knowledge_revision_id"],
                    "title": str(item["title"])[:500],
                    "kind": item["kind"],
                    "semantic_key": item["semantic_key"],
                },
            }
        )
    return result


def _fit_projection(value: dict[str, Any]) -> dict[str, Any]:
    if len(canonical_json(value).encode("utf-8")) <= _MAX_EMBEDDED_PROJECTION_BYTES:
        return value
    statements = value.get("statements", [])
    for item in statements:
        if isinstance(item, dict) and isinstance(item.get("statement_text"), str):
            item["statement_text"] = item["statement_text"][:512]
    for item in value.get("evidence", []):
        if isinstance(item, dict) and isinstance(item.get("excerpt"), str):
            item["excerpt"] = item["excerpt"][:512]
    audit = value.get("audit")
    if isinstance(audit, dict):
        audit["candidates"] = audit.get("candidates", [])[:64]
        audit["rejections"] = audit.get("rejections", [])[:128]
        audit["suppressions"] = audit.get("suppressions", [])[:128]
    if (
        len(canonical_json(value).encode("utf-8")) > _MAX_EMBEDDED_PROJECTION_BYTES
        and isinstance(audit, dict)
    ):
        audit["candidates"] = audit.get("candidates", [])[:16]
        audit["rejections"] = audit.get("rejections", [])[:32]
        audit["suppressions"] = audit.get("suppressions", [])[:32]
        plan = audit.get("query_plan")
        if isinstance(plan, dict):
            audit["query_plan"] = {
                "schema_version": plan.get("schema_version"),
                "receipt_id": plan.get("receipt_id"),
                "coverage_after": plan.get("coverage_after"),
                "fallback": plan.get("fallback"),
            }
    if (
        len(canonical_json(value).encode("utf-8")) > _MAX_EMBEDDED_PROJECTION_BYTES
        and isinstance(audit, dict)
    ):
        audit["candidates"] = []
        audit["rejections"] = []
        audit["suppressions"] = []
        audit["deduplications"] = []
        audit["fallback"] = audit.get("fallback", [])[:4]
    if len(canonical_json(value).encode("utf-8")) > _MAX_EMBEDDED_PROJECTION_BYTES:
        raise RuntimeError("v6 Knowledge Capsule projection exceeds its hard 64 KiB budget")
    return value


def _projection(
    *,
    projection: str,
    statements: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    contradictions: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    receipt_id: str,
    plan: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    compact = projection == "compact"
    result: dict[str, Any] = {
        "schema_version": "deeplaw.knowledge-capsule-projection/v1",
        "projection": projection,
        "receipt_id": receipt_id,
        "hard_limit_bytes": _MAX_PROJECTION_BYTES,
        "statements": [_projection_item(item, compact=compact) for item in statements],
        "gaps": gaps[:32],
        "selected_statement_count": len(statements),
        "selected_source_count": len(evidence),
    }
    if compact:
        result["citations"] = [
            reference
            for item in statements
            for reference in item.get("source_refs", [])[:1]
        ][:16]
    else:
        result["evidence"] = evidence[:32]
        result["contradictions"] = contradictions[:16]
    if projection == "audit":
        result["audit"] = {
            "query_plan": plan,
            "candidates": audit.get("candidates", [])[:_MAX_CANDIDATE_RECEIPTS],
            "rejections": audit.get("rejections", [])[:_MAX_CANDIDATE_RECEIPTS],
            "fallback": audit.get("fallback", []),
            "deduplications": audit.get("deduplications", []),
            "suppressions": audit.get("suppressions", []),
        }
    _validate_contract("knowledge-capsule-projection.v1.schema.json", _fit_projection(result))
    return result


def execute_v6(
    service: Any,
    *,
    evidence_store: KnowledgeVault,
    knowledge_store: AutonomousKnowledgeStore,
    query: str,
    purpose: str,
    policy: str,
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
    force_canonical_lexical: bool,
    query_target: str | dict[str, Any] | None,
    applicable_duties: tuple[str, ...] | list[str] | None,
    projection: str,
) -> dict[str, Any]:
    del graph_hops, retrieval_mode, force_canonical_lexical
    if projection not in V6_PROJECTIONS:
        raise ValueError("query projection is invalid")
    target = _target(query, query_target)
    applicable = _applicable_duties(
        query=query,
        purpose=purpose,
        requested=applicable_duties,
    )
    if purpose == "legal":
        # The general Knowledge OS cannot satisfy legal evidence duties.  Do not
        # even discover general Statements for this purpose: the separate
        # read-only law_support process owns Authoritative Evidence admission.
        candidates: list[dict[str, Any]] = []
        rejections: list[dict[str, str]] = []
        scanned_count = 0
        scan_truncated = False
    else:
        candidates, rejections, scanned_count, scan_truncated = (
            _load_statement_candidates(
                knowledge_store,
                target=target,
                query=query,
                scope=scope,
                max_sensitivity=max_sensitivity,
                purpose=purpose,
                as_of=as_of,
                kinds=kinds,
            )
        )
    statement_budget, evidence_budget = service._partition_budget(
        policy,
        limit=limit,
        max_chars=max_chars,
    )
    statement_item_limit = statement_budget["items"]
    statement_character_limit = statement_budget["characters"]
    selected: list[dict[str, Any]] = []
    budget_suppressions: list[dict[str, str]] = []
    selected_statement_characters = 0
    for item in candidates:
        if len(selected) >= statement_item_limit:
            break
        item_characters = len(str(item.get("statement_text", "")))
        if selected_statement_characters + item_characters > statement_character_limit:
            budget_suppressions.append(
                {
                    "candidate_id": str(item["statement_id"]),
                    "reason": "character_budget",
                }
            )
            continue
        selected.append(item)
        selected_statement_characters += item_characters
    budget_suppressed_ids = {
        item["candidate_id"] for item in budget_suppressions
    }
    selected_statement_ids = {str(item["statement_id"]) for item in selected}
    suppressions: list[dict[str, str]] = [
        {"candidate_id": str(item["statement_id"]), "reason": "selection_budget"}
        for item in candidates
        if str(item["statement_id"]) not in selected_statement_ids
        and str(item["statement_id"]) not in budget_suppressed_ids
    ][:_MAX_CANDIDATE_RECEIPTS]
    suppressions.extend(budget_suppressions)
    if scan_truncated:
        suppressions.append({"candidate_id": "statement_scan", "reason": "scan_bound"})
    deduplications: list[dict[str, str]] = []
    evidence_seen: dict[str, dict[str, Any]] = {}
    citation_seen: set[str] = set()
    for item in selected:
        for reference in item.get("source_refs", []):
            if not isinstance(reference, dict):
                continue
            source_key = _source_key(reference)
            if source_key in citation_seen:
                deduplications.append(
                    {"source_key": source_key, "reason": "duplicate_statement_citation"}
                )
            citation_seen.add(source_key)
    evidence: list[dict[str, Any]] = []
    evidence_item_limit = min(max_sources, evidence_budget["items"])
    evidence_character_limit = evidence_budget["characters"]
    evidence_first = purpose in {"verify", "quote", "historical"}
    identity_target = any(
        target.get(field) is not None
        for field in ("semantic_key", "knowledge_id", "revision_id", "kind")
    )
    source_discovery_allowed = purpose != "legal" and (
        not identity_target or bool(selected)
    )
    if (
        evidence_first
        and source_discovery_allowed
        and evidence_item_limit > 0
        and evidence_character_limit > 0
    ):
        initial_compiled = (
            [
                {
                    **item,
                    "revision_id": item["knowledge_revision_id"],
                }
                for item in selected
            ]
            if as_of is not None
            else []
        )
        initial = (
            None
            if identity_target
            else service._evidence(
                evidence_store,
                knowledge_store,
                query=query,
                scope=scope,
                max_sensitivity=max_sensitivity,
                limit=min(5, evidence_item_limit),
                max_chars=max(200, evidence_character_limit),
                as_of=as_of,
                kinds=kinds,
                compiled=initial_compiled,
            )
        )
        if identity_target:
            initial_refs = [
                reference
                for item in selected
                for reference in item.get("source_refs", [])
                if isinstance(reference, dict)
            ]
            if as_of is None:
                for reference in initial_refs:
                    key = _source_key(reference)
                    if key in citation_seen:
                        deduplications.append(
                            {
                                "source_key": key,
                                "reason": "statement_citation_also_evidence",
                            }
                        )
                evidence, _selected_chars = _source_evidence(
                    evidence_store,
                    knowledge_store,
                    references=initial_refs,
                    scope=scope,
                    max_sensitivity=max_sensitivity,
                    max_sources=evidence_item_limit,
                    max_chars=evidence_character_limit,
                    reason="identity_target_evidence",
                    seen=evidence_seen,
                    represented_keys=set(),
                    deduplications=deduplications,
                    suppressions=suppressions,
                )
            else:
                initial = service._evidence(
                    evidence_store,
                    knowledge_store,
                    query=query,
                    scope=scope,
                    max_sensitivity=max_sensitivity,
                    limit=min(5, evidence_item_limit),
                    max_chars=max(200, evidence_character_limit),
                    as_of=as_of,
                    kinds=kinds,
                    compiled=initial_compiled,
                )
        if identity_target and as_of is None:
            initial = None
        if initial is not None and as_of is not None:
            evidence = _historical_evidence_cards(
                initial.cards, reason="historical_evidence_first"
            )[:evidence_item_limit]
            historical_characters = 0
            for item in evidence:
                excerpt = str(item.get("excerpt", ""))
                remaining = evidence_character_limit - historical_characters
                item["excerpt"] = excerpt[:remaining]
                historical_characters += len(item["excerpt"])
            for item in evidence:
                key = _source_key(item["source_refs"][0])
                if key in citation_seen:
                    deduplications.append(
                        {
                            "source_key": key,
                            "reason": "statement_citation_also_evidence",
                        }
                    )
                evidence_seen[key] = item
                citation_seen.add(key)
        elif initial is not None:
            initial_refs = [
                reference
                for card in initial.cards
                for reference in card.get("source_refs", [])
                if isinstance(reference, dict)
            ]
            for reference in initial_refs:
                key = _source_key(reference)
                if key in citation_seen:
                    deduplications.append(
                        {
                            "source_key": key,
                            "reason": "statement_citation_also_evidence",
                        }
                    )
            evidence, _selected_chars = _source_evidence(
                evidence_store,
                knowledge_store,
                references=initial_refs,
                scope=scope,
                max_sensitivity=max_sensitivity,
                max_sources=evidence_item_limit,
                max_chars=evidence_character_limit,
                reason="evidence_first",
                seen=evidence_seen,
                represented_keys=set(),
                deduplications=deduplications,
                suppressions=suppressions,
            )
            statement_refs = [
                reference
                for item in selected
                for reference in item.get("source_refs", [])
                if isinstance(reference, dict)
            ]
            statement_evidence, _selected_chars = _source_evidence(
                evidence_store,
                knowledge_store,
                references=statement_refs,
                scope=scope,
                max_sensitivity=max_sensitivity,
                max_sources=evidence_item_limit,
                max_chars=evidence_character_limit,
                reason="statement_evidence_first",
                seen=evidence_seen,
                represented_keys=set(),
                deduplications=deduplications,
                suppressions=suppressions,
            )
            evidence.extend(statement_evidence)
    contradictions = [
        {
            "statement_id": item["statement_id"],
            "support_status": item["support_status"],
            "source_refs": item.get("source_refs", [])[:4],
            "gaps": item.get("gaps", [])[:4],
        }
        for item in selected
        if item.get("support_status") == "contested"
    ]
    before_reports = _duty_reports(
        applicable=applicable,
        statements=selected,
        evidence=evidence,
        contradictions=contradictions,
        residual_gaps=[],
        before=True,
    )
    uncovered = [
        report["duty"]
        for report in before_reports
        if report["applicable"] and report["status"] == "unresolved"
    ]
    fallback_events: list[dict[str, Any]] = []
    fallback_duties = [
        duty
        for duty in uncovered
        if duty != "unresolved_gap" and source_discovery_allowed and not identity_target
    ]
    for duty in fallback_duties:
        remaining_items = evidence_item_limit - len(evidence)
        remaining_characters = evidence_character_limit - sum(
            len(str(item.get("excerpt", ""))) for item in evidence
        )
        if remaining_items <= 0 or remaining_characters <= 0:
            suppressions.append(
                {"candidate_id": f"fallback:{duty}", "reason": "selection_budget"}
            )
            continue
        fallback_query = f"{query} {duty.replace('_', ' ')}"
        fallback = service._evidence(
            evidence_store,
            knowledge_store,
            query=fallback_query[:5_000],
            scope=scope,
            max_sensitivity=max_sensitivity,
            limit=min(5, remaining_items),
            max_chars=max(200, remaining_characters),
            as_of=as_of,
            kinds=kinds,
            compiled=(
                [
                    {
                        **item,
                        "revision_id": item["knowledge_revision_id"],
                    }
                    for item in selected
                ]
                if as_of is not None
                else selected
            ),
        )
        cards = fallback.cards
        fallback_refs = [
            reference
            for card in cards
            for reference in card.get("source_refs", [])
            if isinstance(reference, dict)
        ]
        before_count = len(evidence)
        if as_of is not None:
            extra = _historical_evidence_cards(
                cards, reason=f"targeted_source_fallback:{duty}"
            )[:remaining_items]
            selected_historical_characters = 0
            for item in extra:
                excerpt = str(item.get("excerpt", ""))
                remaining = remaining_characters - selected_historical_characters
                item["excerpt"] = excerpt[:remaining]
                selected_historical_characters += len(item["excerpt"])
            for item in extra:
                key = _source_key(item["source_refs"][0])
                if key in evidence_seen:
                    deduplications.append(
                        {"source_key": key, "reason": "duplicate_source_reference"}
                    )
                    continue
                evidence_seen[key] = item
                citation_seen.add(key)
        else:
            extra, _ = _source_evidence(
                evidence_store,
                knowledge_store,
                references=fallback_refs,
                scope=scope,
                max_sensitivity=max_sensitivity,
                max_sources=evidence_item_limit,
                max_chars=evidence_character_limit,
                reason=f"targeted_source_fallback:{duty}",
                seen=evidence_seen,
                represented_keys=citation_seen,
                deduplications=deduplications,
                suppressions=suppressions,
            )
        evidence.extend(extra)
        fallback_events.append(
            {
                "duty": duty,
                "query_sha256": sha256_bytes(fallback_query[:5_000].encode("utf-8")),
                "candidate_count": len(cards),
                "selected_source_count": len(evidence) - before_count,
                "source_keys": [
                    _source_key(reference) for reference in fallback_refs[:16]
                ],
            }
        )
    residual_gaps: list[dict[str, Any]] = []
    stale_gaps = service._stale_knowledge_gaps(
        knowledge_store,
        query=query,
        scope=scope,
        max_sensitivity=max_sensitivity,
        limit=16,
    )
    uncompiled_sources = service._uncompiled_sources(
        knowledge_store,
        query=query,
        scope=scope,
        max_sensitivity=max_sensitivity,
        limit=16,
    )
    for index, gap in enumerate(stale_gaps):
        residual_gaps.append(
            {
                **gap,
                "gap_id": stable_id(
                    "querygap",
                    target["query_sha256"],
                    "stale_knowledge",
                    str(index),
                    knowledge_store.audit_head,
                ),
                "duty": "temporal_freshness",
            }
        )
    if uncompiled_sources:
        residual_gaps.append(
            {
                "gap_id": stable_id(
                    "querygap",
                    target["query_sha256"],
                    "uncompiled_source",
                    knowledge_store.audit_head,
                ),
                "code": "uncompiled_source",
                "duty": "unresolved_gap",
                "message": "Relevant admitted Source Revisions have no successful compilation.",
                "count": len(uncompiled_sources),
                "source_revision_ids": [
                    item["source_revision_id"] for item in uncompiled_sources
                ],
            }
        )
    after_reports = _duty_reports(
        applicable=applicable,
        statements=selected,
        evidence=evidence,
        contradictions=contradictions,
        residual_gaps=[],
        before=False,
    )
    for report in after_reports:
        if report["applicable"] and report["status"] == "unresolved":
            gap_id = stable_id(
                "querygap",
                target["query_sha256"],
                report["duty"],
                knowledge_store.audit_head,
            )
            residual_gaps.append(
                {
                    "gap_id": gap_id,
                    "code": "duty_unresolved",
                    "duty": report["duty"],
                    "message": (
                        "No admitted statement or exact source evidence covers "
                        f"{report['duty']}."
                    ),
                }
            )
    answerable = bool(
        evidence
        or any(
            item.get("statement_type") == "factual"
            and item.get("support_status") in {"supported", "contested"}
            for item in selected
        )
    )
    if not answerable:
        residual_gaps.append(
            {
                "gap_id": stable_id(
                    "querygap",
                    target["query_sha256"],
                    "no_answer",
                    knowledge_store.audit_head,
                ),
                "code": "no_answer",
                "duty": "primary_answer",
                "message": (
                    "No admitted current-supported Statement or exact Source evidence "
                    "matched the query."
                ),
            }
        )
    if purpose == "legal":
        residual_gaps.append(
            {
                "gap_id": stable_id(
                    "querygap",
                    target["query_sha256"],
                    "law_support_required",
                    knowledge_store.audit_head,
                ),
                "code": "law_support_required",
                "duty": "source_evidence",
                "message": (
                    "Legal evidence is unavailable through general knowledge_support; "
                    "use the separate read-only law_support process."
                ),
            }
        )
    after_reports = _duty_reports(
        applicable=applicable,
        statements=selected,
        evidence=evidence,
        contradictions=contradictions,
        residual_gaps=residual_gaps,
        before=False,
    )
    coverage_before = {
        "applicable_count": sum(1 for item in before_reports if item["applicable"]),
        "satisfied_count": sum(1 for item in before_reports if item["status"] == "satisfied"),
        "unresolved_duties": [
            item["duty"] for item in before_reports if item["status"] == "unresolved"
        ],
    }
    coverage_after = {
        "applicable_count": sum(1 for item in after_reports if item["applicable"]),
        "satisfied_count": sum(1 for item in after_reports if item["status"] == "satisfied"),
        "unresolved_duties": [
            item["duty"] for item in after_reports if item["status"] == "unresolved"
        ],
    }
    plan_core = {
        "schema_version": "deeplaw.knowledge-query-plan/v6",
        "intent": "purpose_aware_knowledge_retrieval",
        "purpose": purpose,
        "policy_id": policy,
        "scope": scope,
        "max_sensitivity": max_sensitivity,
        "as_of": as_of,
        "query_target": target,
        "applicable_duties": applicable,
        "budget": {
            "items": limit,
            "characters": max_chars,
            "tokens": max_tokens,
            "sources": max_sources,
            "provider_characters": _MAX_PROJECTION_BYTES,
        },
        "projection": projection,
        "input_audit_head": knowledge_store.audit_head,
        "input_legacy_audit_head": knowledge_store.legacy_audit_head,
        "compiled_candidate_count": scanned_count,
        "admitted_statement_count": len(candidates),
        "selected_statement_count": len(selected),
        "evidence_selected_count": len(evidence),
        "duties": after_reports,
        "coverage_before": coverage_before,
        "coverage_after": coverage_after,
        "selection": {
            "statement_ids": [str(item["statement_id"]) for item in selected],
            "source_keys": list(
                dict.fromkeys(
                    [
                        _source_key(reference)
                        for item in selected
                        for reference in item.get("source_refs", [])[:4]
                    ]
                    + [
                        _source_key(reference)
                        for item in evidence
                        for reference in item.get("source_refs", [])[:4]
                    ]
                )
            ),
            "evidence_ids": [str(item["evidence_id"]) for item in evidence],
        },
        "fallback": {
            "used": bool(fallback_events),
            "duties": [item["duty"] for item in fallback_events],
            "events": fallback_events,
        },
        "residual_gap_ids": [str(item["gap_id"]) for item in residual_gaps],
        "rejected_candidate_count": len(rejections),
        "suppressed_candidate_count": len(suppressions),
        "deduplicated_evidence_count": len(deduplications),
        "ranking_authority_changed": False,
    }
    seed_sha256 = sha256_bytes(canonical_json(plan_core).encode("utf-8"))
    receipt_id = stable_id("queryreceipt", seed_sha256, knowledge_store.audit_head)
    query_sha256 = sha256_bytes(query.encode("utf-8"))
    plan = {**plan_core, "query_sha256": query_sha256, "receipt_id": receipt_id}
    _validate_contract("knowledge-query-plan.v6.schema.json", plan)
    plan_sha256 = sha256_bytes(canonical_json(plan).encode("utf-8"))
    candidate_receipts = [
        {
            "statement_id": str(item["statement_id"]),
            "score": list(item["_score"]),
        }
        for item in candidates[:_MAX_CANDIDATE_RECEIPTS]
    ]
    audit_body = {
        "schema_version": "deeplaw.query-audit-receipt/v1",
        "receipt_id": receipt_id,
        "query_plan_sha256": plan_sha256,
        "query_sha256": query_sha256,
        "input_audit_head": knowledge_store.audit_head,
        "input_legacy_audit_head": knowledge_store.legacy_audit_head,
        "candidate_count": scanned_count,
        "admitted_statement_count": len(candidates),
        "selected_statement_ids": [str(item["statement_id"]) for item in selected],
        "fallback": fallback_events,
        "deduplications": deduplications,
        "suppressions": suppressions[:_MAX_CANDIDATE_RECEIPTS],
        "rejections": rejections[:_MAX_CANDIDATE_RECEIPTS],
        "residual_gap_ids": [str(item["gap_id"]) for item in residual_gaps],
        "ranking_authority_changed": False,
        "write_performed": False,
        "candidates": candidate_receipts,
    }
    if len(canonical_json(audit_body).encode("utf-8")) > _MAX_LOCAL_AUDIT_BYTES:
        for candidate_limit, rejection_limit, suppression_limit in (
            (128, 128, 128),
            (32, 64, 64),
            (0, 0, 0),
        ):
            audit_body["candidates"] = candidate_receipts[:candidate_limit]
            audit_body["rejections"] = rejections[:rejection_limit]
            audit_body["suppressions"] = suppressions[:suppression_limit]
            if len(canonical_json(audit_body).encode("utf-8")) <= _MAX_LOCAL_AUDIT_BYTES:
                break
        else:
            raise RuntimeError("v6 local query audit exceeds its 256 KiB bound")
    audit_digest = sha256_bytes(canonical_json(audit_body).encode("utf-8"))
    audit_receipt = {**audit_body, "receipt_sha256": audit_digest}
    _validate_contract("query-audit-receipt.v1.schema.json", audit_receipt)
    local_audit = audit_receipt
    capsule = _projection(
        projection=projection,
        statements=selected,
        evidence=evidence,
        contradictions=contradictions,
        gaps=residual_gaps,
        receipt_id=receipt_id,
        plan=plan,
        audit=local_audit,
    )
    result = {
        "schema_version": "deeplaw.purpose-aware-retrieval/v3",
        "vault_id": knowledge_store.vault_id,
        "purpose": purpose,
        "policy_id": policy,
        "query": query,
        "query_plan": plan,
        "query_plan_sha256": plan_sha256,
        "statements": [_projection_item(item, compact=False) for item in selected],
        "evidence": evidence,
        "contradictions": contradictions,
        "gaps": residual_gaps[:32],
        "capsule": capsule,
        "projection": projection,
        "receipt_id": receipt_id,
        "local_audit": local_audit,
        "metrics": {
            "compiled_candidate_count": scanned_count,
            "admitted_statement_count": len(candidates),
            "selected_statement_count": len(selected),
            "source_fallback_used": bool(fallback_events),
            "fallback_duty_count": len(fallback_events),
            "deduplicated_evidence_count": len(deduplications),
            "suppressed_candidate_count": len(suppressions),
            "uncompiled_source_count": len(uncompiled_sources),
            "stale_selection_prevented_count": len(stale_gaps),
            "duty_coverage": (
                coverage_after["satisfied_count"] / coverage_after["applicable_count"]
                if coverage_after["applicable_count"]
                else 1.0
            ),
        },
        "budget": {
            "max_items": limit,
            "selected_items": len(selected) + len(evidence),
            "max_characters": max_chars,
            "selected_characters": sum(
                len(str(item.get("statement_text", ""))) for item in selected
            ) + sum(len(str(item.get("excerpt", ""))) for item in evidence),
            "max_tokens": max_tokens,
            "max_provider_characters": _MAX_PROJECTION_BYTES,
        },
        "audit_head": knowledge_store.audit_head,
        "authority_changed_by_ranking": False,
        "write_performed": False,
    }
    _validate_contract("purpose-aware-retrieval.v3.schema.json", result)
    if len(canonical_json(result["capsule"]).encode("utf-8")) > _MAX_PROJECTION_BYTES:
        raise RuntimeError("v6 Knowledge Capsule exceeds its hard 64 KiB budget")
    return result
