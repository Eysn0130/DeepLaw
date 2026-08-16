from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..compilation.artifacts import read_compilation_artifact
from ..knowledge_autonomy import (
    AutonomousKnowledgeStore,
    _validate_contract,
)
from ..knowledge_models import canonical_timestamp
from ..util import canonical_json, sha256_bytes, stable_id, strict_json_loads

MAX_STATEMENTS_PER_REVISION = 4096
MAX_STATEMENT_TEXT_CHARS = 16 * 1024
MAX_REFS_PER_STATEMENT = 256
MAX_GAPS_PER_STATEMENT = 64
MAX_EVIDENCE_ARTIFACT_BYTES = 256 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REVISION = re.compile(r"^sourcerev_[0-9a-f]{24}$")
_FRAGMENT = re.compile(r"^fragment_[0-9a-f]{24}$")
_KNOWLEDGE_REVISION = re.compile(r"^knowledgerev_[0-9a-f]{24}$")
_RELATION_REVISION = re.compile(r"^relationrev_[0-9a-f]{24}$")
_REVISION = re.compile(r"^knowledgerev_[0-9a-f]{24}$")
_STATEMENT_ID = re.compile(r"^statement_[0-9a-f]{24}$")

_SOURCE_REFERENCE_KEYS = frozenset(
    {"source_revision_id", "fragment_id", "locator", "quote_sha256"}
)
_STATEMENT_TYPES = frozenset({"factual", "interpretation", "limitation", "unresolved"})
_SUPPORT_STATUSES = frozenset(
    {"supported", "contested", "unsupported", "not_applicable"}
)
_FORCED_KINDS = frozenset({"synthesis", "overview", "community", "comparison"})
_FRESHNESS_ORDER = {"fresh": 0, "unknown": 1, "stale": 2, "invalidated": 3}


