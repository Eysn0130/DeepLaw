"""Deterministic Living Wiki Wikilink index and bounded backlink/outlink queries."""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ..util import canonical_json, sha256_bytes, stable_id, strict_json_loads
from .registry import (
    MANIFEST_BYTE_LIMIT,
    PUBLIC_EDGE_LIMIT,
    PUBLIC_RECORD_LIMIT,
    PUBLIC_SHARD_COUNT_LIMIT,
    SHARD_BYTE_LIMIT,
    SHARD_RECORD_LIMIT,
    RegistryError,
    _as_records,
    _canonical_digest,
    _id,
    _path,
    _safe_read_file,
    _sha,
    _shard_records,
    _timestamp,
    _validated_timestamp,
    validate_living_wiki_manifest_v3,
    validate_page_record,
)
from .resolver import StableResolver

LINK_INDEX_SCHEMA = "deeplaw.living-wiki-link-index/v1"
_FENCE = re.compile(r"^[ ]{0,3}([`~]{3,})")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LINK_CANDIDATE_LIMIT = 2_000
_SHARD_PATH = re.compile(r"^\.deeplaw/derived/wiki/v3/links/link-[0-9]{5}\.json$")
_COVERAGE_SHARD_PATH = re.compile(
    r"^\.deeplaw/derived/wiki/v3/coverage/coverage-[0-9]{5}\.json$"
)


def _contains_control(value: str) -> bool:
    return bool(_CONTROL.search(value))


def _fence_run(line: str) -> tuple[str, int] | None:
    match = _FENCE.match(line)
    if not match:
        return None
    run = match.group(1)
    if len(set(run)) != 1:
        return None
    return run[0], len(run)


def _iter_wikilinks(text: str) -> list[str]:
    """Return real Markdown wikilinks in source order, excluding code and escapes."""

    links: list[str] = []
    fence: tuple[str, int] | None = None
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        run = _fence_run(line)
        if fence is not None:
            if run is not None and run[0] == fence[0] and run[1] >= fence[1]:
                fence_match = _FENCE.match(line)
                rest = line[fence_match.end() :] if fence_match else line
                if not rest.strip():
                    fence = None
            continue
        if run is not None:
            fence = run
            continue

        inline_delimiter: str | None = None
        index = 0
        while index < len(line):
            character = line[index]
            if character == "`":
                end = index + 1
                while end < len(line) and line[end] == "`":
                    end += 1
                delimiter = line[index:end]
                if inline_delimiter is None:
                    inline_delimiter = delimiter
                elif delimiter == inline_delimiter:
                    inline_delimiter = None
                index = end
                continue
            if inline_delimiter is not None:
                index += 1
                continue
            if character == "[" and line.startswith("[[", index):
                backslashes = 0
                cursor = index - 1
                while cursor >= 0 and line[cursor] == "\\":
                    backslashes += 1
                    cursor -= 1
                if backslashes % 2:
                    index += 2
                    continue
                closing = line.find("]]", index + 2)
                if closing < 0:
                    index += 2
                    continue
                target = line[index + 2 : closing]
                if 1 <= len(target) <= 2_048:
                    links.append(target)
                index = closing + 2
                continue
            index += 1
    return links


class _MutationTracker:
    __slots__ = ("dirty",)

    def __init__(self) -> None:
        self.dirty = False


class _TrackedList(list[Any]):
    def __init__(self, values: Sequence[Any], tracker: _MutationTracker) -> None:
        super().__init__(values)
        self._tracker = tracker

    def __setitem__(self, key: Any, value: Any) -> None:
        self._tracker.dirty = True
        super().__setitem__(key, value)

    def __delitem__(self, key: Any) -> None:
        self._tracker.dirty = True
        super().__delitem__(key)

    def append(self, value: Any) -> None:
        self._tracker.dirty = True
        super().append(value)

    def extend(self, values: Any) -> None:
        self._tracker.dirty = True
        super().extend(values)

    def insert(self, index: int, value: Any) -> None:
        self._tracker.dirty = True
        super().insert(index, value)

    def pop(self, index: int = -1) -> Any:
        self._tracker.dirty = True
        return super().pop(index)

    def remove(self, value: Any) -> None:
        self._tracker.dirty = True
        super().remove(value)

    def clear(self) -> None:
        self._tracker.dirty = True
        super().clear()

    def sort(self, *args: Any, **kwargs: Any) -> None:
        self._tracker.dirty = True
        super().sort(*args, **kwargs)

    def reverse(self) -> None:
        self._tracker.dirty = True
        super().reverse()


class _TrackedDict(dict[str, Any]):
    def __init__(self, values: Mapping[str, Any], tracker: _MutationTracker) -> None:
        super().__init__(values)
        self._tracker = tracker

    def __setitem__(self, key: str, value: Any) -> None:
        self._tracker.dirty = True
        super().__setitem__(key, value)

    def __delitem__(self, key: str) -> None:
        self._tracker.dirty = True
        super().__delitem__(key)

    def clear(self) -> None:
        self._tracker.dirty = True
        super().clear()

    def pop(self, key: str, *args: Any) -> Any:
        self._tracker.dirty = True
        return super().pop(key, *args)

    def popitem(self) -> tuple[str, Any]:
        self._tracker.dirty = True
        return super().popitem()

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._tracker.dirty = True
        super().update(*args, **kwargs)


