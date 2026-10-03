from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

from ..knowledge_autonomy import AutonomousKnowledgeStore, _validate_contract, _write_object
from ..util import canonical_json, sha256_bytes, stable_id
from .models import SOURCE_FRESHNESS_REPORT_SCHEMA

MAX_FRAGMENT_INVENTORY = 20_000
MAX_DEPENDENCY_EDGES = 4_096
MAX_TRANSITIVE_CONSUMER_REVISIONS = 2_048


class MaintenanceBudgetExceeded(ValueError):
    code = "maintenance_budget_exceeded"
    review_required = True

    def __init__(self, budget_name: str, limit: int) -> None:
        self.budget_name = budget_name
        self.limit = limit
        super().__init__(f"{self.code}: {budget_name} limit {limit} exceeded")


class _MaintenanceBudget:
    __slots__ = ("budget_name", "limit", "used")

    def __init__(self, budget_name: str, limit: int) -> None:
        if limit < 0:
            raise ValueError("maintenance budget limit must be non-negative")
        self.budget_name = budget_name
        self.limit = limit
        self.used = 0

    def consume(self, amount: int = 1) -> None:
        if amount < 0:
            raise ValueError("maintenance budget consumption must be non-negative")
        if self.used + amount > self.limit:
            raise MaintenanceBudgetExceeded(self.budget_name, self.limit)
        self.used += amount


def _bounded_rows(
    store: AutonomousKnowledgeStore,
    query: str,
    parameters: tuple[Any, ...],
    *,
    budget: _MaintenanceBudget,
) -> list[Any]:
    rows = store.connection.execute(
        f"{query.rstrip()}\nLIMIT ?",
        (*parameters, budget.limit - budget.used + 1),
    ).fetchall()
    budget.consume(len(rows))
    return rows


