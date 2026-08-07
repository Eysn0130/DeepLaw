from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..knowledge_autonomy import AutonomousKnowledgeStore, _validate_contract
from ..util import canonical_json, sha256_bytes, stable_id, strict_json_loads
from .coordinator import CompilationCoordinator, _artifact, _decoded_artifact
from .models import MAX_COMPILATION_REQUEST_BYTES

OBSERVATION_PLAN_SCHEMA = "deeplaw.source-compilation-observation-plan/v2"


class ObservationStore:
    """Persist run-local semantic observations without publishing canonical knowledge."""

    def __init__(self, path: str | Path) -> None:
        self.root = Path(path).expanduser().absolute()
        CompilationCoordinator(self.root)

    @staticmethod
    def observation_id(
        *,
        compilation_run_id: str,
        packet_id: str,
        observation: dict[str, Any],
    ) -> str:
        identity = dict(observation)
        identity.pop("observation_id", None)
        return stable_id(
            "observation",
            compilation_run_id,
            packet_id,
            canonical_json(identity),
        )

    @staticmethod
    def _validate_against_packet(
        *,
        plan: dict[str, Any],
        packet: dict[str, Any],
    ) -> None:
        if (
            plan["compilation_run_id"] != packet["compilation_run_id"]
            or plan["source_revision_id"] != packet["source_revision_id"]
            or plan["packet_id"] != packet["packet_id"]
            or plan["expected_audit_head"] != packet["input_audit_head"]
        ):
            raise ValueError("semantic observation plan does not match its packet")
        fragments = {item["fragment_id"]: item for item in packet["fragments"]}
        coverage = plan["coverage"]
        covered = set(coverage["covered_fragment_ids"])
        omitted = {item["fragment_id"] for item in coverage["omitted_fragments"]}
        if (
            coverage["packet_fragment_count"] != len(fragments)
            or covered & omitted
            or covered | omitted != set(fragments)
            or not math.isclose(
                coverage["ratio"],
                len(covered) / len(fragments),
                abs_tol=1e-9,
            )
        ):
            raise ValueError("semantic observation coverage does not match the packet")
        seen: set[str] = set()
        for observation in plan["observations"]:
            if observation["packet_id"] != packet["packet_id"]:
                raise ValueError("semantic observation targets another packet")
            expected_id = ObservationStore.observation_id(
                compilation_run_id=plan["compilation_run_id"],
                packet_id=plan["packet_id"],
                observation=observation,
            )
            if observation["observation_id"] != expected_id:
                raise ValueError("semantic observation ID is not content-addressed")
            if expected_id in seen:
                raise ValueError("semantic observation ID is duplicated")
            seen.add(expected_id)
            for reference in observation["source_refs"]:
                fragment = fragments.get(reference["fragment_id"])
                if (
                    fragment is None
                    or reference["source_revision_id"] != packet["source_revision_id"]
                    or reference["locator"] != fragment["locator"]
                    or reference["quote_sha256"] != fragment["text_sha256"]
                ):
                    raise ValueError(
                        "semantic observation cites evidence outside its packet"
                    )

    def next_packet(self, compilation_run_id: str) -> dict[str, Any] | None:
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            run = CompilationCoordinator._run(store, compilation_run_id)
            if run["compiler_profile_version"] not in {"2", "3"}:
                raise ValueError("semantic observations require compiler profile v2 or v3")
            row = store.connection.execute(
                """
                SELECT packets.artifact_sha256
                FROM source_compilation_packets_v1 AS packets
                LEFT JOIN semantic_observation_batches_v2 AS batches
                  ON batches.compilation_run_id = packets.compilation_run_id
                 AND batches.packet_id = packets.packet_id
                WHERE packets.compilation_run_id = ?
                  AND batches.packet_id IS NULL
                ORDER BY packets.ordinal
                LIMIT 1
                """,
                (compilation_run_id,),
            ).fetchone()
            if row is None:
                return None
            packet = _decoded_artifact(store, row["artifact_sha256"], role="packet")
            inventory_preview = self._bounded_preview(store, compilation_run_id)
            packet["semantic_protocol"] = {
                "schema_version": "deeplaw.semantic-observation-packet-context/v1",
                "observation_plan_contract": OBSERVATION_PLAN_SCHEMA,
                "prior_inventory": inventory_preview,
            }
            return packet

    @staticmethod
    def _bounded_preview(
        store: AutonomousKnowledgeStore,
        compilation_run_id: str,
    ) -> dict[str, Any]:
        rows = store.connection.execute(
            """
            SELECT observation_id, kind, semantic_key_candidate,
                   normalized_aliases_json
            FROM semantic_observations_v2
            WHERE compilation_run_id = ?
            ORDER BY kind, semantic_key_candidate, observation_id
            LIMIT 128
            """,
            (compilation_run_id,),
        ).fetchall()
        total = store.connection.execute(
            """
            SELECT COUNT(*) FROM semantic_observations_v2
            WHERE compilation_run_id = ?
            """,
            (compilation_run_id,),
        ).fetchone()[0]
        return {
            "observation_count": total,
            "items": [
                {
                    "observation_id": row["observation_id"],
                    "kind": row["kind"],
                    "semantic_key_candidate": row["semantic_key_candidate"],
                    "aliases": strict_json_loads(row["normalized_aliases_json"]),
                }
                for row in rows
            ],
            "truncated": total > len(rows),
        }

    def stage(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        plan: dict[str, Any],
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "semantic compilation requires confirmation that no case data is present"
            )
        if not isinstance(plan, dict):
            raise ValueError("semantic observation plan must be an object")
        _validate_contract("source-compilation-observation-plan.v2.schema.json", plan)
        payload = canonical_json(plan).encode("utf-8")
        if len(payload) > MAX_COMPILATION_REQUEST_BYTES:
            raise ValueError("semantic observation plan exceeds its request byte limit")
        plan_sha256 = sha256_bytes(payload)
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            run = CompilationCoordinator._run(store, compilation_run_id)
            if run["compiler_profile_version"] not in {"2", "3"}:
                raise ValueError("semantic observations require compiler profile v2 or v3")
            if run["grant_id"] != grant_id:
                raise PermissionError("semantic compilation run is bound to another grant")
            grant = store._grant(
                grant_id,
                operation="stage_semantic_observations",
                request_bytes=len(payload),
            )
            store._enforce_grant_limits(grant, enforce_object_capacity=False)
            if run["status"] not in {"planned", "staging"}:
                raise RuntimeError("semantic compilation no longer accepts observations")
            if store.audit_head != run["input_audit_head"]:
                raise RuntimeError("autonomous audit head changed after compilation began")
            row = store.connection.execute(
                """
                SELECT artifact_sha256 FROM source_compilation_packets_v1
                WHERE compilation_run_id = ? AND packet_id = ?
                """,
                (compilation_run_id, plan["packet_id"]),
            ).fetchone()
            if row is None:
                raise KeyError("semantic compilation packet is unavailable")
            packet = _decoded_artifact(store, row["artifact_sha256"], role="packet")
            self._validate_against_packet(plan=plan, packet=packet)
            existing = store.connection.execute(
                """
                SELECT observation_plan_sha256
                FROM semantic_observation_batches_v2
                WHERE compilation_run_id = ? AND packet_id = ?
                """,
                (compilation_run_id, plan["packet_id"]),
            ).fetchone()
            if existing is not None:
                if existing["observation_plan_sha256"] != plan_sha256:
                    raise RuntimeError("semantic observation packet is already staged")
                return {
                    "schema_version": "deeplaw.semantic-observation-batch/v1",
                    "compilation_run_id": compilation_run_id,
                    "packet_id": plan["packet_id"],
                    "observation_plan_sha256": plan_sha256,
                    "observation_count": len(plan["observations"]),
                    "idempotent_replay": True,
                }
            recorded_at = store._next_transaction_time()
            request_sha256 = sha256_bytes(
                canonical_json(
                    {
                        "operation": "stage_semantic_observations",
                        "compilation_run_id": compilation_run_id,
                        "plan_sha256": plan_sha256,
                    }
                ).encode("utf-8")
            )
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                locked = CompilationCoordinator._run(store, compilation_run_id)
                if (
                    locked["status"] not in {"planned", "staging"}
                    or store.audit_head != locked["input_audit_head"]
                ):
                    raise RuntimeError("semantic observation precondition changed")
                artifact_sha256, _ = _artifact(
                    store,
                    value=plan,
                    role="observation_plan",
                    created_at=recorded_at,
                )
                if artifact_sha256 != plan_sha256:
                    raise RuntimeError("semantic observation plan digest changed")
                for observation in plan["observations"]:
                    observation_sha256 = sha256_bytes(
                        canonical_json(observation).encode("utf-8")
                    )
                    aliases = sorted(
                        {
                            alias.casefold().strip()
                            for alias in observation["aliases"]
                            if alias.strip()
                        }
                    )
                    store.connection.execute(
                        """
                        INSERT INTO semantic_observations_v2(
                            observation_id, compilation_run_id, packet_id,
                            semantic_key_candidate, kind,
                            normalized_aliases_json, source_refs_json,
                            observation_json, observation_sha256, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            observation["observation_id"],
                            compilation_run_id,
                            plan["packet_id"],
                            observation["semantic_key_candidate"],
                            observation["kind"],
                            canonical_json(aliases),
                            canonical_json(observation["source_refs"]),
                            canonical_json(observation),
                            observation_sha256,
                            recorded_at,
                        ),
                    )
                coverage = plan["coverage"]
                store.connection.execute(
                    """
                    INSERT INTO semantic_observation_batches_v2(
                        compilation_run_id, packet_id, observation_plan_sha256,
                        observation_count, covered_fragment_ids_json,
                        omitted_fragments_json, coverage_ratio, warnings_json,
                        recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        compilation_run_id,
                        plan["packet_id"],
                        plan_sha256,
                        len(plan["observations"]),
                        canonical_json(coverage["covered_fragment_ids"]),
                        canonical_json(coverage["omitted_fragments"]),
                        coverage["ratio"],
                        canonical_json(plan["warnings"]),
                        recorded_at,
                    ),
                )
                counts = store.connection.execute(
                    """
                    SELECT COUNT(DISTINCT packet_id), COUNT(*)
                    FROM semantic_observations_v2
                    WHERE compilation_run_id = ?
                    """,
                    (compilation_run_id,),
                ).fetchone()
                observed_packets = store.connection.execute(
                    """
                    SELECT COUNT(*) FROM semantic_observation_batches_v2
                    WHERE compilation_run_id = ?
                    """,
                    (compilation_run_id,),
                ).fetchone()[0]
                store.connection.execute(
                    """
                    UPDATE semantic_compilation_runs_v2
                    SET observed_packet_count = ?, observation_count = ?, updated_at = ?
                    WHERE compilation_run_id = ?
                    """,
                    (observed_packets, counts[1], recorded_at, compilation_run_id),
                )
                CompilationCoordinator._record_usage(
                    store,
                    grant_id=grant_id,
                    operation="stage_semantic_observations",
                    request_sha256=request_sha256,
                    recorded_at=recorded_at,
                    discriminator=plan["packet_id"],
                )
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise
            return {
                "schema_version": "deeplaw.semantic-observation-batch/v1",
                "compilation_run_id": compilation_run_id,
                "packet_id": plan["packet_id"],
                "observation_plan_sha256": plan_sha256,
                "observation_count": len(plan["observations"]),
                "idempotent_replay": False,
            }
