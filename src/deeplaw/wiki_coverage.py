"""Pure, bounded Living Wiki Coverage Spec and Gap evaluation.

The module is intentionally a governance seam.  It accepts an exact, caller-provided inventory
and specification, validates both, and returns deterministic gap records.  It never reads files,
calls a model, writes projections, or persists state.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from math import ceil
from typing import Any

from .knowledge_models import canonical_timestamp
from .util import canonical_json, sha256_bytes, stable_id

COVERAGE_SPEC_SCHEMA = "deeplaw.living-wiki-coverage-spec/v1"
COVERAGE_GAP_SCHEMA = "deeplaw.living-wiki-coverage-gap/v1"
COVERAGE_INVENTORY_SCHEMA = "deeplaw.living-wiki-coverage-inventory/v1"

CANONICAL_PAGE_FAMILIES = (
    "sources",
    "source_evidence",
    "source_summary",
    "concepts",
    "entities",
    "events",
    "claims",
    "procedures",
    "syntheses",
    "legal",
    "memos",
    "statutes",
    "case_law",
    "evidence",
    "memory",
    "guides",
    "codemap",
    "index",
    "moc",
    "glossary",
    "conflicts",
    "gaps",
    "review_queue",
    "recent_changes",
    "activity",
    "communities",
    "evidence_pack",
    "preferences",
    "skills",
)
CANONICAL_SEMANTIC_DUTIES = (
    "answer",
    "recommend",
    "explain",
    "define",
    "compare",
    "verify",
    "quote",
    "cite",
    "refresh",
    "invalidate",
    "update",
    "correct",
    "reconcile",
    "execute",
    "remember",
)
GAP_STATUSES = ("missing", "not_applicable", "unavailable", "over_budget")
CONTENT_ROLES = ("source_evidence", "agent_derived_summary", "knowledge", "navigation")
ORIGINS = ("official", "user_source", "agent_derived", "external_import")
SCOPES = ("personal", "project", "domain")
INPUT_KINDS = (
    "source_revision",
    "knowledge_revision",
    "relation_revision",
    "repository_revision",
)

# Limits are deliberately finite and below the maximums used by the projection/runtime layers.
MAX_ID_CHARS = 256
MAX_SELECTOR_CHARS = 128
MAX_PAGE_PATH_CHARS = 2_000
MAX_PAGES = 200_000
MAX_INPUTS = 200_000
MAX_EDGES = 500_000
MAX_GAPS = 100_000
MAX_BYTES = 1_073_741_824
MAX_PAGE_BYTES = 262_144
MAX_REFS = 512
MAX_INLINE_PAGE_IDS = 512
MAX_SHARDS = MAX_PAGES
MAX_PAGES_PER_SHARD = 2_000
MAX_BYTES_PER_SHARD = 262_144
DEFAULT_ESTIMATED_PAGE_BYTES = 1

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PAGE_FAMILIES = frozenset(CANONICAL_PAGE_FAMILIES)
_DUTIES = frozenset(CANONICAL_SEMANTIC_DUTIES)
_SCOPES = frozenset(SCOPES)
_INPUT_KINDS = frozenset(INPUT_KINDS)
_ROLES = frozenset(CONTENT_ROLES)
_ORIGINS = frozenset(ORIGINS)
_GAP_REASONS = {
    "missing": "required_page_or_revision_absent",
    "not_applicable": "spec_declared_not_applicable",
    "unavailable": "canonical_input_unavailable_or_unverified",
    "over_budget": "hard_page_or_byte_budget_exceeded",
}
_INPUT_REQUIRED_FAMILIES = frozenset({"guides", "codemap"})
_SOURCE_DERIVED_FAMILIES = frozenset({"source_evidence", "source_summary"})


class CoverageSpecError(ValueError):
    """Raised when a Coverage Spec, inventory, or gap is not admissible."""


def _mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CoverageSpecError(f"{field} must be an object")
    return dict(value)


def _unknown(value: Mapping[str, Any], allowed: set[str], *, field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise CoverageSpecError(f"{field} contains unknown fields: {sorted(unknown)}")


def _require(value: Mapping[str, Any], fields: set[str], *, field: str) -> None:
    if not fields <= set(value):
        missing = sorted(fields - set(value))
        raise CoverageSpecError(f"{field} is missing required fields: {missing}")


def _string(value: Any, *, field: str, maximum: int, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or value.strip() != value:
        raise CoverageSpecError(f"{field} must be a bounded canonical string")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise CoverageSpecError(f"{field} contains a forbidden control character")
    if identifier and not _ID.fullmatch(value):
        raise CoverageSpecError(f"{field} must be a stable identifier")
    return value


def _id(value: Any, *, field: str) -> str:
    return _string(value, field=field, maximum=MAX_ID_CHARS, identifier=True)


def _selector(value: Any, *, field: str) -> str:
    return _string(value, field=field, maximum=MAX_SELECTOR_CHARS)


def _topic(value: Any, *, field: str) -> str:
    return _selector(value, field=field)


def _scope(value: Any, *, field: str) -> str:
    if value not in _SCOPES:
        raise CoverageSpecError(f"{field} must be personal, project, or domain")
    return str(value)


def _family(value: Any, *, field: str) -> str:
    if value not in _PAGE_FAMILIES:
        raise CoverageSpecError(f"{field} is not a canonical page family")
    return str(value)


def _duty_name(value: Any, *, field: str) -> str:
    if value not in _DUTIES:
        raise CoverageSpecError(f"{field} is not a canonical semantic duty")
    return str(value)


def _role(value: Any, *, field: str) -> str:
    if value not in _ROLES:
        raise CoverageSpecError(f"{field} is not a permitted content role")
    return str(value)


def _origin(value: Any, *, field: str) -> str:
    if value not in _ORIGINS:
        raise CoverageSpecError(f"{field} is not a permitted origin")
    return str(value)


def _input_kind(value: Any, *, field: str) -> str:
    if value not in _INPUT_KINDS:
        raise CoverageSpecError(f"{field} is not a permitted input kind")
    return str(value)


def _sha(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise CoverageSpecError(f"{field} must be lowercase SHA-256")
    return value


def _timestamp(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise CoverageSpecError(f"{field} must be a canonical timestamp")
    try:
        canonical = canonical_timestamp(value, field=field)
    except ValueError as error:
        raise CoverageSpecError(str(error)) from error
    if value != canonical:
        raise CoverageSpecError(f"{field} must use canonical UTC timestamp form")
    return canonical


def _integer(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CoverageSpecError(f"{field} is outside its hard bound")
    return value


def _boolean(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise CoverageSpecError(f"{field} must be a boolean")
    return value


def _relative_path(value: Any, *, field: str) -> str:
    path = _string(value, field=field, maximum=MAX_PAGE_PATH_CHARS)
    parts = path.replace("\\", "/").split("/")
    if (
        path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in parts)
        or path.startswith("~")
        or (len(path) > 1 and path[1] == ":")
    ):
        raise CoverageSpecError(f"{field} must be a safe relative path")
    return path


def _array(
    value: Any,
    *,
    field: str,
    maximum: int,
    item: str,
    allow_empty: bool = True,
) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CoverageSpecError(f"{field} must be an array")
    if len(value) > maximum or (not allow_empty and not value):
        raise CoverageSpecError(f"{field} exceeds its hard bound")
    result = list(value)
    if item != "record":
        try:
            duplicate_count = len({canonical_json(entry) for entry in result})
        except (TypeError, ValueError) as error:
            raise CoverageSpecError(f"{field} contains an unsupported value") from error
        if duplicate_count != len(result):
            raise CoverageSpecError(f"{field} must not contain duplicate records")
    if item == "id":
        return [_id(entry, field=f"{field}[]") for entry in result]
    if item == "scope":
        return [_scope(entry, field=f"{field}[]") for entry in result]
    if item == "topic":
        return [_topic(entry, field=f"{field}[]") for entry in result]
    if item == "family":
        return [_family(entry, field=f"{field}[]") for entry in result]
    return result


def _validate_owner_confirmation(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    result = _mapping(value, field="owner_confirmation")
    _unknown(
        result,
        {"receipt_id", "confirmed_at", "confirmed_by", "grants_authority"},
        field="owner_confirmation",
    )
    _require(
        result,
        {"receipt_id", "confirmed_at", "confirmed_by", "grants_authority"},
        field="owner_confirmation",
    )
    normalized: dict[str, Any] = {
        "receipt_id": _id(result["receipt_id"], field="owner_confirmation.receipt_id"),
        "confirmed_at": _timestamp(result["confirmed_at"], field="owner_confirmation.confirmed_at"),
        "confirmed_by": _id(result["confirmed_by"], field="owner_confirmation.confirmed_by"),
        "grants_authority": _boolean(
            result["grants_authority"], field="owner_confirmation.grants_authority"
        ),
    }
    if normalized["grants_authority"]:
        raise CoverageSpecError("owner confirmation cannot grant Authority")
    return normalized


def _validate_duty(value: Any, *, field: str) -> dict[str, Any]:
    duty = _mapping(value, field=field)
    _unknown(
        duty,
        {
            "duty",
            "applicability",
            "reason",
            "topics",
            "scopes",
            "page_families",
            "required_input_revision_refs",
            "estimated_page_bytes",
        },
        field=field,
    )
    _require(duty, {"duty", "applicability"}, field=field)
    applicability = duty["applicability"]
    if applicability not in {"required", "not_applicable"}:
        raise CoverageSpecError(f"{field}.applicability is invalid")
    has_reason = "reason" in duty
    if applicability == "not_applicable" and not has_reason:
        raise CoverageSpecError(f"{field}.reason is required for not_applicable")
    if applicability == "required" and has_reason:
        raise CoverageSpecError(f"{field}.reason is only allowed for not_applicable")
    normalized: dict[str, Any] = {
        "duty": _duty_name(duty["duty"], field=f"{field}.duty"),
        "applicability": applicability,
    }
    if has_reason:
        normalized["reason"] = _string(duty["reason"], field=f"{field}.reason", maximum=128)
    for key, item, maximum in (
        ("topics", "topic", 512),
        ("scopes", "scope", 3),
        ("page_families", "family", 64),
        ("required_input_revision_refs", "id", MAX_REFS),
    ):
        if key not in duty:
            continue
        normalized[key] = _array(duty[key], field=f"{field}.{key}", maximum=maximum, item=item)
    if "estimated_page_bytes" in duty:
        normalized["estimated_page_bytes"] = _integer(
            duty["estimated_page_bytes"],
            field=f"{field}.estimated_page_bytes",
            minimum=1,
            maximum=16 * 1024 * 1024,
        )
    return normalized


def _validate_hierarchy(value: Any) -> dict[str, Any]:
    hierarchy = _mapping(value, field="hierarchy")
    _unknown(hierarchy, {"roots", "edges"}, field="hierarchy")
    _require(hierarchy, {"roots", "edges"}, field="hierarchy")
    roots = _array(hierarchy["roots"], field="hierarchy.roots", maximum=64, item="family")
    edges_raw = _array(hierarchy["edges"], field="hierarchy.edges", maximum=512, item="record")
    edges: list[dict[str, str]] = []
    for index, raw in enumerate(edges_raw):
        edge = _mapping(raw, field=f"hierarchy.edges[{index}]")
        _unknown(edge, {"parent", "child"}, field=f"hierarchy.edges[{index}]")
        _require(edge, {"parent", "child"}, field=f"hierarchy.edges[{index}]")
        edges.append(
            {
                "parent": _family(edge["parent"], field=f"hierarchy.edges[{index}].parent"),
                "child": _family(edge["child"], field=f"hierarchy.edges[{index}].child"),
            }
        )
    edge_keys = [(edge["parent"], edge["child"]) for edge in edges]
    if len(edge_keys) != len(set(edge_keys)):
        raise CoverageSpecError("hierarchy edges must be unique")
    if any(parent == child for parent, child in edge_keys):
        raise CoverageSpecError("hierarchy edges cannot be self-referential")
    children: dict[str, set[str]] = {}
    for parent, child in edge_keys:
        children.setdefault(parent, set()).add(child)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise CoverageSpecError("hierarchy must be acyclic")
        if node in visited:
            return
        visiting.add(node)
        for child in children.get(node, set()):
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(children):
        visit(node)
    return {"roots": roots, "edges": edges}


def _validate_guided_tours(value: Any) -> list[dict[str, Any]]:
    rows = _array(value, field="guided_tours", maximum=128, item="record")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        tour = _mapping(raw, field=f"guided_tours[{index}]")
        _unknown(tour, {"tour_id", "page_families", "topics"}, field=f"guided_tours[{index}]")
        _require(tour, {"tour_id", "page_families"}, field=f"guided_tours[{index}]")
        tour_id = _id(tour["tour_id"], field=f"guided_tours[{index}].tour_id")
        if tour_id in seen:
            raise CoverageSpecError("guided tour IDs must be unique")
        seen.add(tour_id)
        families = _array(
            tour["page_families"],
            field=f"guided_tours[{index}].page_families",
            maximum=64,
            item="family",
            allow_empty=False,
        )
        normalized: dict[str, Any] = {"tour_id": tour_id, "page_families": families}
        if "topics" in tour:
            normalized["topics"] = _array(
                tour["topics"], field=f"guided_tours[{index}].topics", maximum=128, item="topic"
            )
        result.append(normalized)
    return result


def _validate_codemap(value: Any) -> dict[str, Any]:
    codemap = _mapping(value, field="codemap")
    _unknown(
        codemap,
        {"enabled", "required_input_revision_refs", "required_edge_ids"},
        field="codemap",
    )
    _require(
        codemap,
        {"enabled", "required_input_revision_refs", "required_edge_ids"},
        field="codemap",
    )
    return {
        "enabled": _boolean(codemap["enabled"], field="codemap.enabled"),
        "required_input_revision_refs": _array(
            codemap["required_input_revision_refs"],
            field="codemap.required_input_revision_refs",
            maximum=MAX_REFS,
            item="id",
        ),
        "required_edge_ids": _array(
            codemap["required_edge_ids"],
            field="codemap.required_edge_ids",
            maximum=MAX_REFS,
            item="id",
        ),
    }


def _validate_shard_bounds(value: Any) -> dict[str, int]:
    bounds = _mapping(value, field="shard_bounds")
    _unknown(
        bounds,
        {"max_shards", "max_pages_per_shard", "max_bytes_per_shard"},
        field="shard_bounds",
    )
    _require(
        bounds,
        {"max_shards", "max_pages_per_shard", "max_bytes_per_shard"},
        field="shard_bounds",
    )
    return {
        "max_shards": _integer(
            bounds["max_shards"], field="shard_bounds.max_shards", minimum=1, maximum=MAX_SHARDS
        ),
        "max_pages_per_shard": _integer(
            bounds["max_pages_per_shard"],
            field="shard_bounds.max_pages_per_shard",
            minimum=1,
            maximum=MAX_PAGES_PER_SHARD,
        ),
        "max_bytes_per_shard": _integer(
            bounds["max_bytes_per_shard"],
            field="shard_bounds.max_bytes_per_shard",
            minimum=1,
            maximum=MAX_BYTES_PER_SHARD,
        ),
    }


def validate_coverage_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a canonical, non-authoritative Coverage Spec copy."""

    value = _mapping(spec, field="coverage spec")
    allowed = {
        "schema_version",
        "spec_id",
        "revision_id",
        "status",
        "generated_at",
        "transaction_head",
        "audit_head",
        "transaction_id",
        "owner_confirmation",
        "scopes",
        "topics",
        "page_families",
        "hierarchy",
        "guided_tours",
        "codemap",
        "duties",
        "max_pages",
        "max_bytes",
        "shard_bounds",
    }
    _unknown(value, allowed, field="coverage spec")
    required = {
        "schema_version",
        "spec_id",
        "revision_id",
        "status",
        "generated_at",
        "transaction_head",
        "audit_head",
        "owner_confirmation",
        "scopes",
        "topics",
        "page_families",
        "hierarchy",
        "guided_tours",
        "codemap",
        "duties",
        "max_pages",
        "max_bytes",
        "shard_bounds",
    }
    _require(value, required, field="coverage spec")
    if value["schema_version"] != COVERAGE_SPEC_SCHEMA:
        raise CoverageSpecError("coverage spec schema_version is invalid")
    if value["status"] not in {"draft", "owner_confirmed", "retired"}:
        raise CoverageSpecError("coverage spec status is invalid")
    normalized: dict[str, Any] = {
        "schema_version": COVERAGE_SPEC_SCHEMA,
        "spec_id": _id(value["spec_id"], field="spec_id"),
        "revision_id": _id(value["revision_id"], field="revision_id"),
        "status": value["status"],
        "generated_at": _timestamp(value["generated_at"], field="generated_at"),
        "transaction_head": _sha(value["transaction_head"], field="transaction_head"),
        "audit_head": _sha(value["audit_head"], field="audit_head"),
        "owner_confirmation": _validate_owner_confirmation(value["owner_confirmation"]),
        "scopes": _array(
            value["scopes"], field="scopes", maximum=3, item="scope", allow_empty=False
        ),
        "topics": _array(
            value["topics"], field="topics", maximum=512, item="topic", allow_empty=False
        ),
        "page_families": _array(
            value["page_families"],
            field="page_families",
            maximum=64,
            item="family",
            allow_empty=False,
        ),
        "hierarchy": _validate_hierarchy(value["hierarchy"]),
        "guided_tours": _validate_guided_tours(value["guided_tours"]),
        "codemap": _validate_codemap(value["codemap"]),
        "max_pages": _integer(value["max_pages"], field="max_pages", minimum=0, maximum=MAX_PAGES),
        "max_bytes": _integer(value["max_bytes"], field="max_bytes", minimum=0, maximum=MAX_BYTES),
        "shard_bounds": _validate_shard_bounds(value["shard_bounds"]),
    }
    if "transaction_id" in value:
        normalized["transaction_id"] = _id(value["transaction_id"], field="transaction_id")
    duties_raw = _array(
        value["duties"], field="duties", maximum=15, item="record", allow_empty=False
    )
    if len(duties_raw) != 15:
        raise CoverageSpecError("duties must contain all 15 canonical semantic duties exactly once")
    duties = [
        _validate_duty(item, field=f"duties[{index}]")
        for index, item in enumerate(duties_raw)
    ]
    names = [item["duty"] for item in duties]
    if set(names) != _DUTIES or len(names) != len(set(names)):
        raise CoverageSpecError("duties must contain all 15 canonical semantic duties exactly once")
    normalized["duties"] = duties
    if normalized["status"] == "owner_confirmed" and normalized["owner_confirmation"] is None:
        raise CoverageSpecError("owner_confirmed spec requires an owner confirmation receipt")
    if normalized["status"] == "draft" and normalized["owner_confirmation"] is not None:
        raise CoverageSpecError("draft spec cannot claim an owner confirmation receipt")
    selected_families = set(normalized["page_families"])
    selected_topics = set(normalized["topics"])
    selected_scopes = set(normalized["scopes"])
    hierarchy_families = set(normalized["hierarchy"]["roots"])
    hierarchy_families.update(
        family
        for edge in normalized["hierarchy"]["edges"]
        for family in (edge["parent"], edge["child"])
    )
    if not hierarchy_families <= selected_families:
        raise CoverageSpecError("hierarchy references an unselected page family")
    for tour in normalized["guided_tours"]:
        if not set(tour["page_families"]) <= selected_families:
            raise CoverageSpecError("guided tour references an unselected page family")
        if not set(tour.get("topics", [])) <= selected_topics:
            raise CoverageSpecError("guided tour references an unselected topic")
    for duty in duties:
        if not set(duty.get("page_families", [])) <= selected_families:
            raise CoverageSpecError("duty references an unselected page family")
        if not set(duty.get("topics", [])) <= selected_topics:
            raise CoverageSpecError("duty references an unselected topic")
        if not set(duty.get("scopes", [])) <= selected_scopes:
            raise CoverageSpecError("duty references an unselected scope")
    if "codemap" in normalized["page_families"] and not normalized["codemap"]["enabled"]:
        applicable = any(
            duty["applicability"] == "required"
            and (not duty.get("page_families") or "codemap" in duty["page_families"])
            for duty in duties
        )
        if applicable:
            raise CoverageSpecError("applicable codemap coverage requires codemap.enabled=true")
    return normalized


