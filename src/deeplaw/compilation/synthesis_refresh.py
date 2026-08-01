from __future__ import annotations

from pathlib import Path
from typing import Any

from ..knowledge_autonomy import AutonomousKnowledgeStore, _validate_contract
from ..util import canonical_json, sha256_bytes, stable_id, strict_json_loads
from .coordinator import CompilationCoordinator


class SynthesisRefreshService:
    """Explicit foreground saga for revising stale governed Syntheses."""

    def __init__(self, path: str | Path) -> None:
        self.root = Path(path).expanduser().absolute()
        self.coordinator = CompilationCoordinator(self.root)

    @staticmethod
    def _task_value(row: Any) -> dict[str, Any]:
        value = {
            "schema_version": "deeplaw.synthesis-refresh-task/v1",
            "refresh_task_id": row["refresh_task_id"],
            "target_knowledge_id": row["target_knowledge_id"],
            "target_revision_id": row["target_revision_id"],
            "input_set_sha256": row["input_set_sha256"],
            "triggering_freshness_event_ids": strict_json_loads(
                row["triggering_freshness_event_ids_json"]
            ),
            "source_revision_ids": strict_json_loads(row["source_revision_ids_json"]),
            "knowledge_revision_ids": strict_json_loads(
                row["knowledge_revision_ids_json"]
            ),
            "relation_revision_ids": strict_json_loads(
                row["relation_revision_ids_json"]
            ),
            "compilation_run_ids": strict_json_loads(
                row["compilation_run_ids_json"]
            ),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        _validate_contract("synthesis-refresh-task.v1.schema.json", value)
        return value

    def tasks(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if status is not None and status not in {
            "planned",
            "started",
            "completed",
            "superseded",
            "blocked",
        }:
            raise ValueError("synthesis refresh task status is invalid")
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            rows = store.connection.execute(
                """
                SELECT * FROM synthesis_refresh_tasks_v1
                WHERE (? IS NULL OR status = ?)
                ORDER BY created_at, refresh_task_id
                LIMIT 1000
                """,
                (status, status),
            ).fetchall()
            return [self._task_value(row) for row in rows]

    def begin(
        self,
        *,
        grant_id: str,
        refresh_task_id: str,
        source_revision_ids: list[str],
        knowledge_revision_ids: list[str],
        relation_revision_ids: list[str],
        host_identity: str,
        model_identity: str | None,
        profile_id: str,
        prompt_sha256: str,
        config_sha256: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "synthesis refresh requires confirmation that no case data is present"
            )
        source_revision_ids = sorted(set(source_revision_ids))
        knowledge_revision_ids = sorted(set(knowledge_revision_ids))
        relation_revision_ids = sorted(set(relation_revision_ids))
        if not source_revision_ids:
            raise ValueError("synthesis refresh requires at least one admitted Source Revision")
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            task = store.connection.execute(
                """
                SELECT tasks.*, objects.semantic_key,
                       objects.current_revision_id
                FROM synthesis_refresh_tasks_v1 AS tasks
                JOIN knowledge_objects_v3 AS objects
                  ON objects.knowledge_id = tasks.target_knowledge_id
                WHERE tasks.refresh_task_id = ?
                """,
                (refresh_task_id,),
            ).fetchone()
            if task is None:
                raise KeyError("synthesis refresh task is unavailable")
            if task["status"] not in {"planned", "started"}:
                raise RuntimeError("synthesis refresh task cannot be started")
            if task["current_revision_id"] != task["target_revision_id"]:
                raise RuntimeError("synthesis refresh target revision changed")
            existing = store.connection.execute(
                """
                SELECT synthesis_refresh_run_id, compilation_run_id
                FROM synthesis_refresh_runs_v1 WHERE refresh_task_id = ?
                """,
                (refresh_task_id,),
            ).fetchone()
            if existing is not None:
                return self.status(existing["synthesis_refresh_run_id"])
        provisional_input = {
            "source_revision_ids": source_revision_ids,
            "knowledge_revision_ids": knowledge_revision_ids,
            "relation_revision_ids": relation_revision_ids,
            "compilation_run_ids": [],
        }
        provenance_seed = canonical_json(
            {
                "profile_id": profile_id,
                "prompt_sha256": prompt_sha256,
                "config_sha256": config_sha256,
                "inputs": provisional_input,
                "task": refresh_task_id,
            }
        )
        provenance_sha256 = sha256_bytes(provenance_seed.encode("utf-8"))
        begun = self.coordinator.begin(
            grant_id=grant_id,
            source_revision_id=source_revision_ids[0],
            compiler_profile="synthesis-refresh-agent",
            compiler_profile_version="1",
            host_identity=host_identity,
            model_identity=model_identity,
            prompt_template_id=profile_id,
            prompt_config_sha256=prompt_sha256,
            plan_configuration_sha256=provenance_sha256,
            packet_max_fragments=128,
            confirm_no_case_data=True,
        )
        input_set = {
            **provisional_input,
            "compilation_run_ids": [begun["compilation_run_id"]],
        }
        input_set_sha256 = sha256_bytes(canonical_json(input_set).encode("utf-8"))
        synthesis_refresh_run_id = stable_id(
            "synthesisrefresh",
            refresh_task_id,
            begun["compilation_run_id"],
            input_set_sha256,
        )
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            recorded_at = store._next_transaction_time()
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                locked = store.connection.execute(
                    """
                    SELECT status FROM synthesis_refresh_tasks_v1
                    WHERE refresh_task_id = ?
                    """,
                    (refresh_task_id,),
                ).fetchone()
                if locked is None or locked["status"] not in {"planned", "started"}:
                    raise RuntimeError("synthesis refresh task precondition changed")
                store.connection.execute(
                    """
                    INSERT INTO synthesis_refresh_runs_v1(
                        synthesis_refresh_run_id, refresh_task_id,
                        compilation_run_id, target_semantic_key,
                        target_knowledge_id, expected_revision_id,
                        input_set_sha256, source_revision_ids_json,
                        knowledge_revision_ids_json,
                        relation_revision_ids_json,
                        compilation_run_ids_json, host_identity, model_identity,
                        profile_id, prompt_sha256, config_sha256,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        synthesis_refresh_run_id,
                        refresh_task_id,
                        begun["compilation_run_id"],
                        task["semantic_key"],
                        task["target_knowledge_id"],
                        task["target_revision_id"],
                        input_set_sha256,
                        canonical_json(input_set["source_revision_ids"]),
                        canonical_json(input_set["knowledge_revision_ids"]),
                        canonical_json(input_set["relation_revision_ids"]),
                        canonical_json(input_set["compilation_run_ids"]),
                        host_identity,
                        model_identity,
                        profile_id,
                        prompt_sha256,
                        config_sha256,
                        recorded_at,
                        recorded_at,
                    ),
                )
                store.connection.execute(
                    """
                    UPDATE synthesis_refresh_tasks_v1
                    SET status = 'started', updated_at = ?
                    WHERE refresh_task_id = ?
                    """,
                    (recorded_at, refresh_task_id),
                )
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise
        return self.status(synthesis_refresh_run_id)

    def packet(self, synthesis_refresh_run_id: str) -> dict[str, Any] | None:
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            run = self._refresh_run(store, synthesis_refresh_run_id)
            packet = self.coordinator.next_packet(run["compilation_run_id"])
            if packet is None:
                return None
            packet["synthesis_refresh"] = {
                "schema_version": "deeplaw.synthesis-refresh-context/v1",
                "synthesis_refresh_run_id": synthesis_refresh_run_id,
                "refresh_task_id": run["refresh_task_id"],
                "target_semantic_key": run["target_semantic_key"],
                "target_knowledge_id": run["target_knowledge_id"],
                "expected_revision_id": run["expected_revision_id"],
                "input_set": self._input_set(store, run),
                "triggering_freshness_event_ids": strict_json_loads(
                    run["triggering_freshness_event_ids_json"]
                ),
            }
            return packet

    @staticmethod
    def _refresh_run(store: AutonomousKnowledgeStore, refresh_run_id: str) -> Any:
        row = store.connection.execute(
            """
            SELECT runs.*, tasks.triggering_freshness_event_ids_json
            FROM synthesis_refresh_runs_v1 AS runs
            JOIN synthesis_refresh_tasks_v1 AS tasks USING(refresh_task_id)
            WHERE runs.synthesis_refresh_run_id = ?
            """,
            (refresh_run_id,),
        ).fetchone()
        if row is None:
            raise KeyError("synthesis refresh run is unavailable")
        return row

    @staticmethod
    def _input_set(store: AutonomousKnowledgeStore, run: Any) -> dict[str, Any]:
        return {
            "source_revision_ids": sorted(
                strict_json_loads(run["source_revision_ids_json"])
            ),
            "knowledge_revision_ids": sorted(
                strict_json_loads(run["knowledge_revision_ids_json"])
            ),
            "relation_revision_ids": sorted(
                strict_json_loads(run["relation_revision_ids_json"])
            ),
            "compilation_run_ids": sorted(
                strict_json_loads(run["compilation_run_ids_json"])
            ),
            "input_set_sha256": run["input_set_sha256"],
        }

    def stage(
        self,
        *,
        grant_id: str,
        synthesis_refresh_run_id: str,
        plan: dict[str, Any],
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "synthesis refresh requires confirmation that no case data is present"
            )
        _validate_contract("synthesis-refresh-plan.v1.schema.json", plan)
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            run = self._refresh_run(store, synthesis_refresh_run_id)
            input_set = self._input_set(store, run)
            packet_ids = [
                row["packet_id"]
                for row in store.connection.execute(
                    """
                    SELECT packet_id FROM source_compilation_packets_v1
                    WHERE compilation_run_id = ? ORDER BY ordinal
                    """,
                    (run["compilation_run_id"],),
                )
            ]
        if (
            plan["synthesis_refresh_run_id"] != synthesis_refresh_run_id
            or plan["compilation_run_id"] != run["compilation_run_id"]
            or plan["target_knowledge_id"] != run["target_knowledge_id"]
            or plan["expected_revision_id"] != run["expected_revision_id"]
            or plan["input_set_sha256"] != run["input_set_sha256"]
        ):
            raise ValueError("synthesis refresh plan binding is invalid")
        by_packet: dict[str, dict[str, Any]] = {}
        actions = []
        for packet_plan in plan["packet_plans"]:
            _validate_contract("source-compilation-plan.v1.schema.json", packet_plan)
            if packet_plan["packet_id"] in by_packet:
                raise ValueError("synthesis refresh packet plan is duplicated")
            by_packet[packet_plan["packet_id"]] = packet_plan
            actions.extend(packet_plan["object_actions"])
            if packet_plan["relation_actions"] or packet_plan["identity_actions"]:
                raise ValueError("synthesis refresh cannot mutate relations or identity")
        if set(by_packet) != set(packet_ids) or len(actions) != 1:
            raise ValueError("synthesis refresh requires one exact Synthesis revision")
        action = actions[0]
        if (
            action["action"] != "revise"
            or action["kind"] != "synthesis"
            or action["semantic_key"] != run["target_semantic_key"]
            or action["knowledge_id"] != run["target_knowledge_id"]
            or action["expected_revision_id"] != run["expected_revision_id"]
            or action["synthesis_inputs"] != input_set
        ):
            raise ValueError("synthesis refresh action does not match the governed target")
        staged = []
        for packet_id in packet_ids:
            staged.append(
                self.coordinator.stage(
                    grant_id=grant_id,
                    compilation_run_id=run["compilation_run_id"],
                    plan=by_packet[packet_id],
                    confirm_no_case_data=True,
                    _allow_run_wide_source_refs=True,
                )
            )
        return {
            "schema_version": "deeplaw.synthesis-refresh-staging/v1",
            "synthesis_refresh_run_id": synthesis_refresh_run_id,
            "input_set_sha256": run["input_set_sha256"],
            "staged_packet_count": len(staged),
        }

    def validate(
        self,
        *,
        grant_id: str,
        synthesis_refresh_run_id: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            run = self._refresh_run(store, synthesis_refresh_run_id)
        return self.coordinator.validate(
            grant_id=grant_id,
            compilation_run_id=run["compilation_run_id"],
            confirm_no_case_data=confirm_no_case_data,
        )

    def commit(
        self,
        *,
        grant_id: str,
        synthesis_refresh_run_id: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            run = self._refresh_run(store, synthesis_refresh_run_id)
        receipt = self.coordinator.commit(
            grant_id=grant_id,
            compilation_run_id=run["compilation_run_id"],
            confirm_no_case_data=confirm_no_case_data,
        )
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            updated_at = store._next_transaction_time()
            store.connection.execute(
                """
                UPDATE synthesis_refresh_tasks_v1
                SET status = 'completed', updated_at = ?
                WHERE refresh_task_id = ?
                """,
                (updated_at, run["refresh_task_id"]),
            )
            store.connection.execute(
                """
                UPDATE synthesis_refresh_runs_v1 SET updated_at = ?
                WHERE synthesis_refresh_run_id = ?
                """,
                (updated_at, synthesis_refresh_run_id),
            )
            store.connection.commit()
        return {
            "schema_version": "deeplaw.synthesis-refresh-receipt/v1",
            "synthesis_refresh_run_id": synthesis_refresh_run_id,
            "target_knowledge_id": run["target_knowledge_id"],
            "previous_revision_id": run["expected_revision_id"],
            "input_set_sha256": run["input_set_sha256"],
            "transaction": receipt,
        }

    def status(self, synthesis_refresh_run_id: str) -> dict[str, Any]:
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            run = self._refresh_run(store, synthesis_refresh_run_id)
            transaction = self.coordinator.status(run["compilation_run_id"])
            result = {
                "schema_version": "deeplaw.synthesis-refresh-run/v1",
                "synthesis_refresh_run_id": synthesis_refresh_run_id,
                "refresh_task_id": run["refresh_task_id"],
                "target_semantic_key": run["target_semantic_key"],
                "target_knowledge_id": run["target_knowledge_id"],
                "expected_revision_id": run["expected_revision_id"],
                "input_set_sha256": run["input_set_sha256"],
                "triggering_freshness_event_ids": strict_json_loads(
                    run["triggering_freshness_event_ids_json"]
                ),
                "transaction": transaction,
            }
            _validate_contract("synthesis-refresh-run.v1.schema.json", result)
            return result
