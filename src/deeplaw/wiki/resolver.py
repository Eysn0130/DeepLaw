"""Fail-closed stable identity resolver for the additive Living Wiki indexes."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..util import canonical_json, sha256_bytes
from .registry import (
    MANIFEST_BYTE_LIMIT,
    PUBLIC_RECORD_LIMIT,
    RegistryError,
    _canonical_digest,
    _safe_read_file,
    _sha,
    _validated_timestamp,
    validate_living_wiki_manifest_v3,
    validate_page_record,
)

RESOLVER_SCHEMA = "deeplaw.living-wiki-resolver/v1"
_MAX_CANDIDATES = PUBLIC_RECORD_LIMIT
_SCOPE_ORDER = {"personal": 0, "project": 1, "domain": 2}
_SENSITIVITY_ORDER = {"public": 0, "internal": 1, "private": 2, "restricted": 3}
_RECEIPT = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_IDENTITY_FIELDS = (
    "knowledge_id",
    "revision_id",
    "semantic_key",
    "wiki_path",
    "wikilink",
    "source_fragment",
    "source_revision_id",
    "alias",
)
_ADMISSION_FIELDS = {
    "allowed_scopes",
    "scope",
    "max_sensitivity",
    "sensitivity",
    "allowed_lifecycles",
    "allowed_freshness",
}
_DEFERRED_FIELDS = {
    "authoritative_segment",
    "statement_target",
    "statement_id",
    "statement_key",
    "statement_evidence",
    "semantic_target",
}
_QUERY_FIELDS = set(_IDENTITY_FIELDS) | _ADMISSION_FIELDS | _DEFERRED_FIELDS


def _query_key(value: Any, field: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 2_000:
        raise RegistryError(f"{field} must be a bounded string")
    if _CONTROL.search(value):
        raise RegistryError(f"{field} contains control characters")
    normalized = value.strip()
    if not normalized:
        raise RegistryError(f"{field} must not be blank")
    return normalized


def _source_fragment_key(value: Any) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise RegistryError("source_fragment query must be an object")
    if set(value) - {"source_revision_id", "fragment_id", "fragment_revision_id"}:
        raise RegistryError("source_fragment query has unknown fields")
    source_revision_id = _query_key(value.get("source_revision_id"), "source_revision_id")
    fragment_id = value.get("fragment_id")
    fragment_revision_id = value.get("fragment_revision_id")
    if (fragment_id is None) == (fragment_revision_id is None):
        raise RegistryError(
            "source_fragment requires source revision and exactly one fragment identity"
        )
    return source_revision_id, _query_key(fragment_id or fragment_revision_id, "fragment identity")


def _canonical_query(query: Mapping[str, Any]) -> dict[str, Any]:
    if any(not isinstance(key, str) for key in query):
        raise RegistryError("resolver query keys must be strings")
    return {key: query[key] for key in sorted(query)}


def _admission_context(query: Mapping[str, Any]) -> tuple[set[str], int, set[str], set[str]]:
    if "allowed_scopes" in query and "scope" in query:
        raise RegistryError("allowed_scopes and scope are mutually exclusive")
    if "max_sensitivity" in query and "sensitivity" in query:
        raise RegistryError("max_sensitivity and sensitivity are mutually exclusive")
    allowed_scopes = query.get("allowed_scopes", query.get("scope"))
    if allowed_scopes is None:
        # The resolver itself is an index seam.  The caller must provide a scope in a production
        # read path; absent context follows the canonical local Knowledge OS default scope.
        scopes = {"project"}
    elif isinstance(allowed_scopes, str):
        scopes = {_query_key(allowed_scopes, "scope")}
    elif isinstance(allowed_scopes, Sequence):
        if any(not isinstance(scope, str) for scope in allowed_scopes):
            raise RegistryError("allowed_scopes is invalid")
        scopes = {_query_key(scope, "scope") for scope in allowed_scopes}
    else:
        raise RegistryError("allowed_scopes must be a string or array")
    if not scopes or not scopes <= set(_SCOPE_ORDER):
        raise RegistryError("allowed_scopes is invalid")
    max_sensitivity = query.get("max_sensitivity", query.get("sensitivity", "private"))
    if not isinstance(max_sensitivity, str):
        raise RegistryError("max_sensitivity is invalid")
    max_sensitivity = _query_key(max_sensitivity, "max_sensitivity")
    if max_sensitivity not in _SENSITIVITY_ORDER:
        raise RegistryError("max_sensitivity is invalid")
    lifecycles = query.get("allowed_lifecycles", {"active"})
    freshness = query.get("allowed_freshness", {"fresh"})
    if isinstance(lifecycles, str):
        lifecycles = {lifecycles}
    if isinstance(freshness, str):
        freshness = {freshness}
    if not isinstance(lifecycles, Sequence) and not isinstance(lifecycles, set):
        raise RegistryError("allowed_lifecycles is invalid")
    if not isinstance(freshness, Sequence) and not isinstance(freshness, set):
        raise RegistryError("allowed_freshness is invalid")
    if any(not isinstance(value, str) for value in lifecycles) or any(
        not isinstance(value, str) for value in freshness
    ):
        raise RegistryError("admission filters are invalid")
    lifecycle_set = {_query_key(value, "lifecycle") for value in lifecycles}
    freshness_set = {_query_key(value, "freshness") for value in freshness}
    if not lifecycle_set or not lifecycle_set <= {
        "active",
        "superseded",
        "revoked",
        "expired",
        "forgotten",
        "archived",
        "quarantined",
    }:
        raise RegistryError("allowed_lifecycles is invalid")
    if not freshness_set or not freshness_set <= {"fresh", "stale", "unknown", "invalidated"}:
        raise RegistryError("allowed_freshness is invalid")
    return scopes, _SENSITIVITY_ORDER[max_sensitivity], lifecycle_set, freshness_set


def _candidate(
    page: Mapping[str, Any], reason: str, anchor: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    current_revision: str | dict[str, str] | None
    if page.get("revision_id") is not None:
        current_revision = page["revision_id"]
    else:
        fragment = dict(page["source_fragment"])
        current_revision = fragment
    result = {
        "page_id": page["page_id"],
        "canonical_page_path": page["canonical_page_path"],
        "namespace": page["namespace"],
        "kind": page["kind"],
        "title": page.get("title"),
        "current_revision": current_revision,
        "freshness": page["freshness"],
        "sensitivity": page["sensitivity"],
        "scope": page["scope"],
        "lifecycle": page["lifecycle"],
        "audit_head": page["audit_head"],
        "reason": reason,
        "authority": "none",
    }
    if anchor is not None:
        result["anchor"] = dict(anchor)
    return result


def _opaque_receipt(value: Any) -> dict[str, Any]:
    # Never echo an authoritative payload.  The resolver can carry an opaque receipt commitment
    # for audit correlation, but it cannot turn it into legal Authority.
    if isinstance(value, str):
        digest = sha256_bytes(value.encode("utf-8"))
    else:
        digest = _canonical_digest(value)
    return {
        "namespace": "authoritative_segment",
        "receipt_sha256": digest,
        "legal_authority": False,
    }


def _suppression_receipt(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Commit to denied identities without returning identity, count, or policy details."""

    page_ids = sorted(candidate["page_id"] for candidate in candidates)
    return {
        "present": bool(page_ids),
        "digest_sha256": _canonical_digest(page_ids),
    }