def _tracked(value: Any, tracker: _MutationTracker) -> Any:
    if isinstance(value, Mapping):
        return _TrackedDict(
            {key: _tracked(item, tracker) for key, item in value.items()}, tracker
        )
    if isinstance(value, list):
        return _TrackedList([_tracked(item, tracker) for item in value], tracker)
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ValidatedLinkIndex:
    """Immutable, fully validated link data with target/source adjacency."""

    component: Mapping[str, Any]
    edges: tuple[Mapping[str, Any], ...]
    coverage: tuple[Mapping[str, Any], ...]
    backlinks: Mapping[str, tuple[Mapping[str, Any], ...]]
    outlinks: Mapping[str, tuple[Mapping[str, Any], ...]]
    manifest_sha256: str
    index_sha256: str
    _public_edges: Any = dataclass_field(repr=False, compare=False)
    _public_coverage: Any = dataclass_field(repr=False, compare=False)
    _tracker: _MutationTracker = dataclass_field(repr=False, compare=False)

    @classmethod
    def from_validated(
        cls,
        component: Mapping[str, Any],
        edges: Sequence[Mapping[str, Any]],
        coverage: Sequence[Mapping[str, Any]],
        *,
        manifest_sha256: str,
    ) -> ValidatedLinkIndex:
        tracker = _MutationTracker()
        public_edges = _tracked([dict(edge) for edge in edges], tracker)
        public_coverage = _tracked([dict(row) for row in coverage], tracker)
        immutable_edges = tuple(_freeze(edge) for edge in public_edges)
        immutable_coverage = tuple(_freeze(row) for row in public_coverage)
        backlinks: dict[str, list[Mapping[str, Any]]] = {}
        outlinks: dict[str, list[Mapping[str, Any]]] = {}
        for edge in immutable_edges:
            source = edge["source_page_id"]
            outlinks.setdefault(source, []).append(edge)
            for target in edge["target_page_ids"]:
                backlinks.setdefault(target, []).append(edge)
        return cls(
            component=_freeze(dict(component)),
            edges=immutable_edges,
            coverage=immutable_coverage,
            backlinks=MappingProxyType(
                {key: tuple(value) for key, value in backlinks.items()}
            ),
            outlinks=MappingProxyType({key: tuple(value) for key, value in outlinks.items()}),
            manifest_sha256=manifest_sha256,
            index_sha256=component["index_sha256"],
            _public_edges=public_edges,
            _public_coverage=public_coverage,
            _tracker=tracker,
        )

    def __deepcopy__(self, memo: dict[int, Any]) -> ValidatedLinkIndex:
        # ``MappingProxyType`` is intentionally not pickleable.  A detached validated handle
        # keeps ordinary test/tooling copies possible while retaining the immutable snapshot and
        # rebuilding mutation tracking for the copied public lists.
        existing = memo.get(id(self))
        if isinstance(existing, ValidatedLinkIndex):
            return existing
        clone = type(self).from_validated(
            _plain(self.component),
            _plain(self.edges),
            _plain(self.coverage),
            manifest_sha256=self.manifest_sha256,
        )
        memo[id(self)] = clone
        return clone

    @property
    def dirty(self) -> bool:
        return self._tracker.dirty

    @property
    def public_edges(self) -> Any:
        return self._public_edges

    @property
    def public_coverage(self) -> Any:
        return self._public_coverage

    def query(self, target_page_id: str, direction: str) -> tuple[Mapping[str, Any], ...]:
        table = self.backlinks if direction == "backlinks" else self.outlinks
        return table.get(target_page_id, ())


