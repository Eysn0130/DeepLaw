from __future__ import annotations

from pathlib import Path
from typing import Any

from ..knowledge_autonomy import AutonomousKnowledgeStore, _validate_contract
from ..util import strict_json_loads
from .coordinator import CompilationCoordinator
from .finalization import SemanticFinalizer
from .observation_store import ObservationStore
from .semantic_inventory import SemanticInventoryBuilder


class SemanticCompilationService:
    """Public domain service for the v2 observe/finalize compilation protocol."""

    def __init__(self, path: str | Path) -> None:
        self.root = Path(path).expanduser().absolute()
        self.coordinator = CompilationCoordinator(self.root)
        self.observations = ObservationStore(self.root)
        self.inventories = SemanticInventoryBuilder(self.root)
        self.finalizer = SemanticFinalizer(self.root)

    @staticmethod
    def observation_id(
        *,
        compilation_run_id: str,
        packet_id: str,
        observation: dict[str, Any],
    ) -> str:
        return ObservationStore.observation_id(
            compilation_run_id=compilation_run_id,
            packet_id=packet_id,
            observation=observation,
        )

    def next_observation_packet(
        self, compilation_run_id: str
    ) -> dict[str, Any] | None:
        return self.observations.next_packet(compilation_run_id)

    def stage_observations(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        plan: dict[str, Any],
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        return self.observations.stage(
            grant_id=grant_id,
            compilation_run_id=compilation_run_id,
            plan=plan,
            confirm_no_case_data=confirm_no_case_data,
        )

    def inventory(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        return self.inventories.build(
            compilation_run_id,
            grant_id=grant_id,
            confirm_no_case_data=confirm_no_case_data,
        )

    def finalization_packet(self, compilation_run_id: str) -> dict[str, Any]:
        return self.inventories.finalization_packet(compilation_run_id)

    def stage_publication(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        plan: dict[str, Any],
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        return self.finalizer.stage_publication(
            grant_id=grant_id,
            compilation_run_id=compilation_run_id,
            plan=plan,
            confirm_no_case_data=confirm_no_case_data,
        )

    def validate(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        return self.coordinator.validate(
            grant_id=grant_id,
            compilation_run_id=compilation_run_id,
            confirm_no_case_data=confirm_no_case_data,
        )

    def commit(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        return self.finalizer.commit(
            grant_id=grant_id,
            compilation_run_id=compilation_run_id,
            confirm_no_case_data=confirm_no_case_data,
        )

    def status(self, compilation_run_id: str) -> dict[str, Any]:
        transaction = self.coordinator.status(compilation_run_id)
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            row = store.connection.execute(
                """
                SELECT * FROM semantic_compilation_runs_v2
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()
            if row is None:
                raise KeyError("semantic compilation run is unavailable")
            reports = [
                strict_json_loads(item["report_json"])
                for item in store.connection.execute(
                    """
                    SELECT report_json FROM semantic_duty_reports_v1
                    WHERE compilation_run_id = ? ORDER BY duty_type
                    """,
                    (compilation_run_id,),
                )
            ]
        result = {
            "schema_version": "deeplaw.source-compilation-run/v2",
            "transaction": transaction,
            "semantic_status": row["semantic_status"],
            "observation_packet_count": row["observation_packet_count"],
            "observed_packet_count": row["observed_packet_count"],
            "observation_count": row["observation_count"],
            "inventory_sha256": row["inventory_sha256"],
            "publication_plan_sha256": row["publication_plan_sha256"],
            "source_summary_revision_id": row["source_summary_revision_id"],
            "quality_receipt_sha256": row["quality_receipt_sha256"],
            "duty_reports": reports,
            "gaps": self._gaps(row=row, reports=reports),
        }
        _validate_contract("source-compilation-run.v2.schema.json", result)
        return result

    @staticmethod
    def _gaps(*, row: Any, reports: list[dict[str, Any]]) -> list[str]:
        gaps = []
        if row["semantic_status"] in {"partial", "blocked"}:
            gaps.append("partial_compilation")
        if row["source_summary_revision_id"] is None:
            gaps.append("source_summary_missing")
        unresolved = {item["duty_type"] for item in reports if item["status"] == "unresolved"}
        if unresolved:
            gaps.append("semantic_duty_unresolved")
        if "identity_resolution" in unresolved:
            gaps.append("identity_resolution_pending")
        if "affected_synthesis_detection" in unresolved:
            gaps.append("synthesis_refresh_pending")
        return gaps

    def explain(self, compilation_run_id: str) -> dict[str, Any]:
        status = self.status(compilation_run_id)
        result = {
            "schema_version": "deeplaw.source-compilation-explanation/v2",
            "compilation_run_id": compilation_run_id,
            "transaction_explanation": self.coordinator.explain(compilation_run_id),
            "semantic_status": status["semantic_status"],
            "duty_reports": status["duty_reports"],
            "gaps": status["gaps"],
        }
        _validate_contract("source-compilation-explanation.v2.schema.json", result)
        return result