class StableResolver:
    """Resolve only explicit canonical identity keys from a validated registry."""

    def __init__(self, registry: Mapping[str, Any], *, index_sha256: str | None = None) -> None:
        records = registry.get("records")
        component = registry.get("component", {})
        if not isinstance(records, Sequence) or not isinstance(component, Mapping):
            raise RegistryError("validated page registry is required")
        if len(records) > PUBLIC_RECORD_LIMIT:
            raise RegistryError("candidate count exceeds public limit")
        self.records = [validate_page_record(row) for row in records]
        self.records.sort(key=lambda row: row["page_id"])
        self._records_by_id = {page["page_id"]: page for page in self.records}
        self.registry_sha256 = registry.get("registry_sha256", component.get("registry_sha256"))
        _sha(self.registry_sha256, "registry_sha256")
        self.v2_manifest_sha256 = component.get("v2_manifest_sha256")
        self.input_audit_head = component.get("input_audit_head")
        self.legacy_audit_head = component.get("legacy_audit_head")
        self.generated_at = component.get("generated_at")
        _sha(self.v2_manifest_sha256, "v2_manifest_sha256")
        _sha(self.input_audit_head, "input_audit_head")
        _sha(self.legacy_audit_head, "legacy_audit_head")
        _validated_timestamp(self.generated_at)
        self.index_sha256 = index_sha256
        self._keys: dict[str, dict[str, list[tuple[str, str, dict[str, Any] | None]]]] = {
            key: {}
            for key in (
                "page_id",
                "knowledge_id",
                "revision_id",
                "semantic_key",
                "wiki_path",
                "wikilink",
                "source_fragment",
                "source_revision_id",
                "alias",
            )
        }
        for page in self.records:
            self._add("page_id", page["page_id"], page, "page_id")
            if page.get("knowledge_id"):
                self._add("knowledge_id", page["knowledge_id"], page, "knowledge_id")
            elif page["namespace"] == "knowledge":
                self._add("knowledge_id", page["page_id"], page, "knowledge_id")
            if page.get("revision_id"):
                self._add("revision_id", page["revision_id"], page, "revision_id")
            if page.get("semantic_key"):
                self._add("semantic_key", page["semantic_key"], page, "semantic_key")
            # ``source_revision_id`` resolves the canonical Source page only.  Fragment pages
            # remain addressable through their composite ``source_fragment`` identity and must
            # not make a source-revision lookup spuriously ambiguous.
            if page["namespace"] == "source" and page.get("revision_id"):
                self._add(
                    "source_revision_id",
                    page["revision_id"],
                    page,
                    "source_revision_id",
                )
            self._add("wiki_path", page["canonical_page_path"], page, "wiki_path")
            self._add("wikilink", page["canonical_page_path"], page, "wiki_path")
            self._add(
                "wikilink", page["canonical_page_path"].removesuffix(".md"), page, "wiki_path"
            )
            for alias in page.get("aliases", []):
                self._add("alias", alias, page, "alias")
                self._add("wikilink", alias, page, "alias")
            fragment = page.get("source_fragment")
            if fragment:
                fragment_identity = fragment.get(
                    "fragment_id", fragment.get("fragment_revision_id")
                )
                fragment_key = f"{fragment['source_revision_id']}:{fragment_identity}"
                self._add("source_fragment", fragment_key, page, "source_fragment")
            for anchor in page.get("anchors", []):
                fragment = anchor.get("source_fragment")
                if fragment:
                    fragment_identity = fragment.get(
                        "fragment_id", fragment.get("fragment_revision_id")
                    )
                    fragment_key = f"{fragment['source_revision_id']}:{fragment_identity}"
                    self._add("source_fragment", fragment_key, page, "source_fragment", anchor)

    def _add(
        self,
        kind: str,
        key: str,
        page: Mapping[str, Any],
        reason: str,
        anchor: Mapping[str, Any] | None = None,
    ) -> None:
        self._keys[kind].setdefault(key, []).append(
            (page["page_id"], reason, dict(anchor) if anchor else None)
        )

    def _resolve_candidates(self, kind: str, key: str) -> list[dict[str, Any]]:
        rows = list(self._keys.get(kind, {}).get(key, []))
        # Some records can be indexed by several identity channels.  Keep deterministic order and
        # avoid duplicate candidate IDs while preserving the strongest first reason.
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str | None]] = set()
        for page_id, reason, anchor in sorted(
            rows, key=lambda row: (row[0], row[2].get("anchor_id", "") if row[2] else "")
        ):
            identity = (page_id, anchor.get("anchor_id") if anchor else None)
            if identity not in seen:
                result.append(_candidate(self._records_by_id[page_id], reason, anchor))
                seen.add(identity)
        return result

    def resolve(
        self,
        query: Mapping[str, Any] | None = None,
        *,
        limit: int = 20,
        **identity: Any,
    ) -> dict[str, Any]:
        query_error: str | None = None
        if query is not None and not isinstance(query, Mapping):
            incoming = {}
            query_error = "resolver query must be an object"
        else:
            incoming = dict(query or {})
            overlap = set(incoming) & set(identity)
            if overlap:
                query_error = "resolver identity was supplied more than once"
            incoming.update(identity)
        try:
            query_hash = _canonical_digest(_canonical_query(incoming))
        except (RegistryError, TypeError, ValueError):
            query_hash = _canonical_digest({"invalid_query": True})
            query_error = query_error or "resolver query is not canonical"
        receipt: dict[str, Any] = {
            "resolver_schema": RESOLVER_SCHEMA,
            "registry_sha256": self.registry_sha256,
            "legal_authority": False,
        }
        base = {
            "status": "not_found",
            "candidate_count": 0,
            "candidates": [],
            "candidates_truncated": False,
            "truncation_reason": None,
            "ambiguity": None,
            "admission": {"admitted": False, "reason": "not_found"},
            "query_sha256": query_hash,
            "receipt": receipt,
        }
        if query_error is None:
            unknown = set(incoming) - _QUERY_FIELDS
            if any(not isinstance(key, str) for key in incoming) or unknown:
                query_error = "resolver query contains unknown fields"
        if query_error is not None:
            base["status"] = "invalid"
            base["admission"] = {"admitted": False, "reason": query_error}
            return base
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2_000:
            base["status"] = "invalid"
            base["admission"] = {"admitted": False, "reason": "invalid_limit"}
            return base
        authoritative = incoming.get("authoritative_segment")
        identity_present = [field for field in _IDENTITY_FIELDS if field in incoming]
        deferred_present = [field for field in _DEFERRED_FIELDS if field in incoming]
        if deferred_present and (len(deferred_present) != 1 or identity_present):
            base["status"] = "invalid"
            base["admission"] = {
                "admitted": False,
                "reason": "one canonical identity or deferred target is required",
            }
            return base
        if authoritative is not None:
            if not isinstance(authoritative, Mapping) or set(authoritative) != {"receipt"}:
                base["status"] = "invalid"
                base["admission"] = {"admitted": False, "reason": "opaque_receipt_required"}
                return base
            base["status"] = "index_unavailable"
            base["admission"] = {"admitted": False, "reason": "authoritative_segment_deferred"}
            base["receipt"] = _opaque_receipt(authoritative["receipt"])
            return base
        if deferred_present and deferred_present[0] != "authoritative_segment":
            base["status"] = "index_unavailable"
            base["admission"] = {"admitted": False, "reason": "statement_map_deferred"}
            base["receipt"]["gap"] = "statement_semantic_target_not_indexed"
            return base
        try:
            scopes, max_sensitivity, lifecycles, freshness = _admission_context(incoming)
        except RegistryError as error:
            base["status"] = "invalid"
            base["admission"] = {"admitted": False, "reason": str(error)}
            return base
        if len(identity_present) != 1:
            base["status"] = "invalid"
            base["admission"] = {
                "admitted": False,
                "reason": "exactly one canonical identity is required",
            }
            return base
        identity_keys = [(field, field) for field in _IDENTITY_FIELDS]
        chosen: tuple[str, str] | None = None
        for kind, field in identity_keys:
            if field not in incoming:
                continue
            if kind == "source_fragment":
                try:
                    source_revision_id, fragment_id = _source_fragment_key(incoming[field])
                except RegistryError as error:
                    base["status"] = "invalid"
                    base["admission"] = {"admitted": False, "reason": str(error)}
                    return base
                chosen = (kind, f"{source_revision_id}:{fragment_id}")
            else:
                try:
                    value = incoming[field]
                    if kind == "wikilink" and isinstance(value, str):
                        value = value.strip()
                        if value.startswith("[[") and value.endswith("]]"):
                            value = value[2:-2].strip()
                        if "|" in value:
                            value = value.split("|", 1)[0].strip()
                        if "#" in value:
                            value = value.split("#", 1)[0].strip()
                    chosen = (kind, _query_key(value, field))
                except RegistryError as error:
                    base["status"] = "invalid"
                    base["admission"] = {"admitted": False, "reason": str(error)}
                    return base
            break
        if chosen is None and "alias" in incoming:
            try:
                chosen = ("alias", _query_key(incoming["alias"], "alias"))
            except RegistryError as error:
                base["status"] = "invalid"
                base["admission"] = {"admitted": False, "reason": str(error)}
                return base
        if chosen is None:
            base["status"] = "invalid"
            base["admission"] = {"admitted": False, "reason": "one canonical identity is required"}
            return base
        candidates = self._resolve_candidates(*chosen)
        if not candidates:
            return base
        by_id = self._records_by_id
        admission_failures: dict[str, str] = {}
        admitted: list[dict[str, Any]] = []
        for candidate in candidates:
            page = by_id[candidate["page_id"]]
            if page["scope"] not in scopes:
                admission_failures[candidate["page_id"]] = "scope_denied"
            elif _SENSITIVITY_ORDER[page["sensitivity"]] > max_sensitivity:
                admission_failures[candidate["page_id"]] = "sensitivity_denied"
            elif page["lifecycle"] not in lifecycles:
                admission_failures[candidate["page_id"]] = "lifecycle_denied"
            elif page["freshness"] not in freshness:
                admission_failures[candidate["page_id"]] = "freshness_denied"
            else:
                admitted.append(candidate)
        denied = [
            candidate for candidate in candidates if candidate["page_id"] in admission_failures
        ]
        if denied:
            base["receipt"]["suppressed_candidates"] = _suppression_receipt(denied)
        if not admitted:
            all_stale = all(
                admission_failures.get(candidate["page_id"]) == "freshness_denied"
                and by_id[candidate["page_id"]]["freshness"] in {"stale", "invalidated"}
                for candidate in candidates
            )
            if len(candidates) == 1:
                reason = admission_failures.get(candidates[0]["page_id"], "not_admitted")
                status = "stale" if all_stale else "not_admitted"
            else:
                reason = "stale" if all_stale else "not_admitted"
                status = reason
            base["status"] = status
            base["admission"] = {"admitted": False, "reason": reason}
            return base
        base["candidate_count"] = len(admitted)
        base["candidates"] = admitted[:limit]
        base["candidates_truncated"] = len(admitted) > limit
        base["truncation_reason"] = "candidate_limit" if base["candidates_truncated"] else None
        if len(admitted) > 1:
            base["status"] = "ambiguous"
            base["ambiguity"] = {
                "reason": "multiple_candidates",
                "candidate_count": len(admitted),
            }
            base["admission"] = {"admitted": False, "reason": "ambiguous"}
            return base
        base["status"] = "resolved"
        base["admission"] = {"admitted": True, "reason": "canonical_identity"}
        base["candidates"] = admitted[:limit]
        base["candidate_count"] = len(admitted)
        base["receipt"]["resolved_page_id"] = admitted[0]["page_id"]
        return base