def _validate_edge(edge: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "edge_id",
        "source_page_id",
        "source_page_revision",
        "audit_head",
        "ordinal",
        "target_raw",
        "lookup_key",
        "status",
        "candidate_count",
        "candidates_truncated",
        "truncation_reason",
        "target_page_ids",
        "candidate_reasons",
        "link_type",
    }
    if set(edge) != expected:
        raise RegistryError("link edge shape is invalid")
    for name in ("edge_id", "source_page_id"):
        _id(edge[name], name)
    source_page_revision = edge["source_page_revision"]
    if isinstance(source_page_revision, str):
        _id(source_page_revision, "source_page_revision")
    elif isinstance(source_page_revision, Mapping):
        if set(source_page_revision) - {
            "source_revision_id",
            "fragment_id",
            "fragment_revision_id",
        }:
            raise RegistryError("source_page_revision has unknown fields")
        _id(source_page_revision.get("source_revision_id"), "source_revision_id")
        fragment_id = source_page_revision.get("fragment_id")
        fragment_revision_id = source_page_revision.get("fragment_revision_id")
        if (fragment_id is None) == (fragment_revision_id is None):
            raise RegistryError("source_page_revision requires one fragment identity")
        if fragment_id is not None:
            _id(fragment_id, "fragment_id")
        if fragment_revision_id is not None:
            _id(fragment_revision_id, "fragment_revision_id")
    else:
        raise RegistryError("source_page_revision is invalid")
    _sha(edge["audit_head"], "edge audit_head")
    if not isinstance(edge["ordinal"], int) or edge["ordinal"] < 0:
        raise RegistryError("link edge ordinal is invalid")
    if (
        not isinstance(edge["target_raw"], str)
        or not 1 <= len(edge["target_raw"]) <= 2048
        or not isinstance(edge["lookup_key"], str)
        or len(edge["lookup_key"]) > 2048
    ):
        raise RegistryError("link edge target is invalid")
    for field in ("target_raw", "lookup_key"):
        if _contains_control(edge[field]):
            raise RegistryError("link edge target contains control characters")
    if not isinstance(edge["status"], str) or edge["status"] not in {
        "resolved",
        "ambiguous",
        "unresolved",
        "out_of_scope",
        "stale",
    }:
        raise RegistryError("link edge status is invalid")
    if not isinstance(edge["link_type"], str) or edge["link_type"] not in {
        "wikilink",
        "relation",
    }:
        raise RegistryError("link edge type is invalid")
    candidate_count = edge["candidate_count"]
    if (
        not isinstance(candidate_count, int)
        or isinstance(candidate_count, bool)
        or not 0 <= candidate_count <= PUBLIC_RECORD_LIMIT
    ):
        raise RegistryError("link candidate_count is invalid")
    if not isinstance(edge["candidates_truncated"], bool):
        raise RegistryError("link candidates_truncated is invalid")
    truncation_reason = edge["truncation_reason"]
    if truncation_reason not in {None, "candidate_limit"}:
        raise RegistryError("link truncation_reason is invalid")
    targets = edge["target_page_ids"]
    if not isinstance(targets, Sequence) or isinstance(targets, (str, bytes, bytearray)):
        raise RegistryError("link edge targets are invalid")
    if any(not isinstance(target, str) for target in targets):
        raise RegistryError("link edge targets must be sorted and unique")
    for target in targets:
        _id(target, "target page_id")
    if list(targets) != sorted(set(targets)):
        raise RegistryError("link edge targets must be sorted and unique")
    reasons = edge["candidate_reasons"]
    if not isinstance(reasons, Sequence) or isinstance(reasons, (str, bytes, bytearray)):
        raise RegistryError("link edge candidate reasons are invalid")
    reason_ids: list[str] = []
    for reason in reasons:
        if not isinstance(reason, Mapping) or set(reason) != {"page_id", "reason"}:
            raise RegistryError("link edge candidate reason shape is invalid")
        _id(reason["page_id"], "candidate page_id")
        if not isinstance(reason["reason"], str) or not 1 <= len(reason["reason"]) <= 128:
            raise RegistryError("link edge candidate reason is invalid")
        if _contains_control(reason["reason"]):
            raise RegistryError("link edge candidate reason contains control characters")
        reason_ids.append(reason["page_id"])
    if reason_ids != sorted(set(reason_ids)):
        raise RegistryError("link edge candidate reasons must be sorted and unique")
    target_ids = list(targets)
    if reason_ids != target_ids:
        raise RegistryError("link edge candidate reasons must match target page IDs")
    if candidate_count == 0 and (target_ids or reason_ids):
        raise RegistryError("unresolved link cannot contain candidates")
    if candidate_count > 0 and not target_ids:
        raise RegistryError("link candidates are missing target page IDs")
    if edge["status"] == "resolved":
        if candidate_count != 1 or len(target_ids) != 1 or edge["candidates_truncated"]:
            raise RegistryError("resolved link candidate cardinality is invalid")
        if truncation_reason is not None:
            raise RegistryError("resolved link truncation reason is invalid")
    elif edge["status"] == "unresolved":
        if candidate_count != 0 or target_ids or edge["candidates_truncated"]:
            raise RegistryError("unresolved link candidate cardinality is invalid")
        if truncation_reason is not None:
            raise RegistryError("unresolved link truncation reason is invalid")
    elif edge["status"] == "ambiguous" and candidate_count <= 1:
        raise RegistryError("ambiguous link candidate cardinality is invalid")
    if edge["candidates_truncated"]:
        if candidate_count <= len(target_ids) or truncation_reason != "candidate_limit":
            raise RegistryError("link candidate truncation is not bound")
    elif candidate_count != len(target_ids):
        raise RegistryError("link candidate count is incomplete")
    return dict(edge)


def _validate_coverage_row(row: Mapping[str, Any]) -> dict[str, Any]:
    if set(row) != {"page_id", "canonical_page_path", "link_count", "edge_ids_sha256"}:
        raise RegistryError("link coverage shape is invalid")
    _id(row["page_id"], "coverage page_id")
    if (
        not isinstance(row["canonical_page_path"], str)
        or not isinstance(row["link_count"], int)
        or isinstance(row["link_count"], bool)
        or not 0 <= row["link_count"] <= PUBLIC_EDGE_LIMIT
    ):
        raise RegistryError("link coverage bounds are invalid")
    if _path(row["canonical_page_path"], field="coverage canonical_page_path") != row[
        "canonical_page_path"
    ]:
        raise RegistryError("coverage canonical_page_path is not canonical")
    _sha(row["edge_ids_sha256"], "coverage edge_ids_sha256")
    return dict(row)


def _source_page_revision(page: Mapping[str, Any]) -> str | dict[str, str]:
    if page.get("revision_id"):
        return str(page["revision_id"])
    fragment = page.get("source_fragment") or {}
    if not isinstance(fragment, Mapping):
        raise RegistryError("page source fragment identity is missing")
    return dict(fragment)


def _target_parts(target_raw: str) -> tuple[str, str]:
    value = target_raw.strip()
    if "|" in value:
        value = value.split("|", 1)[0].strip()
    if value.startswith("#"):
        return value, ""
    # A heading suffix is a display concern, not an identity.  Keep target_raw intact while
    # resolving the canonical page path before the first heading marker.
    return value, value.split("#", 1)[0].strip()


def _page_bytes_for(page: Mapping[str, Any], page_bytes: Mapping[str, bytes]) -> bytes:
    for key in (page["page_id"], page["canonical_page_path"]):
        value = page_bytes.get(key)
        if isinstance(value, (bytes, bytearray)):
            raw = bytes(value)
            if len(raw) != page["byte_size"] or sha256_bytes(raw) != page["sha256"]:
                raise RegistryError("registered page bytes do not match registry identity")
            return raw
    raise RegistryError(f"missing bytes for registered page {page['page_id']}")