def _normalize_refs(value: Any, *, field: str) -> list[str]:
    return sorted(set(_array(value, field=field, maximum=MAX_REFS, item="id")))


def _validate_input(value: Any, *, field: str) -> dict[str, Any]:
    row = _mapping(value, field=field)
    allowed = {
        "revision_id",
        "kind",
        "topic",
        "scope",
        "canonical_path",
        "byte_size",
        "verified",
        "committed",
        "registered",
    }
    _unknown(row, allowed, field=field)
    _require(row, allowed, field=field)
    return {
        "revision_id": _id(row["revision_id"], field=f"{field}.revision_id"),
        "kind": _input_kind(row["kind"], field=f"{field}.kind"),
        "topic": _topic(row["topic"], field=f"{field}.topic"),
        "scope": _scope(row["scope"], field=f"{field}.scope"),
        "canonical_path": _relative_path(row["canonical_path"], field=f"{field}.canonical_path"),
        "byte_size": _integer(
            row["byte_size"], field=f"{field}.byte_size", minimum=0, maximum=MAX_BYTES
        ),
        "verified": _boolean(row["verified"], field=f"{field}.verified"),
        "committed": _boolean(row["committed"], field=f"{field}.committed"),
        "registered": _boolean(row["registered"], field=f"{field}.registered"),
    }