def _bounded_text(value: Any, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise ValueError(f"{field} must be a bounded canonical string")
    return value


def _digest(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be lowercase SHA-256")
    return value


def statement_sha256(text: str) -> str:
    """Hash the exact UTF-8 statement text, without normalization."""

    _bounded_text(text, field="statement_text", maximum=MAX_STATEMENT_TEXT_CHARS)
    return sha256_bytes(text.encode("utf-8"))


def statement_id(knowledge_revision_id: str, ordinal: int, text_sha256: str) -> str:
    if not _REVISION.fullmatch(knowledge_revision_id):
        raise ValueError("knowledge_revision_id is invalid")
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
        raise ValueError("statement ordinal is invalid")
    _digest(text_sha256, field="statement_sha256")
    return stable_id("statement", knowledge_revision_id, str(ordinal), text_sha256)


def _canonical_source_reference(reference: Any) -> dict[str, str]:
    if not isinstance(reference, dict) or set(reference) != _SOURCE_REFERENCE_KEYS:
        raise ValueError("statement source reference shape is invalid")
    source_revision_id = reference.get("source_revision_id")
    fragment_id = reference.get("fragment_id")
    locator = reference.get("locator")
    quote_sha = reference.get("quote_sha256")
    if not isinstance(source_revision_id, str) or not _SOURCE_REVISION.fullmatch(
        source_revision_id
    ):
        raise ValueError("statement source revision reference is invalid")
    if not isinstance(fragment_id, str) or not _FRAGMENT.fullmatch(fragment_id):
        raise ValueError("statement fragment reference is invalid")
    _bounded_text(locator, field="statement source locator", maximum=2000)
    _digest(quote_sha, field="statement source quote hash")
    return {
        "source_revision_id": source_revision_id,
        "fragment_id": fragment_id,
        "locator": locator,
        "quote_sha256": quote_sha,
    }


def _canonical_refs(values: Any, *, field: str, pattern: re.Pattern[str]) -> list[str]:
    if not isinstance(values, list) or len(values) > MAX_REFS_PER_STATEMENT:
        raise ValueError(f"{field} exceeds its bound")
    canonical: list[str] = []
    for value in values:
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ValueError(f"{field} contains an invalid ID")
        canonical.append(value)
    if len(set(canonical)) != len(canonical):
        raise ValueError(f"{field} contains duplicate IDs")
    return sorted(canonical)


def _canonical_gap(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"gap_id", "reason"}:
        raise ValueError("statement gap shape is invalid")
    gap_id = _bounded_text(value.get("gap_id"), field="gap_id", maximum=200)
    reason = _bounded_text(value.get("reason"), field="gap reason", maximum=2000)
    return {"gap_id": gap_id, "reason": reason}


def _canonical_gaps(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list) or len(values) > MAX_GAPS_PER_STATEMENT:
        raise ValueError("statement gaps exceed their bound")
    gaps = [_canonical_gap(value) for value in values]
    keys = [canonical_json(value) for value in gaps]
    if len(set(keys)) != len(keys):
        raise ValueError("statement gaps contain duplicates")
    return sorted(gaps, key=canonical_json)


def build_input_set_sha256(
    *,
    source_refs: Iterable[dict[str, Any]],
    knowledge_revision_refs: Iterable[str],
    relation_revision_refs: Iterable[str],
    valid_from: str | None,
    valid_to: str | None,
    statement_type: str,
    support_status: str,
    limitation: str | None,
    gaps: Iterable[dict[str, str]],
) -> str:
    """Return the canonical evidence/input binding digest.

    Source references are sorted by canonical JSON; all ID sets are sorted and
    deduplicated by the validators before this helper is called.  The shape is
    deliberately explicit so changing a temporal interval, label, limitation,
    or bounded gap changes the digest.
    """

    source_values = [_canonical_source_reference(item) for item in source_refs]
    canonical_sources_by_key = {canonical_json(item): item for item in source_values}
    canonical_sources = sorted(canonical_sources_by_key.values(), key=canonical_json)
    knowledge = sorted(set(knowledge_revision_refs))
    relations = sorted(set(relation_revision_refs))
    gap_values = [_canonical_gap(item) for item in gaps]
    canonical_gaps_by_key = {canonical_json(item): item for item in gap_values}
    canonical_gaps = sorted(canonical_gaps_by_key.values(), key=canonical_json)
    payload = {
        "source_refs": canonical_sources,
        "knowledge_revision_refs": knowledge,
        "relation_revision_refs": relations,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "statement_type": statement_type,
        "support_status": support_status,
        "limitation": limitation,
        "gaps": canonical_gaps,
    }
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def validate_statement(
    value: dict[str, Any],
    *,
    body: str | None = None,
    knowledge_revision_id: str | None = None,
    require_statement_id: bool = False,
) -> dict[str, Any]:
    """Validate and canonicalize one publication or persisted statement.

    A publication plan may omit ``knowledge_revision_id`` and ``statement_id``
    because the revision is assigned by the coordinator.  Persisted rows must
    provide both, and are checked against the deterministic ID formula.
    """

    if not isinstance(value, dict):
        raise ValueError("statement is not an object")
    allowed = {
        "schema_version",
        "statement_id",
        "knowledge_revision_id",
        "ordinal",
        "char_start",
        "char_end",
        "statement_text",
        "statement_sha256",
        "statement_type",
        "support_status",
        "source_refs",
        "knowledge_revision_refs",
        "relation_revision_refs",
        "valid_from",
        "valid_to",
        "limitation",
        "gaps",
        "input_set_sha256",
    }
    if set(value) - allowed:
        raise ValueError("statement contains unknown fields")
    if "schema_version" in value and value["schema_version"] != "deeplaw.knowledge-statement/v1":
        raise ValueError("statement schema_version is invalid")
    ordinal = value.get("ordinal")
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
        raise ValueError("statement ordinal is invalid")
    text = _bounded_text(
        value.get("statement_text"),
        field="statement_text",
        maximum=MAX_STATEMENT_TEXT_CHARS,
    )
    actual_sha = statement_sha256(text)
    supplied_sha = _digest(value.get("statement_sha256"), field="statement_sha256")
    if supplied_sha != actual_sha:
        raise ValueError("statement text hash does not match its exact text")
    statement_type = value.get("statement_type")
    support_status = value.get("support_status")
    if statement_type not in _STATEMENT_TYPES:
        raise ValueError("statement_type is invalid")
    if support_status not in _SUPPORT_STATUSES:
        raise ValueError("support_status is invalid")
    source_refs_raw = value.get("source_refs")
    if not isinstance(source_refs_raw, list) or len(source_refs_raw) > MAX_REFS_PER_STATEMENT:
        raise ValueError("statement source refs exceed their bound")
    source_refs = [_canonical_source_reference(item) for item in source_refs_raw]
    source_keys = [canonical_json(item) for item in source_refs]
    if len(set(source_keys)) != len(source_keys):
        raise ValueError("statement source refs contain duplicates")
    source_refs.sort(key=canonical_json)
    knowledge_refs = _canonical_refs(
        value.get("knowledge_revision_refs"),
        field="statement knowledge refs",
        pattern=_KNOWLEDGE_REVISION,
    )
    relation_refs = _canonical_refs(
        value.get("relation_revision_refs"),
        field="statement relation refs",
        pattern=_RELATION_REVISION,
    )
    valid_from = value.get("valid_from")
    valid_to = value.get("valid_to")
    if valid_from is not None:
        valid_from = canonical_timestamp(valid_from, field="valid_from")
    if valid_to is not None:
        valid_to = canonical_timestamp(valid_to, field="valid_to")
    if valid_from is not None and valid_to is not None and valid_from >= valid_to:
        raise ValueError("statement valid interval is invalid")
    limitation = value.get("limitation")
    if limitation is not None:
        limitation = _bounded_text(limitation, field="limitation", maximum=4000)
    gaps = _canonical_gaps(value.get("gaps"))
    evidence_count = len(source_refs) + len(knowledge_refs) + len(relation_refs)
    if support_status == "not_applicable" and evidence_count:
        raise ValueError("not_applicable statement cannot carry evidence")
    if statement_type == "limitation" and support_status == "supported" and not limitation:
        raise ValueError("supported limitation statement requires limitation text")
    if statement_type == "interpretation" and support_status == "supported":
        # The type itself is the durable label; no confidence field can promote it
        # to factual evidence.
        pass
    if statement_type == "factual" and support_status == "supported" and not source_refs:
        raise ValueError("supported factual statement requires an exact source ref")
    if support_status == "contested" and len(source_refs) < 2 and not gaps:
        raise ValueError("contested statement requires two exact source refs or a bounded gap")
    input_set = _digest(value.get("input_set_sha256"), field="input_set_sha256")
    expected_input_set = build_input_set_sha256(
        source_refs=source_refs,
        knowledge_revision_refs=knowledge_refs,
        relation_revision_refs=relation_refs,
        valid_from=valid_from,
        valid_to=valid_to,
        statement_type=statement_type,
        support_status=support_status,
        limitation=limitation,
        gaps=gaps,
    )
    if input_set != expected_input_set:
        raise ValueError("statement input-set digest is invalid")
    revision = knowledge_revision_id or value.get("knowledge_revision_id")
    if revision is not None and not _REVISION.fullmatch(revision):
        raise ValueError("statement knowledge revision ID is invalid")
    supplied_revision = value.get("knowledge_revision_id")
    if supplied_revision is not None and revision != supplied_revision:
        raise ValueError("statement revision binding is inconsistent")
    expected_id = statement_id(revision, ordinal, supplied_sha) if revision else None
    supplied_id = value.get("statement_id")
    if require_statement_id and supplied_id is None:
        raise ValueError("persisted statement requires statement_id")
    if supplied_id is not None and (
        not isinstance(supplied_id, str) or supplied_id != expected_id
    ):
        raise ValueError("statement_id does not match its deterministic identity")
    char_start = value.get("char_start")
    char_end = value.get("char_end")
    if (char_start is not None or char_end is not None) and (
        not isinstance(char_start, int)
        or isinstance(char_start, bool)
        or not isinstance(char_end, int)
        or isinstance(char_end, bool)
        or char_start < 0
        or char_end <= char_start
        or char_end - char_start != len(text)
    ):
        raise ValueError("statement body span is invalid")
    if body is not None:
        _bounded_text(body, field="Knowledge body", maximum=200_000)
        if char_start is not None and char_end is not None:
            if body[char_start:char_end] != text:
                raise ValueError("statement body span does not match exact codepoint text")
        else:
            start = body.find(text)
            if start < 0 or body.find(text, start + 1) >= 0:
                raise ValueError("statement text must occur exactly once in the final body")
    result = {
        "schema_version": "deeplaw.knowledge-statement/v1",
        "statement_id": expected_id if expected_id is not None else supplied_id,
        "knowledge_revision_id": revision,
        "ordinal": ordinal,
        "statement_text": text,
        "statement_sha256": supplied_sha,
        "statement_type": statement_type,
        "support_status": support_status,
        "source_refs": source_refs,
        "knowledge_revision_refs": knowledge_refs,
        "relation_revision_refs": relation_refs,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "limitation": limitation,
        "gaps": gaps,
        "input_set_sha256": input_set,
    }
    if require_statement_id:
        _validate_contract("knowledge-statement.v1.schema.json", result)
    if "char_start" in value or "char_end" in value:
        result["char_start"] = char_start
        result["char_end"] = char_end
    return result


def validate_statement_plans(
    plans: list[dict[str, Any]],
    *,
    action_bodies: dict[tuple[str, int], str],
    action_kinds: dict[tuple[str, int], str],
    forced_kinds: Iterable[str] = _FORCED_KINDS,
) -> dict[tuple[str, int], list[dict[str, Any]]]:
    """Validate v3 statement plans against final action bodies.

    ``action_bodies`` and ``action_kinds`` are keyed by ``(packet_id,
    object_action_ordinal)``.  This keeps pre-commit plans independent of a
    revision ID while still making every forced object fail closed.
    """

    if not isinstance(plans, list) or len(plans) > MAX_STATEMENTS_PER_REVISION * 100:
        raise ValueError("statement plan inventory exceeds its bound")
    by_target: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for plan in plans:
        if not isinstance(plan, dict):
            raise ValueError("statement plan is not an object")
        target = (plan.get("packet_id"), plan.get("object_action_ordinal"))
        if not isinstance(target[0], str) or not isinstance(target[1], int):
            raise ValueError("statement plan target is invalid")
        body = action_bodies.get(target)
        if body is None:
            raise ValueError("statement plan targets an unknown object action")
        if target in by_target:
            raise ValueError("statement plan target is duplicated")
        statements = plan.get("statements")
        if not isinstance(statements, list) or len(statements) > MAX_STATEMENTS_PER_REVISION:
            raise ValueError("statement plan exceeds its per-revision bound")
        if not statements and action_kinds.get(target) in set(forced_kinds):
            raise ValueError("forced Knowledge kind requires at least one statement")
        normalized: list[dict[str, Any]] = []
        for item in statements:
            if "char_start" not in item or "char_end" not in item:
                raise ValueError("statement plan requires an explicit body span")
            normalized.append(validate_statement(item, body=body))
        ordinals = [item["ordinal"] for item in normalized]
        if ordinals != list(range(1, len(normalized) + 1)):
            raise ValueError("statement ordinals must be contiguous and one-based")
        hashes = [item["statement_sha256"] for item in normalized]
        if len(hashes) != len(set(hashes)):
            raise ValueError("statement text/hash is duplicated")
        spans = sorted(
            (item["char_start"], item["char_end"], item["ordinal"])
            for item in normalized
        )
        prior_end = -1
        for start, end, _ordinal in spans:
            if not isinstance(start, int) or isinstance(start, bool) or start < 0:
                raise ValueError("statement char_start is invalid")
            if not isinstance(end, int) or isinstance(end, bool) or end <= start:
                raise ValueError("statement char_end is invalid")
            if start < prior_end:
                raise ValueError("statement spans overlap")
            prior_end = end
        by_target[target] = normalized
    for target, kind in action_kinds.items():
        if kind in set(forced_kinds) and target not in by_target:
            raise ValueError("forced Knowledge kind is missing its statement plan")
    return by_target


def _bounded_artifact(
    store: AutonomousKnowledgeStore,
    digest: str,
    role: str,
) -> dict[str, Any]:
    row = store.connection.execute(
        """
        SELECT artifact_role, byte_size
        FROM source_compilation_artifacts_v1
        WHERE artifact_sha256 = ?
        """,
        (digest,),
    ).fetchone()
    if row is None or row["artifact_role"] != role:
        raise RuntimeError("statement evidence artifact binding is invalid")
    if row["byte_size"] > MAX_EVIDENCE_ARTIFACT_BYTES:
        raise RuntimeError("statement evidence artifact exceeds its read bound")
    payload = read_compilation_artifact(
        store.connection,
        store.root,
        digest,
        role=role,
        maximum_bytes=MAX_EVIDENCE_ARTIFACT_BYTES,
    )
    value = strict_json_loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError("statement evidence artifact is not an object")
    return value


def _statement_freshness(
    store: AutonomousKnowledgeStore,
    *,
    knowledge_revision_id: str,
    source_refs: list[dict[str, Any]],
) -> str:
    """Return the worst recorded direct-source freshness for one statement.

    Statement evidence is immutable, while source successors can change the
    admissibility of a dependent Knowledge Revision.  The direct dependency
    rows are the deterministic bridge between those domains: a missing row is
    treated as ``unknown`` (fail closed for ``current_supported``), and the
    worst freshness wins when a statement cites multiple fragments.
    """

    freshness = "fresh"
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
                knowledge_revision_id,
                reference["source_revision_id"],
                reference["fragment_id"],
            ),
        ).fetchone()
        candidate = row["freshness"] if row is not None else "unknown"
        if candidate not in _FRESHNESS_ORDER:
            candidate = "unknown"
        if _FRESHNESS_ORDER[candidate] > _FRESHNESS_ORDER[freshness]:
            freshness = candidate
    return freshness