def _edge_status(resolved: Mapping[str, Any]) -> str:
    status = resolved.get("status")
    return {
        "resolved": "resolved",
        "ambiguous": "ambiguous",
        "not_admitted": "out_of_scope",
        "stale": "stale",
        "invalid": "unresolved",
        "not_found": "unresolved",
        "index_unavailable": "unresolved",
    }.get(status, "unresolved")


def build_link_index(
    registry: Mapping[str, Any],
    page_bytes: Mapping[str, bytes],
    *,
    v2_manifest_sha256: str | None = None,
    input_audit_head: str | None = None,
    legacy_audit_head: str | None = None,
    generated_at: str | None = None,
    resolver: StableResolver | None = None,
) -> dict[str, Any]:
    """Build a deterministic edge index from registered bytes only.

    No filesystem traversal occurs.  A page with no wikilink still contributes a coverage row,
    making completeness explicit and permitting exact zero-link checks.
    """

    records = list(registry.get("records", []))
    component = dict(registry.get("component", {}))
    if not component:
        raise RegistryError("validated page registry is required")
    if len(records) > PUBLIC_RECORD_LIMIT:
        raise RegistryError("page count exceeds public limit")
    records = [validate_page_record(row) for row in records]
    if "page_count" in component and component["page_count"] != len(records):
        raise RegistryError("page registry record count mismatch")
    registry_page_ids = sorted(row["page_id"] for row in records)
    if "page_ids_sha256" in component and _canonical_digest(registry_page_ids) != component[
        "page_ids_sha256"
    ]:
        raise RegistryError("page registry identity digest mismatch")
    registry_sha256 = registry.get("registry_sha256", component.get("registry_sha256"))
    _sha(registry_sha256, "registry_sha256")
    expected_v2 = v2_manifest_sha256 or component.get("v2_manifest_sha256")
    expected_input = input_audit_head or component.get("input_audit_head")
    expected_legacy = legacy_audit_head or component.get("legacy_audit_head")
    expected_generated = generated_at or component.get("generated_at")
    _sha(expected_v2, "v2_manifest_sha256")
    _sha(expected_input, "input_audit_head")
    _sha(expected_legacy, "legacy_audit_head")
    expected_generated = _timestamp(expected_generated)
    edges: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for page in sorted(records, key=lambda row: row["page_id"]):
        raw = _page_bytes_for(page, page_bytes)
        page_edges: list[dict[str, Any]] = []
        for ordinal, target_unparsed in enumerate(
            _iter_wikilinks(raw.decode("utf-8", errors="strict"))
        ):
            if resolver is None:
                resolver = StableResolver(registry)
            target_raw, lookup_key = _target_parts(target_unparsed)
            source_page_revision = _source_page_revision(page)
            result = resolver.resolve(
                {
                    "wikilink": target_raw,
                    # Index construction is not provider admission.  Resolve identities across
                    # every governed scope/sensitivity, while stale targets remain visibly stale.
                    "allowed_scopes": ["personal", "project", "domain"],
                    "max_sensitivity": "restricted",
                },
                limit=_LINK_CANDIDATE_LIMIT,
            )
            candidates = result.get("candidates", [])
            candidate_count = result.get("candidate_count", len(candidates))
            if not isinstance(candidate_count, int) or isinstance(candidate_count, bool):
                raise RegistryError("resolver candidate_count is invalid")
            candidate_rows = sorted(
                (
                    {
                        "page_id": candidate.get("page_id"),
                        "reason": candidate.get("reason", "candidate"),
                    }
                    for candidate in candidates
                    if isinstance(candidate, Mapping)
                ),
                key=lambda row: row["page_id"] or "",
            )
            target_page_ids = [row["page_id"] for row in candidate_rows]
            candidates_truncated = candidate_count > len(candidate_rows)
            edge = {
                "edge_id": stable_id(
                    "wikilink",
                    page["page_id"],
                    canonical_json(source_page_revision),
                    page["audit_head"],
                    str(ordinal),
                    target_raw,
                ),
                "source_page_id": page["page_id"],
                "source_page_revision": source_page_revision,
                "audit_head": page["audit_head"],
                "ordinal": ordinal,
                "target_raw": target_raw,
                "lookup_key": lookup_key,
                "status": _edge_status(result),
                "candidate_count": candidate_count,
                "candidates_truncated": candidates_truncated,
                "truncation_reason": "candidate_limit" if candidates_truncated else None,
                "target_page_ids": target_page_ids,
                "candidate_reasons": candidate_rows,
                "link_type": "wikilink",
            }
            _validate_edge(edge)
            page_edges.append(edge)
            edges.append(edge)
        coverage.append(
            {
                "page_id": page["page_id"],
                "canonical_page_path": page["canonical_page_path"],
                "link_count": len(page_edges),
                # Edges are globally sorted by stable edge identity before sharding.  Bind
                # coverage to that canonical order as well; ordinal/page generation order is
                # intentionally not an alternative digest representation.
                "edge_ids_sha256": _canonical_digest(
                    sorted(edge["edge_id"] for edge in page_edges)
                ),
            }
        )
    edges.sort(key=lambda edge: edge["edge_id"])
    coverage.sort(key=lambda row: row["page_id"])
    shards, payloads = _shard_records(
        edges, prefix="links", record_limit=PUBLIC_EDGE_LIMIT
    )
    coverage_shards, coverage_payloads = _shard_records(
        coverage, prefix="coverage", item_key="coverage"
    )
    payloads.update(coverage_payloads)
    component_body: dict[str, Any] = {
        "schema_version": LINK_INDEX_SCHEMA,
        "registry_sha256": registry_sha256,
        "v2_manifest_sha256": expected_v2,
        "input_audit_head": expected_input,
        "legacy_audit_head": expected_legacy,
        "generated_at": expected_generated,
        "page_count": len(records),
        "edge_count": len(edges),
        "edge_ids_sha256": _canonical_digest([edge["edge_id"] for edge in edges]),
        "edges_sha256": _canonical_digest(edges),
        "coverage_sha256": _canonical_digest(coverage),
        "registry_page_ids_sha256": component.get(
            "page_ids_sha256", _canonical_digest([page["page_id"] for page in records])
        ),
        "coverage_shard_count": len(coverage_shards),
        "coverage_record_count": len(coverage),
        "coverage_shards": coverage_shards,
        "shard_count": len(shards),
        "shards": shards,
    }
    component_body["index_sha256"] = _canonical_digest(component_body)
    manifest_path = ".deeplaw/derived/wiki/v3/links/manifest.json"
    manifest_bytes = canonical_json(component_body).encode("utf-8")
    if len(manifest_bytes) > MANIFEST_BYTE_LIMIT:
        raise RegistryError("link index manifest exceeds 1 MiB")
    payloads[manifest_path] = manifest_bytes
    validated = validate_link_index_component(component_body, payloads=payloads)
    handle = ValidatedLinkIndex.from_validated(
        component_body,
        validated["edges"],
        validated["coverage"],
        manifest_sha256=sha256_bytes(manifest_bytes),
    )
    return {
        "component": component_body,
        "manifest_path": manifest_path,
        "manifest_bytes": manifest_bytes,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "payloads": payloads,
        "records": records,
        "edges": handle.public_edges,
        "coverage": handle.public_coverage,
        "handle": handle,
        "registry_sha256": registry_sha256,
        "index_sha256": component_body["index_sha256"],
        "valid": True,
    }