def _validate_page(value: Any, *, field: str) -> dict[str, Any]:
    row = _mapping(value, field=field)
    allowed = {
        "page_id",
        "revision_id",
        "family",
        "topic",
        "scope",
        "canonical_page_path",
        "byte_size",
        "verified",
        "committed",
        "registered",
        "input_revision_refs",
        "edge_ids",
        "content_role",
        "origin",
        "legal_authority",
    }
    _unknown(row, allowed, field=field)
    _require(row, allowed, field=field)
    legal_authority = _boolean(row["legal_authority"], field=f"{field}.legal_authority")
    return {
        "page_id": _id(row["page_id"], field=f"{field}.page_id"),
        "revision_id": _id(row["revision_id"], field=f"{field}.revision_id"),
        "family": _family(row["family"], field=f"{field}.family"),
        "topic": _topic(row["topic"], field=f"{field}.topic"),
        "scope": _scope(row["scope"], field=f"{field}.scope"),
        "canonical_page_path": _relative_path(
            row["canonical_page_path"], field=f"{field}.canonical_page_path"
        ),
        "byte_size": _integer(
            row["byte_size"], field=f"{field}.byte_size", minimum=0, maximum=MAX_PAGE_BYTES
        ),
        "verified": _boolean(row["verified"], field=f"{field}.verified"),
        "committed": _boolean(row["committed"], field=f"{field}.committed"),
        "registered": _boolean(row["registered"], field=f"{field}.registered"),
        "input_revision_refs": _normalize_refs(
            row["input_revision_refs"], field=f"{field}.input_revision_refs"
        ),
        "edge_ids": _normalize_refs(row["edge_ids"], field=f"{field}.edge_ids"),
        "content_role": _role(row["content_role"], field=f"{field}.content_role"),
        "origin": _origin(row["origin"], field=f"{field}.origin"),
        "legal_authority": legal_authority,
    }