def build_resolver_index(registry: Mapping[str, Any]) -> dict[str, Any]:
    resolver = StableResolver(registry)
    candidate_ids = [page["page_id"] for page in resolver.records]
    body: dict[str, Any] = {
        "schema_version": RESOLVER_SCHEMA,
        "registry_sha256": resolver.registry_sha256,
        "v2_manifest_sha256": resolver.v2_manifest_sha256,
        "input_audit_head": resolver.input_audit_head,
        "legacy_audit_head": resolver.legacy_audit_head,
        "generated_at": resolver.generated_at,
        "candidate_count": len(candidate_ids),
        "candidate_ids_sha256": _canonical_digest(candidate_ids),
    }
    body["index_sha256"] = _canonical_digest(body)
    data = canonical_json(body).encode("utf-8")
    if len(data) > MANIFEST_BYTE_LIMIT:
        raise RegistryError("resolver manifest exceeds 1 MiB")
    manifest_path = ".deeplaw/derived/wiki/v3/resolver/manifest.json"
    return {
        "component": body,
        "manifest_path": manifest_path,
        "manifest_bytes": data,
        "payloads": {manifest_path: data},
        "records": resolver.records,
        "resolver": resolver,
        "registry_sha256": resolver.registry_sha256,
        "index_sha256": body["index_sha256"],
        "valid": True,
    }


