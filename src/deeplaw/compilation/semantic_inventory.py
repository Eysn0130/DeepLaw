from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from ..knowledge_autonomy import AutonomousKnowledgeStore, _validate_contract
from ..knowledge_intelligence import normalize_identity_text
from ..util import canonical_json, sha256_bytes, stable_id, strict_json_loads
from .applicability import (
    admitted_knowledge_candidates,
    applicability_digest,
    collect_runtime_facts,
    derive_applicability,
    policy_digest,
    run_reference_time,
)
from .coordinator import CompilationCoordinator, _artifact, _decoded_artifact
from .profiles import REQUIRED_SEMANTIC_DUTIES, SEMANTIC_DUTIES

MAX_INVENTORY_OBSERVATIONS = 10_000
MAX_FINALIZATION_PROVIDER_BYTES = 64 * 1024


def _inventory_digest(value: dict[str, Any]) -> str:
    body = dict(value)
    body.pop("inventory_sha256", None)
    return sha256_bytes(canonical_json(body).encode("utf-8"))


class SemanticInventoryBuilder:
    """Build deterministic, run-local semantic inventory and finalization context."""

    def __init__(self, path: str | Path) -> None:
        self.root = Path(path).expanduser().absolute()
        CompilationCoordinator(self.root)

    @staticmethod
    def _observations(
        store: AutonomousKnowledgeStore,
        compilation_run_id: str,
    ) -> list[dict[str, Any]]:
        rows = store.connection.execute(
            """
            SELECT observation_json FROM semantic_observations_v2
            WHERE compilation_run_id = ?
            ORDER BY observation_id
            """,
            (compilation_run_id,),
        ).fetchall()
        if len(rows) > MAX_INVENTORY_OBSERVATIONS:
            raise ValueError("semantic inventory observation bound exceeded")
        values = [strict_json_loads(row["observation_json"]) for row in rows]
        if any(not isinstance(value, dict) for value in values):
            raise RuntimeError("semantic observation inventory is invalid")
        return values

    @staticmethod
    def _previous_outputs(
        store: AutonomousKnowledgeStore,
        run: Any,
    ) -> list[dict[str, Any]]:
        rows = store.connection.execute(
            """
            SELECT outputs.output_kind, outputs.output_id, outputs.object_id,
                   outputs.compilation_run_id
            FROM source_compilation_outputs_v1 AS outputs
            JOIN source_compilation_runs_v1 AS runs
              ON runs.compilation_run_id = outputs.compilation_run_id
            WHERE runs.source_key = ?
              AND outputs.compilation_run_id <> ?
            ORDER BY outputs.compilation_run_id DESC, outputs.output_kind, outputs.output_id
            LIMIT 1000
            """,
            (run["source_key"], run["compilation_run_id"]),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _affected_syntheses(
        store: AutonomousKnowledgeStore,
        source_revision_id: str,
    ) -> list[dict[str, Any]]:
        rows = store.connection.execute(
            """
            SELECT synthesis_revision_id, source_revision_ids_json,
                   knowledge_revision_ids_json, relation_revision_ids_json,
                   compilation_run_ids_json, input_set_sha256
            FROM synthesis_input_sets_v1
            ORDER BY synthesis_revision_id
            """
        ).fetchall()
        affected = []
        for row in rows:
            source_ids = strict_json_loads(row["source_revision_ids_json"])
            if source_revision_id in source_ids:
                affected.append(
                    {
                        "synthesis_revision_id": row["synthesis_revision_id"],
                        "input_set_sha256": row["input_set_sha256"],
                    }
                )
            if len(affected) >= 1000:
                break
        return affected

    @staticmethod
    def _clusters(
        observations: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        by_identity: dict[tuple[str, str], list[str]] = defaultdict(list)
        aliases: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for observation in observations:
            candidate = observation["semantic_key_candidate"]
            if candidate is None:
                continue
            identity = (observation["kind"], normalize_identity_text(candidate))
            by_identity[identity].append(observation["observation_id"])
            for alias in observation["aliases"]:
                normalized = normalize_identity_text(alias)
                if normalized:
                    aliases[normalized].add(identity)
        duplicate_clusters = [
            {
                "cluster_id": stable_id("semanticcluster", kind, key),
                "kind": kind,
                "normalized_semantic_key": key,
                "observation_ids": sorted(ids),
            }
            for (kind, key), ids in sorted(by_identity.items())
            if len(ids) > 1
        ]
        alias_collisions = [
            {
                "alias": alias,
                "identity_candidates": [
                    {"kind": kind, "normalized_semantic_key": key}
                    for kind, key in sorted(identities)
                ],
            }
            for alias, identities in sorted(aliases.items())
            if len(identities) > 1
        ]
        return duplicate_clusters, alias_collisions

    def build(
        self,
        compilation_run_id: str,
        *,
        grant_id: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "semantic inventory requires confirmation that no case data is present"
            )
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            run = CompilationCoordinator._run(store, compilation_run_id)
            semantic_run = store.connection.execute(
                """
                SELECT * FROM semantic_compilation_runs_v2
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()
            if semantic_run is None or run["compiler_profile_version"] not in {"2", "3"}:
                raise ValueError("semantic inventory requires compiler profile v2 or v3")
            if run["grant_id"] != grant_id:
                raise PermissionError("semantic compilation run is bound to another grant")
            grant = store._grant(
                grant_id,
                operation="freeze_semantic_inventory",
                request_bytes=len(compilation_run_id.encode("utf-8")),
            )
            store._enforce_grant_limits(grant, enforce_object_capacity=False)
            if semantic_run["observed_packet_count"] != run["packet_count"]:
                raise RuntimeError("semantic inventory requires every observation packet")
            if semantic_run["inventory_sha256"] is not None:
                row = store.connection.execute(
                    """
                    SELECT artifact_sha256 FROM semantic_inventories_v1
                    WHERE compilation_run_id = ? AND inventory_sha256 = ?
                    """,
                    (compilation_run_id, semantic_run["inventory_sha256"]),
                ).fetchone()
                if row is None:
                    raise RuntimeError("semantic inventory binding is missing")
                return _decoded_artifact(store, row["artifact_sha256"], role="semantic_inventory")
            observations = self._observations(store, compilation_run_id)
            duplicates, alias_collisions = self._clusters(observations)
            batches = store.connection.execute(
                """
                SELECT covered_fragment_ids_json, omitted_fragments_json,
                       coverage_ratio, warnings_json
                FROM semantic_observation_batches_v2
                WHERE compilation_run_id = ?
                ORDER BY packet_id
                """,
                (compilation_run_id,),
            ).fetchall()
            covered = sum(
                len(strict_json_loads(row["covered_fragment_ids_json"])) for row in batches
            )
            omitted = sum(len(strict_json_loads(row["omitted_fragments_json"])) for row in batches)
            previous_outputs = self._previous_outputs(store, run)
            affected_syntheses = self._affected_syntheses(store, run["source_revision_id"])
            runtime_facts: dict[str, Any] | None = None
            applicability: dict[str, dict[str, Any]] | None = None
            admitted_candidates: list[dict[str, Any]] = []
            admitted_candidates_sha256: str | None = None
            admitted_candidates_truncated = False
            if run["compiler_profile_version"] == "3":
                reference_time = run_reference_time(store, run)
                identity_keys = sorted(
                    {
                        (item["kind"], normalize_identity_text(candidate))
                        for item in observations
                        if (candidate := item["semantic_key_candidate"]) is not None
                        and item["kind"]
                        not in {
                            "relation",
                            "identity_candidate",
                            "contradiction_candidate",
                            "unresolved_item",
                        }
                    }
                )
                identity_keys_truncated = len(identity_keys) > 256
                admitted_candidates, admitted_candidates_truncated = (
                    admitted_knowledge_candidates(
                        store,
                        grant=grant,
                        reference_time=reference_time,
                        limit=256,
                        identity_keys=set(identity_keys[:256]),
                    )
                )
                admitted_candidates_truncated = bool(
                    admitted_candidates_truncated or identity_keys_truncated
                )
                admitted_candidates_sha256 = sha256_bytes(
                    canonical_json(admitted_candidates).encode("utf-8")
                )
                runtime_facts = collect_runtime_facts(
                    store,
                    run,
                    observations=observations,
                    previous_outputs=previous_outputs,
                    affected_syntheses=affected_syntheses,
                    reference_time=reference_time,
                )
                applicability = derive_applicability(runtime_facts)
            inventory_id = stable_id(
                "semanticinventory",
                compilation_run_id,
                canonical_json([item["observation_id"] for item in observations]),
            )
            inventory = {
                "schema_version": "deeplaw.run-semantic-inventory/v1",
                "inventory_id": inventory_id,
                "compilation_run_id": compilation_run_id,
                "source_revision_id": run["source_revision_id"],
                "observation_count": len(observations),
                "packet_count": run["packet_count"],
                "observations": observations,
                "duplicate_clusters": duplicates,
                "alias_collisions": alias_collisions,
                "contradiction_candidates": [
                    item for item in observations if item["kind"] == "contradiction_candidate"
                ],
                "unresolved_identities": [
                    item
                    for item in observations
                    if item["kind"] in {"identity_candidate", "unresolved_item"}
                    and item["semantic_key_candidate"] is None
                ],
                "previous_outputs": previous_outputs,
                "affected_syntheses": affected_syntheses,
                "coverage": {
                    "covered_fragment_count": covered,
                    "omitted_fragment_count": omitted,
                    "ratio": covered / (covered + omitted) if covered + omitted else 0.0,
                    "warnings": sorted(
                        {
                            warning
                            for row in batches
                            for warning in strict_json_loads(row["warnings_json"])
                        }
                    ),
                },
                "truncated": admitted_candidates_truncated,
                "inventory_sha256": "0" * 64,
            }
            if runtime_facts is not None and applicability is not None:
                inventory["coverage"]["runtime_facts"] = runtime_facts
                inventory["coverage"]["applicability"] = applicability
                inventory["coverage"]["applicability_digest"] = applicability_digest(applicability)
                inventory["coverage"]["applicability_policy_sha256"] = policy_digest()
                inventory["coverage"]["existing_admitted_candidates"] = admitted_candidates
                inventory["coverage"]["existing_admitted_candidates_sha256"] = (
                    admitted_candidates_sha256
                )
                inventory["coverage"]["existing_admitted_candidates_truncated"] = (
                    admitted_candidates_truncated
                )
            inventory["inventory_sha256"] = _inventory_digest(inventory)
            _validate_contract("run-semantic-inventory.v1.schema.json", inventory)
            recorded_at = store._next_transaction_time()
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                locked = store.connection.execute(
                    """
                    SELECT inventory_sha256, observed_packet_count,
                           observation_packet_count
                    FROM semantic_compilation_runs_v2
                    WHERE compilation_run_id = ?
                    """,
                    (compilation_run_id,),
                ).fetchone()
                if (
                    locked is None
                    or locked["inventory_sha256"] is not None
                    or locked["observed_packet_count"] != locked["observation_packet_count"]
                ):
                    raise RuntimeError("semantic inventory precondition changed")
                artifact_sha256, _ = _artifact(
                    store,
                    value=inventory,
                    role="semantic_inventory",
                    created_at=recorded_at,
                )
                store.connection.execute(
                    """
                    INSERT INTO semantic_inventories_v1(
                        artifact_sha256, inventory_sha256, inventory_id,
                        compilation_run_id, observation_count, packet_count,
                        truncated, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        artifact_sha256,
                        inventory["inventory_sha256"],
                        inventory_id,
                        compilation_run_id,
                        len(observations),
                        run["packet_count"],
                        recorded_at,
                    ),
                )
                store.connection.execute(
                    """
                    UPDATE semantic_compilation_runs_v2
                    SET inventory_sha256 = ?, updated_at = ?
                    WHERE compilation_run_id = ? AND inventory_sha256 IS NULL
                    """,
                    (inventory["inventory_sha256"], recorded_at, compilation_run_id),
                )
                CompilationCoordinator._record_usage(
                    store,
                    grant_id=grant_id,
                    operation="freeze_semantic_inventory",
                    request_sha256=sha256_bytes(compilation_run_id.encode("utf-8")),
                    recorded_at=recorded_at,
                    discriminator=compilation_run_id,
                )
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise
            return inventory

    def finalization_packet(self, compilation_run_id: str) -> dict[str, Any]:
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            run = CompilationCoordinator._run(store, compilation_run_id)
            row = store.connection.execute(
                """
                SELECT artifact_sha256 FROM semantic_inventories_v1
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("semantic finalization requires a frozen inventory")
            inventory = _decoded_artifact(
                store,
                row["artifact_sha256"],
                role="semantic_inventory",
            )
            if run["compiler_profile_version"] == "3":
                grant = store.connection.execute(
                    """
                    SELECT allowed_scope, max_sensitivity
                    FROM knowledge_sink_grants_v3
                    WHERE grant_id = ? AND revoked_at IS NULL
                    """,
                    (run["grant_id"],),
                ).fetchone()
                if grant is None:
                    raise PermissionError("semantic compilation grant is unavailable")
                return self._finalization_packet_v3(
                    store,
                    run=run,
                    inventory=inventory,
                    grant=grant,
                )
            keys = sorted(
                {
                    (item["kind"], normalize_identity_text(candidate))
                    for item in inventory["observations"]
                    if (candidate := item["semantic_key_candidate"]) is not None
                    and item["kind"]
                    not in {
                        "relation",
                        "identity_candidate",
                        "contradiction_candidate",
                        "unresolved_item",
                    }
                }
            )
            existing = []
            for kind, key in keys[:256]:
                rows = store.connection.execute(
                    """
                    SELECT knowledge_objects_v3.knowledge_id,
                           knowledge_objects_v3.kind,
                           knowledge_objects_v3.semantic_key,
                           knowledge_objects_v3.current_revision_id
                    FROM knowledge_objects_v3
                    WHERE knowledge_objects_v3.kind = ?
                      AND knowledge_objects_v3.semantic_key IS NOT NULL
                    ORDER BY knowledge_objects_v3.knowledge_id
                    """,
                    (kind,),
                ).fetchall()
                row = next(
                    (item for item in rows if normalize_identity_text(item["semantic_key"]) == key),
                    None,
                )
                if row is not None:
                    existing.append(dict(row))
            duty_requirements = [
                {
                    "duty_id": stable_id("duty", compilation_run_id, duty),
                    "duty_type": duty,
                    "required": duty in REQUIRED_SEMANTIC_DUTIES,
                }
                for duty in SEMANTIC_DUTIES
            ]
            inventory_summary = {
                key: inventory[key]
                for key in (
                    "inventory_id",
                    "inventory_sha256",
                    "observation_count",
                    "packet_count",
                    "duplicate_clusters",
                    "alias_collisions",
                    "contradiction_candidates",
                    "unresolved_identities",
                    "coverage",
                )
            }
            inventory_summary["observation_refs"] = [
                {
                    "observation_id": item["observation_id"],
                    "kind": item["kind"],
                    "semantic_key_candidate": item["semantic_key_candidate"],
                }
                for item in inventory["observations"]
            ]
            packet = {
                "schema_version": "deeplaw.semantic-finalization-packet/v1",
                "finalization_packet_id": stable_id(
                    "finalization", compilation_run_id, inventory["inventory_sha256"]
                ),
                "compilation_run_id": compilation_run_id,
                "source_revision_id": run["source_revision_id"],
                "expected_audit_head": run["input_audit_head"],
                "inventory": inventory_summary,
                "duties": duty_requirements,
                "existing_canonical_knowledge": existing,
                "previous_outputs": inventory["previous_outputs"],
                "affected_syntheses": inventory["affected_syntheses"],
                "budgets": {
                    "provider_bytes": MAX_FINALIZATION_PROVIDER_BYTES,
                    "max_publications": 10_000,
                    "max_relations": 10_000,
                },
                "truncated": False,
            }
            payload = canonical_json(packet).encode("utf-8")
            if len(payload) > MAX_FINALIZATION_PROVIDER_BYTES:
                raise ValueError(
                    "semantic finalization context exceeds its provider-visible byte bound"
                )
            _validate_contract("semantic-finalization-packet.v1.schema.json", packet)
            return packet

    @staticmethod
    def _finalization_packet_v3(
        store: AutonomousKnowledgeStore,
        *,
        run: Any,
        inventory: dict[str, Any],
        grant: Any,
    ) -> dict[str, Any]:
        coverage = inventory.get("coverage")
        if not isinstance(coverage, dict):
            raise RuntimeError("v3 semantic inventory coverage is unavailable")
        applicability = coverage.get("applicability")
        runtime_facts = coverage.get("runtime_facts")
        if not isinstance(applicability, dict) or not isinstance(runtime_facts, dict):
            raise RuntimeError("v3 semantic inventory applicability facts are unavailable")
        duties: list[dict[str, Any]] = []
        for duty in SEMANTIC_DUTIES:
            decision = applicability.get(duty)
            if not isinstance(decision, dict):
                raise RuntimeError("v3 semantic inventory duty applicability is incomplete")
            value = decision.get("applicability")
            basis = decision.get("deterministic_basis")
            if value == "unknown":
                status = "unresolved"
                unresolved_items = [
                    "DeepLaw could not prove duty applicability from closed runtime facts."
                ]
                omission_reason = None
            elif value == "not_applicable":
                status = "omitted_with_reason"
                unresolved_items = []
                omission_reason = (
                    basis.get("reason")
                    if isinstance(basis, dict)
                    else "Closed profile rule proves not-applicable."
                )
            else:
                status = "unresolved"
                unresolved_items = ["The applicable duty requires a bounded host report."]
                omission_reason = None
            duties.append(
                {
                    "duty_id": stable_id("duty", run["compilation_run_id"], duty),
                    "duty_type": duty,
                    "required": duty in REQUIRED_SEMANTIC_DUTIES,
                    "applicability": value,
                    "status": status,
                    "output_refs": [],
                    "evidence_refs": [],
                    "reason": basis.get("reason", "DeepLaw applicability decision.")
                    if isinstance(basis, dict)
                    else "DeepLaw applicability decision.",
                    "unresolved_items": unresolved_items,
                    "omission_reason": omission_reason,
                    "deterministic_basis": basis,
                }
            )
        digest = applicability_digest(applicability)
        frozen_candidates = coverage.get("existing_admitted_candidates")
        frozen_candidates_sha256 = coverage.get("existing_admitted_candidates_sha256")
        if not isinstance(frozen_candidates, list) or not isinstance(
            frozen_candidates_sha256, str
        ):
            raise RuntimeError("v3 semantic inventory admitted candidate freeze is unavailable")
        if frozen_candidates_sha256 != sha256_bytes(
            canonical_json(frozen_candidates).encode("utf-8")
        ):
            raise RuntimeError("v3 semantic inventory admitted candidate freeze is invalid")
        frozen_applicability_digest = coverage.get("applicability_digest")
        if frozen_applicability_digest != digest:
            raise RuntimeError("v3 semantic inventory applicability digest is invalid")
        provider_coverage = {
            key: value
            for key, value in coverage.items()
            if key not in {"applicability", "existing_admitted_candidates"}
        }
        inventory_summary = {
            key: inventory[key]
            for key in (
                "inventory_id",
                "inventory_sha256",
                "observation_count",
                "packet_count",
                "duplicate_clusters",
                "alias_collisions",
                "contradiction_candidates",
                "unresolved_identities",
            )
        }
        inventory_summary["coverage"] = provider_coverage
        packet = {
            "schema_version": "deeplaw.semantic-finalization-packet/v2",
            "compiler_profile_version": "3",
            "finalization_packet_id": stable_id(
                "finalization", run["compilation_run_id"], inventory["inventory_sha256"]
            ),
            "compilation_run_id": run["compilation_run_id"],
            "source_revision_id": run["source_revision_id"],
            "expected_audit_head": run["input_audit_head"],
            "inventory_sha256": inventory["inventory_sha256"],
            "applicability_policy_sha256": policy_digest(),
            "applicability_digest": digest,
            "inventory": inventory_summary,
            "duties": duties,
            "existing_canonical_knowledge": [],
            "previous_outputs": inventory["previous_outputs"],
            "affected_syntheses": inventory["affected_syntheses"],
            "budgets": {
                "provider_bytes": MAX_FINALIZATION_PROVIDER_BYTES,
                "max_publications": 10_000,
                "max_relations": 10_000,
            },
            "truncated": bool(inventory.get("truncated") or runtime_facts.get("truncated")),
        }
        keys = sorted(
            {
                (item["kind"], normalize_identity_text(candidate))
                for item in inventory["observations"]
                if (candidate := item["semantic_key_candidate"]) is not None
                and item["kind"]
                not in {
                    "relation",
                    "identity_candidate",
                    "contradiction_candidate",
                    "unresolved_item",
                }
            }
        )
        existing: list[dict[str, Any]] = []
        candidates = {
            (item["kind"], normalize_identity_text(item["semantic_key"])): item
            for item in frozen_candidates
            if item.get("semantic_key") is not None
        }
        for kind, key in keys[:256]:
            row = candidates.get((kind, key))
            if row is not None:
                existing.append(row)
        packet["existing_canonical_knowledge"] = existing
        payload = canonical_json(packet).encode("utf-8")
        if len(payload) > MAX_FINALIZATION_PROVIDER_BYTES:
            raise ValueError(
                "semantic finalization context exceeds its provider-visible byte bound"
            )
        _validate_contract("semantic-finalization-packet.v2.schema.json", packet)
        return packet