def _validate_edge(value: Any, *, field: str) -> dict[str, Any]:
    row = _mapping(value, field=field)
    allowed = {
        "edge_id",
        "source_revision_id",
        "target_revision_id",
        "canonical_path",
        "verified",
        "committed",
        "registered",
    }
    _unknown(row, allowed, field=field)
    _require(row, allowed, field=field)
    return {
        "edge_id": _id(row["edge_id"], field=f"{field}.edge_id"),
        "source_revision_id": _id(row["source_revision_id"], field=f"{field}.source_revision_id"),
        "target_revision_id": _id(row["target_revision_id"], field=f"{field}.target_revision_id"),
        "canonical_path": _relative_path(row["canonical_path"], field=f"{field}.canonical_path"),
        "verified": _boolean(row["verified"], field=f"{field}.verified"),
        "committed": _boolean(row["committed"], field=f"{field}.committed"),
        "registered": _boolean(row["registered"], field=f"{field}.registered"),
    }


def validate_coverage_inventory(inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact bounded internal projection inventory shape."""

    value = _mapping(inventory, field="projection inventory")
    allowed = {"schema_version", "audit_head", "generated_at", "pages", "inputs", "edges"}
    _unknown(value, allowed, field="projection inventory")
    _require(value, allowed, field="projection inventory")
    if value["schema_version"] != COVERAGE_INVENTORY_SCHEMA:
        raise CoverageSpecError("projection inventory schema_version is invalid")
    result: dict[str, Any] = {
        "schema_version": COVERAGE_INVENTORY_SCHEMA,
        "audit_head": _sha(value["audit_head"], field="inventory.audit_head"),
        "generated_at": _timestamp(value["generated_at"], field="inventory.generated_at"),
    }
    pages_raw = _array(value["pages"], field="inventory.pages", maximum=MAX_PAGES, item="record")
    inputs_raw = _array(
        value["inputs"], field="inventory.inputs", maximum=MAX_INPUTS, item="record"
    )
    edges_raw = _array(value["edges"], field="inventory.edges", maximum=MAX_EDGES, item="record")
    result["pages"] = [
        _validate_page(row, field=f"inventory.pages[{index}]")
        for index, row in enumerate(pages_raw)
    ]
    result["inputs"] = [
        _validate_input(row, field=f"inventory.inputs[{index}]")
        for index, row in enumerate(inputs_raw)
    ]
    result["edges"] = [
        _validate_edge(row, field=f"inventory.edges[{index}]")
        for index, row in enumerate(edges_raw)
    ]
    page_ids = [row["page_id"] for row in result["pages"]]
    page_revisions = [row["revision_id"] for row in result["pages"]]
    input_revisions = [row["revision_id"] for row in result["inputs"]]
    edge_ids = [row["edge_id"] for row in result["edges"]]
    if len(page_ids) != len(set(page_ids)) or len(page_revisions) != len(set(page_revisions)):
        raise CoverageSpecError("inventory page IDs and revisions must be unique")
    if len(input_revisions) != len(set(input_revisions)):
        raise CoverageSpecError("inventory input revisions must be unique")
    if len(edge_ids) != len(set(edge_ids)):
        raise CoverageSpecError("inventory edge IDs must be unique")
    return result


def _matches(value: str, wanted: str) -> bool:
    return wanted == "*" or value == "*" or value == wanted


def _duty_matches(rule: Mapping[str, Any], *, topic: str, scope: str, family: str) -> bool:
    if rule.get("topics") and not any(_matches(topic, wanted) for wanted in rule["topics"]):
        return False
    if rule.get("scopes") and scope not in rule["scopes"]:
        return False
    return not (rule.get("page_families") and family not in rule["page_families"])


def _page_is_available(
    page: Mapping[str, Any],
    *,
    family: str,
    inputs: Mapping[str, Mapping[str, Any]],
    edges: Mapping[str, Mapping[str, Any]],
    required_refs: Sequence[str],
    required_edges: Sequence[str],
) -> bool:
    if not page["verified"] or not page["committed"] or not page["registered"]:
        return False
    if page["legal_authority"]:
        return False
    if family == "source_evidence":
        if page["content_role"] != "source_evidence" or page["origin"] not in {
            "official",
            "user_source",
        }:
            return False
    elif family == "source_summary":
        if (
            page["content_role"] != "agent_derived_summary"
            or page["origin"] != "agent_derived"
            or page["legal_authority"]
        ):
            return False
    elif page["content_role"] not in {"knowledge", "navigation"}:
        return False
    refs = set(page["input_revision_refs"]) | set(required_refs)
    if (family in _INPUT_REQUIRED_FAMILIES or family in _SOURCE_DERIVED_FAMILIES) and not page[
        "input_revision_refs"
    ]:
        return False
    for reference in refs:
        source = inputs.get(reference)
        if source is None or not all(
            source[key] for key in ("verified", "committed", "registered")
        ):
            return False
        if source is not None and (
            source["scope"] != page["scope"] or not _matches(source["topic"], page["topic"])
        ):
            return False
        if family in _SOURCE_DERIVED_FAMILIES and source["kind"] != "source_revision":
            return False
        if family in _INPUT_REQUIRED_FAMILIES and not source["canonical_path"]:
            return False
    edge_ids = set(page["edge_ids"]) | set(required_edges)
    if family == "codemap" and not edge_ids:
        return False
    for edge_id in edge_ids:
        edge = edges.get(edge_id)
        if edge is None or not all(edge[key] for key in ("verified", "committed", "registered")):
            return False
        if family == "codemap" and not edge["canonical_path"]:
            return False
    return True


def _budget_exceeded(
    spec: Mapping[str, Any], *, page_count: int, byte_count: int, estimated_page_bytes: int
) -> bool:
    projected_pages = page_count + 1
    projected_bytes = byte_count + estimated_page_bytes
    bounds = spec["shard_bounds"]
    projected_shards = max(
        ceil(projected_pages / bounds["max_pages_per_shard"]),
        ceil(projected_bytes / bounds["max_bytes_per_shard"]),
    )
    return bool(
        projected_pages > spec["max_pages"]
        or projected_bytes > spec["max_bytes"]
        or estimated_page_bytes > bounds["max_bytes_per_shard"]
        or projected_shards > bounds["max_shards"]
    )


def _page_id_summary(page_ids: Sequence[str]) -> tuple[list[str], int, str, bool]:
    complete = sorted(set(page_ids))
    digest = sha256_bytes(canonical_json(complete).encode("utf-8"))
    inline = complete[:MAX_INLINE_PAGE_IDS]
    return inline, len(complete), digest, len(complete) > MAX_INLINE_PAGE_IDS


def _make_gap(
    *,
    spec: Mapping[str, Any],
    inventory: Mapping[str, Any],
    status: str,
    topic: str,
    scope: str,
    family: str,
    duty: str,
    required_refs: Sequence[str],
    observed_refs: Sequence[str],
    observed_page_ids: Sequence[str],
    observed_page_count: int,
    observed_page_ids_sha256: str,
    observed_page_ids_truncated: bool,
    observed_bytes: int,
    estimated_page_bytes: int,
) -> dict[str, Any]:
    reason = _GAP_REASONS[status]
    required_sorted = sorted(set(required_refs))
    observed_sorted = sorted(set(observed_refs))
    pages_inline = list(observed_page_ids)
    binding = {
        "spec_audit_head": spec["audit_head"],
        "inventory_audit_head": inventory["audit_head"],
        "transaction_head": spec["transaction_head"],
    }
    value = {
        "schema_version": COVERAGE_GAP_SCHEMA,
        "spec_id": spec["spec_id"],
        "spec_revision_id": spec["revision_id"],
        "status": status,
        "reason": reason,
        "topic": topic,
        "scope": scope,
        "page_family": family,
        "duty": duty,
        "required_input_revision_refs": required_sorted,
        "observed_input_revision_refs": observed_sorted,
        "observed_page_ids": pages_inline,
        "observed_page_ids_sha256": observed_page_ids_sha256,
        "observed_page_ids_truncated": observed_page_ids_truncated,
        "observed_page_count": observed_page_count,
        "required_page_count": 0 if status == "not_applicable" else 1,
        "required_bytes": 0 if status == "not_applicable" else estimated_page_bytes,
        "observed_bytes": observed_bytes,
        "audit_head": inventory["audit_head"],
        "transaction_head": spec["transaction_head"],
        "audit_binding": binding,
    }
    value["gap_id"] = stable_id("coveragegap", _gap_identity_seed(value))
    return validate_coverage_gap(value)


def _gap_identity_seed(value: Mapping[str, Any]) -> str:
    return canonical_json(
        {
            key: value[key]
            for key in (
                "spec_id",
                "spec_revision_id",
                "status",
                "reason",
                "topic",
                "scope",
                "page_family",
                "duty",
                "required_input_revision_refs",
                "observed_input_revision_refs",
                "observed_page_ids",
                "observed_page_ids_sha256",
                "observed_page_ids_truncated",
                "observed_page_count",
                "required_page_count",
                "required_bytes",
                "observed_bytes",
                "audit_head",
                "transaction_head",
                "audit_binding",
            )
        }
    )


def validate_coverage_gap(gap: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one closed gap and bind its top-level audit/transaction heads exactly."""

    value = _mapping(gap, field="coverage gap")
    allowed = {
        "schema_version",
        "gap_id",
        "spec_id",
        "spec_revision_id",
        "status",
        "reason",
        "topic",
        "scope",
        "page_family",
        "duty",
        "required_input_revision_refs",
        "observed_input_revision_refs",
        "observed_page_ids",
        "observed_page_ids_sha256",
        "observed_page_ids_truncated",
        "required_page_count",
        "observed_page_count",
        "required_bytes",
        "observed_bytes",
        "audit_head",
        "transaction_head",
        "audit_binding",
    }
    _unknown(value, allowed, field="coverage gap")
    required = {
        "schema_version",
        "gap_id",
        "spec_id",
        "spec_revision_id",
        "status",
        "reason",
        "topic",
        "scope",
        "page_family",
        "duty",
        "required_input_revision_refs",
        "observed_input_revision_refs",
        "observed_page_ids",
        "observed_page_ids_sha256",
        "observed_page_ids_truncated",
        "observed_page_count",
        "required_page_count",
        "required_bytes",
        "observed_bytes",
        "audit_head",
        "transaction_head",
        "audit_binding",
    }
    _require(value, required, field="coverage gap")
    if value["schema_version"] != COVERAGE_GAP_SCHEMA:
        raise CoverageSpecError("coverage gap schema_version is invalid")
    status = value["status"]
    if status not in GAP_STATUSES or value["reason"] != _GAP_REASONS[status]:
        raise CoverageSpecError("coverage gap status/reason is invalid")
    binding = _mapping(value["audit_binding"], field="audit_binding")
    _unknown(
        binding,
        {"spec_audit_head", "inventory_audit_head", "transaction_head"},
        field="audit_binding",
    )
    _require(
        binding,
        {"spec_audit_head", "inventory_audit_head", "transaction_head"},
        field="audit_binding",
    )
    audit_head = _sha(value["audit_head"], field="audit_head")
    transaction_head = _sha(value["transaction_head"], field="transaction_head")
    binding_normalized = {
        "spec_audit_head": _sha(binding["spec_audit_head"], field="audit_binding.spec_audit_head"),
        "inventory_audit_head": _sha(
            binding["inventory_audit_head"], field="audit_binding.inventory_audit_head"
        ),
        "transaction_head": _sha(
            binding["transaction_head"], field="audit_binding.transaction_head"
        ),
    }
    if (
        audit_head != binding_normalized["inventory_audit_head"]
        or audit_head != binding_normalized["spec_audit_head"]
        or transaction_head != binding_normalized["transaction_head"]
    ):
        raise CoverageSpecError("coverage gap audit/transaction binding mismatch")
    raw_page_ids = _array(
        value["observed_page_ids"],
        field="observed_page_ids",
        maximum=MAX_INLINE_PAGE_IDS,
        item="id",
    )
    if raw_page_ids != sorted(raw_page_ids):
        raise CoverageSpecError("observed_page_ids must be sorted")
    page_count = _integer(
        value["observed_page_count"], field="observed_page_count", minimum=0, maximum=MAX_PAGES
    )
    page_digest = _sha(value["observed_page_ids_sha256"], field="observed_page_ids_sha256")
    page_truncated = _boolean(
        value["observed_page_ids_truncated"], field="observed_page_ids_truncated"
    )
    if page_truncated:
        if page_count <= len(raw_page_ids):
            raise CoverageSpecError("truncated observed_page_ids must retain a larger full count")
    elif page_count != len(raw_page_ids) or page_digest != sha256_bytes(
        canonical_json(raw_page_ids).encode("utf-8")
    ):
        raise CoverageSpecError("untruncated observed_page_ids digest/count mismatch")
    normalized: dict[str, Any] = {
        "schema_version": COVERAGE_GAP_SCHEMA,
        "gap_id": _id(value["gap_id"], field="gap_id"),
        "spec_id": _id(value["spec_id"], field="spec_id"),
        "spec_revision_id": _id(value["spec_revision_id"], field="spec_revision_id"),
        "status": status,
        "reason": value["reason"],
        "topic": _topic(value["topic"], field="topic"),
        "scope": _scope(value["scope"], field="scope"),
        "page_family": _family(value["page_family"], field="page_family"),
        "duty": _duty_name(value["duty"], field="duty"),
        "required_input_revision_refs": _normalize_refs(
            value["required_input_revision_refs"], field="required_input_revision_refs"
        ),
        "observed_input_revision_refs": _normalize_refs(
            value["observed_input_revision_refs"], field="observed_input_revision_refs"
        ),
        "observed_page_ids": raw_page_ids,
        "observed_page_ids_sha256": page_digest,
        "observed_page_ids_truncated": page_truncated,
        "observed_page_count": page_count,
        "audit_head": audit_head,
        "transaction_head": transaction_head,
        "audit_binding": binding_normalized,
    }
    normalized["required_page_count"] = _integer(
        value["required_page_count"],
        field="required_page_count",
        minimum=0,
        maximum=MAX_PAGES,
    )
    normalized["required_bytes"] = _integer(
        value["required_bytes"], field="required_bytes", minimum=0, maximum=MAX_BYTES
    )
    normalized["observed_bytes"] = _integer(
        value["observed_bytes"], field="observed_bytes", minimum=0, maximum=MAX_BYTES
    )
    if status == "not_applicable":
        if normalized["required_page_count"] != 0 or normalized["required_bytes"] != 0:
            raise CoverageSpecError("not_applicable gap cannot claim required output")
    elif normalized["required_page_count"] != 1 or normalized["required_bytes"] < 1:
        raise CoverageSpecError("applicable gap must identify one required page and byte budget")
    expected_gap_id = stable_id("coveragegap", _gap_identity_seed(normalized))
    if normalized["gap_id"] != expected_gap_id:
        raise CoverageSpecError("coverage gap ID is not content-addressed")
    return normalized


def _build_page_index(
    pages: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, list[Mapping[str, Any]]]]:
    index: dict[tuple[str, str], dict[str, list[Mapping[str, Any]]]] = {}
    for page in pages:
        bucket = index.setdefault((page["scope"], page["family"]), {})
        bucket.setdefault(page["topic"], []).append(page)
    return index