def validate_link_index_component(
    component: Mapping[str, Any], *, payloads: Mapping[str, bytes] | None = None
) -> dict[str, Any]:
    if not isinstance(component, Mapping):
        raise RegistryError("link index component must be an object")
    expected = {
        "schema_version",
        "registry_sha256",
        "v2_manifest_sha256",
        "input_audit_head",
        "legacy_audit_head",
        "generated_at",
        "page_count",
        "edge_count",
        "edge_ids_sha256",
        "edges_sha256",
        "coverage_sha256",
        "registry_page_ids_sha256",
        "coverage_record_count",
        "coverage_shard_count",
        "coverage_shards",
        "shard_count",
        "shards",
        "index_sha256",
    }
    if set(component) != expected or component.get("schema_version") != LINK_INDEX_SCHEMA:
        raise RegistryError("invalid link index component shape")
    for name in (
        "registry_sha256",
        "v2_manifest_sha256",
        "input_audit_head",
        "legacy_audit_head",
        "edge_ids_sha256",
        "edges_sha256",
        "coverage_sha256",
        "registry_page_ids_sha256",
        "index_sha256",
    ):
        _sha(component[name], name)
    _validated_timestamp(component["generated_at"])
    if (
        not isinstance(component["page_count"], int)
        or isinstance(component["page_count"], bool)
        or not 0 <= component["page_count"] <= PUBLIC_RECORD_LIMIT
    ):
        raise RegistryError("link page_count is invalid")
    if (
        not isinstance(component["edge_count"], int)
        or isinstance(component["edge_count"], bool)
        or not 0 <= component["edge_count"] <= PUBLIC_EDGE_LIMIT
    ):
        raise RegistryError("link edge_count is invalid")
    shards = _as_records(component["shards"], field="shards")
    coverage_shards = _as_records(component["coverage_shards"], field="coverage_shards")
    if (
        not isinstance(component["shard_count"], int)
        or isinstance(component["shard_count"], bool)
        or not 0 <= component["shard_count"] <= PUBLIC_SHARD_COUNT_LIMIT
        or component["shard_count"] != len(shards)
    ):
        raise RegistryError("link shard_count mismatch")
    if (
        not isinstance(component["coverage_record_count"], int)
        or isinstance(component["coverage_record_count"], bool)
        or not 0 <= component["coverage_record_count"] <= PUBLIC_RECORD_LIMIT
        or component["coverage_record_count"] != component["page_count"]
    ):
        raise RegistryError("link coverage/page count mismatch")
    if (
        not isinstance(component["coverage_shard_count"], int)
        or isinstance(component["coverage_shard_count"], bool)
        or not 0 <= component["coverage_shard_count"] <= PUBLIC_SHARD_COUNT_LIMIT
        or component["coverage_shard_count"] != len(coverage_shards)
    ):
        raise RegistryError("link coverage shard count mismatch")
    previous_coverage = ""
    for shard in coverage_shards:
        if set(shard) != {"path", "byte_size", "sha256", "record_count", "records_sha256"}:
            raise RegistryError("coverage shard shape is invalid")
        if (
            not isinstance(shard["path"], str)
            or not _COVERAGE_SHARD_PATH.fullmatch(shard["path"])
            or shard["path"] <= previous_coverage
        ):
            raise RegistryError("coverage shard paths must be sorted and unique")
        previous_coverage = shard["path"]
        if (
            not isinstance(shard["byte_size"], int)
            or not 1 <= shard["byte_size"] <= SHARD_BYTE_LIMIT
            or not isinstance(shard["record_count"], int)
            or not 1 <= shard["record_count"] <= SHARD_RECORD_LIMIT
        ):
            raise RegistryError("coverage shard bounds are invalid")
        _sha(shard["sha256"], "coverage shard sha256")
        _sha(shard["records_sha256"], "coverage shard records_sha256")
    previous = ""
    for shard in shards:
        if set(shard) != {"path", "byte_size", "sha256", "record_count", "records_sha256"}:
            raise RegistryError("link shard shape is invalid")
        if (
            not isinstance(shard["path"], str)
            or not _SHARD_PATH.fullmatch(shard["path"])
            or shard["path"] <= previous
        ):
            raise RegistryError("link shard paths must be sorted and unique")
        previous = shard["path"]
        if (
            not isinstance(shard["byte_size"], int)
            or not 1 <= shard["byte_size"] <= SHARD_BYTE_LIMIT
        ):
            raise RegistryError("link shard byte bound is invalid")
        if (
            not isinstance(shard["record_count"], int)
            or not 1 <= shard["record_count"] <= SHARD_RECORD_LIMIT
        ):
            raise RegistryError("link shard record bound is invalid")
        _sha(shard["sha256"], "link shard sha256")
        _sha(shard["records_sha256"], "link shard records_sha256")
    body = {key: component[key] for key in expected if key != "index_sha256"}
    if _canonical_digest(body) != component["index_sha256"]:
        raise RegistryError("link index digest mismatch")
    edges: list[dict[str, Any]] = []
    if payloads is not None:
        for shard in shards:
            raw = payloads.get(shard["path"])
            if not isinstance(raw, (bytes, bytearray)) or len(raw) != shard["byte_size"]:
                raise RegistryError("link shard is missing or has wrong size")
            if sha256_bytes(bytes(raw)) != shard["sha256"]:
                raise RegistryError("link shard hash mismatch")
            decoded = strict_json_loads(raw)
            if (
                not isinstance(decoded, Mapping)
                or set(decoded) != {"schema_version", "records"}
                or decoded["schema_version"] != LINK_INDEX_SCHEMA
            ):
                raise RegistryError("link shard shape/schema mismatch")
            rows = _as_records(decoded["records"], field="link shard records")
            if (
                len(rows) != shard["record_count"]
                or _canonical_digest(rows) != shard["records_sha256"]
            ):
                raise RegistryError("link shard count/digest mismatch")
            edges.extend(_validate_edge(row) for row in rows)
        coverage: list[dict[str, Any]] = []
        for shard in coverage_shards:
            raw = payloads.get(shard["path"])
            if not isinstance(raw, (bytes, bytearray)) or len(raw) != shard["byte_size"]:
                raise RegistryError("coverage shard is missing or has wrong size")
            if sha256_bytes(bytes(raw)) != shard["sha256"]:
                raise RegistryError("coverage shard hash mismatch")
            decoded = strict_json_loads(raw)
            if (
                not isinstance(decoded, Mapping)
                or set(decoded) != {"schema_version", "coverage"}
                or decoded["schema_version"] != LINK_INDEX_SCHEMA
            ):
                raise RegistryError("coverage shard shape/schema mismatch")
            rows = _as_records(decoded["coverage"], field="coverage shard rows")
            if (
                len(rows) != shard["record_count"]
                or _canonical_digest(rows) != shard["records_sha256"]
            ):
                raise RegistryError("coverage shard count/digest mismatch")
            coverage.extend(_validate_coverage_row(row) for row in rows)
        if len(edges) != component["edge_count"] or len(
            {edge.get("edge_id") for edge in edges}
        ) != len(edges):
            raise RegistryError("link edge completeness or uniqueness failure")
        if _canonical_digest([edge["edge_id"] for edge in edges]) != component["edge_ids_sha256"]:
            raise RegistryError("edge_ids_sha256 mismatch")
        if _canonical_digest(edges) != component["edges_sha256"]:
            raise RegistryError("edges_sha256 mismatch")
        if len(coverage) != component["coverage_record_count"]:
            raise RegistryError("coverage record completeness failure")
        if [row["page_id"] for row in coverage] != sorted(row["page_id"] for row in coverage):
            raise RegistryError("coverage rows are not globally sorted")
        if len({row["page_id"] for row in coverage}) != len(coverage):
            raise RegistryError("coverage page IDs are not unique")
        if _canonical_digest(coverage) != component["coverage_sha256"]:
            raise RegistryError("coverage_sha256 mismatch")
        if (
            _canonical_digest([row["page_id"] for row in coverage])
            != component["registry_page_ids_sha256"]
        ):
            raise RegistryError("registry page coverage binding mismatch")
        by_source: dict[str, list[str]] = {}
        for edge in edges:
            by_source.setdefault(edge["source_page_id"], []).append(edge["edge_id"])
        for row in coverage:
            source_edges = sorted(by_source.get(row["page_id"], []))
            if (
                row["link_count"] != len(source_edges)
                or _canonical_digest(source_edges) != row["edge_ids_sha256"]
            ):
                raise RegistryError("link coverage edge completeness failure")
    return {
        "component": dict(component),
        "edges": edges,
        "coverage": coverage if payloads is not None else None,
        "valid": True,
    }