class FreshnessService:
    """Propagate Source Revision lifecycle and structural changes to dependencies."""

    def __init__(self, path: str | Path) -> None:
        self.root = Path(path).expanduser().absolute()

    def refresh(
        self,
        *,
        grant_id: str,
        source_revision_id: str,
        replacement_source_revision_id: str | None = None,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "source freshness refresh requires confirmation that no case data is present"
            )
        request = {
            "operation": "refresh_compilation",
            "source_revision_id": source_revision_id,
            "replacement_source_revision_id": replacement_source_revision_id,
        }
        request_bytes = canonical_json(request).encode("utf-8")
        request_sha256 = sha256_bytes(request_bytes)
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            grant = store._grant(
                grant_id,
                operation="refresh_compilation",
                request_bytes=len(request_bytes),
            )
            store._enforce_grant_limits(grant, enforce_object_capacity=False)
            input_audit_head = store.audit_head
            input_legacy_audit_head = store.legacy_audit_head
            source = self._source(store, source_revision_id)
            replacement = (
                self._source(store, replacement_source_revision_id)
                if replacement_source_revision_id is not None
                else self._replacement(store, source)
            )
            if replacement is not None and replacement["source_key"] != source["source_key"]:
                raise ValueError("freshness replacement belongs to another source identity")
            source_status = self._report_status(source["status"])
            fragment_diff = self._fragment_diff(
                store,
                source_revision_id=source_revision_id,
                replacement_source_revision_id=(
                    replacement["source_revision_id"] if replacement is not None else None
                ),
            )
            changed_fragment_ids = set(fragment_diff["changed_fragment_ids"])
            unchanged_fragment_ids = set(fragment_diff["unchanged_fragment_ids"])
            moved_fragment_ids = set(fragment_diff["moved_fragment_ids"])
            missing_fragment_ids = set(fragment_diff["missing_fragment_ids"])
            dependency_budget = _MaintenanceBudget(
                "dependency_edges", MAX_DEPENDENCY_EDGES
            )
            dependencies = _bounded_rows(
                store,
                """
                SELECT * FROM knowledge_dependencies_v1
                WHERE source_revision_id = ?
                  AND dependency_kind = 'direct'
                ORDER BY consumer_kind, consumer_revision_id, fragment_id
                """,
                (source_revision_id,),
                budget=dependency_budget,
            )
            transitions: list[dict[str, Any]] = []
            affected_knowledge: set[str] = set()
            affected_relations: set[str] = set()
            direct_revision_states: dict[tuple[str, str], str] = {}
            recorded_at = store._next_transaction_time(strictly_after_event=True)
            for dependency in dependencies:
                freshness, reason = self._dependency_state(
                    source_status=source_status,
                    fragment_id=dependency["fragment_id"],
                    replacement_source_revision_id=(
                        replacement["source_revision_id"]
                        if replacement is not None
                        else None
                    ),
                    changed_fragment_ids=changed_fragment_ids,
                    unchanged_fragment_ids=unchanged_fragment_ids,
                    moved_fragment_ids=moved_fragment_ids,
                    missing_fragment_ids=missing_fragment_ids,
                )
                if dependency["consumer_kind"] == "knowledge_revision":
                    affected_knowledge.add(dependency["consumer_revision_id"])
                else:
                    affected_relations.add(dependency["consumer_revision_id"])
                direct_key = (
                    dependency["consumer_kind"],
                    dependency["consumer_revision_id"],
                )
                direct_revision_states[direct_key] = self._worst_freshness(
                    direct_revision_states.get(direct_key),
                    freshness,
                )
                transitions.append(
                    {
                        "dependency_id": dependency["dependency_id"],
                        "target_kind": dependency["consumer_kind"],
                        "target_id": dependency["consumer_revision_id"],
                        "previous_freshness": dependency["freshness"],
                        "freshness": freshness,
                        "reason": reason,
                    }
                )
            transitive = self._transitive_consumers(
                store,
                source_revision_id=source_revision_id,
                direct_transitions=transitions,
                direct_revision_states=direct_revision_states,
                dependency_budget=dependency_budget,
            )
            for kind, revision_id in transitive["reached_consumers"]:
                if kind == "knowledge_revision":
                    affected_knowledge.add(revision_id)
                else:
                    affected_relations.add(revision_id)
            for item in transitive["consumers"]:
                if item["consumer_kind"] == "knowledge_revision":
                    affected_knowledge.add(item["consumer_revision_id"])
                else:
                    affected_relations.add(item["consumer_revision_id"])
                transitions.append(
                    {
                        "dependency_id": item["source_dependency_id"],
                        "target_kind": item["consumer_kind"],
                        "target_id": item["consumer_revision_id"],
                        "consumer_object_id": item["consumer_object_id"],
                        "compilation_run_id": item["compilation_run_id"],
                        "previous_freshness": item["previous_freshness"],
                        "freshness": item["freshness"],
                        "reason": item["reason"],
                        "insert": item["insert"],
                    }
                )
            replacement_id = replacement["source_revision_id"] if replacement is not None else None
            event_ids = [
                stable_id(
                    "freshness",
                    source_revision_id,
                    replacement_id or "none",
                    item["target_kind"],
                    item["target_id"],
                    item["dependency_id"],
                    item["freshness"],
                    store.legacy_audit_head,
                )
                for item in transitions
            ]
            report = {
                "schema_version": SOURCE_FRESHNESS_REPORT_SCHEMA,
                "source_revision_id": source_revision_id,
                "replacement_source_revision_id": replacement_id,
                "source_status": source_status,
                "changed_fragment_ids": fragment_diff["changed_fragment_ids"],
                "unchanged_fragment_ids": fragment_diff["unchanged_fragment_ids"],
                "moved_fragment_ids": fragment_diff["moved_fragment_ids"],
                "added_fragment_ids": fragment_diff["added_fragment_ids"],
                "missing_fragment_ids": fragment_diff["missing_fragment_ids"],
                "affected_knowledge_revision_ids": sorted(affected_knowledge),
                "affected_relation_revision_ids": sorted(affected_relations),
                "freshness_event_ids": event_ids,
                "recorded_at": recorded_at,
            }
            _validate_contract("source-freshness-report.v1.schema.json", report)
            report_bytes = canonical_json(report).encode("utf-8")
            report_sha256, _ = _write_object(store.root, report_bytes)
            report_id = stable_id(
                "freshnessreport",
                source_revision_id,
                replacement_id or "none",
                report_sha256,
            )
            synthesis_refresh_task_ids: list[str] = []
            with store._file_lease("canonical-mutation"):
                try:
                    store.connection.execute("BEGIN IMMEDIATE")
                    locked_grant = store._grant(
                        grant_id,
                        operation="refresh_compilation",
                        request_bytes=len(request_bytes),
                    )
                    if (
                        store.audit_head != input_audit_head
                        or store.legacy_audit_head != input_legacy_audit_head
                    ):
                        raise RuntimeError("Freshness input audit head changed before commit")
                    locked_source = self._source(store, source_revision_id)
                    if self._report_status(locked_source["status"]) != source_status:
                        raise RuntimeError("Source lifecycle changed during freshness refresh")
                    store.connection.execute(
                        """
                        INSERT OR IGNORE INTO source_compilation_artifacts_v1(
                            artifact_sha256, artifact_role, byte_size,
                            media_type, created_at
                        ) VALUES (?, 'freshness', ?, 'application/json', ?)
                        """,
                        (report_sha256, len(report_bytes), recorded_at),
                    )
                    for dependency_update in transitive["revision_dependencies"]:
                        store.connection.execute(
                            """
                            UPDATE revision_dependencies_v1
                            SET freshness = ?, reason = ?, updated_at = ?
                            WHERE dependency_id = ?
                            """,
                            (
                                dependency_update["freshness"],
                                dependency_update["reason"],
                                recorded_at,
                                dependency_update["dependency_id"],
                            ),
                        )
                    for event_id, transition in zip(event_ids, transitions, strict=True):
                        if transition.get("insert"):
                            store.connection.execute(
                                """
                                INSERT OR IGNORE INTO knowledge_dependencies_v1(
                                    dependency_id, compilation_run_id,
                                    consumer_kind, consumer_object_id,
                                    consumer_revision_id, source_revision_id,
                                    fragment_id, dependency_kind, freshness,
                                    reason, recorded_at, updated_at
                                ) VALUES (
                                    ?, ?, ?, ?, ?, ?, NULL,
                                    'transitive', ?, ?, ?, ?
                                )
                                """,
                                (
                                    transition["dependency_id"],
                                    transition["compilation_run_id"],
                                    transition["target_kind"],
                                    transition["consumer_object_id"],
                                    transition["target_id"],
                                    source_revision_id,
                                    transition["freshness"],
                                    transition["reason"],
                                    recorded_at,
                                    recorded_at,
                                ),
                            )
                        store.connection.execute(
                            """
                            UPDATE knowledge_dependencies_v1
                            SET freshness = ?, reason = ?, updated_at = ?
                            WHERE dependency_id = ?
                            """,
                            (
                                transition["freshness"],
                                transition["reason"],
                                recorded_at,
                                transition["dependency_id"],
                            ),
                        )
                        store.connection.execute(
                            """
                            INSERT OR IGNORE INTO source_freshness_events_v1(
                                freshness_event_id, target_kind, target_id,
                                previous_freshness, freshness, reason,
                                source_revision_id,
                                replacement_source_revision_id,
                                report_sha256, recorded_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                event_id,
                                transition["target_kind"],
                                transition["target_id"],
                                transition["previous_freshness"],
                                transition["freshness"],
                                transition["reason"],
                                source_revision_id,
                                replacement_id,
                                report_sha256,
                                recorded_at,
                            ),
                        )
                    for synthesis_revision_id in sorted(affected_knowledge):
                        grouped = store.connection.execute(
                            "SELECT r.scope, r.sensitivity FROM knowledge_revisions_v3 r "
                            "JOIN knowledge_statements_v1 s "
                            "ON s.knowledge_revision_id = r.revision_id "
                            "WHERE r.revision_id = ? AND json_extract(s.statement_json, "
                            "'$.schema_version') = 'deeplaw.knowledge-statement/v2' LIMIT 1",
                            (synthesis_revision_id,),
                        ).fetchone()
                        if grouped is not None:
                            from ..evidence.support import SupportEvaluator

                            if SupportEvaluator(
                                store, scope=grouped["scope"], sensitivity=grouped["sensitivity"]
                            ).revision("knowledge_revision", synthesis_revision_id) == "fresh":
                                continue
                        synthesis = store.connection.execute(
                            """
                            SELECT revisions.knowledge_id,
                                   inputs.source_revision_ids_json,
                                   inputs.knowledge_revision_ids_json,
                                   inputs.relation_revision_ids_json,
                                   inputs.compilation_run_ids_json,
                                   inputs.input_set_sha256
                            FROM knowledge_revisions_v3 AS revisions
                            JOIN synthesis_input_sets_v1 AS inputs
                              ON inputs.synthesis_revision_id = revisions.revision_id
                            WHERE revisions.revision_id = ?
                              AND revisions.kind = 'synthesis'
                            """,
                            (synthesis_revision_id,),
                        ).fetchone()
                        if synthesis is None:
                            continue
                        triggering_event_ids = sorted(
                            event_id
                            for event_id, transition in zip(
                                event_ids, transitions, strict=True
                            )
                            if transition["target_kind"] == "knowledge_revision"
                            and transition["target_id"] == synthesis_revision_id
                        )
                        if not triggering_event_ids:
                            continue
                        refresh_task_id = stable_id(
                            "synthesisrefreshtask",
                            synthesis_revision_id,
                            synthesis["input_set_sha256"],
                        )
                        store.connection.execute(
                            """
                            INSERT OR IGNORE INTO synthesis_refresh_tasks_v1(
                                refresh_task_id, target_knowledge_id,
                                target_revision_id, input_set_sha256,
                                triggering_freshness_event_ids_json,
                                source_revision_ids_json,
                                knowledge_revision_ids_json,
                                relation_revision_ids_json,
                                compilation_run_ids_json, status,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)
                            """,
                            (
                                refresh_task_id,
                                synthesis["knowledge_id"],
                                synthesis_revision_id,
                                synthesis["input_set_sha256"],
                                canonical_json(triggering_event_ids),
                                synthesis["source_revision_ids_json"],
                                synthesis["knowledge_revision_ids_json"],
                                synthesis["relation_revision_ids_json"],
                                synthesis["compilation_run_ids_json"],
                                recorded_at,
                                recorded_at,
                            ),
                        )
                        synthesis_refresh_task_ids.append(refresh_task_id)
                    store._append_event(
                        event_type="source_freshness_changed",
                        object_id=report_id,
                        payload={
                            "source_revision_id": source_revision_id,
                            "replacement_source_revision_id": replacement_id,
                            "source_status": source_status,
                            "report_sha256": report_sha256,
                            "freshness_event_count": len(event_ids),
                            "grant_id": grant_id,
                            "writer_id": locked_grant["writer_id"],
                        },
                        recorded_at=recorded_at,
                    )
                    queue_id = stable_id("rebuild", report_id, store.audit_head)
                    store.connection.execute(
                        """
                        INSERT INTO derived_rebuild_queue_v3(
                            queue_id, input_audit_head, reason, created_at,
                            completed_at
                        ) VALUES (?, ?, 'source_freshness_changed', ?, NULL)
                        """,
                        (queue_id, store.audit_head, recorded_at),
                    )
                    operation_id = stable_id(
                        "compilationop",
                        grant_id,
                        "refresh_compilation",
                        report_id,
                        request_sha256,
                    )
                    store.connection.execute(
                        """
                        INSERT OR IGNORE INTO source_compilation_usage_v1(
                            operation_id, grant_id, operation,
                            request_sha256, recorded_at
                        ) VALUES (?, ?, 'refresh_compilation', ?, ?)
                        """,
                        (
                            operation_id,
                            grant_id,
                            request_sha256,
                            recorded_at,
                        ),
                    )
                    store.connection.commit()
                except BaseException:
                    store.connection.rollback()
                    raise
            return {
                **report,
                "report_sha256": report_sha256,
                "report_id": report_id,
                "audit_head": store.audit_head,
                "synthesis_refresh_task_ids": synthesis_refresh_task_ids,
            }

    @staticmethod
    def _source(
        store: AutonomousKnowledgeStore,
        source_revision_id: str | None,
    ) -> dict[str, Any]:
        if source_revision_id is None:
            raise ValueError("Source Revision ID is required")
        row = store.connection.execute(
            """
            SELECT source_revisions_v2.source_revision_id,
                   source_revisions_v2.source_key,
                   source_lifecycle.status,
                   source_lifecycle.activated_at,
                   source_lifecycle.superseded_at,
                   source_lifecycle.removed_at
            FROM source_revisions_v2
            LEFT JOIN source_revision_bindings_v2 USING(source_revision_id)
            LEFT JOIN source_lifecycle
              ON source_lifecycle.source_id =
                 source_revision_bindings_v2.legacy_source_id
            WHERE source_revisions_v2.source_revision_id = ?
            """,
            (source_revision_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Source Revision is unavailable: {source_revision_id}")
        return dict(row)

    @staticmethod
    def _replacement(
        store: AutonomousKnowledgeStore,
        source: dict[str, Any],
    ) -> dict[str, Any] | None:
        row = store.connection.execute(
            """
            SELECT source_revisions_v2.source_revision_id,
                   source_revisions_v2.source_key,
                   source_lifecycle.status,
                   source_lifecycle.activated_at,
                   source_lifecycle.superseded_at,
                   source_lifecycle.removed_at
            FROM source_revisions_v2
            JOIN source_revision_bindings_v2 USING(source_revision_id)
            JOIN source_lifecycle
              ON source_lifecycle.source_id =
                 source_revision_bindings_v2.legacy_source_id
            WHERE source_revisions_v2.source_key = ?
              AND source_revisions_v2.source_revision_id <> ?
              AND source_lifecycle.status IN ('active', 'pending')
            ORDER BY COALESCE(
                source_lifecycle.activated_at,
                source_lifecycle.superseded_at,
                source_lifecycle.removed_at
            ) DESC,
            source_revisions_v2.source_revision_id DESC
            LIMIT 1
            """,
            (source["source_key"], source["source_revision_id"]),
        ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _report_status(value: str | None) -> str:
        return {
            "pending": "active",
            "active": "active",
            "superseded": "superseded",
            "removed": "removed",
            None: "unavailable",
        }[value]

    @staticmethod
    def _fragment_inventory(
        store: AutonomousKnowledgeStore,
        source_revision_id: str,
        *,
        budget: _MaintenanceBudget | None = None,
    ) -> dict[str, dict[str, Any]]:
        selected_budget = budget or _MaintenanceBudget(
            "fragment_inventory", MAX_FRAGMENT_INVENTORY
        )
        rows = store.connection.execute(
            """
            SELECT legacy_fragment_bindings_v2.fragment_id,
                   fragments_v2.ordinal,
                   source_ir_nodes_v2.logical_node_key,
                   source_ir_nodes_v2.content_sha256
            FROM compilations_v2
            JOIN fragments_v2 USING(compilation_id)
            JOIN legacy_fragment_bindings_v2 USING(fragment_revision_id)
            JOIN fragment_node_membership_v2 USING(fragment_revision_id)
            JOIN source_ir_nodes_v2 USING(node_id)
            WHERE compilations_v2.source_revision_id = ?
            ORDER BY legacy_fragment_bindings_v2.fragment_id,
                     fragment_node_membership_v2.node_ordinal
            """,
            (source_revision_id,),
        )
        fragments: dict[str, dict[str, Any]] = {}
        for row in rows:
            fragment_id = row["fragment_id"]
            if fragment_id not in fragments:
                selected_budget.consume()
                fragments[fragment_id] = {"ordinal": row["ordinal"], "nodes": {}}
            fragment = fragments[fragment_id]
            fragment["nodes"][row["logical_node_key"]] = row[
                "content_sha256"
            ]
        return fragments

    @classmethod
    def _fragment_diff(
        cls,
        store: AutonomousKnowledgeStore,
        *,
        source_revision_id: str,
        replacement_source_revision_id: str | None,
    ) -> dict[str, list[str]]:
        inventory_budget = _MaintenanceBudget(
            "fragment_inventory", MAX_FRAGMENT_INVENTORY
        )
        old = cls._fragment_inventory(
            store, source_revision_id, budget=inventory_budget
        )
        if replacement_source_revision_id is None:
            return {
                "added_fragment_ids": [],
                "changed_fragment_ids": [],
                "moved_fragment_ids": [],
                "unchanged_fragment_ids": [],
                "missing_fragment_ids": sorted(old),
            }
        new_fragments = cls._fragment_inventory(
            store,
            replacement_source_revision_id,
            budget=inventory_budget,
        )
        new_nodes = {
            logical_key: content_sha256
            for fragment in new_fragments.values()
            for logical_key, content_sha256 in fragment["nodes"].items()
        }
        old_nodes = {
            logical_key: content_sha256
            for fragment in old.values()
            for logical_key, content_sha256 in fragment["nodes"].items()
        }
        new_digests = set(new_nodes.values())
        old_digests = set(old_nodes.values())
        changed: list[str] = []
        moved: list[str] = []
        unchanged: list[str] = []
        missing: list[str] = []
        new_by_nodes: dict[
            tuple[tuple[str, str], ...], deque[dict[str, Any]]
        ] = {}
        for candidate in new_fragments.values():
            nodes_key = tuple(sorted(candidate["nodes"].items()))
            new_by_nodes.setdefault(nodes_key, deque()).append(candidate)
        for fragment_id, fragment in old.items():
            nodes = fragment["nodes"]
            exact_destinations = new_by_nodes.get(tuple(sorted(nodes.items())))
            exact_destination = (
                exact_destinations.popleft()
                if exact_destinations
                else None
            )
            if exact_destination is not None:
                if exact_destination["ordinal"] == fragment["ordinal"]:
                    unchanged.append(fragment_id)
                else:
                    moved.append(fragment_id)
                continue
            if not any(key in new_nodes for key in nodes):
                if all(digest in new_digests for digest in nodes.values()):
                    moved.append(fragment_id)
                else:
                    missing.append(fragment_id)
            else:
                changed.append(fragment_id)
        added = [
            fragment_id
            for fragment_id, fragment in new_fragments.items()
            if not any(key in old_nodes for key in fragment["nodes"])
            and not all(
                digest in old_digests for digest in fragment["nodes"].values()
            )
        ]
        return {
            "added_fragment_ids": sorted(added),
            "changed_fragment_ids": sorted(changed),
            "moved_fragment_ids": sorted(moved),
            "unchanged_fragment_ids": sorted(unchanged),
            "missing_fragment_ids": sorted(missing),
        }

    @staticmethod
    def _dependency_state(
        *,
        source_status: str,
        fragment_id: str | None,
        replacement_source_revision_id: str | None,
        changed_fragment_ids: set[str],
        unchanged_fragment_ids: set[str],
        moved_fragment_ids: set[str],
        missing_fragment_ids: set[str],
    ) -> tuple[str, str]:
        if source_status in {"removed", "unavailable"}:
            return "invalidated", f"source_revision_{source_status}"
        if fragment_id is not None and fragment_id in missing_fragment_ids:
            return "invalidated", "source_fragment_missing_from_successor"
        if fragment_id is not None and fragment_id in changed_fragment_ids:
            return "stale", "source_fragment_changed_in_successor"
        if fragment_id is not None and fragment_id in moved_fragment_ids:
            return "fresh", "source_fragment_moved_unchanged_in_successor"
        if (
            replacement_source_revision_id is not None
            and fragment_id is not None
            and fragment_id in unchanged_fragment_ids
        ):
            return "fresh", "source_fragment_unchanged_in_successor"
        if source_status == "superseded":
            return "stale", "source_revision_superseded"
        return "fresh", "source_revision_active"

    @staticmethod
    def _worst_freshness(previous: str | None, current: str) -> str:
        order = {"fresh": 0, "unknown": 1, "stale": 2, "invalidated": 3}
        if previous is None or order[current] > order[previous]:
            return current
        return previous

    @classmethod
    def _transitive_consumers(
        cls,
        store: AutonomousKnowledgeStore,
        *,
        source_revision_id: str,
        direct_transitions: list[dict[str, Any]],
        direct_revision_states: dict[tuple[str, str], str],
        dependency_budget: _MaintenanceBudget | None = None,
        consumer_budget: _MaintenanceBudget | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        dependency_budget = dependency_budget or _MaintenanceBudget(
            "dependency_edges", MAX_DEPENDENCY_EDGES
        )
        consumer_budget = consumer_budget or _MaintenanceBudget(
            "transitive_consumer_revisions", MAX_TRANSITIVE_CONSUMER_REVISIONS
        )
        source_overrides = {
            item["dependency_id"]: item["freshness"] for item in direct_transitions
        }
        revision_overrides: dict[str, str] = {}
        revision_updates: dict[str, dict[str, Any]] = {}
        reached_consumers: set[tuple[str, str]] = set()
        observed_states: dict[tuple[str, str], str] = {}
        pending = sorted(direct_revision_states)

        def effective_state(target: tuple[str, str]) -> str:
            consumer_kind, consumer_revision_id = target
            if consumer_kind == "knowledge_revision":
                grouped = store.connection.execute(
                    "SELECT 1 FROM knowledge_statements_v1 WHERE knowledge_revision_id = ? "
                    "AND json_extract(statement_json, '$.schema_version') = "
                    "'deeplaw.knowledge-statement/v2' LIMIT 1", (consumer_revision_id,),
                ).fetchone()
                if grouped is not None:
                    from ..evidence.support import SupportEvaluator

                    row = store.connection.execute(
                        "SELECT scope, sensitivity FROM knowledge_revisions_v3 "
                        "WHERE revision_id = ?", (consumer_revision_id,),
                    ).fetchone()
                    if row is None:
                        return "unknown"
                    return SupportEvaluator(
                        store, scope=row["scope"], sensitivity=row["sensitivity"],
                        source_overrides=source_overrides,
                    ).revision("knowledge_revision", consumer_revision_id)
            states: list[str] = []
            state_budget = _MaintenanceBudget(
                "dependency_edges", MAX_DEPENDENCY_EDGES
            )
            for row in _bounded_rows(
                store,
                """
                SELECT dependency_id, source_revision_id,
                       dependency_kind, freshness
                FROM knowledge_dependencies_v1
                WHERE consumer_kind = ? AND consumer_revision_id = ?
                """,
                target,
                budget=state_budget,
            ):
                if (
                    row["source_revision_id"] == source_revision_id
                    and row["dependency_kind"] == "transitive"
                ):
                    continue
                states.append(source_overrides.get(row["dependency_id"], row["freshness"]))
            for row in _bounded_rows(
                store,
                """
                SELECT dependency_id, freshness
                FROM revision_dependencies_v1
                WHERE consumer_kind = ? AND consumer_revision_id = ?
                """,
                (consumer_kind, consumer_revision_id),
                budget=state_budget,
            ):
                states.append(revision_overrides.get(row["dependency_id"], row["freshness"]))
            result = "fresh"
            for state in states:
                result = cls._worst_freshness(result, state)
            return result

        while pending:
            input_kind, upstream_revision_id = pending.pop(0)
            upstream_state = effective_state((input_kind, upstream_revision_id))
            if observed_states.get((input_kind, upstream_revision_id)) == upstream_state:
                continue
            observed_states[(input_kind, upstream_revision_id)] = upstream_state
            rows = _bounded_rows(
                store,
                """
                SELECT dependency_id, consumer_kind, consumer_object_id,
                       consumer_revision_id, freshness
                FROM revision_dependencies_v1
                WHERE input_kind = ? AND input_id = ?
                ORDER BY consumer_kind, consumer_revision_id, dependency_id
                """,
                (input_kind, upstream_revision_id),
                budget=dependency_budget,
            )
            for row in rows:
                reason = (
                    "upstream_revision_fresh"
                    if upstream_state == "fresh"
                    else "upstream_revision_invalidated"
                    if upstream_state == "invalidated"
                    else "upstream_revision_changed"
                )
                revision_overrides[row["dependency_id"]] = upstream_state
                revision_updates[row["dependency_id"]] = {
                    "dependency_id": row["dependency_id"],
                    "previous_freshness": row["freshness"],
                    "freshness": upstream_state,
                    "reason": reason,
                }
                consumer = (row["consumer_kind"], row["consumer_revision_id"])
                if consumer not in reached_consumers:
                    consumer_budget.consume()
                    reached_consumers.add(consumer)
                consumer_state = effective_state(consumer)
                if observed_states.get(consumer) != consumer_state:
                    pending.append(consumer)
                    pending.sort()

        consumers: list[dict[str, Any]] = []
        for consumer_kind, consumer_revision_id in sorted(reached_consumers):
            output = store.connection.execute(
                """
                SELECT compilation_run_id, object_id
                FROM source_compilation_outputs_v1
                WHERE output_kind = ? AND output_id = ?
                LIMIT 1
                """,
                (consumer_kind, consumer_revision_id),
            ).fetchone()
            if output is None:
                continue
            freshness = effective_state((consumer_kind, consumer_revision_id))
            dependency_id = stable_id(
                "dependency",
                consumer_kind,
                consumer_revision_id,
                source_revision_id,
                "transitive",
            )
            existing = store.connection.execute(
                """
                SELECT freshness FROM knowledge_dependencies_v1
                WHERE dependency_id = ?
                """,
                (dependency_id,),
            ).fetchone()
            consumers.append(
                {
                    "source_dependency_id": dependency_id,
                    "consumer_kind": consumer_kind,
                    "consumer_object_id": output["object_id"],
                    "consumer_revision_id": consumer_revision_id,
                    "compilation_run_id": output["compilation_run_id"],
                    "previous_freshness": (
                        existing["freshness"] if existing is not None else None
                    ),
                    "freshness": freshness,
                    "reason": (
                        "upstream_revision_fresh"
                        if freshness == "fresh"
                        else "upstream_revision_invalidated"
                        if freshness == "invalidated"
                        else "upstream_revision_changed"
                    ),
                    "insert": existing is None,
                }
            )
        return {
            "consumers": consumers,
            "reached_consumers": sorted(reached_consumers),
            "revision_dependencies": [
                revision_updates[key] for key in sorted(revision_updates)
            ],
        }
