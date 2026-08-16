from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from ..evidence.statements import validate_statement_plans
from ..knowledge_autonomy import AutonomousKnowledgeStore, _validate_contract
from ..util import canonical_json, sha256_bytes, stable_id, strict_json_loads
from .applicability import (
    applicability_digest,
    collect_runtime_facts,
    derive_applicability,
)
from .coordinator import CompilationCoordinator, _artifact, _decoded_artifact
from .models import MAX_COMPILATION_REQUEST_BYTES
from .profiles import REQUIRED_SEMANTIC_DUTIES, SEMANTIC_DUTIES

CONTENT_OUTPUT_DUTIES: Final = frozenset(
    {
        "key_claims",
        "entities",
        "concepts",
        "events",
        "procedures",
        "comparisons",
    }
)

RELATION_OUTPUT_DUTY: Final = "typed_relations"

CONTENT_DUTY_OBSERVATION_KINDS: Final = {
    "key_claims": "claim",
    "entities": "entity",
    "concepts": "concept",
    "events": "event",
    "procedures": "procedure",
    "comparisons": "comparison",
}


class SemanticFinalizer:
    """Validate one run-wide publication decision before canonical commit."""

    def __init__(self, path: str | Path) -> None:
        self.root = Path(path).expanduser().absolute()
        self.coordinator = CompilationCoordinator(self.root)

    @staticmethod
    def _validate_duties(
        *,
        compilation_run_id: str,
        reports: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        by_type: dict[str, dict[str, Any]] = {}
        for report in reports:
            _validate_contract("semantic-compilation-duty-report.v1.schema.json", report)
            if report["duty_type"] in by_type:
                raise ValueError("semantic duty report is duplicated")
            expected_id = stable_id("duty", compilation_run_id, report["duty_type"])
            if report["duty_id"] != expected_id:
                raise ValueError("semantic duty ID does not match its run")
            if report["required"] != (report["duty_type"] in REQUIRED_SEMANTIC_DUTIES):
                raise ValueError("semantic duty required flag is policy-controlled")
            if report["status"] == "omitted_with_reason":
                if report["omission_reason"] is None:
                    raise ValueError("omitted semantic duty requires an omission reason")
            elif report["omission_reason"] is not None:
                raise ValueError("semantic duty omission reason is inconsistent")
            if report["status"] == "unresolved" and not report["unresolved_items"]:
                raise ValueError("unresolved semantic duty requires unresolved items")
            elif report["unresolved_items"]:
                raise ValueError("non-unresolved semantic duty cannot carry unresolved items")
            if report["status"] == "not_applicable" and (
                report["output_refs"]
                or report["evidence_refs"]
                or report["unresolved_items"]
                or report["omission_reason"] is not None
            ):
                raise ValueError(
                    "not-applicable semantic duty cannot carry outputs, evidence, "
                    "unresolved items, or an omission reason"
                )
            by_type[report["duty_type"]] = report
        if set(by_type) != set(SEMANTIC_DUTIES):
            raise ValueError("semantic duty inventory is incomplete")
        return by_type

    @staticmethod
    def _validate_duty_evidence(
        *,
        reports: dict[str, dict[str, Any]],
        source_summary: dict[str, Any] | None,
        supported_output_refs: set[str],
        output_ref_kinds: dict[str, str],
    ) -> None:
        """Require content duties to point at durable output and evidence.

        Source Summary is the one v2 exception: its canonical Synthesis action is
        validated separately, so the duty report may leave ``output_refs`` empty.
        Deterministic control/scan duties may be satisfied by their bounded reason
        alone; they must not be forced to invent a Statement or evidence witness.
        """

        for report in reports.values():
            expected_kind = CONTENT_DUTY_OBSERVATION_KINDS.get(report["duty_type"])
            if report["status"] != "satisfied":
                continue
            if report["duty_type"] == "source_summary":
                if not report["evidence_refs"]:
                    raise ValueError(
                        "satisfied Source Summary duty requires source evidence references"
                    )
                if source_summary is None:
                    raise ValueError("satisfied Source Summary duty has no canonical Synthesis")
                if not set(report["output_refs"]).issubset(supported_output_refs):
                    raise ValueError("Source Summary duty references an unsupported output")
                continue
            if report["duty_type"] not in CONTENT_OUTPUT_DUTIES:
                continue
            if not report["evidence_refs"]:
                raise ValueError(
                    "satisfied semantic content duty requires source evidence references"
                )
            if not report["output_refs"]:
                raise ValueError(
                    "satisfied semantic content duty requires a supported output reference"
                )
            if not set(report["output_refs"]).issubset(supported_output_refs):
                raise ValueError("satisfied semantic content duty references an unsupported output")
            if expected_kind is not None and any(
                output_ref_kinds.get(output_ref) != expected_kind
                for output_ref in report["output_refs"]
            ):
                raise ValueError(
                    "satisfied semantic content duty references an output of the wrong kind"
                )

    @staticmethod
    def _validate_relation_duty(
        *,
        report: dict[str, Any],
        packet_plans: list[dict[str, Any]],
    ) -> None:
        relation_actions = [
            action for packet_plan in packet_plans for action in packet_plan["relation_actions"]
        ]
        if report["status"] == "not_applicable" and relation_actions:
            raise ValueError("not-applicable typed-relations duty has relation actions")
        if report["status"] != "satisfied":
            return
        if not relation_actions:
            raise ValueError("satisfied typed-relations duty has no relation action")
        if report["output_refs"]:
            raise ValueError("typed-relations duty cannot predeclare relation output IDs")
        if not report["evidence_refs"]:
            raise ValueError("satisfied typed-relations duty requires relation evidence")
        action_evidence = {
            canonical_json(reference)
            for action in relation_actions
            for reference in action["evidence_refs"]
        }
        report_evidence = {canonical_json(reference) for reference in report["evidence_refs"]}
        if not all(action["evidence_refs"] for action in relation_actions):
            raise ValueError("relation action requires source evidence")
        if report_evidence != action_evidence:
            raise ValueError("typed-relations duty evidence does not cover relation actions")

    @staticmethod
    def _validate_dispositions(
        *,
        observation_ids: set[str],
        dispositions: list[dict[str, Any]],
        publication_targets: set[str],
    ) -> None:
        received = [item["observation_id"] for item in dispositions]
        if len(received) != len(set(received)) or set(received) != observation_ids:
            raise ValueError("every semantic observation requires exactly one disposition")
        for item in dispositions:
            disposition = item["disposition"]
            target = item["target_ref"]
            if disposition in {"published", "merged_into"}:
                if target is None or target not in publication_targets:
                    raise ValueError("published semantic observation has no publication target")
            elif disposition == "retained_existing":
                if target is None or not target.startswith("knowledge_"):
                    raise ValueError("retained semantic observation has no existing target")
            elif target is not None:
                raise ValueError("non-publication disposition cannot claim a target")

    @staticmethod
    def _source_summary_action(
        *,
        source_revision_id: str,
        compilation_run_id: str,
        packet_plans: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        matches = [
            action
            for packet_plan in packet_plans
            for action in packet_plan["object_actions"]
            if action["kind"] == "synthesis"
            and action["semantic_key"] == f"source-summary:{source_revision_id}"
        ]
        if len(matches) > 1:
            raise ValueError("semantic publication has multiple Source Summaries")
        if not matches:
            return None
        action = matches[0]
        inputs = action["synthesis_inputs"]
        if (
            inputs is None
            or source_revision_id not in inputs["source_revision_ids"]
            or compilation_run_id not in inputs["compilation_run_ids"]
            or not action["source_refs"]
        ):
            raise ValueError("Source Summary has an incomplete synthesis input set")
        expected_input_sha256 = sha256_bytes(
            canonical_json(
                {
                    "source_revision_ids": sorted(inputs["source_revision_ids"]),
                    "knowledge_revision_ids": sorted(inputs["knowledge_revision_ids"]),
                    "relation_revision_ids": sorted(inputs["relation_revision_ids"]),
                    "compilation_run_ids": sorted(inputs["compilation_run_ids"]),
                }
            ).encode("utf-8")
        )
        if inputs["input_set_sha256"] != expected_input_sha256:
            raise ValueError("Source Summary synthesis input digest is invalid")
        return action

    def stage_publication(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        plan: dict[str, Any],
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if (
            isinstance(plan, dict)
            and plan.get("schema_version") == "deeplaw.semantic-publication-plan/v3"
        ):
            return self._stage_publication_v3(
                grant_id=grant_id,
                compilation_run_id=compilation_run_id,
                plan=plan,
                confirm_no_case_data=confirm_no_case_data,
            )
        if not confirm_no_case_data:
            raise ValueError(
                "semantic finalization requires confirmation that no case data is present"
            )
        if not isinstance(plan, dict):
            raise ValueError("semantic publication plan must be an object")
        _validate_contract("semantic-publication-plan.v2.schema.json", plan)
        payload = canonical_json(plan).encode("utf-8")
        if len(payload) > MAX_COMPILATION_REQUEST_BYTES:
            raise ValueError("semantic publication plan exceeds its request byte limit")
        plan_sha256 = sha256_bytes(payload)
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            run = CompilationCoordinator._run(store, compilation_run_id)
            semantic_run = store.connection.execute(
                """
                SELECT * FROM semantic_compilation_runs_v2
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()
            if semantic_run is None or run["compiler_profile_version"] != "2":
                raise ValueError("semantic publication requires compiler profile v2")
            if run["grant_id"] != grant_id:
                raise PermissionError("semantic compilation run is bound to another grant")
            grant = store._grant(
                grant_id,
                operation="finalize_semantic_compilation",
                request_bytes=len(payload),
            )
            store._enforce_grant_limits(grant, enforce_object_capacity=False)
            if run["status"] not in {"planned", "staging", "validating"}:
                raise RuntimeError("semantic compilation cannot be finalized now")
            if (
                plan["compilation_run_id"] != compilation_run_id
                or plan["source_revision_id"] != run["source_revision_id"]
                or plan["expected_audit_head"] != run["input_audit_head"]
                or plan["inventory_sha256"] != semantic_run["inventory_sha256"]
            ):
                raise ValueError("semantic publication precondition is invalid")
            if semantic_run["inventory_sha256"] is None:
                raise RuntimeError("semantic publication requires a frozen inventory")
            if store.audit_head != run["input_audit_head"]:
                raise RuntimeError("autonomous audit head changed after semantic compilation began")
            existing_plan = semantic_run["publication_plan_sha256"]
            if existing_plan is not None:
                if existing_plan != plan_sha256:
                    raise RuntimeError("semantic publication plan is already frozen")
                return {
                    "schema_version": "deeplaw.semantic-publication-staging/v1",
                    "compilation_run_id": compilation_run_id,
                    "publication_plan_sha256": plan_sha256,
                    "semantic_status": semantic_run["semantic_status"],
                    "idempotent_replay": True,
                }
            observation_rows = store.connection.execute(
                """
                SELECT observation_id, kind FROM semantic_observations_v2
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchall()
            observation_kinds = {row["observation_id"]: row["kind"] for row in observation_rows}
            observation_ids = set(observation_kinds)
            packet_ids = [
                row["packet_id"]
                for row in store.connection.execute(
                    """
                    SELECT packet_id FROM source_compilation_packets_v1
                    WHERE compilation_run_id = ? ORDER BY ordinal
                    """,
                    (compilation_run_id,),
                )
            ]
        packet_plans = plan["packet_plans"]
        if len(packet_plans) != len(packet_ids):
            raise ValueError("semantic publication must decide every packet")
        by_packet: dict[str, dict[str, Any]] = {}
        publication_targets: set[str] = set()
        for packet_plan in packet_plans:
            _validate_contract("source-compilation-plan.v1.schema.json", packet_plan)
            packet_id = packet_plan["packet_id"]
            if packet_id in by_packet:
                raise ValueError("semantic publication packet plan is duplicated")
            by_packet[packet_id] = packet_plan
            publication_targets.update(
                action["semantic_key"] for action in packet_plan["object_actions"]
            )
            publication_targets.update(
                action["knowledge_id"]
                for action in packet_plan["object_actions"]
                if action["knowledge_id"] is not None
            )
        if set(by_packet) != set(packet_ids):
            raise ValueError("semantic publication packet inventory is inconsistent")
        output_ref_kinds = dict(observation_kinds)
        output_ref_kinds.update(
            {
                action["knowledge_id"]: action["kind"]
                for packet_plan in packet_plans
                for action in packet_plan["object_actions"]
                if action["knowledge_id"] is not None
            }
        )
        supported_output_refs = set(output_ref_kinds)
        duty_reports = self._validate_duties(
            compilation_run_id=compilation_run_id,
            reports=plan["duty_reports"],
        )
        self._validate_dispositions(
            observation_ids=observation_ids,
            dispositions=plan["observation_dispositions"],
            publication_targets=publication_targets,
        )
        source_summary = self._source_summary_action(
            source_revision_id=plan["source_revision_id"],
            compilation_run_id=compilation_run_id,
            packet_plans=packet_plans,
        )
        summary_report = duty_reports["source_summary"]
        if summary_report["status"] == "satisfied" and source_summary is None:
            raise ValueError("satisfied Source Summary duty has no canonical Synthesis")
        if source_summary is not None and summary_report["status"] != "satisfied":
            raise ValueError("Source Summary publication and duty status disagree")
        self._validate_relation_duty(
            report=duty_reports[RELATION_OUTPUT_DUTY],
            packet_plans=packet_plans,
        )
        self._validate_duty_evidence(
            reports=duty_reports,
            source_summary=source_summary,
            supported_output_refs=supported_output_refs,
            output_ref_kinds=output_ref_kinds,
        )
        complete_allowed = source_summary is not None and all(
            report["status"] in {"satisfied", "not_applicable"} for report in duty_reports.values()
        )
        if plan["semantic_status"] == "complete" and not complete_allowed:
            raise ValueError("semantic completeness is not supported by the duty evidence")
        if plan["semantic_status"] != "complete" and complete_allowed:
            raise ValueError("semantic status understates a complete deterministic result")
        for packet_id in packet_ids:
            self.coordinator.stage(
                grant_id=grant_id,
                compilation_run_id=compilation_run_id,
                plan=by_packet[packet_id],
                confirm_no_case_data=True,
                _allow_run_wide_source_refs=True,
            )
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            recorded_at = store._next_transaction_time()
            request_sha256 = sha256_bytes(
                canonical_json(
                    {
                        "operation": "finalize_semantic_compilation",
                        "compilation_run_id": compilation_run_id,
                        "publication_plan_sha256": plan_sha256,
                    }
                ).encode("utf-8")
            )
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                semantic_run = store.connection.execute(
                    """
                    SELECT publication_plan_sha256 FROM semantic_compilation_runs_v2
                    WHERE compilation_run_id = ?
                    """,
                    (compilation_run_id,),
                ).fetchone()
                if semantic_run is None or semantic_run["publication_plan_sha256"] is not None:
                    raise RuntimeError("semantic publication finalization precondition changed")
                artifact_sha256, _ = _artifact(
                    store,
                    value=plan,
                    role="publication_plan",
                    created_at=recorded_at,
                )
                if artifact_sha256 != plan_sha256:
                    raise RuntimeError("semantic publication plan digest changed")
                for item in plan["observation_dispositions"]:
                    store.connection.execute(
                        """
                        INSERT INTO semantic_observation_dispositions_v1(
                            compilation_run_id, observation_id, disposition,
                            target_ref, reason
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            compilation_run_id,
                            item["observation_id"],
                            item["disposition"],
                            item["target_ref"],
                            item["reason"],
                        ),
                    )
                for report in plan["duty_reports"]:
                    store.connection.execute(
                        """
                        INSERT INTO semantic_duty_reports_v1(
                            compilation_run_id, duty_id, duty_type, required,
                            status, report_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            compilation_run_id,
                            report["duty_id"],
                            report["duty_type"],
                            int(report["required"]),
                            report["status"],
                            canonical_json(report),
                        ),
                    )
                store.connection.execute(
                    """
                    UPDATE semantic_compilation_runs_v2
                    SET semantic_status = ?, publication_plan_sha256 = ?, updated_at = ?
                    WHERE compilation_run_id = ?
                    """,
                    (
                        plan["semantic_status"],
                        plan_sha256,
                        recorded_at,
                        compilation_run_id,
                    ),
                )
                CompilationCoordinator._record_usage(
                    store,
                    grant_id=grant_id,
                    operation="finalize_semantic_compilation",
                    request_sha256=request_sha256,
                    recorded_at=recorded_at,
                    discriminator=compilation_run_id,
                )
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise
        return {
            "schema_version": "deeplaw.semantic-publication-staging/v1",
            "compilation_run_id": compilation_run_id,
            "publication_plan_sha256": plan_sha256,
            "semantic_status": plan["semantic_status"],
            "idempotent_replay": False,
        }

    @staticmethod
    def _validate_duties_v3(
        *,
        compilation_run_id: str,
        reports: list[dict[str, Any]],
        expected: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Validate the additive v3 report shape and frozen applicability basis."""
        by_type: dict[str, dict[str, Any]] = {}
        for report in reports:
            _validate_contract("semantic-compilation-duty-report.v2.schema.json", report)
            duty_type = report["duty_type"]
            if duty_type in by_type:
                raise ValueError("semantic duty report is duplicated")
            expected_id = stable_id("duty", compilation_run_id, duty_type)
            if report["duty_id"] != expected_id:
                raise ValueError("semantic duty ID does not match its run")
            if report["required"] != (duty_type in REQUIRED_SEMANTIC_DUTIES):
                raise ValueError("semantic duty required flag is policy-controlled")
            frozen = expected.get(duty_type)
            if frozen is None or report["applicability"] != frozen["applicability"]:
                raise ValueError("semantic duty applicability differs from the finalization packet")
            if report["deterministic_basis"] != frozen["deterministic_basis"]:
                raise ValueError(
                    "semantic duty deterministic basis differs from the finalization packet"
                )
            basis = report["deterministic_basis"]
            if basis["facts_sha256"] != sha256_bytes(
                canonical_json(basis["facts"]).encode("utf-8")
            ):
                raise ValueError("semantic duty deterministic facts digest is invalid")
            applicability = report["applicability"]
            status = report["status"]
            if applicability == "unknown":
                if status != "unresolved" or not report["unresolved_items"]:
                    raise ValueError(
                        "unknown duty applicability requires unresolved status and items"
                    )
                if (
                    report["output_refs"]
                    or report["evidence_refs"]
                    or report["omission_reason"] is not None
                ):
                    raise ValueError("unknown duty applicability cannot claim output or omission")
            elif applicability == "not_applicable":
                if status != "omitted_with_reason" or report["omission_reason"] is None:
                    raise ValueError(
                        "not-applicable duty requires omitted_with_reason and omission reason"
                    )
                if report["output_refs"] or report["evidence_refs"] or report["unresolved_items"]:
                    raise ValueError(
                        "not-applicable duty cannot claim output, evidence, or unresolved items"
                    )
            elif status == "omitted_with_reason" and report["omission_reason"] is None:
                raise ValueError("omitted applicable duty requires an omission reason")
            elif status == "unresolved" and not report["unresolved_items"]:
                raise ValueError("unresolved applicable duty requires unresolved items")
            elif status == "satisfied" and report["omission_reason"] is not None:
                raise ValueError("satisfied applicable duty cannot carry an omission reason")
            if status != "unresolved" and report["unresolved_items"]:
                raise ValueError("non-unresolved duty cannot carry unresolved items")
            by_type[duty_type] = report
        if set(by_type) != set(SEMANTIC_DUTIES):
            raise ValueError("semantic duty inventory is incomplete")
        return by_type

    def _stage_publication_v3(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        plan: dict[str, Any],
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "semantic finalization requires confirmation that no case data is present"
            )
        _validate_contract("semantic-publication-plan.v3.schema.json", plan)
        payload = canonical_json(plan).encode("utf-8")
        if len(payload) > MAX_COMPILATION_REQUEST_BYTES:
            raise ValueError("semantic publication plan exceeds its request byte limit")
        plan_sha256 = sha256_bytes(payload)
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            run = CompilationCoordinator._run(store, compilation_run_id)
            semantic_run = store.connection.execute(
                "SELECT * FROM semantic_compilation_runs_v2 WHERE compilation_run_id = ?",
                (compilation_run_id,),
            ).fetchone()
            if semantic_run is None or run["compiler_profile_version"] != "3":
                raise ValueError("semantic publication requires compiler profile v3")
            if run["grant_id"] != grant_id:
                raise PermissionError("semantic compilation run is bound to another grant")
            grant = store._grant(
                grant_id,
                operation="finalize_semantic_compilation",
                request_bytes=len(payload),
            )
            store._enforce_grant_limits(grant, enforce_object_capacity=False)
            if run["status"] not in {"planned", "staging", "validating"}:
                raise RuntimeError("semantic compilation cannot be finalized now")
            if semantic_run["inventory_sha256"] is None:
                raise RuntimeError("semantic publication requires a frozen inventory")
            if (
                plan["compilation_run_id"] != compilation_run_id
                or plan["source_revision_id"] != run["source_revision_id"]
                or plan["expected_audit_head"] != run["input_audit_head"]
                or plan["inventory_sha256"] != semantic_run["inventory_sha256"]
            ):
                raise ValueError("semantic publication precondition is invalid")
            existing_plan = semantic_run["publication_plan_sha256"]
            if existing_plan is not None:
                if existing_plan != plan_sha256:
                    raise RuntimeError("semantic publication plan is already frozen")
                return {
                    "schema_version": "deeplaw.semantic-publication-staging/v1",
                    "compilation_run_id": compilation_run_id,
                    "publication_plan_sha256": plan_sha256,
                    "semantic_status": semantic_run["semantic_status"],
                    "idempotent_replay": True,
                }
            from .semantic_inventory import SemanticInventoryBuilder

            inventory_row = store.connection.execute(
                "SELECT artifact_sha256 FROM semantic_inventories_v1 WHERE compilation_run_id = ?",
                (compilation_run_id,),
            ).fetchone()
            if inventory_row is None:
                raise RuntimeError("semantic inventory binding is missing")
            frozen_inventory = _decoded_artifact(
                store, inventory_row["artifact_sha256"], role="semantic_inventory"
            )
            observation_values = [
                strict_json_loads(item["observation_json"])
                for item in store.connection.execute(
                    """
                    SELECT observation_json FROM semantic_observations_v2
                    WHERE compilation_run_id = ? ORDER BY observation_id
                    """,
                    (compilation_run_id,),
                )
            ]
            fresh_facts = collect_runtime_facts(
                store,
                run,
                observations=observation_values,
                previous_outputs=frozen_inventory["previous_outputs"],
                affected_syntheses=frozen_inventory["affected_syntheses"],
            )
            fresh_applicability = derive_applicability(fresh_facts)
            frozen_coverage = frozen_inventory.get("coverage", {})
            if frozen_coverage.get("runtime_facts", {}).get("facts_sha256") != fresh_facts.get(
                "facts_sha256"
            ) or applicability_digest(fresh_applicability) != frozen_coverage.get(
                "applicability_digest"
            ):
                raise RuntimeError("semantic runtime facts changed after inventory freeze")

            finalization_packet = SemanticInventoryBuilder(self.root).finalization_packet(
                compilation_run_id
            )
            if (
                plan["compiler_profile_version"] != "3"
                or finalization_packet["compiler_profile_version"] != "3"
                or plan["finalization_packet_id"] != finalization_packet["finalization_packet_id"]
                or plan["applicability_policy_sha256"]
                != finalization_packet["applicability_policy_sha256"]
                or plan["applicability_digest"] != finalization_packet["applicability_digest"]
            ):
                raise ValueError("semantic publication applicability binding is invalid")
            expected_duties = {item["duty_type"]: item for item in finalization_packet["duties"]}
            observation_rows = store.connection.execute(
                """
                SELECT observation_id, kind FROM semantic_observations_v2
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchall()
            observation_kinds = {row["observation_id"]: row["kind"] for row in observation_rows}
            observation_ids = set(observation_kinds)
            packet_ids = [
                row["packet_id"]
                for row in store.connection.execute(
                    """
                    SELECT packet_id FROM source_compilation_packets_v1
                    WHERE compilation_run_id = ? ORDER BY ordinal
                    """,
                    (compilation_run_id,),
                )
            ]
        packet_plans = plan["packet_plans"]
        if len(packet_plans) != len(packet_ids):
            raise ValueError("semantic publication must decide every packet")
        by_packet: dict[str, dict[str, Any]] = {}
        publication_targets: set[str] = set()
        for packet_plan in packet_plans:
            _validate_contract("source-compilation-plan.v1.schema.json", packet_plan)
            packet_id = packet_plan["packet_id"]
            if packet_id in by_packet:
                raise ValueError("semantic publication packet plan is duplicated")
            by_packet[packet_id] = packet_plan
            publication_targets.update(
                action["semantic_key"] for action in packet_plan["object_actions"]
            )
            publication_targets.update(
                action["knowledge_id"]
                for action in packet_plan["object_actions"]
                if action["knowledge_id"] is not None
            )
        if set(by_packet) != set(packet_ids):
            raise ValueError("semantic publication packet inventory is inconsistent")
        output_ref_kinds = dict(observation_kinds)
        output_ref_kinds.update(
            {
                action["knowledge_id"]: action["kind"]
                for packet_plan in packet_plans
                for action in packet_plan["object_actions"]
                if action["knowledge_id"] is not None
            }
        )
        action_bodies: dict[tuple[str, int], str] = {}
        action_kinds: dict[tuple[str, int], str] = {}
        for packet_plan in packet_plans:
            for action_ordinal, action in enumerate(packet_plan["object_actions"], start=1):
                target = (packet_plan["packet_id"], action_ordinal)
                action_bodies[target] = action["body"]
                action_kinds[target] = action["kind"]
        validate_statement_plans(
            plan["statement_plans"],
            action_bodies=action_bodies,
            action_kinds=action_kinds,
        )
        supported_output_refs = set(output_ref_kinds)
        duty_reports = self._validate_duties_v3(
            compilation_run_id=compilation_run_id,
            reports=plan["duty_reports"],
            expected=expected_duties,
        )
        self._validate_dispositions(
            observation_ids=observation_ids,
            dispositions=plan["observation_dispositions"],
            publication_targets=publication_targets,
        )
        source_summary = self._source_summary_action(
            source_revision_id=plan["source_revision_id"],
            compilation_run_id=compilation_run_id,
            packet_plans=packet_plans,
        )
        summary_report = duty_reports["source_summary"]
        if summary_report["status"] == "satisfied" and source_summary is None:
            raise ValueError("satisfied Source Summary duty has no canonical Synthesis")
        if source_summary is not None and summary_report["status"] != "satisfied":
            raise ValueError("Source Summary publication and duty status disagree")
        relation_report = duty_reports[RELATION_OUTPUT_DUTY]
        relation_actions = [
            action for packet_plan in packet_plans for action in packet_plan["relation_actions"]
        ]
        if relation_report["applicability"] == "not_applicable" and relation_actions:
            raise ValueError("not-applicable typed-relations duty has relation actions")
        self._validate_relation_duty(report=relation_report, packet_plans=packet_plans)
        for duty_type, report in duty_reports.items():
            if report["applicability"] != "not_applicable":
                continue
            expected_kind = (
                "synthesis"
                if duty_type == "source_summary"
                else CONTENT_DUTY_OBSERVATION_KINDS.get(duty_type)
            )
            if expected_kind is not None and any(
                action["kind"] == expected_kind
                for packet_plan in packet_plans
                for action in packet_plan["object_actions"]
            ):
                raise ValueError("not-applicable semantic duty has publication actions")
        self._validate_duty_evidence(
            reports=duty_reports,
            source_summary=source_summary,
            supported_output_refs=supported_output_refs,
            output_ref_kinds=output_ref_kinds,
        )
        complete_allowed = source_summary is not None and all(
            (
                report["applicability"] == "not_applicable"
                and report["status"] == "omitted_with_reason"
            )
            or (report["applicability"] == "applicable" and report["status"] == "satisfied")
            for report in duty_reports.values()
        )
        if any(report["applicability"] == "unknown" for report in duty_reports.values()):
            complete_allowed = False
        if plan["semantic_status"] == "complete" and not complete_allowed:
            raise ValueError(
                "semantic completeness is not supported by deterministic duty applicability"
            )
        if plan["semantic_status"] != "complete" and complete_allowed:
            raise ValueError("semantic status understates a complete deterministic result")
        for packet_id in packet_ids:
            self.coordinator.stage(
                grant_id=grant_id,
                compilation_run_id=compilation_run_id,
                plan=by_packet[packet_id],
                confirm_no_case_data=True,
                _allow_run_wide_source_refs=True,
            )
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            recorded_at = store._next_transaction_time()
            request_sha256 = sha256_bytes(
                canonical_json(
                    {
                        "operation": "finalize_semantic_compilation",
                        "compilation_run_id": compilation_run_id,
                        "publication_plan_sha256": plan_sha256,
                    }
                ).encode("utf-8")
            )
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                locked = store.connection.execute(
                    """
                    SELECT publication_plan_sha256 FROM semantic_compilation_runs_v2
                    WHERE compilation_run_id = ?
                    """,
                    (compilation_run_id,),
                ).fetchone()
                if locked is None or locked["publication_plan_sha256"] is not None:
                    raise RuntimeError("semantic publication finalization precondition changed")
                artifact_sha256, _ = _artifact(
                    store, value=plan, role="publication_plan", created_at=recorded_at
                )
                if artifact_sha256 != plan_sha256:
                    raise RuntimeError("semantic publication plan digest changed")
                for item in plan["observation_dispositions"]:
                    store.connection.execute(
                        """
                        INSERT INTO semantic_observation_dispositions_v1(
                            compilation_run_id, observation_id, disposition,
                            target_ref, reason
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            compilation_run_id,
                            item["observation_id"],
                            item["disposition"],
                            item["target_ref"],
                            item["reason"],
                        ),
                    )
                for report in plan["duty_reports"]:
                    store.connection.execute(
                        """
                        INSERT INTO semantic_duty_reports_v1(
                            compilation_run_id, duty_id, duty_type, required,
                            status, report_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            compilation_run_id,
                            report["duty_id"],
                            report["duty_type"],
                            int(report["required"]),
                            report["status"],
                            canonical_json(report),
                        ),
                    )
                store.connection.execute(
                    """
                    UPDATE semantic_compilation_runs_v2
                    SET semantic_status = ?, publication_plan_sha256 = ?, updated_at = ?
                    WHERE compilation_run_id = ?
                    """,
                    (plan["semantic_status"], plan_sha256, recorded_at, compilation_run_id),
                )
                CompilationCoordinator._record_usage(
                    store,
                    grant_id=grant_id,
                    operation="finalize_semantic_compilation",
                    request_sha256=request_sha256,
                    recorded_at=recorded_at,
                    discriminator=compilation_run_id,
                )
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise
        return {
            "schema_version": "deeplaw.semantic-publication-staging/v1",
            "compilation_run_id": compilation_run_id,
            "publication_plan_sha256": plan_sha256,
            "semantic_status": plan["semantic_status"],
            "idempotent_replay": False,
        }

    def commit(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            semantic = store.connection.execute(
                """
                SELECT publication_plan_sha256 FROM semantic_compilation_runs_v2
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()
            if semantic is None or semantic["publication_plan_sha256"] is None:
                raise RuntimeError("semantic publication plan is not finalized")
        result = self.coordinator.commit(
            grant_id=grant_id,
            compilation_run_id=compilation_run_id,
            confirm_no_case_data=confirm_no_case_data,
        )
        return self._quality_receipt(compilation_run_id, result)

    def _quality_receipt(
        self,
        compilation_run_id: str,
        commit_result: dict[str, Any],
    ) -> dict[str, Any]:
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            semantic = store.connection.execute(
                """
                SELECT * FROM semantic_compilation_runs_v2
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()
            if semantic is None:
                raise RuntimeError("semantic compilation state is missing")
            existing = store.connection.execute(
                """
                SELECT artifact_sha256 FROM semantic_quality_receipts_v1
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()
            if existing is not None:
                from .coordinator import _decoded_artifact

                return _decoded_artifact(
                    store, existing["artifact_sha256"], role="semantic_receipt"
                )
            summary = store.connection.execute(
                """
                SELECT revisions.revision_id
                FROM source_compilation_outputs_v1 AS outputs
                JOIN knowledge_revisions_v3 AS revisions
                  ON revisions.revision_id = outputs.output_id
                WHERE outputs.compilation_run_id = ?
                  AND revisions.kind = 'synthesis'
                  AND revisions.semantic_key = ?
                """,
                (
                    compilation_run_id,
                    f"source-summary:{commit_result['source_revision_id']}",
                ),
            ).fetchone()
            reports = [
                strict_json_loads(row["report_json"])
                for row in store.connection.execute(
                    """
                    SELECT report_json FROM semantic_duty_reports_v1
                    WHERE compilation_run_id = ? ORDER BY duty_type
                    """,
                    (compilation_run_id,),
                )
            ]
            disposition_count = store.connection.execute(
                """
                SELECT COUNT(*) FROM semantic_observation_dispositions_v1
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()[0]
            receipt = {
                "schema_version": "deeplaw.semantic-compilation-quality-receipt/v1",
                "compilation_run_id": compilation_run_id,
                "inventory_sha256": semantic["inventory_sha256"],
                "publication_plan_sha256": semantic["publication_plan_sha256"],
                "semantic_status": semantic["semantic_status"],
                "duty_reports": reports,
                "observation_count": semantic["observation_count"],
                "disposition_count": disposition_count,
                "source_summary_revision_id": summary["revision_id"] if summary else None,
                "hard_failures": [],
                "receipt_sha256": "0" * 64,
            }
            body = dict(receipt)
            body.pop("receipt_sha256")
            receipt["receipt_sha256"] = sha256_bytes(canonical_json(body).encode("utf-8"))
            _validate_contract("semantic-compilation-quality-receipt.v1.schema.json", receipt)
            recorded_at = store._next_transaction_time()
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                artifact_sha256, _ = _artifact(
                    store,
                    value=receipt,
                    role="semantic_receipt",
                    created_at=recorded_at,
                )
                store.connection.execute(
                    """
                    INSERT INTO semantic_quality_receipts_v1(
                        artifact_sha256, receipt_sha256,
                        compilation_run_id, recorded_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        artifact_sha256,
                        receipt["receipt_sha256"],
                        compilation_run_id,
                        recorded_at,
                    ),
                )
                store.connection.execute(
                    """
                    UPDATE semantic_compilation_runs_v2
                    SET quality_receipt_sha256 = ?, source_summary_revision_id = ?,
                        updated_at = ?
                    WHERE compilation_run_id = ?
                    """,
                    (
                        receipt["receipt_sha256"],
                        receipt["source_summary_revision_id"],
                        recorded_at,
                        compilation_run_id,
                    ),
                )
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise
            return receipt