def validate_resolver_component(component: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version",
        "registry_sha256",
        "v2_manifest_sha256",
        "input_audit_head",
        "legacy_audit_head",
        "generated_at",
        "candidate_count",
        "candidate_ids_sha256",
        "index_sha256",
    }
    if (
        not isinstance(component, Mapping)
        or set(component) != expected
        or component.get("schema_version") != RESOLVER_SCHEMA
    ):
        raise RegistryError("invalid resolver component shape")
    for field in (
        "registry_sha256",
        "v2_manifest_sha256",
        "input_audit_head",
        "legacy_audit_head",
        "candidate_ids_sha256",
        "index_sha256",
    ):
        _sha(component[field], field)
    _validated_timestamp(component["generated_at"])
    if (
        not isinstance(component["candidate_count"], int)
        or isinstance(component["candidate_count"], bool)
        or not 0 <= component["candidate_count"] <= _MAX_CANDIDATES
    ):
        raise RegistryError("resolver candidate_count is invalid")
    body = {key: component[key] for key in expected if key != "index_sha256"}
    if _canonical_digest(body) != component["index_sha256"]:
        raise RegistryError("resolver index digest mismatch")
    return {"component": dict(component), "valid": True}


def load_resolver(
    root: Path, manifest: Mapping[str, Any], registry: Mapping[str, Any]
) -> StableResolver:
    from ..util import strict_json_loads

    validate_living_wiki_manifest_v3(manifest)
    component = next(row for row in manifest["components"] if row["component"] == "resolver")
    raw = _safe_read_file(
        root,
        component["manifest_path"],
        max_bytes=MANIFEST_BYTE_LIMIT,
        field="resolver index manifest",
    )
    if (
        len(raw) != component["manifest_byte_size"]
        or sha256_bytes(raw) != component["manifest_sha256"]
    ):
        raise RegistryError("resolver index manifest hash mismatch")
    decoded = strict_json_loads(raw)
    validate_resolver_component(decoded)
    if (
        decoded["input_audit_head"] != manifest["input_audit_head"]
        or decoded["legacy_audit_head"] != manifest["legacy_audit_head"]
        or decoded["v2_manifest_sha256"] != manifest["v2_manifest_sha256"]
    ):
        raise RegistryError("resolver audit/v2 binding mismatch")
    if decoded["candidate_count"] != component["record_count"]:
        raise RegistryError("resolver candidate count binding mismatch")
    if component["shard_count"] != 0:
        raise RegistryError("resolver descriptor shard count mismatch")
    if decoded["index_sha256"] != component["registry_or_index_sha256"]:
        raise RegistryError("resolver descriptor digest mismatch")
    for field in ("candidate_count", "candidate_ids_sha256"):
        if field in component and decoded.get(field) != component[field]:
            raise RegistryError(f"resolver descriptor {field} mismatch")
    if decoded["registry_sha256"] != registry.get(
        "registry_sha256", registry.get("component", {}).get("registry_sha256")
    ):
        raise RegistryError("resolver registry binding mismatch")
    return StableResolver(registry, index_sha256=decoded["index_sha256"])


__all__ = [
    "RESOLVER_SCHEMA",
    "StableResolver",
    "build_resolver_index",
    "load_resolver",
    "validate_resolver_component",
]