def _cursor_encode(
    manifest_sha256: str,
    target_page_id: str,
    direction: str,
    offset: int,
    limit: int,
) -> str:
    body = {
        "manifest_sha256": manifest_sha256,
        "target_page_id": target_page_id,
        "direction": direction,
        "offset": offset,
        "limit": limit,
    }
    return (
        base64.urlsafe_b64encode(canonical_json(body).encode("utf-8")).decode("ascii").rstrip("=")
    )


def _cursor_decode(value: str) -> dict[str, Any]:
    if not isinstance(value, str) or len(value) > 2_000:
        raise RegistryError("cursor is invalid")
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = strict_json_loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception as error:
        raise RegistryError("cursor is invalid") from error
    if not isinstance(decoded, Mapping) or set(decoded) != {
        "manifest_sha256", "target_page_id", "direction", "offset", "limit"
    }:
        raise RegistryError("cursor shape is invalid")
    _sha(decoded["manifest_sha256"], "cursor manifest_sha256")
    _id(decoded["target_page_id"], "cursor target_page_id")
    if decoded["direction"] not in {"backlinks", "outlinks"}:
        raise RegistryError("cursor direction is invalid")
    if not isinstance(decoded["offset"], int) or decoded["offset"] < 0:
        raise RegistryError("cursor offset is invalid")
    if (
        not isinstance(decoded["limit"], int)
        or isinstance(decoded["limit"], bool)
        or not 1 <= decoded["limit"] <= 2_000
    ):
        raise RegistryError("cursor limit is invalid")
    return dict(decoded)


