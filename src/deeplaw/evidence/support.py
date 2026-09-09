"""Bounded support evaluation over the existing immutable revisions and dependencies.

This module is read-only. It does not infer edges, repair knowledge or mutate the
Ledger; the compilation Coordinator remains the sole publication owner.
"""
from __future__ import annotations

from typing import Any

from ..knowledge_models import utc_now
from ..util import canonical_json, sha256_bytes, strict_json_loads

_ORDER = {"fresh": 0, "unknown": 1, "stale": 2, "invalidated": 3}


class SupportEvaluator:
    def __init__(
        self, store: Any, *, scope: str, sensitivity: str, limit: int = 256,
        source_overrides: dict[str, str] | None = None,
        as_of: str | None = None, legacy_audit_head: str | None = None,
    ):
        self.store = store
        self.source_overrides = source_overrides or {}
        self.scope = scope
        self.sensitivity = sensitivity
        self.remaining = min(256, max(0, limit))
        self.visiting: set[tuple[str, str]] = set()
        self.as_of = as_of
        self.legacy_audit_head = legacy_audit_head
        self.now = as_of or utc_now()

    def _admitted(self, row: Any) -> bool:
        levels = ("public", "internal", "private", "restricted")
        return bool(
            row is not None
            and row["scope"] == self.scope
            and self.sensitivity in levels
            and row["sensitivity"] in levels[:3]
            and levels.index(row["sensitivity"]) <= levels.index(self.sensitivity)
            and row["lifecycle"] == "active"
            and (row["valid_from"] is None or row["valid_from"] <= self.now)
            and (row["valid_to"] is None or row["valid_to"] > self.now)
        )

    def _current_at(self, row: Any, kind: str, identity: str) -> bool:
        if self.as_of is None:
            return row["current_revision_id"] == identity
        if kind == "knowledge":
            table, key, revision = "knowledge_revisions_v3", "knowledge_id", "revision_id"
        else:
            table, key, revision = (
                "knowledge_relation_revisions_v3", "relation_key", "relation_revision_id"
            )
        current = self.store.connection.execute(
            f"SELECT {revision} FROM {table} WHERE {key} = ? AND recorded_at <= ? "
            f"ORDER BY recorded_at DESC, {revision} DESC LIMIT 1", (row[key], self.as_of),
        ).fetchone()
        return current is not None and current[0] == identity

    def _take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True

    def source(
        self, consumer: str, reference: dict[str, Any],
        *, consumer_kind: str = "knowledge_revision",
    ) -> str:
        if not self._take():
            return "unknown"
        row = self.store.connection.execute(
            """SELECT dependency_id, freshness FROM knowledge_dependencies_v1
               WHERE consumer_kind = ? AND consumer_revision_id = ?
                 AND source_revision_id = ? AND fragment_id IS ?
                 AND dependency_kind = 'direct'""",
            (
                consumer_kind, consumer, reference["source_revision_id"],
                reference.get("fragment_id"),
            ),
        ).fetchone()
        if row is None:
            return "unknown"
        if self.as_of is not None:
            return "fresh" if self.store._source_reference_is_bound(
                reference, scope=self.scope, max_sensitivity=self.sensitivity,
                as_of=self.as_of, legacy_audit_head=self.legacy_audit_head,
            ) else "unknown"
        state = self.source_overrides.get(row["dependency_id"], row["freshness"])
        if state != "fresh":
            return state if state in _ORDER else "unknown"
        if row["dependency_id"] in self.source_overrides:
            # Only the Coordinator supplies prospective states, derived from
            # its exact same-identity Source successor diff. Original bytes,
            # scope and sensitivity must still bind before a fresh override.
            return "fresh" if self.store._source_reference_is_bound(
                reference, scope=self.scope, max_sensitivity=self.sensitivity,
                require_active=False,
            ) else "invalidated"
        if not self.store._source_reference_is_bound(
            reference, scope=self.scope, max_sensitivity=self.sensitivity,
        ) and not self.store._successor_equivalent_reference_is_admitted(
            reference, consumer_kind=consumer_kind, consumer_revision_id=consumer,
            scope=self.scope, max_sensitivity=self.sensitivity,
        ):
            return "invalidated"
        return "fresh"

    def revision(
        self, kind: str, identity: str, *, require_grounded: bool = True,
    ) -> str:
        key = (kind, identity)
        if key in self.visiting or not self._take():
            return "unknown"
        self.visiting.add(key)
        try:
            if kind == "knowledge_revision":
                row = self.store.connection.execute(
                    """SELECT r.*, o.current_revision_id FROM knowledge_revisions_v3 r
                       JOIN knowledge_objects_v3 o USING(knowledge_id) WHERE revision_id = ?""",
                    (identity,),
                ).fetchone()
                if row is None:
                    return "unknown"
                if not self._admitted(row) or not self._current_at(row, "knowledge", identity):
                    return "invalidated"
                if row["expires_at"] is not None and row["expires_at"] <= self.now:
                    return "stale"
                statements = self.store.connection.execute(
                    "SELECT statement_json FROM knowledge_statements_v1 "
                    "WHERE knowledge_revision_id = ? ORDER BY ordinal LIMIT 4097", (identity,),
                ).fetchall()
                if len(statements) > 4096:
                    return "unknown"
                if statements:
                    states = [self.statement(strict_json_loads(s[0]))["freshness"]
                              for s in statements]
                    return max(states, key=_ORDER.__getitem__)
                revision = self.store._revision_row(row, include_body=False)
                # Source-free assertions may be recalled as tentative data, but
                # cannot independently ground a supported derived conclusion.
                if revision["source_free"]:
                    return "unknown" if require_grounded else "fresh"
                references = revision.get("source_refs", [])
                if any("revision_id" in reference for reference in references):
                    states = []
                    for reference in references:
                        if "revision_id" in reference:
                            states.append(self.revision(
                                "knowledge_revision", reference["revision_id"],
                                require_grounded=require_grounded,
                            ))
                        else:
                            states.append("fresh" if self._take() and
                                self.store._source_reference_is_bound(
                                    reference, scope=self.scope, max_sensitivity=self.sensitivity,
                                    as_of=self.as_of, legacy_audit_head=self.legacy_audit_head,
                                ) else "unknown")
                    return max(states, key=_ORDER.__getitem__)
                return "fresh" if self.store.revision_provenance_admitted(
                    revision, as_of=self.as_of, legacy_audit_head=self.legacy_audit_head
                ) else "stale"
            row = self.store.connection.execute(
                """SELECT r.*, o.current_revision_id FROM knowledge_relation_revisions_v3 r
                   JOIN knowledge_relations_v3 o USING(relation_key)
                   WHERE relation_revision_id = ?""", (identity,),
            ).fetchone()
            if row is None:
                return "unknown"
            if not self._admitted(row) or not self._current_at(row, "relation", identity):
                return "invalidated"
            refs = strict_json_loads(row["evidence_refs_json"])
            if not refs:
                return "unknown"
            for ref in refs:
                state = (
                    self.revision("knowledge_revision", ref["revision_id"])
                    if "revision_id" in ref
                    else self.source(identity, ref, consumer_kind="relation_revision")
                )
                if state != "fresh":
                    return state
            endpoints = self.store.connection.execute(
                "SELECT d.input_id, r.knowledge_id FROM revision_dependencies_v1 d "
                "LEFT JOIN knowledge_revisions_v3 r ON r.revision_id = d.input_id "
                "WHERE d.consumer_kind = 'relation_revision' AND d.consumer_revision_id = ? "
                "AND d.input_kind = 'knowledge_revision' "
                "AND r.knowledge_id IN (?, ?) LIMIT 3",
                (identity, row["subject_knowledge_id"], row["object_knowledge_id"]),
            ).fetchall()
            expected = {row["subject_knowledge_id"], row["object_knowledge_id"]}
            if (
                len(endpoints) != len(expected)
                or {e["knowledge_id"] for e in endpoints} != expected
            ):
                return "unknown"
            for endpoint in endpoints:
                state = self.revision("knowledge_revision", endpoint["input_id"])
                if state != "fresh":
                    return state
            return "fresh"
        finally:
            self.visiting.remove(key)

    def statement(self, value: dict[str, Any]) -> dict[str, Any]:
        from .statements import validate_statement

        if not self._take():
            return {"freshness": "unknown", "support_set": None, "support_set_sha256": None}
        value = validate_statement(value, require_statement_id=True)
        if value["support_status"] != "supported":
            return {"freshness": "unknown", "support_set": None, "support_set_sha256": None}
        groups = value.get("support_sets") or [{
            field: value[field] for field in (
                "source_refs", "knowledge_revision_refs", "relation_revision_refs"
            )
        }]
        states = []
        for group in groups:
            members = [self.source(value["knowledge_revision_id"], ref)
                       for ref in group["source_refs"]]
            for field, kind in (("knowledge_revision_refs", "knowledge_revision"),
                                ("relation_revision_refs", "relation_revision")):
                members.extend(self.revision(kind, identity) for identity in group[field])
            state = max(members, key=_ORDER.__getitem__) if members else "unknown"
            states.append(state)
            if state == "fresh":
                return {
                    "freshness": "fresh", "support_set": group,
                    "support_set_sha256": sha256_bytes(canonical_json(group).encode("utf-8")),
                }
        return {"freshness": min(states, key=_ORDER.__getitem__), "support_set": None,
                "support_set_sha256": None}