def _matching_pages(
    index: Mapping[tuple[str, str], Mapping[str, Sequence[Mapping[str, Any]]]],
    *,
    scope: str,
    family: str,
    topic: str,
) -> list[Mapping[str, Any]]:
    buckets = index.get((scope, family), {})
    if topic == "*":
        return [page for rows in buckets.values() for page in rows]
    rows = list(buckets.get(topic, ()))
    if topic != "*":
        rows.extend(buckets.get("*", ()))
    return rows


def compute_coverage_gaps(
    projection_inventory: Mapping[str, Any], spec: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Compute sorted, closed gaps for an exact verified inventory and Coverage Spec."""

    normalized_spec = validate_coverage_spec(spec)
    inventory = validate_coverage_inventory(projection_inventory)
    if inventory["audit_head"] != normalized_spec["audit_head"]:
        raise CoverageSpecError("inventory audit_head does not match spec audit_head")
    topics = normalized_spec["topics"]
    scopes = normalized_spec["scopes"]
    families = normalized_spec["page_families"]
    potential = len(topics) * len(scopes) * len(families) * len(normalized_spec["duties"])
    if potential > MAX_GAPS:
        raise CoverageSpecError("coverage gap record count exceeds its hard bound")
    inputs = {row["revision_id"]: row for row in inventory["inputs"]}
    edges = {row["edge_id"]: row for row in inventory["edges"]}
    pages = inventory["pages"]
    page_index = _build_page_index(pages)
    total_page_count = len(pages)
    total_byte_count = sum(page["byte_size"] for page in pages)
    codemap = normalized_spec["codemap"]
    gaps: list[dict[str, Any]] = []
    for topic in topics:
        for scope in scopes:
            for family in families:
                matching = _matching_pages(
                    page_index, scope=scope, family=family, topic=topic
                )
                page_ids, page_count, page_ids_digest, page_ids_truncated = _page_id_summary(
                    [page["page_id"] for page in matching]
                )
                observed_refs = sorted(
                    {reference for page in matching for reference in page["input_revision_refs"]}
                )
                observed_bytes = sum(page["byte_size"] for page in matching)
                for rule in normalized_spec["duties"]:
                    if not _duty_matches(rule, topic=topic, scope=scope, family=family):
                        continue
                    duty = rule["duty"]
                    required_refs = set(rule.get("required_input_revision_refs", []))
                    required_edges: set[str] = set()
                    if family == "codemap" and codemap["enabled"]:
                        required_refs.update(codemap["required_input_revision_refs"])
                        required_edges.update(codemap["required_edge_ids"])
                    estimated = rule.get("estimated_page_bytes", DEFAULT_ESTIMATED_PAGE_BYTES)
                    if rule["applicability"] == "not_applicable":
                        gaps.append(
                            _make_gap(
                                spec=normalized_spec,
                                inventory=inventory,
                                status="not_applicable",
                                topic=topic,
                                scope=scope,
                                family=family,
                                duty=duty,
                                required_refs=sorted(required_refs),
                                observed_refs=observed_refs,
                                observed_page_ids=page_ids,
                                observed_page_count=page_count,
                                observed_page_ids_sha256=page_ids_digest,
                                observed_page_ids_truncated=page_ids_truncated,
                                observed_bytes=observed_bytes,
                                estimated_page_bytes=estimated,
                            )
                        )
                        continue
                    available = any(
                        _page_is_available(
                            page,
                            family=family,
                            inputs=inputs,
                            edges=edges,
                            required_refs=sorted(required_refs),
                            required_edges=sorted(required_edges),
                        )
                        for page in matching
                    )
                    if available:
                        continue
                    input_unavailable = any(
                        reference not in inputs
                        or not all(
                            inputs[reference][key]
                            for key in ("verified", "committed", "registered")
                        )
                        for reference in required_refs
                    )
                    edge_unavailable = any(
                        edge_id not in edges
                        or not all(
                            edges[edge_id][key]
                            for key in ("verified", "committed", "registered")
                        )
                        for edge_id in required_edges
                    )
                    page_refs_unavailable = any(
                        reference not in inputs
                        or not all(
                            inputs[reference][key]
                            for key in ("verified", "committed", "registered")
                        )
                        for page in matching
                        for reference in page["input_revision_refs"]
                    )
                    page_flags_unavailable = any(
                        not all(page[key] for key in ("verified", "committed", "registered"))
                        for page in matching
                    )
                    if (
                        input_unavailable
                        or edge_unavailable
                        or page_refs_unavailable
                        or page_flags_unavailable
                    ) or (
                        family in _INPUT_REQUIRED_FAMILIES and not matching
                    ) or (matching and family in _SOURCE_DERIVED_FAMILIES) or matching:
                        status = "unavailable"
                    elif _budget_exceeded(
                        normalized_spec,
                        page_count=total_page_count,
                        byte_count=total_byte_count,
                        estimated_page_bytes=estimated,
                    ):
                        status = "over_budget"
                    else:
                        status = "missing"
                    gaps.append(
                        _make_gap(
                            spec=normalized_spec,
                            inventory=inventory,
                            status=status,
                            topic=topic,
                            scope=scope,
                            family=family,
                            duty=duty,
                            required_refs=sorted(required_refs),
                            observed_refs=observed_refs,
                            observed_page_ids=page_ids,
                            observed_page_count=page_count,
                            observed_page_ids_sha256=page_ids_digest,
                            observed_page_ids_truncated=page_ids_truncated,
                            observed_bytes=observed_bytes,
                            estimated_page_bytes=estimated,
                        )
                    )
    gaps.sort(
        key=lambda gap: (
            gap["topic"],
            gap["scope"],
            gap["page_family"],
            gap["duty"],
            gap["status"],
            gap["reason"],
            gap["gap_id"],
        )
    )
    return gaps


__all__ = [
    "CANONICAL_PAGE_FAMILIES",
    "CANONICAL_SEMANTIC_DUTIES",
    "CONTENT_ROLES",
    "COVERAGE_GAP_SCHEMA",
    "COVERAGE_INVENTORY_SCHEMA",
    "COVERAGE_SPEC_SCHEMA",
    "GAP_STATUSES",
    "INPUT_KINDS",
    "ORIGINS",
    "SCOPES",
    "CoverageSpecError",
    "compute_coverage_gaps",
    "validate_coverage_gap",
    "validate_coverage_inventory",
    "validate_coverage_spec",
]