class StatementEvidenceStore:
    """Bounded, read-only statement/map/receipt access for one local vault."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().absolute()

    def statement(self, statement_id_value: str) -> dict[str, Any]:
        if not isinstance(statement_id_value, str) or not _STATEMENT_ID.fullmatch(
            statement_id_value
        ):
            raise ValueError("statement_id is invalid")
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            row = store.connection.execute(
                """
                SELECT knowledge_statements_v1.statement_artifact_sha256,
                       knowledge_statements_v1.statement_json,
                       knowledge_statements_v1.knowledge_revision_id,
                       knowledge_statements_v1.statement_text,
                       knowledge_statements_v1.statement_sha256,
                       knowledge_statements_v1.statement_type,
                       knowledge_statements_v1.support_status,
                       knowledge_statements_v1.valid_from,
                       knowledge_statements_v1.valid_to,
                       knowledge_statements_v1.limitation,
                       knowledge_statements_v1.input_set_sha256,
                       objects.current_revision_id
                FROM knowledge_statements_v1
                JOIN knowledge_revisions_v3 AS revisions
                  ON revisions.revision_id = knowledge_statements_v1.knowledge_revision_id
                LEFT JOIN knowledge_objects_v3 AS objects
                  ON objects.knowledge_id = revisions.knowledge_id
                WHERE knowledge_statements_v1.statement_id = ?
                """,
                (statement_id_value,),
            ).fetchone()
            if row is None:
                return {"status": "missing", "statement_id": statement_id_value}
            value = _bounded_artifact(store, row["statement_artifact_sha256"], "statement")
            _validate_contract("knowledge-statement.v1.schema.json", value)
            if (
                value.get("statement_id") != statement_id_value
                or value.get("knowledge_revision_id") != row["knowledge_revision_id"]
                or value.get("statement_text") != row["statement_text"]
                or value.get("statement_sha256") != row["statement_sha256"]
                or value.get("statement_type") != row["statement_type"]
                or value.get("support_status") != row["support_status"]
                or value.get("valid_from") != row["valid_from"]
                or value.get("valid_to") != row["valid_to"]
                or value.get("limitation") != row["limitation"]
                or value.get("input_set_sha256") != row["input_set_sha256"]
                or canonical_json(value) != row["statement_json"]
            ):
                raise RuntimeError("statement artifact identity is inconsistent")
            validate_statement(value, require_statement_id=True)
            current_revision_id = row["current_revision_id"]
            is_current = current_revision_id == row["knowledge_revision_id"]
            freshness = _statement_freshness(
                store,
                knowledge_revision_id=row["knowledge_revision_id"],
                source_refs=value["source_refs"],
            )
            status = "historical" if not is_current else (
                "present" if freshness == "fresh" else freshness
            )
            return {
                "status": status,
                "current_revision_id": current_revision_id,
                "is_current": is_current,
                "freshness": freshness,
                "current_supported": bool(
                    is_current
                    and freshness == "fresh"
                    and value["support_status"] == "supported"
                ),
                **value,
            }

    def map_for_revision(self, knowledge_revision_id: str) -> dict[str, Any]:
        if not _REVISION.fullmatch(knowledge_revision_id):
            raise ValueError("knowledge_revision_id is invalid")
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            revision_row = store.connection.execute(
                """
                SELECT objects.current_revision_id
                FROM knowledge_revisions_v3 AS revisions
                JOIN knowledge_objects_v3 AS objects USING(knowledge_id)
                WHERE revisions.revision_id = ?
                """,
                (knowledge_revision_id,),
            ).fetchone()
            current_revision_id = (
                revision_row["current_revision_id"] if revision_row is not None else None
            )
            is_current = current_revision_id == knowledge_revision_id
            rows = store.connection.execute(
                """
                SELECT maps.statement_id, maps.map_artifact_sha256, maps.map_sha256,
                       maps.map_json, maps.statement_sha256, maps.input_set_sha256,
                       objects.current_revision_id
                FROM statement_evidence_maps_v1 AS maps
                LEFT JOIN knowledge_objects_v3 AS objects
                  ON objects.current_revision_id = maps.knowledge_revision_id
                WHERE knowledge_revision_id = ? ORDER BY ordinal
                """,
                (knowledge_revision_id,),
            ).fetchall()
            if not rows:
                return {
                    "status": "missing",
                    "knowledge_revision_id": knowledge_revision_id,
                    "current_revision_id": current_revision_id,
                    "is_current": is_current,
                    "freshness": "fresh",
                    "current_supported": False,
                    "maps": [],
                }
            maps: list[dict[str, Any]] = []
            freshness = "fresh"
            all_supported = True
            for row in rows:
                value = _bounded_artifact(store, row["map_artifact_sha256"], "statement_map")
                _validate_contract("statement-evidence-map.v1.schema.json", value)
                if (
                    value.get("statement_id") != row["statement_id"]
                    or value.get("statement_sha256") != row["statement_sha256"]
                    or value.get("input_set_sha256") != row["input_set_sha256"]
                    or row["map_sha256"] != row["map_artifact_sha256"]
                    or canonical_json(value) != row["map_json"]
                ):
                    raise RuntimeError("statement evidence map identity is inconsistent")
                statement_row = store.connection.execute(
                    """
                    SELECT statement_artifact_sha256
                    FROM knowledge_statements_v1
                    WHERE statement_id = ?
                    """,
                    (row["statement_id"],),
                ).fetchone()
                if statement_row is None:
                    raise RuntimeError("statement evidence map has no statement row")
                statement_value = _bounded_artifact(
                    store, statement_row["statement_artifact_sha256"], "statement"
                )
                _validate_contract("knowledge-statement.v1.schema.json", statement_value)
                candidate = _statement_freshness(
                    store,
                    knowledge_revision_id=knowledge_revision_id,
                    source_refs=statement_value["source_refs"],
                )
                if _FRESHNESS_ORDER[candidate] > _FRESHNESS_ORDER[freshness]:
                    freshness = candidate
                all_supported = all_supported and (
                    statement_value["support_status"] == "supported"
                )
                maps.append(value)
            status = "historical" if not is_current else (
                "current" if freshness == "fresh" else freshness
            )
            return {
                "status": status,
                "knowledge_revision_id": knowledge_revision_id,
                "current_revision_id": current_revision_id,
                "is_current": is_current,
                "freshness": freshness,
                "current_supported": bool(
                    is_current and freshness == "fresh" and all_supported
                ),
                "maps": maps,
            }

    def receipt(self, statement_id_value: str) -> dict[str, Any]:
        if not isinstance(statement_id_value, str) or not _STATEMENT_ID.fullmatch(
            statement_id_value
        ):
            raise ValueError("statement_id is invalid")
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            row = store.connection.execute(
                """
                SELECT receipts.*, statements.statement_sha256 AS statement_row_sha256,
                       statements.statement_type AS statement_row_type,
                       statements.support_status AS statement_row_support,
                       statements.valid_from AS statement_row_valid_from,
                       statements.valid_to AS statement_row_valid_to,
                       statements.limitation AS statement_row_limitation,
                       statements.input_set_sha256 AS statement_row_input
                FROM statement_evidence_receipts_v1 AS receipts
                JOIN knowledge_statements_v1 AS statements
                  ON statements.statement_id = receipts.statement_id
                WHERE receipts.statement_id = ?
                """,
                (statement_id_value,),
            ).fetchone()
            if row is None:
                return {"status": "missing", "statement_id": statement_id_value}
            value = _bounded_artifact(
                store, row["artifact_sha256"], "statement_evidence_receipt"
            )
            _validate_contract("statement-evidence-receipt.v1.schema.json", value)
            if (
                value.get("statement_id") != statement_id_value
                or value.get("receipt_sha256") != row["receipt_sha256"]
                or value.get("knowledge_revision_id") != row["knowledge_revision_id"]
                or value.get("map_sha256") != row["map_sha256"]
                or value.get("statement_sha256") != row["statement_row_sha256"]
                or value.get("statement_type") != row["statement_row_type"]
                or value.get("support_status") != row["statement_row_support"]
                or value.get("valid_from") != row["statement_row_valid_from"]
                or value.get("valid_to") != row["statement_row_valid_to"]
                or value.get("limitation") != row["statement_row_limitation"]
                or value.get("input_set_sha256") != row["statement_row_input"]
                or value.get("commit_audit_head") != row["commit_audit_head"]
            ):
                raise RuntimeError("statement evidence receipt identity is inconsistent")
            statement_artifact_row = store.connection.execute(
                """
                SELECT statement_artifact_sha256
                FROM knowledge_statements_v1
                WHERE statement_id = ?
                """,
                (statement_id_value,),
            ).fetchone()
            if statement_artifact_row is None:
                raise RuntimeError("statement evidence receipt has no statement row")
            statement_value = _bounded_artifact(
                store, statement_artifact_row["statement_artifact_sha256"], "statement"
            )
            for field in (
                "statement_type",
                "support_status",
                "valid_from",
                "valid_to",
                "limitation",
                "source_refs",
                "knowledge_revision_refs",
                "relation_revision_refs",
                "gaps",
            ):
                if value.get(field) != statement_value.get(field):
                    raise RuntimeError("statement evidence receipt references are inconsistent")
            receipt_body = dict(value)
            supplied_digest = receipt_body.pop("receipt_sha256", None)
            if supplied_digest != sha256_bytes(canonical_json(receipt_body).encode("utf-8")):
                raise RuntimeError("statement evidence receipt digest is invalid")
            return {"status": "present", **value}