def _index_unavailable(gap: str) -> dict[str, Any]:
    return {
        "status": "index_unavailable",
        "index_used": False,
        "gap": gap,
        "total_count": 0,
        "links": [],
        "cursor": None,
        "truncated": False,
        "truncation_reason": "index_unavailable",
    }


def query_links(
    link_index: Mapping[str, Any],
    target_page_id: str,
    *,
    direction: str = "backlinks",
    limit: int = 100,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Query exact indexed backlinks/outlinks with a manifest-bound cursor."""

    _id(target_page_id, "target_page_id")
    if direction not in {"backlinks", "outlinks"}:
        raise RegistryError("direction must be backlinks or outlinks")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2_000:
        raise RegistryError("limit is out of bounds")
    component_value = link_index.get("component", {})
    if not isinstance(component_value, Mapping):
        return _index_unavailable("living_wiki_link_index_not_loaded")
    component = dict(component_value)
    edges = link_index.get("edges")
    coverage = link_index.get("coverage", component.get("coverage"))
    handle = link_index.get("handle")
    manifest_sha: str

    if isinstance(handle, ValidatedLinkIndex):
        # A builder/loader-provided handle carries immutable validated snapshots and adjacency.
        # Do only O(1)/descriptor-sized binding checks here; never rescan edge or shard payloads.
        try:
            if link_index.get("valid") is not True or handle.dirty:
                raise RegistryError("validated link handle is dirty or unavailable")
            if edges is not handle.public_edges or coverage is not handle.public_coverage:
                raise RegistryError("link handle is not bound to public state")
            if _plain(handle.component) != component:
                raise RegistryError("link handle component binding mismatch")
            _sha(component.get("index_sha256"), "index_sha256")
            _sha(component.get("registry_sha256"), "registry_sha256")
            if handle.index_sha256 != component["index_sha256"]:
                raise RegistryError("link handle index digest mismatch")
            manifest_sha = str(link_index.get("manifest_sha256", handle.manifest_sha256))
            _sha(manifest_sha, "link manifest_sha256")
            if manifest_sha != handle.manifest_sha256:
                raise RegistryError("link handle manifest binding mismatch")
            manifest_bytes = link_index.get("manifest_bytes")
            if isinstance(manifest_bytes, (bytes, bytearray)) and sha256_bytes(
                bytes(manifest_bytes)
            ) != manifest_sha:
                raise RegistryError("link manifest digest mismatch")
            filtered = list(handle.query(target_page_id, direction))
        except (RegistryError, TypeError, ValueError):
            return _index_unavailable("living_wiki_link_index_invalid_or_stale")
    else:
        # A plain dict remains accepted for compatibility, but is fully validated once per query
        # and therefore is not the production fast path.
        if (
            not isinstance(edges, Sequence)
            or not isinstance(coverage, Sequence)
            or link_index.get("valid") is not True
        ):
            return _index_unavailable("living_wiki_link_index_not_loaded")
        try:
            _sha(component.get("index_sha256"), "index_sha256")
            _sha(component.get("registry_sha256"), "registry_sha256")
            validate_link_index_component(component)
            payloads = link_index.get("payloads")
            if not isinstance(payloads, Mapping):
                raise RegistryError("link index shards are not materialized")
            loaded = validate_link_index_component(component, payloads=payloads)
            if loaded["edges"] != list(edges) or loaded["coverage"] != list(coverage):
                raise RegistryError("materialized link index differs from query state")
            manifest_bytes = link_index.get("manifest_bytes")
            if isinstance(manifest_bytes, (bytes, bytearray)) and link_index.get(
                "manifest_sha256"
            ) != sha256_bytes(bytes(manifest_bytes)):
                raise RegistryError("link manifest digest mismatch")
            edges = [_validate_edge(edge) for edge in edges]
            if (
                _canonical_digest([edge.get("edge_id") for edge in edges])
                != component["edge_ids_sha256"]
            ):
                raise RegistryError("edge digest mismatch")
            if _canonical_digest(list(edges)) != component["edges_sha256"]:
                raise RegistryError("edge content digest mismatch")
            if _canonical_digest(list(coverage)) != component["coverage_sha256"]:
                raise RegistryError("coverage digest mismatch")
            by_source: dict[str, list[str]] = {}
            for edge in edges:
                by_source.setdefault(edge["source_page_id"], []).append(edge["edge_id"])
            for row in coverage:
                source_edges = sorted(by_source.get(row["page_id"], []))
                if (
                    row["link_count"] != len(source_edges)
                    or _canonical_digest(source_edges) != row["edge_ids_sha256"]
                ):
                    raise RegistryError("coverage edge completeness failure")
            manifest_sha = str(link_index.get("manifest_sha256", component["index_sha256"]))
            _sha(manifest_sha, "link manifest_sha256")
            filtered = [
                edge
                for edge in edges
                if (
                    edge.get("target_page_ids") and target_page_id in edge["target_page_ids"]
                    if direction == "backlinks"
                    else edge.get("source_page_id") == target_page_id
                )
            ]
            filtered.sort(key=lambda edge: edge.get("edge_id", ""))
        except (RegistryError, TypeError, ValueError):
            return _index_unavailable("living_wiki_link_index_invalid_or_stale")

    offset = 0
    if cursor is not None:
        decoded = _cursor_decode(cursor)
        if (
            decoded["manifest_sha256"] != manifest_sha
            or decoded["target_page_id"] != target_page_id
            or decoded["direction"] != direction
            or decoded["limit"] != limit
        ):
            raise RegistryError("cursor is bound to another index, target, direction, or limit")
        offset = decoded["offset"]
    page = filtered[offset : offset + limit]
    next_offset = offset + len(page)
    truncated = next_offset < len(filtered)
    next_cursor = (
        _cursor_encode(manifest_sha, target_page_id, direction, next_offset, limit)
        if truncated
        else None
    )
    page = [_plain(edge) for edge in page]
    return {
        "status": "ok",
        "index_used": True,
        "total_count": len(filtered),
        "links": page,
        "cursor": next_cursor,
        "truncated": truncated,
        "truncation_reason": "page_limit" if truncated else None,
    }


def load_link_index(
    root: Path, manifest: Mapping[str, Any], registry: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    validate_living_wiki_manifest_v3(manifest)
    component = next(row for row in manifest["components"] if row["component"] == "link_index")
    manifest_bytes = _safe_read_file(
        root,
        component["manifest_path"],
        max_bytes=MANIFEST_BYTE_LIMIT,
        field="link index manifest",
    )
    if (
        len(manifest_bytes) != component["manifest_byte_size"]
        or sha256_bytes(manifest_bytes) != component["manifest_sha256"]
    ):
        raise RegistryError("link index manifest hash mismatch")
    component_body = strict_json_loads(manifest_bytes)
    validate_link_index_component(component_body)
    if (
        component_body["input_audit_head"] != manifest["input_audit_head"]
        or component_body["legacy_audit_head"] != manifest["legacy_audit_head"]
        or component_body["v2_manifest_sha256"] != manifest["v2_manifest_sha256"]
    ):
        raise RegistryError("link index audit/v2 binding mismatch")
    if component_body["edge_count"] != component["record_count"]:
        raise RegistryError("link index descriptor count mismatch")
    if component_body["shard_count"] != component["shard_count"]:
        raise RegistryError("link index descriptor shard count mismatch")
    if component_body["index_sha256"] != component["registry_or_index_sha256"]:
        raise RegistryError("link index descriptor digest mismatch")
    for name in (
        "page_count", "edge_count", "edge_ids_sha256", "edges_sha256", "coverage_record_count",
        "coverage_shard_count", "coverage_sha256", "registry_page_ids_sha256",
    ):
        if name in component and component_body.get(name) != component[name]:
            raise RegistryError(f"link index descriptor {name} mismatch")
    if registry is not None:
        registry_sha256 = registry.get(
            "registry_sha256", registry.get("component", {}).get("registry_sha256")
        )
        if registry_sha256 != component_body["registry_sha256"]:
            raise RegistryError("link index registry binding mismatch")
        registry_ids_sha256 = registry.get("component", {}).get("page_ids_sha256")
        if registry_ids_sha256 != component_body["registry_page_ids_sha256"]:
            raise RegistryError("link index page coverage binding mismatch")
    payloads: dict[str, bytes] = {}
    for shard in [*component_body["shards"], *component_body["coverage_shards"]]:
        raw = _safe_read_file(
            root,
            shard["path"],
            max_bytes=SHARD_BYTE_LIMIT,
            field="link index or coverage shard",
        )
        payloads[shard["path"]] = raw
        if len(raw) != shard["byte_size"] or sha256_bytes(raw) != shard["sha256"]:
            raise RegistryError("link index or coverage shard hash/size mismatch")
    loaded = validate_link_index_component(component_body, payloads=payloads)
    handle = ValidatedLinkIndex.from_validated(
        component_body,
        loaded["edges"],
        loaded["coverage"],
        manifest_sha256=component["manifest_sha256"],
    )
    return {
        "component": component_body,
        "edges": handle.public_edges,
        "coverage": handle.public_coverage,
        "handle": handle,
        "manifest_sha256": component["manifest_sha256"],
        "index_sha256": component_body["index_sha256"],
        "registry_sha256": component_body["registry_sha256"],
        "payloads": payloads,
        "valid": True,
    }


__all__ = [
    "LINK_INDEX_SCHEMA",
    "ValidatedLinkIndex",
    "build_link_index",
    "load_link_index",
    "query_links",
    "validate_link_index_component",
]
