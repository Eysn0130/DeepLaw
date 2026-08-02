from __future__ import annotations

import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ..knowledge_autonomy import (
    AGENT_KNOWLEDGE_MUTABILITY,
    AUTONOMOUS_ACTIVATION_POLICY,
    EPISTEMIC_STATES,
    KNOWLEDGE_KINDS,
    KNOWLEDGE_OBJECT_SCHEMA,
    RELATION_PREDICATES,
    SENSITIVITIES,
    SENSITIVITY_ORDER,
    AutonomousKnowledgeStore,
    _canonical_source_references,
    _read_object,
    _register_content_object,
    _safe_knowledge_workspace_path,
    _timestamp_after,
    _validate_contract,
    _workspace_path,
    _write_object,
    render_knowledge_markdown,
)
from ..knowledge_intelligence import normalize_identity_text
from ..knowledge_models import canonical_timestamp
from ..util import (
    canonical_json,
    compact_text,
    has_instruction_risk,
    sha256_bytes,
    stable_id,
    strict_json_loads,
)
from .models import (
    COMPILATION_BATCH_SCHEMA,
    COMPILATION_PACKET_SCHEMA,
    COMPILATION_RECEIPT_SCHEMA,
    COMPILATION_RUN_SCHEMA,
    MAX_ACTIONS_PER_PACKET,
    MAX_ACTIONS_PER_RUN,
    MAX_COMPILATION_CONTEXT_BYTES,
    MAX_COMPILATION_REQUEST_BYTES,
    MAX_PACKET_FRAGMENTS,
    MAX_PACKET_PROVIDER_BYTES,
    MAX_PACKETS_PER_RUN,
)
from .profiles import compiler_profile as registered_compiler_profile

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^compilationrun_[0-9a-f]{24}$")
_PACKET_ID = re.compile(r"^packet_[0-9a-f]{24}$")
_MAX_TEXT = 200_000


def _bounded(value: Any, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or "\x00" in value
    ):
        raise ValueError(f"{field} must be a bounded canonical string")
    return value


def _digest(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be lowercase SHA-256")
    return value


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return canonical_json(value).encode("utf-8")


def _artifact(
    store: AutonomousKnowledgeStore,
    *,
    value: dict[str, Any],
    role: str,
    created_at: str,
) -> tuple[str, bytes]:
    payload = _canonical_bytes(value)
    digest, _ = _write_object(store.root, payload)
    store.connection.execute(
        """
        INSERT OR IGNORE INTO source_compilation_artifacts_v1(
            artifact_sha256, artifact_role, byte_size, media_type, created_at
        ) VALUES (?, ?, ?, 'application/json', ?)
        """,
        (digest, role, len(payload), created_at),
    )
    row = store.connection.execute(
        """
        SELECT artifact_role, byte_size
        FROM source_compilation_artifacts_v1
        WHERE artifact_sha256 = ?
        """,
        (digest,),
    ).fetchone()
    if row is None or row["artifact_role"] != role or row["byte_size"] != len(payload):
        raise RuntimeError("source compilation artifact metadata is inconsistent")
    return digest, payload


def _decoded_artifact(
    store: AutonomousKnowledgeStore,
    digest: str,
    *,
    role: str,
) -> dict[str, Any]:
    row = store.connection.execute(
        """
        SELECT artifact_role, byte_size FROM source_compilation_artifacts_v1
        WHERE artifact_sha256 = ?
        """,
        (digest,),
    ).fetchone()
    if row is None or row["artifact_role"] != role:
        raise RuntimeError("source compilation artifact is unavailable")
    payload = _read_object(store.root, digest)
    if len(payload) != row["byte_size"]:
        raise RuntimeError("source compilation artifact byte size changed")
    value = strict_json_loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError("source compilation artifact is not an object")
    return value


class CompilationCoordinator:
    """Coordinate an invisible staging saga and one atomic canonical commit."""

    def __init__(self, path: str | Path) -> None:
        self.root = Path(path).expanduser().absolute()
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            installed = store.connection.execute(
                """
                SELECT schema_version FROM source_compilation_core_v1
                WHERE schema_version = 'deeplaw.source-compilation-core/v1'
                """
            ).fetchone()
            if installed is None:
                raise RuntimeError(
                    "source compilation core is not initialized; run knowledge autonomy migrate"
                )

    @staticmethod
    def _run(store: AutonomousKnowledgeStore, compilation_run_id: str) -> sqlite3.Row:
        if not _RUN_ID.fullmatch(compilation_run_id):
            raise ValueError("source compilation run ID is invalid")
        row = store.connection.execute(
            """
            SELECT * FROM source_compilation_runs_v1
            WHERE compilation_run_id = ?
            """,
            (compilation_run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"source compilation run is unavailable: {compilation_run_id}")
        return row

    @staticmethod
    def _record_usage(
        store: AutonomousKnowledgeStore,
        *,
        grant_id: str,
        operation: str,
        request_sha256: str,
        recorded_at: str,
        discriminator: str,
    ) -> None:
        operation_id = stable_id(
            "compilationop",
            grant_id,
            operation,
            discriminator,
            request_sha256,
        )
        store.connection.execute(
            """
            INSERT OR IGNORE INTO source_compilation_usage_v1(
                operation_id, grant_id, operation, request_sha256, recorded_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (operation_id, grant_id, operation, request_sha256, recorded_at),
        )

    @staticmethod
    def _elapsed_ms(*, created_at: str, ended_at: str) -> int:
        start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        return max(0, int((end - start).total_seconds() * 1_000))

    @staticmethod
    def _run_response(
        store: AutonomousKnowledgeStore,
        run: sqlite3.Row,
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        staged = store.connection.execute(
            """
            SELECT COUNT(*) FROM source_compilation_packets_v1
            WHERE compilation_run_id = ? AND state IN ('staged', 'validated')
            """,
            (run["compilation_run_id"],),
        ).fetchone()[0]
        metadata = store.connection.execute(
            """
            SELECT * FROM source_compilation_run_metadata_v1
            WHERE compilation_run_id = ?
            """,
            (run["compilation_run_id"],),
        ).fetchone()
        if metadata is None:
            raise RuntimeError("source compilation run metadata is unavailable")
        aggregates = store.connection.execute(
            """
            SELECT COALESCE(SUM(object_count), 0) AS object_count,
                   COALESCE(SUM(relation_count), 0) AS relation_count,
                   COALESCE(SUM(unresolved_identity_count), 0)
                       AS unresolved_identity_count,
                   COALESCE(SUM(contradiction_count), 0) AS contradiction_count,
                   COALESCE(SUM(skipped_fragment_count), 0)
                       AS skipped_fragment_count,
                   COALESCE(SUM(warning_count), 0) AS warning_count,
                   CASE
                     WHEN COALESCE(SUM(source_compilation_packets_v1.fragment_count), 0) = 0
                       THEN 0.0
                     ELSE SUM(
                       source_compilation_batches_v1.coverage_ratio
                       * source_compilation_packets_v1.fragment_count
                     ) / SUM(source_compilation_packets_v1.fragment_count)
                   END AS coverage_ratio
            FROM source_compilation_batches_v1
            JOIN source_compilation_packets_v1 USING(packet_id)
            WHERE source_compilation_batches_v1.compilation_run_id = ?
            """,
            (run["compilation_run_id"],),
        ).fetchone()
        response = {
            "schema_version": COMPILATION_RUN_SCHEMA,
            "compilation_run_id": run["compilation_run_id"],
            "source_key": run["source_key"],
            "source_revision_id": run["source_revision_id"],
            "previous_source_revision_id": metadata["previous_source_revision_id"],
            "source_ir_compilation_id": run["source_ir_compilation_id"],
            "source_ir_sha256": metadata["source_ir_sha256"],
            "expected_source_status": metadata["expected_source_status"],
            "compiler_profile": run["compiler_profile"],
            "compiler_profile_version": run["compiler_profile_version"],
            "host_identity": run["host_identity"],
            "model_identity": run["model_identity"],
            "prompt_template_id": run["prompt_template_id"],
            "prompt_config_sha256": run["prompt_config_sha256"],
            "plan_configuration_sha256": run["plan_configuration_sha256"],
            "status": run["status"],
            "resumable": bool(run["resumable"]),
            "packet_count": run["packet_count"],
            "staged_packet_count": staged,
            "staged_object_count": aggregates["object_count"],
            "staged_relation_count": aggregates["relation_count"],
            "unresolved_identity_count": aggregates["unresolved_identity_count"],
            "contradiction_count": aggregates["contradiction_count"],
            "skipped_fragment_count": aggregates["skipped_fragment_count"],
            "warning_count": aggregates["warning_count"],
            "coverage_ratio": aggregates["coverage_ratio"],
            "input_audit_head": run["input_audit_head"],
            "input_legacy_audit_head": run["input_legacy_audit_head"],
            "validation_sha256": metadata["validation_sha256"],
            "output_set_sha256": run["output_set_sha256"],
            "canonical_commit_sha256": metadata["canonical_commit_sha256"],
            "projection_manifest_sha256": metadata["projection_manifest_sha256"],
            "receipt_sha256": run["receipt_sha256"],
            "token_usage": strict_json_loads(metadata["token_usage_json"]),
            "elapsed_ms": metadata["elapsed_ms"],
            "retry_count": metadata["retry_count"],
            "failure_stage": run["failure_stage"],
            "failure_sha256": run["failure_sha256"],
            "idempotent_replay": idempotent_replay,
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
        }
        _validate_contract("source-compilation-run.v1.schema.json", response)
        return response

    def begin(
        self,
        *,
        grant_id: str,
        source_revision_id: str,
        compiler_profile: str,
        compiler_profile_version: str,
        host_identity: str,
        model_identity: str | None,
        prompt_template_id: str,
        prompt_config_sha256: str,
        plan_configuration_sha256: str,
        confirm_no_case_data: bool,
        packet_max_fragments: int = 32,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "source compilation requires confirmation that no case data is present"
            )
        source_revision_id = _bounded(source_revision_id, field="source revision ID", maximum=200)
        compiler_profile = _bounded(compiler_profile, field="compiler profile", maximum=200)
        compiler_profile_version = _bounded(
            compiler_profile_version, field="compiler profile version", maximum=100
        )
        host_identity = _bounded(host_identity, field="host identity", maximum=200)
        if model_identity is not None:
            model_identity = _bounded(model_identity, field="model identity", maximum=500)
        prompt_template_id = _bounded(prompt_template_id, field="prompt template ID", maximum=300)
        prompt_config_sha256 = _digest(prompt_config_sha256, field="prompt configuration")
        plan_configuration_sha256 = _digest(plan_configuration_sha256, field="plan configuration")
        if (
            not isinstance(packet_max_fragments, int)
            or isinstance(packet_max_fragments, bool)
            or not 1 <= packet_max_fragments <= MAX_PACKET_FRAGMENTS
        ):
            raise ValueError("packet fragment bound is invalid")
        if compiler_profile == "living-wiki-agent":
            registered = registered_compiler_profile(
                compiler_profile,
                compiler_profile_version,
            )
            if (
                prompt_template_id != registered["prompt_template_id"]
                or prompt_config_sha256 != registered["prompt_config_sha256"]
                or plan_configuration_sha256
                != registered["plan_configuration_sha256"]
            ):
                raise ValueError(
                    "source compilation provenance differs from its registered profile"
                )
        request = {
            "operation": "begin_compilation",
            "source_revision_id": source_revision_id,
            "compiler_profile": compiler_profile,
            "compiler_profile_version": compiler_profile_version,
            "host_identity": host_identity,
            "model_identity": model_identity,
            "prompt_template_id": prompt_template_id,
            "prompt_config_sha256": prompt_config_sha256,
            "plan_configuration_sha256": plan_configuration_sha256,
            "packet_max_fragments": packet_max_fragments,
        }
        request_bytes = _canonical_bytes(request)
        if len(request_bytes) > MAX_COMPILATION_REQUEST_BYTES:
            raise ValueError("source compilation request exceeds its global byte limit")
        request_sha256 = sha256_bytes(request_bytes)
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            grant = store._grant(
                grant_id,
                operation="begin_compilation",
                request_bytes=len(request_bytes),
            )
            store._enforce_grant_limits(grant, enforce_object_capacity=False)
            source = store.connection.execute(
                """
                SELECT source_revisions_v2.source_key,
                       compilations_v2.compilation_id
                FROM source_revisions_v2
                JOIN source_revision_bindings_v2
                  ON source_revision_bindings_v2.source_revision_id =
                     source_revisions_v2.source_revision_id
                JOIN source_build_bindings_v2
                  ON source_build_bindings_v2.legacy_source_id =
                     source_revision_bindings_v2.legacy_source_id
                JOIN compilations_v2
                  ON compilations_v2.compilation_id =
                     source_build_bindings_v2.compilation_id
                 AND compilations_v2.source_revision_id =
                     source_revisions_v2.source_revision_id
                WHERE source_revisions_v2.source_revision_id = ?
                LIMIT 1
                """,
                (source_revision_id,),
            ).fetchone()
            if source is None:
                source = store.connection.execute(
                    """
                    SELECT source_revisions_v2.source_key,
                           compilations_v2.compilation_id
                    FROM source_revisions_v2
                    JOIN compilations_v2 USING(source_revision_id)
                    WHERE source_revisions_v2.source_revision_id = ?
                    ORDER BY compilations_v2.compilation_id
                    LIMIT 1
                    """,
                    (source_revision_id,),
                ).fetchone()
            if source is None:
                raise KeyError(f"compiled Source Revision is unavailable: {source_revision_id}")
            binding = store._source_reference_binding({"source_revision_id": source_revision_id})
            if binding is None or binding["active"] is not True:
                raise PermissionError("Source Revision is not currently admitted")
            source_ir = store.connection.execute(
                """
                SELECT adapter, adapter_version, configuration_sha256,
                       source_ir_schema, fragment_inventory_sha256
                FROM compilations_v2
                WHERE compilation_id = ?
                """,
                (source["compilation_id"],),
            ).fetchone()
            if source_ir is None:
                raise RuntimeError("Source IR compilation metadata is unavailable")
            source_ir_sha256 = sha256_bytes(
                canonical_json(
                    {
                        "compilation_id": source["compilation_id"],
                        **dict(source_ir),
                    }
                ).encode("utf-8")
            )
            previous = store.connection.execute(
                """
                SELECT source_revisions_v2.source_revision_id
                FROM source_revisions_v2
                LEFT JOIN source_revision_bindings_v2 USING(source_revision_id)
                WHERE source_revisions_v2.source_key = ?
                  AND source_revisions_v2.source_revision_id <> ?
                ORDER BY source_revision_bindings_v2.observed_at DESC,
                         source_revisions_v2.source_revision_id DESC
                LIMIT 1
                """,
                (source["source_key"], source_revision_id),
            ).fetchone()
            lifecycle = store.connection.execute(
                """
                SELECT source_lifecycle.status
                FROM source_revision_bindings_v2
                LEFT JOIN source_lifecycle
                  ON source_lifecycle.source_id =
                     source_revision_bindings_v2.legacy_source_id
                WHERE source_revision_bindings_v2.source_revision_id = ?
                ORDER BY source_revision_bindings_v2.observed_at DESC
                LIMIT 1
                """,
                (source_revision_id,),
            ).fetchone()
            expected_source_status = (
                lifecycle["status"]
                if lifecycle is not None and lifecycle["status"] is not None
                else "registered"
            )
            fragments = self._source_fragments(
                store,
                source_revision_id=source_revision_id,
                source_ir_compilation_id=source["compilation_id"],
            )
            if not fragments:
                raise RuntimeError("Source Revision has no persisted Source IR fragments")
            compilation_run_id = stable_id(
                "compilationrun",
                store.vault_id,
                source_revision_id,
                source["compilation_id"],
                source_ir_sha256,
                compiler_profile,
                compiler_profile_version,
                prompt_template_id,
                prompt_config_sha256,
                plan_configuration_sha256,
                str(packet_max_fragments),
            )
            fragment_groups: list[list[dict[str, Any]]] = []
            current_group: list[dict[str, Any]] = []
            fragment_budget = (
                MAX_PACKET_PROVIDER_BYTES
                - MAX_COMPILATION_CONTEXT_BYTES
                - 4_096
            )
            for fragment in fragments:
                if len(_canonical_bytes(fragment)) > fragment_budget:
                    raise ValueError(
                        "Source IR fragment exceeds the provider-visible compilation "
                        "packet budget; re-ingest with a smaller fragment policy"
                    )
                candidate = [*current_group, fragment]
                if current_group and (
                    len(candidate) > packet_max_fragments
                    or len(canonical_json(candidate).encode("utf-8")) > fragment_budget
                ):
                    fragment_groups.append(current_group)
                    current_group = [fragment]
                else:
                    current_group = candidate
            if current_group:
                fragment_groups.append(current_group)
            if len(fragment_groups) > MAX_PACKETS_PER_RUN:
                raise ValueError("Source Revision exceeds the compilation packet bound")
            existing = store.connection.execute(
                """
                SELECT * FROM source_compilation_runs_v1
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()
            if existing is not None:
                if existing["grant_id"] != grant_id:
                    raise PermissionError(
                        "source compilation idempotency identity is bound to another grant"
                    )
                return self._run_response(store, existing, idempotent_replay=True)
            created_at = store._next_transaction_time()
            input_audit_head = store.audit_head
            input_legacy_audit_head = store.legacy_audit_head
            while True:
                packet_count = len(fragment_groups)
                packet_values = []
                oversized_group_index: int | None = None
                for packet_ordinal, selected in enumerate(fragment_groups, start=1):
                    packet_id = stable_id(
                        "packet",
                        compilation_run_id,
                        str(packet_ordinal),
                        canonical_json(
                            [fragment["fragment_revision_id"] for fragment in selected]
                        ),
                    )
                    packet = {
                        "schema_version": COMPILATION_PACKET_SCHEMA,
                        "compilation_run_id": compilation_run_id,
                        "packet_id": packet_id,
                        "ordinal": packet_ordinal,
                        "packet_count": packet_count,
                        "source_revision_id": source_revision_id,
                        "source_ir_compilation_id": source["compilation_id"],
                        "input_audit_head": input_audit_head,
                        "compiler_profile": compiler_profile,
                        "compilation_context": self._compilation_context(
                            store,
                            source_revision_id=source_revision_id,
                            source_key=source["source_key"],
                            scope=binding["scope"],
                            max_sensitivity=binding["sensitivity"],
                            fragments=selected,
                        ),
                        "fragments": selected,
                    }
                    _validate_contract("source-compilation-packet.v1.schema.json", packet)
                    payload = _canonical_bytes(packet)
                    if len(payload) > MAX_PACKET_PROVIDER_BYTES:
                        oversized_group_index = packet_ordinal - 1
                        break
                    packet_sha256 = sha256_bytes(payload)
                    packet_values.append((packet, packet_sha256, payload))
                if oversized_group_index is None:
                    break
                oversized = fragment_groups[oversized_group_index]
                if len(oversized) == 1:
                    raise ValueError(
                        "Source IR fragment exceeds the provider-visible compilation "
                        "packet budget; re-ingest with a smaller fragment policy"
                    )
                midpoint = len(oversized) // 2
                fragment_groups[oversized_group_index : oversized_group_index + 1] = [
                    oversized[:midpoint],
                    oversized[midpoint:],
                ]
                if len(fragment_groups) > MAX_PACKETS_PER_RUN:
                    raise ValueError("Source Revision exceeds the compilation packet bound")
            for _packet, packet_sha256, payload in packet_values:
                stored_sha256, _ = _write_object(store.root, payload)
                if stored_sha256 != packet_sha256:
                    raise RuntimeError("source compilation packet digest changed")
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                locked_grant = store._grant(
                    grant_id,
                    operation="begin_compilation",
                    request_bytes=len(request_bytes),
                )
                store._enforce_grant_limits(locked_grant, enforce_object_capacity=False)
                if store.audit_head != input_audit_head:
                    raise RuntimeError("autonomous audit head changed while beginning compilation")
                store.connection.execute(
                    """
                    INSERT INTO source_compilation_runs_v1(
                        compilation_run_id, source_revision_id, source_key,
                        source_ir_compilation_id, grant_id, compiler_profile,
                        compiler_profile_version, host_identity, model_identity,
                        prompt_template_id, prompt_config_sha256,
                        plan_configuration_sha256, request_sha256,
                        input_audit_head, input_legacy_audit_head,
                        packet_max_fragments, packet_count, status, resumable,
                        output_set_sha256, receipt_sha256, failure_stage,
                        failure_sha256, created_at, updated_at, committed_at,
                        completed_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'planned', 1, NULL, NULL, NULL, NULL, ?, ?, NULL, NULL
                    )
                    """,
                    (
                        compilation_run_id,
                        source_revision_id,
                        source["source_key"],
                        source["compilation_id"],
                        grant_id,
                        compiler_profile,
                        compiler_profile_version,
                        host_identity,
                        model_identity,
                        prompt_template_id,
                        prompt_config_sha256,
                        plan_configuration_sha256,
                        request_sha256,
                        input_audit_head,
                        input_legacy_audit_head,
                        packet_max_fragments,
                        packet_count,
                        created_at,
                        created_at,
                    ),
                )
                store.connection.execute(
                    """
                    INSERT INTO source_compilation_run_metadata_v1(
                        compilation_run_id, previous_source_revision_id,
                        source_ir_sha256, expected_source_status,
                        validation_sha256, canonical_commit_sha256,
                        projection_manifest_sha256, token_usage_json,
                        elapsed_ms, retry_count
                    ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, NULL, 0)
                    """,
                    (
                        compilation_run_id,
                        (
                            previous["source_revision_id"]
                            if previous is not None
                            else None
                        ),
                        source_ir_sha256,
                        expected_source_status,
                        canonical_json(
                            {
                                "status": "unreported",
                                "input_tokens": None,
                                "output_tokens": None,
                                "total_tokens": None,
                            }
                        ),
                    ),
                )
                if compiler_profile_version == "2":
                    store.connection.execute(
                        """
                        INSERT INTO semantic_compilation_runs_v2(
                            compilation_run_id, semantic_status,
                            observation_packet_count, observed_packet_count,
                            observation_count, inventory_sha256,
                            publication_plan_sha256, quality_receipt_sha256,
                            source_summary_revision_id, created_at, updated_at
                        ) VALUES (?, 'unknown', ?, 0, 0, NULL, NULL, NULL, NULL, ?, ?)
                        """,
                        (compilation_run_id, packet_count, created_at, created_at),
                    )
                for packet, packet_sha256, payload in packet_values:
                    store.connection.execute(
                        """
                        INSERT OR IGNORE INTO source_compilation_artifacts_v1(
                            artifact_sha256, artifact_role, byte_size,
                            media_type, created_at
                        ) VALUES (?, 'packet', ?, 'application/json', ?)
                        """,
                        (packet_sha256, len(payload), created_at),
                    )
                    selected_fragments = packet["fragments"]
                    store.connection.execute(
                        """
                        INSERT INTO source_compilation_packets_v1(
                            packet_id, compilation_run_id, ordinal,
                            fragment_start_ordinal, fragment_end_ordinal,
                            fragment_count, artifact_sha256, state,
                            plan_sha256, staged_at, validated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL)
                        """,
                        (
                            packet["packet_id"],
                            compilation_run_id,
                            packet["ordinal"],
                            selected_fragments[0]["ordinal"],
                            selected_fragments[-1]["ordinal"],
                            len(selected_fragments),
                            packet_sha256,
                        ),
                    )
                self._record_usage(
                    store,
                    grant_id=grant_id,
                    operation="begin_compilation",
                    request_sha256=request_sha256,
                    recorded_at=created_at,
                    discriminator=compilation_run_id,
                )
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise
            run = self._run(store, compilation_run_id)
            return self._run_response(store, run, idempotent_replay=False)

    @staticmethod
    def _source_fragments(
        store: AutonomousKnowledgeStore,
        *,
        source_revision_id: str,
        source_ir_compilation_id: str,
    ) -> list[dict[str, Any]]:
        rows = store.connection.execute(
            """
            SELECT legacy_fragment_bindings_v2.fragment_id,
                   fragments_v2.fragment_revision_id,
                   fragments_v2.ordinal,
                   fragments_v2.locator,
                   source_fragments.text,
                   source_fragments.text_sha256,
                   fragments_v2.instruction_risk
            FROM fragments_v2
            JOIN compilations_v2 USING(compilation_id)
            JOIN legacy_fragment_bindings_v2 USING(fragment_revision_id)
            JOIN source_fragments USING(fragment_id)
            JOIN source_revision_bindings_v2
              ON source_revision_bindings_v2.legacy_source_id =
                 legacy_fragment_bindings_v2.legacy_source_id
            WHERE fragments_v2.compilation_id = ?
              AND compilations_v2.source_revision_id = ?
              AND source_revision_bindings_v2.source_revision_id = ?
            ORDER BY fragments_v2.ordinal
            """,
            (
                source_ir_compilation_id,
                source_revision_id,
                source_revision_id,
            ),
        ).fetchall()
        fragments: list[dict[str, Any]] = []
        for row in rows:
            keys = [
                item["logical_node_key"]
                for item in store.connection.execute(
                    """
                    SELECT source_ir_nodes_v2.logical_node_key
                    FROM fragment_node_membership_v2
                    JOIN source_ir_nodes_v2 USING(node_id)
                    WHERE fragment_revision_id = ?
                    ORDER BY fragment_node_membership_v2.node_ordinal
                    """,
                    (row["fragment_revision_id"],),
                )
            ]
            fragments.append(
                {
                    "fragment_id": row["fragment_id"],
                    "fragment_revision_id": row["fragment_revision_id"],
                    "ordinal": row["ordinal"],
                    "locator": row["locator"],
                    "text": row["text"],
                    "text_sha256": row["text_sha256"],
                    "logical_node_keys": keys,
                    "instruction_risk": bool(row["instruction_risk"]),
                }
            )
        return fragments

    @staticmethod
    def _compilation_context(
        store: AutonomousKnowledgeStore,
        *,
        source_revision_id: str,
        source_key: str,
        scope: str,
        max_sensitivity: str,
        fragments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        query = compact_text(" ".join(fragment["text"] for fragment in fragments))[
            :5_000
        ]
        recall = store.recall(
            query,
            scope=cast(Any, scope),
            max_sensitivity=cast(Any, max_sensitivity),
            limit=6,
            max_chars=4_000,
            max_tokens=2_500,
            max_sources=8,
            graph_hops=0,
            retrieval_mode="lexical",
            force_canonical_lexical=True,
        )
        relevant = [
            {
                "knowledge_id": item["knowledge_id"],
                "revision_id": item["revision_id"],
                "kind": item["kind"],
                "semantic_key": item.get("semantic_key"),
                "title": item["title"],
                "body_excerpt": item["content"][:800],
                "selection_reason": item["selection_reason"],
            }
            for item in recall["results"]
        ]
        identity_candidates = [
            {
                "knowledge_id": item["knowledge_id"],
                "revision_id": item["revision_id"],
                "kind": item["kind"],
                "semantic_key": item.get("semantic_key"),
                "title": item["title"],
                "aliases": item.get("aliases", [])[:8],
                "match_basis": "bounded_retrieval_candidate",
            }
            for item in recall["results"]
            if item["kind"] in {"concept", "entity"}
        ][:16]
        output_rows = store.connection.execute(
            """
            SELECT source_compilation_outputs_v1.compilation_run_id,
                   source_compilation_outputs_v1.output_kind,
                   source_compilation_outputs_v1.output_id,
                   source_compilation_outputs_v1.object_id,
                   source_compilation_runs_v1.source_revision_id
            FROM source_compilation_outputs_v1
            JOIN source_compilation_runs_v1 USING(compilation_run_id)
            WHERE source_compilation_runs_v1.source_key = ?
            ORDER BY source_compilation_outputs_v1.recorded_at DESC,
                     source_compilation_outputs_v1.output_kind,
                     source_compilation_outputs_v1.output_id
            LIMIT 33
            """,
            (source_key,),
        ).fetchall()
        previous_outputs = [dict(row) for row in output_rows[:32]]
        source_revision_ids = {
            row["source_revision_id"]
            for row in store.connection.execute(
                """
                SELECT source_revision_id FROM source_revisions_v2
                WHERE source_key = ?
                """,
                (source_key,),
            )
        }
        source_revision_ids.add(source_revision_id)
        affected_syntheses: list[dict[str, Any]] = []
        synthesis_rows = store.connection.execute(
            """
            SELECT synthesis_input_sets_v1.source_revision_ids_json,
                   knowledge_revisions_v3.knowledge_id,
                   knowledge_revisions_v3.revision_id,
                   knowledge_revisions_v3.title
            FROM synthesis_input_sets_v1
            JOIN knowledge_revisions_v3
              ON knowledge_revisions_v3.revision_id =
                 synthesis_input_sets_v1.synthesis_revision_id
            JOIN knowledge_objects_v3
              ON knowledge_objects_v3.current_revision_id =
                 knowledge_revisions_v3.revision_id
            WHERE knowledge_revisions_v3.lifecycle = 'active'
            ORDER BY knowledge_revisions_v3.revision_id
            LIMIT 501
            """
        ).fetchall()
        synthesis_scan_truncated = len(synthesis_rows) > 500
        for row in synthesis_rows[:500]:
            inputs = strict_json_loads(row["source_revision_ids_json"])
            if isinstance(inputs, list) and source_revision_ids.intersection(inputs):
                affected_syntheses.append(
                    {
                        "knowledge_id": row["knowledge_id"],
                        "revision_id": row["revision_id"],
                        "title": row["title"],
                    }
                )
                if len(affected_syntheses) == 16:
                    synthesis_scan_truncated = True
                    break
        context = {
            "plan_schema": "deeplaw.source-compilation-plan/v1",
            "relevant_knowledge": relevant,
            "identity_candidates": identity_candidates,
            "previous_outputs": previous_outputs,
            "affected_syntheses": affected_syntheses,
            "truncated": bool(
                recall["rejected"]
                or output_rows[32:]
                or synthesis_scan_truncated
            ),
        }
        lists = (
            "relevant_knowledge",
            "identity_candidates",
            "previous_outputs",
            "affected_syntheses",
        )
        while len(_canonical_bytes(context)) > MAX_COMPILATION_CONTEXT_BYTES:
            selected = next(
                (
                    field
                    for field in lists
                    if context[field]
                ),
                None,
            )
            if selected is None:
                raise RuntimeError("bounded compilation context envelope is oversized")
            context[selected].pop()
            context["truncated"] = True
        return context

    def next_packet(self, compilation_run_id: str) -> dict[str, Any] | None:
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            run = self._run(store, compilation_run_id)
            if run["status"] in {
                "failed",
                "aborted",
                "committed",
                "projection_pending",
                "succeeded",
            }:
                return None
            row = store.connection.execute(
                """
                SELECT artifact_sha256 FROM source_compilation_packets_v1
                WHERE compilation_run_id = ? AND state = 'pending'
                ORDER BY ordinal
                LIMIT 1
                """,
                (compilation_run_id,),
            ).fetchone()
            if row is None:
                return None
            packet = _decoded_artifact(store, row["artifact_sha256"], role="packet")
            _validate_contract("source-compilation-packet.v1.schema.json", packet)
            return packet

    def stage(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        plan: dict[str, Any],
        confirm_no_case_data: bool,
        _allow_run_wide_source_refs: bool = False,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "source compilation requires confirmation that no case data is present"
            )
        if not isinstance(plan, dict):
            raise ValueError("source compilation plan must be an object")
        _validate_contract("source-compilation-plan.v1.schema.json", plan)
        plan_bytes = _canonical_bytes(plan)
        if len(plan_bytes) > MAX_COMPILATION_REQUEST_BYTES:
            raise ValueError("source compilation plan exceeds its request byte limit")
        plan_sha256 = sha256_bytes(plan_bytes)
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            run = self._run(store, compilation_run_id)
            if run["grant_id"] != grant_id:
                raise PermissionError("source compilation run is bound to another grant")
            grant = store._grant(
                grant_id,
                operation="stage_compilation_batch",
                request_bytes=len(plan_bytes),
            )
            store._enforce_grant_limits(grant, enforce_object_capacity=False)
            if run["status"] in {
                "failed",
                "aborted",
                "committed",
                "projection_pending",
                "succeeded",
            }:
                raise RuntimeError("source compilation run no longer accepts staged plans")
            if plan["source_revision_id"] != run["source_revision_id"]:
                raise ValueError("source compilation plan targets another Source Revision")
            if plan["expected_audit_head"] != run["input_audit_head"]:
                raise ValueError("source compilation plan has the wrong audit precondition")
            if store.audit_head != run["input_audit_head"]:
                raise RuntimeError("autonomous audit head changed after compilation began")
            packet_row = store.connection.execute(
                """
                SELECT * FROM source_compilation_packets_v1
                WHERE compilation_run_id = ? AND packet_id = ?
                """,
                (compilation_run_id, plan["packet_id"]),
            ).fetchone()
            if packet_row is None:
                raise KeyError("source compilation packet is unavailable")
            replacing_plan = False
            if packet_row["plan_sha256"] is not None:
                if packet_row["plan_sha256"] != plan_sha256:
                    if run["status"] not in {"staging", "validating"}:
                        raise RuntimeError(
                            "validated source compilation packet cannot be replaced"
                        )
                    replacing_plan = True
                else:
                    batch = store.connection.execute(
                        """
                        SELECT batch_sha256 FROM source_compilation_batches_v1
                        WHERE packet_id = ?
                        """,
                        (plan["packet_id"],),
                    ).fetchone()
                    if batch is None:
                        raise RuntimeError("staged source compilation batch is missing")
                    response = _decoded_artifact(
                        store,
                        batch["batch_sha256"],
                        role="batch",
                    )
                    response["idempotent_replay"] = True
                    return response
            packet = _decoded_artifact(store, packet_row["artifact_sha256"], role="packet")
            run_fragments: dict[str, dict[str, Any]] | None = None
            if _allow_run_wide_source_refs:
                if not (
                    run["compiler_profile_version"] == "2"
                    or run["compiler_profile"] == "synthesis-refresh-agent"
                ):
                    raise PermissionError(
                        "run-wide evidence binding requires compiler profile v2"
                    )
                run_fragments = {}
                rows = store.connection.execute(
                    """
                    SELECT artifact_sha256 FROM source_compilation_packets_v1
                    WHERE compilation_run_id = ? ORDER BY ordinal
                    """,
                    (compilation_run_id,),
                ).fetchall()
                for item in rows:
                    run_packet = _decoded_artifact(
                        store, item["artifact_sha256"], role="packet"
                    )
                    run_fragments.update(
                        {
                            fragment["fragment_id"]: {
                                **fragment,
                                "source_revision_id": run_packet[
                                    "source_revision_id"
                                ],
                            }
                            for fragment in run_packet["fragments"]
                        }
                    )
                run_fragments.update(
                    self._admitted_synthesis_input_fragments(
                        store,
                        plan=plan,
                        scope=grant["allowed_scope"],
                        max_sensitivity=grant["max_sensitivity"],
                    )
                )
            relation_evidence_fragments = (
                self._existing_relation_endpoint_fragments(
                    store,
                    plan=plan,
                    scope=grant["allowed_scope"],
                    max_sensitivity=grant["max_sensitivity"],
                )
                if _allow_run_wide_source_refs
                else None
            )
            self._validate_plan_against_packet(
                plan=plan,
                packet=packet,
                allowed_source_fragments=run_fragments,
                allowed_relation_fragments=relation_evidence_fragments,
            )
            total_actions = sum(
                len(plan[field])
                for field in ("object_actions", "relation_actions", "identity_actions")
            )
            if total_actions > MAX_ACTIONS_PER_PACKET:
                raise ValueError("source compilation packet action bound exceeded")
            existing_actions = (
                store.connection.execute(
                    """
                    SELECT COUNT(*) FROM source_compilation_staged_objects_v1
                    WHERE compilation_run_id = ? AND packet_id != ?
                    """,
                    (compilation_run_id, plan["packet_id"]),
                ).fetchone()[0]
                + store.connection.execute(
                    """
                    SELECT COUNT(*) FROM source_compilation_staged_relations_v1
                    WHERE compilation_run_id = ? AND packet_id != ?
                    """,
                    (compilation_run_id, plan["packet_id"]),
                ).fetchone()[0]
            )
            if existing_actions + total_actions > MAX_ACTIONS_PER_RUN:
                raise ValueError("source compilation run action bound exceeded")
            recorded_at = store._next_transaction_time()
            batch_id = stable_id("batch", compilation_run_id, plan["packet_id"], plan_sha256)
            batch_value = {
                "schema_version": COMPILATION_BATCH_SCHEMA,
                "batch_id": batch_id,
                "compilation_run_id": compilation_run_id,
                "packet_id": plan["packet_id"],
                "plan_sha256": plan_sha256,
                "object_count": len(plan["object_actions"]),
                "relation_count": len(plan["relation_actions"]),
                "identity_count": len(plan["identity_actions"]),
                "coverage_ratio": plan["coverage"]["ratio"],
                "recorded_at": recorded_at,
            }
            _validate_contract("source-compilation-batch.v1.schema.json", batch_value)
            request_sha256 = sha256_bytes(
                _canonical_bytes(
                    {
                        "operation": "stage_compilation_batch",
                        "compilation_run_id": compilation_run_id,
                        "plan_sha256": plan_sha256,
                    }
                )
            )
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                locked = self._run(store, compilation_run_id)
                if (
                    locked["status"]
                    not in {"planned", "staging", "validating"}
                    or store.audit_head != locked["input_audit_head"]
                ):
                    raise RuntimeError("source compilation staging precondition changed")
                if replacing_plan:
                    locked_packet = store.connection.execute(
                        """
                        SELECT state, plan_sha256
                        FROM source_compilation_packets_v1
                        WHERE compilation_run_id = ? AND packet_id = ?
                        """,
                        (compilation_run_id, plan["packet_id"]),
                    ).fetchone()
                    if (
                        locked_packet is None
                        or locked_packet["state"] != "staged"
                        or locked_packet["plan_sha256"] != packet_row["plan_sha256"]
                    ):
                        raise RuntimeError(
                            "source compilation packet replacement precondition changed"
                        )
                    store.connection.execute(
                        """
                        DELETE FROM source_compilation_staged_objects_v1
                        WHERE compilation_run_id = ? AND packet_id = ?
                        """,
                        (compilation_run_id, plan["packet_id"]),
                    )
                    store.connection.execute(
                        """
                        DELETE FROM source_compilation_staged_relations_v1
                        WHERE compilation_run_id = ? AND packet_id = ?
                        """,
                        (compilation_run_id, plan["packet_id"]),
                    )
                    store.connection.execute(
                        """
                        DELETE FROM source_compilation_identity_candidates_v1
                        WHERE compilation_run_id = ? AND packet_id = ?
                        """,
                        (compilation_run_id, plan["packet_id"]),
                    )
                    store.connection.execute(
                        """
                        DELETE FROM source_compilation_batches_v1
                        WHERE compilation_run_id = ? AND packet_id = ?
                        """,
                        (compilation_run_id, plan["packet_id"]),
                    )
                plan_artifact_sha256, _ = _artifact(
                    store, value=plan, role="plan", created_at=recorded_at
                )
                if plan_artifact_sha256 != plan_sha256:
                    raise RuntimeError("source compilation plan digest changed")
                batch_sha256, _ = _artifact(
                    store, value=batch_value, role="batch", created_at=recorded_at
                )
                store.connection.execute(
                    """
                    INSERT INTO source_compilation_batches_v1(
                        batch_id, compilation_run_id, packet_id, plan_sha256,
                        batch_sha256, object_count, relation_count, identity_count,
                        unresolved_identity_count, contradiction_count,
                        skipped_fragment_count, warning_count,
                        coverage_ratio, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        compilation_run_id,
                        plan["packet_id"],
                        plan_sha256,
                        batch_sha256,
                        len(plan["object_actions"]),
                        len(plan["relation_actions"]),
                        len(plan["identity_actions"]),
                        len(plan["unresolved_identities"]),
                        len(plan["contradictions"]),
                        len(plan["skipped_fragments"]),
                        len(plan["warnings"]),
                        plan["coverage"]["ratio"],
                        recorded_at,
                    ),
                )
                base = (packet_row["ordinal"] - 1) * MAX_ACTIONS_PER_PACKET
                for index, action in enumerate(plan["object_actions"], start=1):
                    store.connection.execute(
                        """
                        INSERT INTO source_compilation_staged_objects_v1(
                            compilation_run_id, action_ordinal, packet_id,
                            requested_action, resolved_action, kind, semantic_key,
                            requested_knowledge_id, expected_revision_id,
                            resolved_knowledge_id, resolved_parent_revision_id,
                            prepared_revision_id, prepared_markdown_sha256,
                            prepared_json, action_json, validation_state,
                            validation_error_sha256
                        ) VALUES (
                            ?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, NULL, NULL,
                            NULL, NULL, ?, 'staged', NULL
                        )
                        """,
                        (
                            compilation_run_id,
                            base + index,
                            plan["packet_id"],
                            action["action"],
                            action["kind"],
                            action["semantic_key"],
                            action["knowledge_id"],
                            action["expected_revision_id"],
                            canonical_json(action),
                        ),
                    )
                for index, action in enumerate(plan["relation_actions"], start=1):
                    store.connection.execute(
                        """
                        INSERT INTO source_compilation_staged_relations_v1(
                            compilation_run_id, action_ordinal, packet_id,
                            predicate, expected_relation_revision_id,
                            resolved_relation_key, resolved_parent_revision_id,
                            prepared_relation_revision_id, prepared_json,
                            action_json, validation_state, validation_error_sha256
                        ) VALUES (
                            ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?,
                            'staged', NULL
                        )
                        """,
                        (
                            compilation_run_id,
                            base + index,
                            plan["packet_id"],
                            action["predicate"],
                            action["expected_relation_revision_id"],
                            canonical_json(action),
                        ),
                    )
                for index, action in enumerate(plan["identity_actions"], start=1):
                    store.connection.execute(
                        """
                        INSERT INTO source_compilation_identity_candidates_v1(
                            compilation_run_id, packet_id, candidate_ordinal,
                            candidate_json, status
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            compilation_run_id,
                            plan["packet_id"],
                            index,
                            canonical_json(action),
                            (
                                "ambiguous"
                                if action["action"] in {"ambiguous", "possible_duplicate"}
                                else "proposed"
                            ),
                        ),
                    )
                store.connection.execute(
                    """
                    UPDATE source_compilation_packets_v1
                    SET state = 'staged', plan_sha256 = ?, staged_at = ?,
                        validated_at = NULL
                    WHERE packet_id = ? AND state IN ('pending', 'staged')
                    """,
                    (plan_sha256, recorded_at, plan["packet_id"]),
                )
                pending = store.connection.execute(
                    """
                    SELECT COUNT(*) FROM source_compilation_packets_v1
                    WHERE compilation_run_id = ? AND state = 'pending'
                    """,
                    (compilation_run_id,),
                ).fetchone()[0]
                store.connection.execute(
                    """
                    UPDATE source_compilation_runs_v1
                    SET status = ?, updated_at = ?
                    WHERE compilation_run_id = ?
                    """,
                    (
                        "validating" if pending == 0 else "staging",
                        recorded_at,
                        compilation_run_id,
                    ),
                )
                self._record_usage(
                    store,
                    grant_id=grant_id,
                    operation="stage_compilation_batch",
                    request_sha256=request_sha256,
                    recorded_at=recorded_at,
                    discriminator=plan["packet_id"],
                )
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise
            batch_value["idempotent_replay"] = False
            return batch_value

    @staticmethod
    def _validate_plan_against_packet(
        *,
        plan: dict[str, Any],
        packet: dict[str, Any],
        allowed_source_fragments: dict[str, dict[str, Any]] | None = None,
        allowed_relation_fragments: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        if (
            plan["packet_id"] != packet["packet_id"]
            or plan["source_revision_id"] != packet["source_revision_id"]
            or plan["expected_audit_head"] != packet["input_audit_head"]
        ):
            raise ValueError("source compilation plan does not match its packet")
        fragments = {
            item["fragment_id"]: {
                **item,
                "source_revision_id": packet["source_revision_id"],
            }
            for item in packet["fragments"]
        }
        evidence_fragments = allowed_source_fragments or fragments
        relation_fragments = dict(evidence_fragments)
        relation_fragments.update(allowed_relation_fragments or {})
        coverage = plan["coverage"]
        covered = coverage["covered_fragment_ids"]
        omitted = coverage["omitted_fragment_ids"]
        if (
            coverage["packet_fragment_count"] != len(fragments)
            or set(covered) & set(omitted)
            or set(covered) | set(omitted) != set(fragments)
            or not math.isclose(
                coverage["ratio"],
                len(covered) / len(fragments),
                abs_tol=1e-9,
            )
            or (
                coverage["completeness"] == "complete"
                and (omitted or len(covered) != len(fragments))
            )
            or (coverage["completeness"] == "empty" and (covered or coverage["ratio"] != 0.0))
        ):
            raise ValueError("source compilation coverage does not match the packet")
        skipped = {item["fragment_id"] for item in plan["skipped_fragments"]}
        if skipped != set(omitted):
            raise ValueError("source compilation skipped-fragment inventory is inconsistent")
        packet_bound_groups = [
            action["source_refs"] for action in plan["object_actions"]
        ]
        packet_bound_groups.extend(
            action["evidence_refs"] for action in plan["identity_actions"]
        )
        for references in packet_bound_groups:
            for reference in references:
                fragment = evidence_fragments.get(reference["fragment_id"])
                if (
                    fragment is None
                    or reference["source_revision_id"]
                    != fragment["source_revision_id"]
                    or reference["locator"] != fragment["locator"]
                    or reference["quote_sha256"] != fragment["text_sha256"]
                ):
                    raise ValueError("source compilation action cites evidence outside its packet")
        for action in plan["relation_actions"]:
            for reference in action["evidence_refs"]:
                fragment = relation_fragments.get(reference["fragment_id"])
                if (
                    fragment is None
                    or reference["source_revision_id"]
                    != fragment["source_revision_id"]
                    or reference["locator"] != fragment["locator"]
                    or reference["quote_sha256"] != fragment["text_sha256"]
                ):
                    raise ValueError(
                        "compiled relation cites evidence outside its run or current endpoints"
                    )

    @staticmethod
    def _admitted_synthesis_input_fragments(
        store: AutonomousKnowledgeStore,
        *,
        plan: dict[str, Any],
        scope: str,
        max_sensitivity: str,
    ) -> dict[str, dict[str, Any]]:
        """Admit exact profile-v2 Synthesis evidence named in its input set."""

        allowed: dict[str, dict[str, Any]] = {}
        for action in plan["object_actions"]:
            if action["kind"] != "synthesis":
                continue
            inputs = action.get("synthesis_inputs")
            source_revision_ids = (
                set(inputs.get("source_revision_ids", []))
                if isinstance(inputs, dict)
                else set()
            )
            for reference in action["source_refs"]:
                source_revision_id = reference["source_revision_id"]
                if source_revision_id == plan["source_revision_id"]:
                    continue
                if source_revision_id not in source_revision_ids:
                    raise ValueError(
                        "cross-source Synthesis evidence is absent from its input set"
                    )
                binding = store._source_reference_binding(reference)
                if (
                    binding is None
                    or binding["active"] is not True
                    or binding["scope"] != scope
                    or SENSITIVITY_ORDER.index(binding["sensitivity"])
                    > SENSITIVITY_ORDER.index(max_sensitivity)
                ):
                    raise PermissionError(
                        "cross-source Synthesis evidence is not admitted"
                    )
                allowed[reference["fragment_id"]] = {
                    "source_revision_id": source_revision_id,
                    "fragment_id": reference["fragment_id"],
                    "locator": reference["locator"],
                    "text_sha256": reference["quote_sha256"],
                }
        return allowed

    @staticmethod
    def _existing_relation_endpoint_fragments(
        store: AutonomousKnowledgeStore,
        *,
        plan: dict[str, Any],
        scope: str,
        max_sensitivity: str,
    ) -> dict[str, dict[str, Any]]:
        """Admit exact evidence already bound to a current relation endpoint.

        Semantic compilation may connect a newly staged object to an existing
        object. In that case a relation can bind both sides' immutable evidence,
        but it cannot introduce an unrelated Source Revision. Identity actions remain
        bound to the current Compilation Run; profile-v2 Synthesis objects use their
        separately validated input set for cross-source evidence.
        """

        allowed: dict[str, dict[str, Any]] = {}
        endpoints = [
            action[field]
            for action in plan["relation_actions"]
            for field in ("subject", "object")
        ]
        for endpoint in endpoints:
            knowledge_id = endpoint["knowledge_id"]
            semantic_key = endpoint["semantic_key"]
            kind = endpoint["kind"]
            if knowledge_id is not None:
                rows = store.connection.execute(
                    """
                    SELECT knowledge_objects_v3.workspace_path AS current_workspace_path,
                           knowledge_revisions_v3.*
                    FROM knowledge_objects_v3
                    JOIN knowledge_revisions_v3
                      ON knowledge_revisions_v3.revision_id =
                         knowledge_objects_v3.current_revision_id
                    WHERE knowledge_objects_v3.knowledge_id = ?
                      AND knowledge_revisions_v3.scope = ?
                      AND knowledge_revisions_v3.lifecycle = 'active'
                    """,
                    (knowledge_id, scope),
                ).fetchall()
            elif semantic_key is not None and kind is not None:
                rows = store.connection.execute(
                    """
                    SELECT knowledge_objects_v3.workspace_path AS current_workspace_path,
                           knowledge_revisions_v3.*
                    FROM knowledge_objects_v3
                    JOIN knowledge_revisions_v3
                      ON knowledge_revisions_v3.revision_id =
                         knowledge_objects_v3.current_revision_id
                    WHERE knowledge_objects_v3.semantic_key = ?
                      AND knowledge_objects_v3.kind = ?
                      AND knowledge_revisions_v3.scope = ?
                      AND knowledge_revisions_v3.lifecycle = 'active'
                    ORDER BY knowledge_objects_v3.knowledge_id
                    LIMIT 2
                    """,
                    (semantic_key, kind, scope),
                ).fetchall()
            else:
                continue
            if len(rows) > 1:
                raise RuntimeError("compiled relation endpoint identity is ambiguous")
            if not rows:
                continue
            revision = store._revision_row(rows[0], include_body=False)
            if (
                SENSITIVITY_ORDER.index(revision["sensitivity"])
                > SENSITIVITY_ORDER.index(max_sensitivity)
                or not store.revision_provenance_admitted(revision)
            ):
                raise PermissionError(
                    "compiled relation endpoint provenance is not admitted"
                )
            for reference in revision["source_refs"]:
                binding = store._source_reference_binding(reference)
                if (
                    binding is None
                    or binding["active"] is not True
                    or binding["scope"] != scope
                    or SENSITIVITY_ORDER.index(binding["sensitivity"])
                    > SENSITIVITY_ORDER.index(max_sensitivity)
                ):
                    raise PermissionError(
                        "compiled relation endpoint evidence is not admitted"
                    )
                allowed[reference["fragment_id"]] = {
                    "source_revision_id": reference["source_revision_id"],
                    "fragment_id": reference["fragment_id"],
                    "locator": reference["locator"],
                    "text_sha256": reference["quote_sha256"],
                }
        return allowed

    def validate(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "source compilation requires confirmation that no case data is present"
            )
        request = {
            "operation": "validate_compilation",
            "compilation_run_id": compilation_run_id,
        }
        request_bytes = _canonical_bytes(request)
        request_sha256 = sha256_bytes(request_bytes)
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            run = self._run(store, compilation_run_id)
            if run["grant_id"] != grant_id:
                raise PermissionError("source compilation run is bound to another grant")
            grant = store._grant(
                grant_id,
                operation="validate_compilation",
                request_bytes=len(request_bytes),
            )
            store._enforce_grant_limits(grant, enforce_object_capacity=False)
            if run["status"] == "ready_to_commit":
                counts = self._validated_counts(store, compilation_run_id)
                response = {
                    "schema_version": "deeplaw.source-compilation-validation/v1",
                    "compilation_run_id": compilation_run_id,
                    "valid": True,
                    **counts,
                    "identity_candidate_count": store.connection.execute(
                        """
                        SELECT COUNT(*) FROM source_compilation_identity_candidates_v1
                        WHERE compilation_run_id = ?
                        """,
                        (compilation_run_id,),
                    ).fetchone()[0],
                    "plan_inventory_sha256": self._plan_inventory_sha256(
                        store,
                        compilation_run_id,
                    ),
                    "validation_sha256": self._latest_validation_sha256(store, compilation_run_id),
                    "idempotent_replay": True,
                }
                _validate_contract(
                    "source-compilation-validation.v1.schema.json",
                    response,
                )
                return response
            if run["status"] not in {"validating", "staging"}:
                raise RuntimeError(
                    "source compilation run cannot be validated in its current state"
                )
            pending = store.connection.execute(
                """
                SELECT COUNT(*) FROM source_compilation_packets_v1
                WHERE compilation_run_id = ? AND state = 'pending'
                """,
                (compilation_run_id,),
            ).fetchone()[0]
            if pending:
                raise RuntimeError("source compilation has unstaged packets")
            if store.audit_head != run["input_audit_head"]:
                raise RuntimeError("autonomous audit head changed after compilation began")
            plan_inventory_sha256 = self._plan_inventory_sha256(
                store,
                compilation_run_id,
            )
            object_rows = store.connection.execute(
                """
                SELECT * FROM source_compilation_staged_objects_v1
                WHERE compilation_run_id = ?
                ORDER BY action_ordinal
                """,
                (compilation_run_id,),
            ).fetchall()
            relation_rows = store.connection.execute(
                """
                SELECT * FROM source_compilation_staged_relations_v1
                WHERE compilation_run_id = ?
                ORDER BY action_ordinal
                """,
                (compilation_run_id,),
            ).fetchall()
            if len(object_rows) + len(relation_rows) > MAX_ACTIONS_PER_RUN:
                raise RuntimeError("source compilation staged action inventory exceeds its bound")
            if not object_rows and not relation_rows:
                raise ValueError(
                    "source compilation cannot succeed without a semantic output action"
                )
            covered_batches = store.connection.execute(
                """
                SELECT COUNT(*) AS batch_count,
                       SUM(CASE WHEN coverage_ratio > 0.0 THEN 1 ELSE 0 END)
                           AS covered_batch_count
                FROM source_compilation_batches_v1
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()
            if (
                covered_batches is None
                or covered_batches["batch_count"] != run["packet_count"]
                or not covered_batches["covered_batch_count"]
            ):
                raise ValueError(
                    "source compilation cannot succeed with entirely empty coverage"
                )
            prepared_objects: list[tuple[sqlite3.Row, dict[str, Any]]] = []
            identity_map: dict[tuple[str, str], str] = {}
            seen_identity: set[tuple[str, str]] = set()
            seen_digest: set[str] = set()
            recorded_at = store._next_transaction_time(strictly_after_event=True)
            for row in object_rows:
                action = strict_json_loads(row["action_json"])
                if not isinstance(action, dict):
                    raise RuntimeError("staged Knowledge action is invalid")
                identity = (action["kind"], action["semantic_key"])
                if identity in seen_identity:
                    raise ValueError("source compilation contains duplicate semantic actions")
                seen_identity.add(identity)
                prepared = self._prepare_object(
                    store,
                    run=run,
                    grant=grant,
                    row=row,
                    action=action,
                    recorded_at=recorded_at,
                )
                recorded_at = _timestamp_after(
                    prepared.get("recorded_at", recorded_at), recorded_at
                )
                if prepared["resolved_action"] not in {"retain", "propose"}:
                    semantic_digest = prepared["semantic_digest"]
                    if semantic_digest in seen_digest:
                        raise ValueError(
                            "source compilation contains exact duplicate Knowledge content"
                        )
                    duplicate = store.connection.execute(
                        """
                        SELECT knowledge_objects_v3.knowledge_id
                        FROM knowledge_objects_v3
                        JOIN knowledge_revisions_v3
                          ON knowledge_revisions_v3.revision_id =
                             knowledge_objects_v3.current_revision_id
                        WHERE knowledge_revisions_v3.lifecycle = 'active'
                          AND knowledge_revisions_v3.kind = ?
                          AND knowledge_revisions_v3.scope = ?
                          AND knowledge_revisions_v3.sensitivity = ?
                          AND knowledge_revisions_v3.semantic_digest = ?
                          AND knowledge_objects_v3.knowledge_id <> ?
                        LIMIT 1
                        """,
                        (
                            prepared["kind"],
                            prepared["scope"],
                            prepared["sensitivity"],
                            semantic_digest,
                            prepared["knowledge_id"],
                        ),
                    ).fetchone()
                    if duplicate is not None:
                        raise ValueError("compiled Knowledge duplicates another active identity")
                    seen_digest.add(semantic_digest)
                identity_map[identity] = prepared["knowledge_id"]
                prepared_objects.append((row, prepared))
            prepared_relations: list[tuple[sqlite3.Row, dict[str, Any]]] = []
            seen_relations: set[str] = set()
            for row in relation_rows:
                action = strict_json_loads(row["action_json"])
                if not isinstance(action, dict):
                    raise RuntimeError("staged relation action is invalid")
                prepared = self._prepare_relation(
                    store,
                    run=run,
                    grant=grant,
                    row=row,
                    action=action,
                    identity_map=identity_map,
                    recorded_at=recorded_at,
                )
                recorded_at = _timestamp_after(
                    prepared.get("recorded_at", recorded_at), recorded_at
                )
                if prepared["relation_key"] in seen_relations:
                    raise ValueError("source compilation contains duplicate relation actions")
                seen_relations.add(prepared["relation_key"])
                prepared_relations.append((row, prepared))
            validation_value = {
                "schema_version": "deeplaw.source-compilation-validation/v1",
                "compilation_run_id": compilation_run_id,
                "source_revision_id": run["source_revision_id"],
                "input_audit_head": run["input_audit_head"],
                "object_count": len(prepared_objects),
                "relation_count": len(prepared_relations),
                "identity_candidate_count": store.connection.execute(
                    """
                    SELECT COUNT(*) FROM source_compilation_identity_candidates_v1
                    WHERE compilation_run_id = ?
                    """,
                    (compilation_run_id,),
                ).fetchone()[0],
                "plan_inventory_sha256": plan_inventory_sha256,
                "prepared_revision_ids": sorted(
                    prepared["revision_id"]
                    for _, prepared in prepared_objects
                    if prepared.get("revision_id") is not None
                ),
                "prepared_relation_revision_ids": sorted(
                    prepared["relation_revision_id"]
                    for _, prepared in prepared_relations
                    if prepared.get("relation_revision_id") is not None
                ),
                "validated_at": recorded_at,
            }
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                locked = self._run(store, compilation_run_id)
                if (
                    locked["status"] not in {"validating", "staging"}
                    or store.audit_head != locked["input_audit_head"]
                    or self._plan_inventory_sha256(store, compilation_run_id)
                    != plan_inventory_sha256
                ):
                    raise RuntimeError("source compilation validation precondition changed")
                for row, prepared in prepared_objects:
                    markdown_sha256 = prepared.get("markdown_sha256")
                    store.connection.execute(
                        """
                        UPDATE source_compilation_staged_objects_v1
                        SET resolved_action = ?, resolved_knowledge_id = ?,
                            resolved_parent_revision_id = ?,
                            prepared_revision_id = ?,
                            prepared_markdown_sha256 = ?, prepared_json = ?,
                            validation_state = 'valid',
                            validation_error_sha256 = NULL
                        WHERE compilation_run_id = ? AND action_ordinal = ?
                        """,
                        (
                            prepared["resolved_action"],
                            prepared["knowledge_id"],
                            prepared.get("parent_revision_id"),
                            prepared.get("revision_id"),
                            markdown_sha256,
                            canonical_json(prepared),
                            compilation_run_id,
                            row["action_ordinal"],
                        ),
                    )
                for row, prepared in prepared_relations:
                    store.connection.execute(
                        """
                        UPDATE source_compilation_staged_relations_v1
                        SET resolved_relation_key = ?,
                            resolved_parent_revision_id = ?,
                            prepared_relation_revision_id = ?,
                            prepared_json = ?, validation_state = 'valid',
                            validation_error_sha256 = NULL
                        WHERE compilation_run_id = ? AND action_ordinal = ?
                        """,
                        (
                            prepared["relation_key"],
                            prepared.get("parent_revision_id"),
                            prepared.get("relation_revision_id"),
                            canonical_json(prepared),
                            compilation_run_id,
                            row["action_ordinal"],
                        ),
                    )
                validation_sha256, _ = _artifact(
                    store,
                    value=validation_value,
                    role="validation",
                    created_at=recorded_at,
                )
                store.connection.execute(
                    """
                    UPDATE source_compilation_run_metadata_v1
                    SET validation_sha256 = ?
                    WHERE compilation_run_id = ?
                    """,
                    (validation_sha256, compilation_run_id),
                )
                store.connection.execute(
                    """
                    UPDATE source_compilation_packets_v1
                    SET state = 'validated', validated_at = ?
                    WHERE compilation_run_id = ? AND state = 'staged'
                    """,
                    (recorded_at, compilation_run_id),
                )
                store.connection.execute(
                    """
                    UPDATE source_compilation_runs_v1
                    SET status = 'ready_to_commit', updated_at = ?,
                        failure_stage = NULL, failure_sha256 = NULL
                    WHERE compilation_run_id = ?
                    """,
                    (recorded_at, compilation_run_id),
                )
                self._record_usage(
                    store,
                    grant_id=grant_id,
                    operation="validate_compilation",
                    request_sha256=request_sha256,
                    recorded_at=recorded_at,
                    discriminator=compilation_run_id,
                )
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise
            response = {
                "schema_version": "deeplaw.source-compilation-validation/v1",
                "compilation_run_id": compilation_run_id,
                "valid": True,
                "staged_object_count": len(prepared_objects),
                "staged_relation_count": len(prepared_relations),
                "identity_candidate_count": validation_value["identity_candidate_count"],
                "plan_inventory_sha256": plan_inventory_sha256,
                "validation_sha256": validation_sha256,
                "idempotent_replay": False,
            }
            _validate_contract(
                "source-compilation-validation.v1.schema.json",
                response,
            )
            return response

    @staticmethod
    def _plan_inventory_sha256(
        store: AutonomousKnowledgeStore,
        compilation_run_id: str,
    ) -> str:
        inventory = [
            {
                "packet_id": row["packet_id"],
                "ordinal": row["ordinal"],
                "plan_sha256": row["plan_sha256"],
            }
            for row in store.connection.execute(
                """
                SELECT packet_id, ordinal, plan_sha256
                FROM source_compilation_packets_v1
                WHERE compilation_run_id = ?
                ORDER BY ordinal, packet_id
                """,
                (compilation_run_id,),
            )
        ]
        if not inventory or any(item["plan_sha256"] is None for item in inventory):
            raise RuntimeError("source compilation plan inventory is incomplete")
        return sha256_bytes(canonical_json(inventory).encode("utf-8"))

    @staticmethod
    def _validated_counts(
        store: AutonomousKnowledgeStore,
        compilation_run_id: str,
    ) -> dict[str, int]:
        return {
            "staged_object_count": store.connection.execute(
                """
                SELECT COUNT(*) FROM source_compilation_staged_objects_v1
                WHERE compilation_run_id = ? AND validation_state = 'valid'
                """,
                (compilation_run_id,),
            ).fetchone()[0],
            "staged_relation_count": store.connection.execute(
                """
                SELECT COUNT(*) FROM source_compilation_staged_relations_v1
                WHERE compilation_run_id = ? AND validation_state = 'valid'
                """,
                (compilation_run_id,),
            ).fetchone()[0],
        }

    @staticmethod
    def _latest_validation_sha256(
        store: AutonomousKnowledgeStore,
        compilation_run_id: str,
    ) -> str | None:
        candidates = store.connection.execute(
            """
            SELECT artifact_sha256 FROM source_compilation_artifacts_v1
            WHERE artifact_role = 'validation'
            ORDER BY created_at DESC, artifact_sha256 DESC
            """
        ).fetchall()
        for candidate in candidates:
            value = _decoded_artifact(store, candidate["artifact_sha256"], role="validation")
            if value.get("compilation_run_id") == compilation_run_id:
                return cast(str, candidate["artifact_sha256"])
        return None

    @staticmethod
    def _resolve_current_identity(
        store: AutonomousKnowledgeStore,
        *,
        compilation_run_id: str,
        packet_id: str,
        kind: str,
        semantic_key: str,
        scope: str,
        requested_knowledge_id: str | None,
        identity_terms: list[str],
    ) -> sqlite3.Row | None:
        def alias_candidates() -> list[sqlite3.Row]:
            normalized_terms = sorted(
                {
                    normalized
                    for term in identity_terms
                    if (normalized := normalize_identity_text(term))
                }
            )
            if not normalized_terms or kind not in {"concept", "entity"}:
                return []
            placeholders = ",".join("?" for _ in normalized_terms)
            return store.connection.execute(
                f"""
                SELECT DISTINCT knowledge_objects_v3.knowledge_id,
                                knowledge_objects_v3.kind,
                                knowledge_objects_v3.semantic_key,
                                knowledge_objects_v3.current_revision_id,
                                knowledge_objects_v3.workspace_path,
                                knowledge_revisions_v3.scope,
                                knowledge_revisions_v3.sensitivity,
                                knowledge_revisions_v3.lifecycle,
                                knowledge_revisions_v3.recorded_at
                FROM knowledge_aliases_v4
                JOIN knowledge_objects_v3 USING(knowledge_id)
                JOIN knowledge_revisions_v3
                  ON knowledge_revisions_v3.revision_id =
                     knowledge_objects_v3.current_revision_id
                WHERE knowledge_aliases_v4.alias_key IN ({placeholders})
                  AND knowledge_aliases_v4.kind = ?
                  AND knowledge_aliases_v4.scope = ?
                  AND knowledge_aliases_v4.retired_at IS NULL
                  AND knowledge_aliases_v4.revision_id =
                      knowledge_revisions_v3.revision_id
                  AND knowledge_revisions_v3.lifecycle = 'active'
                ORDER BY knowledge_objects_v3.knowledge_id
                """,
                (*normalized_terms, kind, scope),
            ).fetchall()

        def explicit_ambiguity(candidate_ids: set[str], *, subject_id: str | None) -> bool:
            rows = store.connection.execute(
                """
                SELECT candidate_json
                FROM source_compilation_identity_candidates_v1
                WHERE compilation_run_id = ? AND packet_id = ?
                  AND status = 'ambiguous'
                ORDER BY candidate_ordinal
                """,
                (compilation_run_id, packet_id),
            ).fetchall()
            for candidate_row in rows:
                candidate = strict_json_loads(candidate_row["candidate_json"])
                if not isinstance(candidate, dict):
                    continue
                subject = candidate.get("subject")
                objects = candidate.get("objects")
                if not isinstance(subject, dict) or not isinstance(objects, list):
                    continue
                subject_matches = (
                    subject_id is not None
                    and subject.get("knowledge_id") == subject_id
                ) or (
                    subject.get("semantic_key") == semantic_key
                    and subject.get("kind") == kind
                )
                object_ids = {
                    endpoint.get("knowledge_id")
                    for endpoint in objects
                    if isinstance(endpoint, dict)
                    and isinstance(endpoint.get("knowledge_id"), str)
                }
                if subject_matches and candidate_ids <= object_ids:
                    return True
            return False

        aliases = alias_candidates()
        if requested_knowledge_id is not None:
            row = store.connection.execute(
                """
                SELECT knowledge_objects_v3.knowledge_id,
                       knowledge_objects_v3.kind,
                       knowledge_objects_v3.semantic_key,
                       knowledge_objects_v3.current_revision_id,
                       knowledge_objects_v3.workspace_path,
                       knowledge_revisions_v3.scope,
                       knowledge_revisions_v3.sensitivity,
                       knowledge_revisions_v3.lifecycle,
                       knowledge_revisions_v3.recorded_at
                FROM knowledge_objects_v3
                LEFT JOIN knowledge_revisions_v3
                  ON knowledge_revisions_v3.revision_id =
                     knowledge_objects_v3.current_revision_id
                WHERE knowledge_objects_v3.knowledge_id = ?
                """,
                (requested_knowledge_id,),
            ).fetchone()
            if row is None:
                return None
            if row["kind"] != kind or row["scope"] != scope or row["semantic_key"] != semantic_key:
                raise ValueError(
                    "requested Knowledge identity conflicts with its semantic identity"
                )
            foreign_alias_ids = {
                candidate["knowledge_id"]
                for candidate in aliases
                if candidate["knowledge_id"] != requested_knowledge_id
            }
            if foreign_alias_ids and not explicit_ambiguity(
                foreign_alias_ids,
                subject_id=requested_knowledge_id,
            ):
                raise RuntimeError(
                    "compiled identity aliases are ambiguous; record an explicit "
                    "ambiguous or possible_duplicate identity candidate"
                )
            return row
        rows = store.connection.execute(
            """
            SELECT knowledge_objects_v3.knowledge_id,
                   knowledge_objects_v3.kind,
                   knowledge_objects_v3.semantic_key,
                   knowledge_objects_v3.current_revision_id,
                   knowledge_objects_v3.workspace_path,
                   knowledge_revisions_v3.scope,
                   knowledge_revisions_v3.sensitivity,
                   knowledge_revisions_v3.lifecycle,
                   knowledge_revisions_v3.recorded_at
            FROM knowledge_objects_v3
            JOIN knowledge_revisions_v3
              ON knowledge_revisions_v3.revision_id =
                 knowledge_objects_v3.current_revision_id
            WHERE knowledge_objects_v3.kind = ?
              AND knowledge_objects_v3.semantic_key = ?
              AND knowledge_revisions_v3.scope = ?
              AND knowledge_revisions_v3.lifecycle = 'active'
            ORDER BY knowledge_objects_v3.knowledge_id
            LIMIT 2
            """,
            (kind, semantic_key, scope),
        ).fetchall()
        if len(rows) > 1:
            raise RuntimeError("semantic identity is ambiguous; resolve it before compilation")
        current = rows[0] if rows else None
        current_id = current["knowledge_id"] if current is not None else None
        foreign_alias_ids = {
            candidate["knowledge_id"]
            for candidate in aliases
            if candidate["knowledge_id"] != current_id
        }
        if foreign_alias_ids and not explicit_ambiguity(
            foreign_alias_ids,
            subject_id=current_id,
        ):
            raise RuntimeError(
                "compiled identity aliases match an existing canonical identity; use its "
                "exact semantic key or record an explicit ambiguous or "
                "possible_duplicate identity candidate"
            )
        return current

    def _prepare_object(
        self,
        store: AutonomousKnowledgeStore,
        *,
        run: sqlite3.Row,
        grant: sqlite3.Row,
        row: sqlite3.Row,
        action: dict[str, Any],
        recorded_at: str,
    ) -> dict[str, Any]:
        kind = action["kind"]
        if kind not in KNOWLEDGE_KINDS or kind == "skill":
            raise ValueError("compiler plan uses an unsupported Knowledge kind")
        title = _bounded(action["title"], field="Knowledge title", maximum=500)
        body = _bounded(action["body"], field="Knowledge body", maximum=_MAX_TEXT)
        semantic_key = _bounded(action["semantic_key"], field="semantic key", maximum=300)
        if action["epistemic_state"] not in EPISTEMIC_STATES:
            raise ValueError("compiled Knowledge epistemic state is invalid")
        synthesis_inputs = action["synthesis_inputs"]
        synthesis_source_revision_ids = (
            set(synthesis_inputs.get("source_revision_ids", []))
            if isinstance(synthesis_inputs, dict)
            else set()
        )
        source_refs = _canonical_source_references(
            action["source_refs"], field="compiled source references"
        )
        scope = grant["allowed_scope"]
        sensitivity_levels: list[int] = []
        for reference in source_refs:
            referenced_source_revision_id = reference.get("source_revision_id")
            if referenced_source_revision_id != run["source_revision_id"] and not (
                kind == "synthesis"
                and run["compiler_profile_version"] == "2"
                and referenced_source_revision_id in synthesis_source_revision_ids
            ):
                raise ValueError(
                    "cross-source Knowledge evidence requires a profile v2 Synthesis "
                    "input binding"
                )
            binding = store._source_reference_binding(reference)
            if binding is None or binding["active"] is not True:
                raise ValueError("compiled Knowledge source reference is not active")
            if binding["scope"] != scope:
                raise PermissionError("compiled Knowledge source exceeds its granted scope")
            level = SENSITIVITY_ORDER.index(binding["sensitivity"])
            if level > SENSITIVITY_ORDER.index(grant["max_sensitivity"]):
                raise PermissionError("compiled Knowledge source exceeds its granted sensitivity")
            sensitivity_levels.append(level)
        sensitivity = SENSITIVITY_ORDER[max(sensitivity_levels)]
        if sensitivity not in SENSITIVITIES:
            raise RuntimeError("compiled Knowledge sensitivity is invalid")
        aliases = action["aliases"]
        if any(not normalize_identity_text(alias) for alias in aliases):
            raise ValueError("compiled Knowledge alias is invalid")
        current = self._resolve_current_identity(
            store,
            compilation_run_id=run["compilation_run_id"],
            packet_id=row["packet_id"],
            kind=kind,
            semantic_key=semantic_key,
            scope=scope,
            requested_knowledge_id=action["knowledge_id"],
            identity_terms=[semantic_key, title, *aliases],
        )
        requested_action = action["action"]
        if kind != "synthesis" and synthesis_inputs is not None:
            raise ValueError("synthesis inputs are only valid for Synthesis knowledge")
        if (
            kind == "synthesis"
            and requested_action not in {"retain", "propose"}
            and synthesis_inputs is None
        ):
            raise ValueError("compiled Synthesis requires its complete input set")
        if requested_action == "retain":
            if current is None:
                raise ValueError("retain action has no current Knowledge identity")
            if (
                action["expected_revision_id"] is not None
                and action["expected_revision_id"] != current["current_revision_id"]
            ):
                raise RuntimeError("retain action compare-and-swap conflict")
            return {
                "resolved_action": "retain",
                "knowledge_id": current["knowledge_id"],
                "revision_id": None,
                "parent_revision_id": current["current_revision_id"],
                "kind": kind,
                "semantic_key": semantic_key,
                "scope": scope,
                "sensitivity": current["sensitivity"],
                "packet_id": row["packet_id"],
                "action_ordinal": row["action_ordinal"],
                "recorded_at": recorded_at,
            }
        if requested_action == "propose":
            knowledge_id = (
                current["knowledge_id"]
                if current is not None
                else stable_id(
                    "knowledge",
                    store.vault_id,
                    "compilation-proposal",
                    run["compilation_run_id"],
                    kind,
                    semantic_key,
                )
            )
            return {
                "resolved_action": "propose",
                "knowledge_id": knowledge_id,
                "revision_id": None,
                "parent_revision_id": (
                    current["current_revision_id"] if current is not None else None
                ),
                "kind": kind,
                "semantic_key": semantic_key,
                "scope": scope,
                "sensitivity": sensitivity,
                "packet_id": row["packet_id"],
                "action_ordinal": row["action_ordinal"],
                "recorded_at": recorded_at,
            }
        prepared_synthesis_inputs = (
            self._validate_synthesis_inputs(
                store,
                run=run,
                grant=grant,
                value=synthesis_inputs,
            )
            if kind == "synthesis"
            else None
        )
        if current is None:
            if requested_action in {"revise", "archive"}:
                raise ValueError(f"{requested_action} action has no current Knowledge identity")
            if action["expected_revision_id"] is not None:
                raise ValueError("new compiled Knowledge cannot expect a revision")
            resolved_action = "create"
            knowledge_id = stable_id(
                "knowledge",
                store.vault_id,
                "source-compilation",
                kind,
                semantic_key,
            )
            parent_revision_id = None
            workspace_path = _workspace_path(
                kind=kind,
                knowledge_id=knowledge_id,
                memory_type="semantic" if kind == "memory" else None,
            )
        else:
            resolved_action = "revise" if requested_action == "create" else requested_action
            knowledge_id = current["knowledge_id"]
            parent_revision_id = current["current_revision_id"]
            workspace_path = _safe_knowledge_workspace_path(current["workspace_path"])
            if (
                action["expected_revision_id"] is not None
                and action["expected_revision_id"] != parent_revision_id
            ):
                raise RuntimeError("compiled Knowledge compare-and-swap conflict")
            if requested_action in {"revise", "archive"} and (
                action["expected_revision_id"] != parent_revision_id
            ):
                raise ValueError("revision and archive actions require the exact expected revision")
            if current["lifecycle"] != "active":
                raise PermissionError("compiled Knowledge cannot revise an inactive identity")
            recorded_at = _timestamp_after(recorded_at, current["recorded_at"])
        if action["valid_from"] is not None:
            canonical_timestamp(action["valid_from"], field="compiled valid_from")
        if action["valid_to"] is not None:
            canonical_timestamp(action["valid_to"], field="compiled valid_to")
        if (
            action["valid_from"] is not None
            and action["valid_to"] is not None
            and action["valid_from"] >= action["valid_to"]
        ):
            raise ValueError("compiled Knowledge valid interval is invalid")
        assertion = action["assertion"]
        if assertion is not None and kind not in {"claim", "event"}:
            raise ValueError("structured assertion is only valid for Claim or Event knowledge")
        source_risk = any(
            store.connection.execute(
                """
                SELECT instruction_risk FROM source_fragments
                WHERE fragment_id = ?
                """,
                (reference["fragment_id"],),
            ).fetchone()["instruction_risk"]
            for reference in source_refs
        )
        content_risk = has_instruction_risk(
            "\n".join(
                (
                    title,
                    body,
                    semantic_key,
                    canonical_json(aliases),
                    canonical_json(action["tags"]),
                    canonical_json(assertion),
                    canonical_json(action["applicability"]),
                )
            )
        )
        quarantine_reasons = (
            ["persistent_prompt_injection_risk"] if source_risk or content_risk else []
        )
        lifecycle = (
            "archived"
            if resolved_action == "archive"
            else "quarantined"
            if quarantine_reasons
            else "active"
        )
        lifecycle_reason = action["reason"] if lifecycle == "archived" else None
        epistemic_state = action["epistemic_state"]
        revision_id = stable_id(
            "knowledgerev",
            knowledge_id,
            run["compilation_run_id"],
            str(row["action_ordinal"]),
            sha256_bytes(_canonical_bytes(action)),
        )
        generation = {
            "activity_id": run["compilation_run_id"],
            "run_id": None,
            "model_id": run["model_identity"],
            "tool_id": "living-wiki-compiler",
        }
        metadata = {
            "quarantine_reasons": quarantine_reasons,
            "memory_type": "semantic" if kind == "memory" else None,
            "preference_basis": "agent_inference" if kind == "preference" else None,
            "skill_manifest": None,
            "lifecycle_reason": lifecycle_reason,
            "mutability": AGENT_KNOWLEDGE_MUTABILITY,
            "writer_scope": scope,
            "activation_policy": AUTONOMOUS_ACTIVATION_POLICY,
            "aliases": aliases,
            "relation_hints": [],
            "assertion": assertion,
        }
        semantic_digest = sha256_bytes(
            canonical_json(
                {
                    "kind": kind,
                    "title": compact_text(title),
                    "body": compact_text(body),
                    "semantic_key": semantic_key,
                    "assertion": assertion,
                }
            ).encode("utf-8")
        )
        verification = "revision_bound" if kind == "synthesis" else "source_bound"
        markdown = render_knowledge_markdown(
            knowledge_id=knowledge_id,
            revision_id=revision_id,
            title=title,
            body=body,
            kind=kind,
            lifecycle=lifecycle,
            epistemic_state=epistemic_state,
            verification=verification,
            scope=scope,
            sensitivity=sensitivity,
            writer_id=grant["writer_id"],
            source_free=False,
            source_refs=source_refs,
            generation=generation,
            tags=action["tags"],
            semantic_key=semantic_key,
            aliases=aliases,
            relation_hints=[],
            assertion=assertion,
            parent_revision_id=parent_revision_id,
            supersedes_revision_id=parent_revision_id,
            valid_from=action["valid_from"],
            valid_to=action["valid_to"],
            observed_at=recorded_at,
            recorded_at=recorded_at,
            expires_at=None,
            preference_basis=metadata["preference_basis"],
            memory_type=metadata["memory_type"],
            skill_manifest=None,
            quarantine_reasons=quarantine_reasons,
            lifecycle_reason=lifecycle_reason,
            schema_version=KNOWLEDGE_OBJECT_SCHEMA,
        )
        markdown_sha256, _ = _write_object(store.root, markdown)
        return {
            "resolved_action": resolved_action,
            "knowledge_id": knowledge_id,
            "revision_id": revision_id,
            "parent_revision_id": parent_revision_id,
            "markdown_sha256": markdown_sha256,
            "markdown_byte_size": len(markdown),
            "workspace_path": workspace_path,
            "title": title,
            "body_sha256": sha256_bytes(body.encode("utf-8")),
            "kind": kind,
            "semantic_key": semantic_key,
            "semantic_digest": semantic_digest,
            "lifecycle": lifecycle,
            "epistemic_state": epistemic_state,
            "verification": verification,
            "scope": scope,
            "sensitivity": sensitivity,
            "writer_id": grant["writer_id"],
            "source_free": False,
            "source_refs": source_refs,
            "generation": generation,
            "tags": action["tags"],
            "metadata": metadata,
            "valid_from": action["valid_from"],
            "valid_to": action["valid_to"],
            "observed_at": recorded_at,
            "recorded_at": recorded_at,
            "expires_at": None,
            "applicability": action["applicability"],
            "synthesis_inputs": prepared_synthesis_inputs,
            "reason": action["reason"],
            "packet_id": row["packet_id"],
            "action_ordinal": row["action_ordinal"],
        }

    @staticmethod
    def _validate_synthesis_inputs(
        store: AutonomousKnowledgeStore,
        *,
        run: sqlite3.Row,
        grant: sqlite3.Row,
        value: Any,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("compiled Synthesis input set is invalid")
        canonical = {
            "source_revision_ids": sorted(value["source_revision_ids"]),
            "knowledge_revision_ids": sorted(value["knowledge_revision_ids"]),
            "relation_revision_ids": sorted(value["relation_revision_ids"]),
            "compilation_run_ids": sorted(value["compilation_run_ids"]),
        }
        if any(value[field] != canonical[field] for field in canonical):
            raise ValueError("compiled Synthesis input sets must be sorted")
        expected_digest = sha256_bytes(_canonical_bytes(canonical))
        if value["input_set_sha256"] != expected_digest:
            raise ValueError("compiled Synthesis input-set digest is invalid")
        if run["source_revision_id"] not in canonical["source_revision_ids"]:
            raise ValueError("compiled Synthesis omits its current Source Revision")
        if run["compilation_run_id"] not in canonical["compilation_run_ids"]:
            raise ValueError("compiled Synthesis omits its current Compilation Run")
        for source_revision_id in canonical["source_revision_ids"]:
            binding = store._source_reference_binding(
                {"source_revision_id": source_revision_id}
            )
            if (
                binding is None
                or binding["active"] is not True
                or binding["scope"] != grant["allowed_scope"]
                or SENSITIVITY_ORDER.index(binding["sensitivity"])
                > SENSITIVITY_ORDER.index(grant["max_sensitivity"])
            ):
                raise PermissionError("compiled Synthesis source input is not admitted")
        for revision_id in canonical["knowledge_revision_ids"]:
            row = store.connection.execute(
                """
                SELECT knowledge_objects_v3.current_revision_id,
                       knowledge_revisions_v3.*
                FROM knowledge_revisions_v3
                JOIN knowledge_objects_v3 USING(knowledge_id)
                WHERE knowledge_revisions_v3.revision_id = ?
                """,
                (revision_id,),
            ).fetchone()
            if row is None or row["current_revision_id"] != revision_id:
                raise ValueError("compiled Synthesis Knowledge input is not current")
            revision = store._revision_row(row, include_body=False)
            if (
                revision["scope"] != grant["allowed_scope"]
                or SENSITIVITY_ORDER.index(revision["sensitivity"])
                > SENSITIVITY_ORDER.index(grant["max_sensitivity"])
                or not store.revision_provenance_admitted(revision)
            ):
                raise PermissionError("compiled Synthesis Knowledge input is not admitted")
        for relation_revision_id in canonical["relation_revision_ids"]:
            row = store.connection.execute(
                """
                SELECT knowledge_relations_v3.current_revision_id,
                       knowledge_relation_revisions_v3.*
                FROM knowledge_relation_revisions_v3
                JOIN knowledge_relations_v3 USING(relation_key)
                WHERE relation_revision_id = ?
                """,
                (relation_revision_id,),
            ).fetchone()
            if row is None or row["current_revision_id"] != relation_revision_id:
                raise ValueError("compiled Synthesis relation input is not current")
            relation = {
                **dict(row),
                "evidence_refs": strict_json_loads(row["evidence_refs_json"]),
                "source_free": bool(row["source_free"]),
            }
            if (
                relation["scope"] != grant["allowed_scope"]
                or SENSITIVITY_ORDER.index(relation["sensitivity"])
                > SENSITIVITY_ORDER.index(grant["max_sensitivity"])
                or not store.relation_provenance_admitted(relation)
            ):
                raise PermissionError("compiled Synthesis relation input is not admitted")
        for compilation_run_id in canonical["compilation_run_ids"]:
            row = store.connection.execute(
                """
                SELECT status FROM source_compilation_runs_v1
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()
            if row is None or (
                compilation_run_id != run["compilation_run_id"]
                and row["status"] not in {
                    "committed",
                    "projection_pending",
                    "succeeded",
                }
            ):
                raise ValueError("compiled Synthesis run input is unavailable")
        return {**canonical, "input_set_sha256": expected_digest}

    def _prepare_relation(
        self,
        store: AutonomousKnowledgeStore,
        *,
        run: sqlite3.Row,
        grant: sqlite3.Row,
        row: sqlite3.Row,
        action: dict[str, Any],
        identity_map: dict[tuple[str, str], str],
        recorded_at: str,
    ) -> dict[str, Any]:
        if action["predicate"] not in RELATION_PREDICATES:
            raise ValueError("compiled relation predicate is invalid")
        subject = self._resolve_endpoint(
            store,
            endpoint=action["subject"],
            scope=grant["allowed_scope"],
            identity_map=identity_map,
        )
        object_id = self._resolve_endpoint(
            store,
            endpoint=action["object"],
            scope=grant["allowed_scope"],
            identity_map=identity_map,
        )
        if subject == object_id:
            raise ValueError("compiled relation cannot be a self-loop")
        refs = _canonical_source_references(
            action["evidence_refs"], field="compiled relation evidence"
        )
        levels: list[int] = []
        for reference in refs:
            if (
                reference.get("source_revision_id") != run["source_revision_id"]
                and run["compiler_profile_version"] != "2"
            ):
                raise ValueError(
                    "cross-source relation evidence requires compiler profile v2"
                )
            binding = store._source_reference_binding(reference)
            if binding is None or binding["active"] is not True:
                raise ValueError("compiled relation evidence is not active")
            if binding["scope"] != grant["allowed_scope"]:
                raise PermissionError("compiled relation evidence exceeds its scope")
            level = SENSITIVITY_ORDER.index(binding["sensitivity"])
            if level > SENSITIVITY_ORDER.index(grant["max_sensitivity"]):
                raise PermissionError("compiled relation evidence exceeds its sensitivity")
            levels.append(level)
        relation_key = stable_id(
            "relationkey",
            store.vault_id,
            subject,
            action["predicate"],
            object_id,
        )
        current = store.connection.execute(
            """
            SELECT knowledge_relations_v3.current_revision_id,
                   knowledge_relation_revisions_v3.recorded_at,
                   knowledge_relation_revisions_v3.lifecycle
            FROM knowledge_relations_v3
            JOIN knowledge_relation_revisions_v3
              ON knowledge_relation_revisions_v3.relation_revision_id =
                 knowledge_relations_v3.current_revision_id
            WHERE knowledge_relations_v3.relation_key = ?
            """,
            (relation_key,),
        ).fetchone()
        requested_action = action["action"]
        if requested_action == "retain":
            if current is None:
                raise ValueError("retain relation has no current identity")
            return {
                "resolved_action": "retain",
                "relation_key": relation_key,
                "relation_revision_id": None,
                "parent_revision_id": current["current_revision_id"],
                "packet_id": row["packet_id"],
                "action_ordinal": row["action_ordinal"],
                "recorded_at": recorded_at,
            }
        if requested_action == "propose":
            return {
                "resolved_action": "propose",
                "relation_key": relation_key,
                "relation_revision_id": None,
                "parent_revision_id": (
                    current["current_revision_id"] if current is not None else None
                ),
                "packet_id": row["packet_id"],
                "action_ordinal": row["action_ordinal"],
                "recorded_at": recorded_at,
            }
        if current is None:
            if requested_action in {"revise", "archive"}:
                raise ValueError("relation revision has no current identity")
            if action["expected_relation_revision_id"] is not None:
                raise ValueError("new relation cannot expect a revision")
            resolved_action = "create"
            parent_revision_id = None
        else:
            resolved_action = "revise" if requested_action == "create" else requested_action
            parent_revision_id = current["current_revision_id"]
            if action["expected_relation_revision_id"] not in {
                None,
                parent_revision_id,
            }:
                raise RuntimeError("compiled relation compare-and-swap conflict")
            if requested_action in {"revise", "archive"} and (
                action["expected_relation_revision_id"] != parent_revision_id
            ):
                raise ValueError(
                    "relation revision and archive require the exact expected revision"
                )
            if current["lifecycle"] != "active":
                raise PermissionError("compiled relation cannot revise an inactive identity")
            recorded_at = _timestamp_after(recorded_at, current["recorded_at"])
        if action["valid_from"] is not None:
            canonical_timestamp(action["valid_from"], field="relation valid_from")
        if action["valid_to"] is not None:
            canonical_timestamp(action["valid_to"], field="relation valid_to")
        if (
            action["valid_from"] is not None
            and action["valid_to"] is not None
            and action["valid_from"] >= action["valid_to"]
        ):
            raise ValueError("compiled relation valid interval is invalid")
        relation_revision_id = stable_id(
            "relationrev",
            relation_key,
            run["compilation_run_id"],
            str(row["action_ordinal"]),
            sha256_bytes(_canonical_bytes(action)),
        )
        return {
            "resolved_action": resolved_action,
            "relation_key": relation_key,
            "relation_revision_id": relation_revision_id,
            "parent_revision_id": parent_revision_id,
            "subject_knowledge_id": subject,
            "predicate": action["predicate"],
            "object_knowledge_id": object_id,
            "evidence_refs": refs,
            "source_free": False,
            "lifecycle": "archived" if resolved_action == "archive" else "active",
            "scope": grant["allowed_scope"],
            "sensitivity": SENSITIVITY_ORDER[max(levels)],
            "writer_id": grant["writer_id"],
            "valid_from": action["valid_from"],
            "valid_to": action["valid_to"],
            "observed_at": recorded_at,
            "recorded_at": recorded_at,
            "reason": action["reason"],
            "packet_id": row["packet_id"],
            "action_ordinal": row["action_ordinal"],
        }

    @staticmethod
    def _resolve_endpoint(
        store: AutonomousKnowledgeStore,
        *,
        endpoint: dict[str, Any],
        scope: str,
        identity_map: dict[tuple[str, str], str],
    ) -> str:
        knowledge_id = endpoint["knowledge_id"]
        semantic_key = endpoint["semantic_key"]
        kind = endpoint["kind"]
        if knowledge_id is not None:
            staged = knowledge_id in identity_map.values()
            current = store.connection.execute(
                """
                SELECT 1 FROM knowledge_objects_v3
                JOIN knowledge_revisions_v3
                  ON knowledge_revisions_v3.revision_id =
                     knowledge_objects_v3.current_revision_id
                WHERE knowledge_objects_v3.knowledge_id = ?
                  AND knowledge_revisions_v3.scope = ?
                  AND knowledge_revisions_v3.lifecycle = 'active'
                """,
                (knowledge_id, scope),
            ).fetchone()
            if not staged and current is None:
                raise KeyError("compiled relation endpoint is unavailable")
            return cast(str, knowledge_id)
        if semantic_key is None or kind is None:
            raise ValueError("compiled relation endpoint is under-specified")
        staged_identity = identity_map.get((kind, semantic_key))
        if staged_identity is not None:
            return staged_identity
        rows = store.connection.execute(
            """
            SELECT knowledge_objects_v3.knowledge_id
            FROM knowledge_objects_v3
            JOIN knowledge_revisions_v3
              ON knowledge_revisions_v3.revision_id =
                 knowledge_objects_v3.current_revision_id
            WHERE knowledge_objects_v3.kind = ?
              AND knowledge_objects_v3.semantic_key = ?
              AND knowledge_revisions_v3.scope = ?
              AND knowledge_revisions_v3.lifecycle = 'active'
            ORDER BY knowledge_objects_v3.knowledge_id
            LIMIT 2
            """,
            (kind, semantic_key, scope),
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError("compiled relation endpoint identity is unresolved or ambiguous")
        return cast(str, rows[0]["knowledge_id"])

    def commit(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "source compilation requires confirmation that no case data is present"
            )
        request = {
            "operation": "commit_compilation",
            "compilation_run_id": compilation_run_id,
        }
        request_bytes = _canonical_bytes(request)
        request_sha256 = sha256_bytes(request_bytes)
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            run = self._run(store, compilation_run_id)
            if run["grant_id"] != grant_id:
                raise PermissionError("source compilation run is bound to another grant")
            grant = store._grant(
                grant_id,
                operation="commit_compilation",
                request_bytes=len(request_bytes),
            )
            if run["receipt_sha256"] is not None:
                receipt = _decoded_artifact(store, run["receipt_sha256"], role="receipt")
                self._finish_materialization(
                    store,
                    compilation_run_id=compilation_run_id,
                    revision_ids=receipt["knowledge_revision_ids"],
                )
                receipt["idempotent_replay"] = True
                return receipt
            if run["status"] != "ready_to_commit":
                raise RuntimeError("source compilation run is not ready to commit")
            if store.audit_head != run["input_audit_head"]:
                raise RuntimeError("autonomous audit head changed after compilation began")
            object_rows = store.connection.execute(
                """
                SELECT * FROM source_compilation_staged_objects_v1
                WHERE compilation_run_id = ? AND validation_state = 'valid'
                ORDER BY action_ordinal
                """,
                (compilation_run_id,),
            ).fetchall()
            relation_rows = store.connection.execute(
                """
                SELECT * FROM source_compilation_staged_relations_v1
                WHERE compilation_run_id = ? AND validation_state = 'valid'
                ORDER BY action_ordinal
                """,
                (compilation_run_id,),
            ).fetchall()
            prepared_objects = [self._prepared_value(row, kind="Knowledge") for row in object_rows]
            prepared_relations = [
                self._prepared_value(row, kind="relation") for row in relation_rows
            ]
            publish_objects = [
                value
                for value in prepared_objects
                if value["resolved_action"] not in {"retain", "propose"}
            ]
            publish_relations = [
                value
                for value in prepared_relations
                if value["resolved_action"] not in {"retain", "propose"}
            ]
            current_count = store.connection.execute(
                "SELECT COUNT(*) FROM knowledge_objects_v3"
            ).fetchone()[0]
            new_count = sum(value["parent_revision_id"] is None for value in publish_objects)
            if current_count + new_count > grant["max_objects"]:
                raise RuntimeError("knowledge sink object capacity exceeded")
            store._enforce_grant_limits(grant, enforce_object_capacity=False)
            outputs = sorted(
                [
                    *(
                        {
                            "output_kind": "knowledge_revision",
                            "output_id": value["revision_id"],
                            "object_id": value["knowledge_id"],
                            "packet_id": value["packet_id"],
                        }
                        for value in publish_objects
                    ),
                    *(
                        {
                            "output_kind": "relation_revision",
                            "output_id": value["relation_revision_id"],
                            "object_id": value["relation_key"],
                            "packet_id": value["packet_id"],
                        }
                        for value in publish_relations
                    ),
                ],
                key=lambda item: (item["output_kind"], item["output_id"]),
            )
            output_set_sha256 = sha256_bytes(canonical_json(outputs).encode("utf-8"))
            last_recorded_at = max(
                [value["recorded_at"] for value in [*publish_objects, *publish_relations]]
                or [store._next_transaction_time()]
            )
            committed_at = _timestamp_after(
                store._next_transaction_time(),
                last_recorded_at,
            )
            knowledge_revision_ids = [value["revision_id"] for value in publish_objects]
            relation_revision_ids = [value["relation_revision_id"] for value in publish_relations]
            receipt: dict[str, Any]
            with store._file_lease("canonical-mutation"):
                try:
                    store.connection.execute("BEGIN IMMEDIATE")
                    locked = self._run(store, compilation_run_id)
                    locked_grant = store._grant(
                        grant_id,
                        operation="commit_compilation",
                        request_bytes=len(request_bytes),
                    )
                    store._enforce_grant_limits(locked_grant, enforce_object_capacity=False)
                    if (
                        locked["status"] != "ready_to_commit"
                        or locked["receipt_sha256"] is not None
                        or store.audit_head != locked["input_audit_head"]
                        or store.legacy_audit_head != locked["input_legacy_audit_head"]
                    ):
                        raise RuntimeError("source compilation commit precondition changed")
                    source_binding = store._source_reference_binding(
                        {"source_revision_id": locked["source_revision_id"]}
                    )
                    if source_binding is None or source_binding["active"] is not True:
                        raise PermissionError("Source Revision became inadmissible before commit")
                    locked_current_count = store.connection.execute(
                        "SELECT COUNT(*) FROM knowledge_objects_v3"
                    ).fetchone()[0]
                    if locked_current_count + new_count > locked_grant["max_objects"]:
                        raise RuntimeError("knowledge sink object capacity exceeded")
                    for value in publish_objects:
                        self._commit_object(
                            store,
                            run=locked,
                            grant=locked_grant,
                            value=value,
                        )
                        store.connection.execute(
                            """
                            INSERT INTO source_compilation_outputs_v1(
                                compilation_run_id, output_kind, output_id,
                                object_id, packet_id, recorded_at
                            ) VALUES (?, 'knowledge_revision', ?, ?, ?, ?)
                            """,
                            (
                                compilation_run_id,
                                value["revision_id"],
                                value["knowledge_id"],
                                value["packet_id"],
                                value["recorded_at"],
                            ),
                        )
                        self._commit_dependencies(
                            store,
                            compilation_run_id=compilation_run_id,
                            consumer_kind="knowledge_revision",
                            consumer_object_id=value["knowledge_id"],
                            consumer_revision_id=value["revision_id"],
                            source_refs=value["source_refs"],
                            recorded_at=value["recorded_at"],
                        )
                        self._commit_object_revision_dependencies(
                            store,
                            compilation_run_id=compilation_run_id,
                            value=value,
                        )
                    for value in publish_relations:
                        self._commit_relation(
                            store,
                            run=locked,
                            grant=locked_grant,
                            value=value,
                        )
                        store.connection.execute(
                            """
                            INSERT INTO source_compilation_outputs_v1(
                                compilation_run_id, output_kind, output_id,
                                object_id, packet_id, recorded_at
                            ) VALUES (?, 'relation_revision', ?, ?, ?, ?)
                            """,
                            (
                                compilation_run_id,
                                value["relation_revision_id"],
                                value["relation_key"],
                                value["packet_id"],
                                value["recorded_at"],
                            ),
                        )
                        self._commit_dependencies(
                            store,
                            compilation_run_id=compilation_run_id,
                            consumer_kind="relation_revision",
                            consumer_object_id=value["relation_key"],
                            consumer_revision_id=value["relation_revision_id"],
                            source_refs=value["evidence_refs"],
                            recorded_at=value["recorded_at"],
                        )
                        self._commit_relation_revision_dependencies(
                            store,
                            compilation_run_id=compilation_run_id,
                            value=value,
                        )
                    store._append_event(
                        event_type="source_compilation_committed",
                        object_id=compilation_run_id,
                        payload={
                            "source_revision_id": locked["source_revision_id"],
                            "compiler_profile": locked["compiler_profile"],
                            "compiler_profile_version": locked["compiler_profile_version"],
                            "grant_id": grant_id,
                            "writer_id": locked_grant["writer_id"],
                            "input_audit_head": locked["input_audit_head"],
                            "output_set_sha256": output_set_sha256,
                            "committed_object_count": len(publish_objects),
                            "committed_relation_count": len(publish_relations),
                            "origin": "agent_derived",
                            "authority": "agent_derived",
                            "legal_authority": False,
                        },
                        recorded_at=committed_at,
                    )
                    commit_audit_head = store.audit_head
                    receipt = {
                        "schema_version": COMPILATION_RECEIPT_SCHEMA,
                        "compilation_run_id": compilation_run_id,
                        "source_revision_id": locked["source_revision_id"],
                        "input_audit_head": locked["input_audit_head"],
                        "commit_audit_head": commit_audit_head,
                        "output_set_sha256": output_set_sha256,
                        "committed_object_count": len(publish_objects),
                        "committed_relation_count": len(publish_relations),
                        "knowledge_revision_ids": knowledge_revision_ids,
                        "relation_revision_ids": relation_revision_ids,
                        "legal_authority": False,
                        "status": "projection_pending",
                        "idempotent_replay": False,
                        "committed_at": committed_at,
                    }
                    _validate_contract("source-compilation-receipt.v1.schema.json", receipt)
                    receipt_sha256, _ = _artifact(
                        store,
                        value=receipt,
                        role="receipt",
                        created_at=committed_at,
                    )
                    queue_id = stable_id("rebuild", compilation_run_id, commit_audit_head)
                    store.connection.execute(
                        """
                        INSERT INTO derived_rebuild_queue_v3(
                            queue_id, input_audit_head, reason, created_at,
                            completed_at
                        ) VALUES (?, ?, 'source_compilation_committed', ?, NULL)
                        """,
                        (queue_id, commit_audit_head, committed_at),
                    )
                    store.connection.execute(
                        """
                        UPDATE source_compilation_runs_v1
                        SET status = 'committed', resumable = 1,
                            output_set_sha256 = ?, receipt_sha256 = ?,
                            updated_at = ?, committed_at = ?,
                            failure_stage = NULL, failure_sha256 = NULL
                        WHERE compilation_run_id = ?
                        """,
                        (
                            output_set_sha256,
                            receipt_sha256,
                            committed_at,
                            committed_at,
                            compilation_run_id,
                        ),
                    )
                    store.connection.execute(
                        """
                        UPDATE source_compilation_run_metadata_v1
                        SET canonical_commit_sha256 = ?
                        WHERE compilation_run_id = ?
                        """,
                        (commit_audit_head, compilation_run_id),
                    )
                    self._record_usage(
                        store,
                        grant_id=grant_id,
                        operation="commit_compilation",
                        request_sha256=request_sha256,
                        recorded_at=committed_at,
                        discriminator=compilation_run_id,
                    )
                    store.connection.commit()
                except BaseException:
                    store.connection.rollback()
                    raise
            self._finish_materialization(
                store,
                compilation_run_id=compilation_run_id,
                revision_ids=knowledge_revision_ids,
            )
            return receipt

    @staticmethod
    def _prepared_value(row: sqlite3.Row, *, kind: str) -> dict[str, Any]:
        value = strict_json_loads(row["prepared_json"])
        if not isinstance(value, dict):
            raise RuntimeError(f"prepared source compilation {kind} is invalid")
        return value

    @staticmethod
    def _commit_object(
        store: AutonomousKnowledgeStore,
        *,
        run: sqlite3.Row,
        grant: sqlite3.Row,
        value: dict[str, Any],
    ) -> None:
        current = store.connection.execute(
            """
            SELECT current_revision_id, kind, workspace_path
            FROM knowledge_objects_v3 WHERE knowledge_id = ?
            """,
            (value["knowledge_id"],),
        ).fetchone()
        parent_revision_id = value["parent_revision_id"]
        if parent_revision_id is None:
            if current is not None:
                raise RuntimeError("compiled Knowledge identity collided during commit")
            store.connection.execute(
                """
                INSERT INTO knowledge_objects_v3(
                    knowledge_id, kind, origin, authority, current_revision_id,
                    workspace_path, semantic_key, created_at, updated_at
                ) VALUES (?, ?, 'agent_derived', 'agent_derived', NULL, ?, ?, ?, ?)
                """,
                (
                    value["knowledge_id"],
                    value["kind"],
                    value["workspace_path"],
                    value["semantic_key"],
                    value["recorded_at"],
                    value["recorded_at"],
                ),
            )
        elif (
            current is None
            or current["current_revision_id"] != parent_revision_id
            or current["kind"] != value["kind"]
            or current["workspace_path"] != value["workspace_path"]
        ):
            raise RuntimeError("compiled Knowledge compare-and-swap conflict")
        for reference in value["source_refs"]:
            if not store._source_reference_is_bound(
                reference,
                scope=value["scope"],
                max_sensitivity=value["sensitivity"],
                require_active=True,
            ):
                raise ValueError("compiled Knowledge evidence changed before commit")
        markdown = _read_object(store.root, value["markdown_sha256"])
        if (
            len(markdown) != value["markdown_byte_size"]
            or sha256_bytes(markdown) != value["markdown_sha256"]
        ):
            raise RuntimeError("prepared Knowledge Markdown changed before commit")
        workspace_file = store.root / value["workspace_path"]
        if workspace_file.exists() or workspace_file.is_symlink():
            if workspace_file.is_symlink() or not workspace_file.is_file():
                raise RuntimeError("Knowledge workspace target is unsafe")
            expected_sha256 = None
            if parent_revision_id is not None:
                parent = store.connection.execute(
                    """
                    SELECT markdown_sha256 FROM knowledge_revisions_v3
                    WHERE revision_id = ?
                    """,
                    (parent_revision_id,),
                ).fetchone()
                expected_sha256 = parent["markdown_sha256"] if parent is not None else None
            if sha256_bytes(workspace_file.read_bytes()) not in {
                expected_sha256,
                value["markdown_sha256"],
            }:
                raise RuntimeError("Knowledge workspace changed after compilation validation")
        duplicate = store.connection.execute(
            """
            SELECT knowledge_objects_v3.knowledge_id
            FROM knowledge_objects_v3
            JOIN knowledge_revisions_v3
              ON knowledge_revisions_v3.revision_id =
                 knowledge_objects_v3.current_revision_id
            WHERE knowledge_revisions_v3.lifecycle = 'active'
              AND knowledge_revisions_v3.kind = ?
              AND knowledge_revisions_v3.scope = ?
              AND knowledge_revisions_v3.sensitivity = ?
              AND knowledge_revisions_v3.semantic_digest = ?
              AND knowledge_objects_v3.knowledge_id <> ?
            LIMIT 1
            """,
            (
                value["kind"],
                value["scope"],
                value["sensitivity"],
                value["semantic_digest"],
                value["knowledge_id"],
            ),
        ).fetchone()
        if duplicate is not None:
            raise ValueError("compiled Knowledge duplicates another active identity")
        _register_content_object(
            store.connection,
            digest=value["markdown_sha256"],
            object_role="knowledge_revision",
            byte_size=len(markdown),
            media_type="text/markdown; charset=utf-8",
            created_at=value["recorded_at"],
        )
        store.connection.execute(
            """
            INSERT INTO knowledge_revisions_v3(
                revision_id, knowledge_id, parent_revision_id,
                supersedes_revision_id, markdown_sha256, semantic_digest,
                title, semantic_key, kind, lifecycle, epistemic_state,
                origin, authority, verification, scope, sensitivity,
                writer_id, source_free, source_refs_json, generation_json,
                tags_json, metadata_json, valid_from, valid_to, observed_at,
                recorded_at, expires_at, workspace_path
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'agent_derived', 'agent_derived', ?, ?, ?, ?, 0, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                value["revision_id"],
                value["knowledge_id"],
                parent_revision_id,
                parent_revision_id,
                value["markdown_sha256"],
                value["semantic_digest"],
                value["title"],
                value["semantic_key"],
                value["kind"],
                value["lifecycle"],
                value["epistemic_state"],
                value["verification"],
                value["scope"],
                value["sensitivity"],
                grant["writer_id"],
                canonical_json(value["source_refs"]),
                canonical_json(value["generation"]),
                canonical_json(value["tags"]),
                canonical_json(value["metadata"]),
                value["valid_from"],
                value["valid_to"],
                value["observed_at"],
                value["recorded_at"],
                value["expires_at"],
                value["workspace_path"],
            ),
        )
        if value["lifecycle"] != "quarantined":
            store.connection.execute(
                """
                UPDATE knowledge_aliases_v4 SET retired_at = ?
                WHERE knowledge_id = ? AND retired_at IS NULL
                """,
                (value["recorded_at"], value["knowledge_id"]),
            )
        if value["lifecycle"] == "active":
            alias_values = list(
                dict.fromkeys(
                    [
                        value["title"],
                        *value["metadata"]["aliases"],
                        value["semantic_key"],
                    ]
                )
            )
            for alias in alias_values:
                alias_key = normalize_identity_text(alias)
                if not alias_key:
                    continue
                store.connection.execute(
                    """
                    INSERT INTO knowledge_aliases_v4(
                        alias_key, alias_text, knowledge_id, kind, scope,
                        revision_id, writer_id, recorded_at, retired_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(alias_key, kind, scope, knowledge_id) DO UPDATE SET
                        alias_text = excluded.alias_text,
                        revision_id = excluded.revision_id,
                        writer_id = excluded.writer_id,
                        recorded_at = excluded.recorded_at,
                        retired_at = NULL
                    """,
                    (
                        alias_key,
                        alias,
                        value["knowledge_id"],
                        value["kind"],
                        value["scope"],
                        value["revision_id"],
                        grant["writer_id"],
                        value["recorded_at"],
                    ),
                )
        if value["lifecycle"] != "quarantined":
            store.connection.execute(
                """
                UPDATE knowledge_objects_v3
                SET current_revision_id = ?, workspace_path = ?,
                    semantic_key = ?, updated_at = ?
                WHERE knowledge_id = ?
                """,
                (
                    value["revision_id"],
                    value["workspace_path"],
                    value["semantic_key"],
                    value["recorded_at"],
                    value["knowledge_id"],
                ),
            )
            store.connection.execute(
                """
                INSERT INTO pending_materializations_v3(
                    revision_id, workspace_path, markdown_sha256, action, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    value["revision_id"],
                    value["workspace_path"],
                    value["markdown_sha256"],
                    "write" if value["lifecycle"] == "active" else "delete",
                    value["recorded_at"],
                ),
            )
        store._append_event(
            event_type="knowledge_revision_committed",
            object_id=value["revision_id"],
            payload={
                "grant_id": grant["grant_id"],
                "idempotency_key_sha256": sha256_bytes(
                    f"{run['compilation_run_id']}:{value['action_ordinal']}".encode()
                ),
                "request_sha256": sha256_bytes(
                    canonical_json(
                        {
                            "run": run["compilation_run_id"],
                            "action": value["action_ordinal"],
                            "revision": value["revision_id"],
                        }
                    ).encode("utf-8")
                ),
                "operation": "commit_compilation",
                "knowledge_id": value["knowledge_id"],
                "parent_revision_id": parent_revision_id,
                "markdown_sha256": value["markdown_sha256"],
                "lifecycle": value["lifecycle"],
                "epistemic_state": value["epistemic_state"],
                "origin": "agent_derived",
                "authority": "agent_derived",
                "writer_id": grant["writer_id"],
                "scope": value["scope"],
                "sensitivity": value["sensitivity"],
                "source_free": False,
                "semantic_digest": value["semantic_digest"],
                "verification": value["verification"],
                "source_refs_sha256": sha256_bytes(
                    canonical_json(value["source_refs"]).encode("utf-8")
                ),
                "generation_sha256": sha256_bytes(
                    canonical_json(value["generation"]).encode("utf-8")
                ),
                "tags_sha256": sha256_bytes(canonical_json(value["tags"]).encode("utf-8")),
                "metadata_sha256": sha256_bytes(canonical_json(value["metadata"]).encode("utf-8")),
                "valid_from": value["valid_from"],
                "valid_to": value["valid_to"],
                "expires_at": value["expires_at"],
                "workspace_edit_sha256": None,
            },
            recorded_at=value["recorded_at"],
        )

    @staticmethod
    def _commit_relation(
        store: AutonomousKnowledgeStore,
        *,
        run: sqlite3.Row,
        grant: sqlite3.Row,
        value: dict[str, Any],
    ) -> None:
        current = store.connection.execute(
            """
            SELECT current_revision_id FROM knowledge_relations_v3
            WHERE relation_key = ?
            """,
            (value["relation_key"],),
        ).fetchone()
        current_revision_id = current["current_revision_id"] if current is not None else None
        if current_revision_id != value["parent_revision_id"]:
            raise RuntimeError("compiled relation compare-and-swap conflict")
        for endpoint in (
            value["subject_knowledge_id"],
            value["object_knowledge_id"],
        ):
            admitted = store.connection.execute(
                """
                SELECT 1 FROM knowledge_objects_v3
                JOIN knowledge_revisions_v3
                  ON knowledge_revisions_v3.revision_id =
                     knowledge_objects_v3.current_revision_id
                WHERE knowledge_objects_v3.knowledge_id = ?
                  AND knowledge_revisions_v3.lifecycle = 'active'
                  AND knowledge_revisions_v3.scope = ?
                """,
                (endpoint, value["scope"]),
            ).fetchone()
            if admitted is None:
                raise ValueError("compiled relation endpoint is not active")
        for reference in value["evidence_refs"]:
            if not store._source_reference_is_bound(
                reference,
                scope=value["scope"],
                max_sensitivity=value["sensitivity"],
                require_active=True,
            ):
                raise ValueError("compiled relation evidence changed before commit")
        if current is None:
            store.connection.execute(
                """
                INSERT INTO knowledge_relations_v3(
                    relation_key, current_revision_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    value["relation_key"],
                    value["relation_revision_id"],
                    value["recorded_at"],
                    value["recorded_at"],
                ),
            )
        store.connection.execute(
            """
            INSERT INTO knowledge_relation_revisions_v3(
                relation_revision_id, relation_key, parent_revision_id,
                subject_knowledge_id, predicate, object_knowledge_id,
                evidence_refs_json, source_free, lifecycle, origin, authority,
                scope, sensitivity, writer_id, valid_from, valid_to, observed_at,
                recorded_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, 0, ?, 'agent_derived', 'agent_derived',
                ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                value["relation_revision_id"],
                value["relation_key"],
                value["parent_revision_id"],
                value["subject_knowledge_id"],
                value["predicate"],
                value["object_knowledge_id"],
                canonical_json(value["evidence_refs"]),
                value["lifecycle"],
                value["scope"],
                value["sensitivity"],
                grant["writer_id"],
                value["valid_from"],
                value["valid_to"],
                value["observed_at"],
                value["recorded_at"],
            ),
        )
        store.connection.execute(
            """
            UPDATE knowledge_relations_v3
            SET current_revision_id = ?, updated_at = ?
            WHERE relation_key = ?
            """,
            (
                value["relation_revision_id"],
                value["recorded_at"],
                value["relation_key"],
            ),
        )
        store._append_event(
            event_type="knowledge_relation_committed",
            object_id=value["relation_revision_id"],
            payload={
                "grant_id": grant["grant_id"],
                "idempotency_key_sha256": sha256_bytes(
                    f"{run['compilation_run_id']}:relation:{value['action_ordinal']}".encode()
                ),
                "request_sha256": sha256_bytes(
                    canonical_json(
                        {
                            "run": run["compilation_run_id"],
                            "action": value["action_ordinal"],
                            "relation_revision": value["relation_revision_id"],
                        }
                    ).encode("utf-8")
                ),
                "operation": "commit_compilation",
                "relation_key": value["relation_key"],
                "parent_revision_id": value["parent_revision_id"],
                "subject_knowledge_id": value["subject_knowledge_id"],
                "predicate": value["predicate"],
                "object_knowledge_id": value["object_knowledge_id"],
                "source_free": False,
                "scope": value["scope"],
                "sensitivity": value["sensitivity"],
                "writer_id": grant["writer_id"],
                "origin": "agent_derived",
                "authority": "agent_derived",
                "evidence_refs_sha256": sha256_bytes(
                    canonical_json(value["evidence_refs"]).encode("utf-8")
                ),
                "valid_from": value["valid_from"],
                "valid_to": value["valid_to"],
            },
            recorded_at=value["recorded_at"],
        )

    @staticmethod
    def _commit_dependencies(
        store: AutonomousKnowledgeStore,
        *,
        compilation_run_id: str,
        consumer_kind: str,
        consumer_object_id: str,
        consumer_revision_id: str,
        source_refs: list[dict[str, str]],
        recorded_at: str,
    ) -> None:
        for reference in source_refs:
            dependency_id = stable_id(
                "dependency",
                consumer_kind,
                consumer_revision_id,
                reference["source_revision_id"],
                reference.get("fragment_id", ""),
            )
            store.connection.execute(
                """
                INSERT INTO knowledge_dependencies_v1(
                    dependency_id, compilation_run_id, consumer_kind,
                    consumer_object_id, consumer_revision_id,
                    source_revision_id, fragment_id, dependency_kind,
                    freshness, reason, recorded_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, 'direct', 'fresh',
                    'source_compilation_commit', ?, ?
                )
                """,
                (
                    dependency_id,
                    compilation_run_id,
                    consumer_kind,
                    consumer_object_id,
                    consumer_revision_id,
                    reference["source_revision_id"],
                    reference.get("fragment_id"),
                    recorded_at,
                    recorded_at,
                ),
            )

    @classmethod
    def _commit_object_revision_dependencies(
        cls,
        store: AutonomousKnowledgeStore,
        *,
        compilation_run_id: str,
        value: dict[str, Any],
    ) -> None:
        synthesis_inputs = value.get("synthesis_inputs")
        if isinstance(synthesis_inputs, dict):
            for source_revision_id in synthesis_inputs["source_revision_ids"]:
                dependency_id = stable_id(
                    "dependency",
                    "knowledge_revision",
                    value["revision_id"],
                    source_revision_id,
                    "",
                )
                store.connection.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_dependencies_v1(
                        dependency_id, compilation_run_id, consumer_kind,
                        consumer_object_id, consumer_revision_id,
                        source_revision_id, fragment_id, dependency_kind,
                        freshness, reason, recorded_at, updated_at
                    ) VALUES (
                        ?, ?, 'knowledge_revision', ?, ?, ?, NULL,
                        'direct', 'fresh', 'synthesis_input_set', ?, ?
                    )
                    """,
                    (
                        dependency_id,
                        compilation_run_id,
                        value["knowledge_id"],
                        value["revision_id"],
                        source_revision_id,
                        value["recorded_at"],
                        value["recorded_at"],
                    ),
                )
            store.connection.execute(
                """
                INSERT INTO synthesis_input_sets_v1(
                    synthesis_revision_id, source_revision_ids_json,
                    knowledge_revision_ids_json, relation_revision_ids_json,
                    compilation_run_ids_json, input_set_sha256, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    value["revision_id"],
                    canonical_json(synthesis_inputs["source_revision_ids"]),
                    canonical_json(synthesis_inputs["knowledge_revision_ids"]),
                    canonical_json(synthesis_inputs["relation_revision_ids"]),
                    canonical_json(synthesis_inputs["compilation_run_ids"]),
                    synthesis_inputs["input_set_sha256"],
                    value["recorded_at"],
                ),
            )
            input_set_sha256 = synthesis_inputs["input_set_sha256"]
            for input_kind, field in (
                ("knowledge_revision", "knowledge_revision_ids"),
                ("relation_revision", "relation_revision_ids"),
                ("compilation_run", "compilation_run_ids"),
            ):
                for input_id in synthesis_inputs[field]:
                    cls._insert_revision_dependency(
                        store,
                        consumer_kind="knowledge_revision",
                        consumer_object_id=value["knowledge_id"],
                        consumer_revision_id=value["revision_id"],
                        input_kind=input_kind,
                        input_id=input_id,
                        input_set_sha256=input_set_sha256,
                        recorded_at=value["recorded_at"],
                    )
        else:
            input_set_sha256 = sha256_bytes(
                canonical_json(
                    {"compilation_run_ids": [compilation_run_id]}
                ).encode("utf-8")
            )
            cls._insert_revision_dependency(
                store,
                consumer_kind="knowledge_revision",
                consumer_object_id=value["knowledge_id"],
                consumer_revision_id=value["revision_id"],
                input_kind="compilation_run",
                input_id=compilation_run_id,
                input_set_sha256=input_set_sha256,
                recorded_at=value["recorded_at"],
            )

    @classmethod
    def _commit_relation_revision_dependencies(
        cls,
        store: AutonomousKnowledgeStore,
        *,
        compilation_run_id: str,
        value: dict[str, Any],
    ) -> None:
        input_revision_ids: list[str] = []
        for knowledge_id in (
            value["subject_knowledge_id"],
            value["object_knowledge_id"],
        ):
            row = store.connection.execute(
                """
                SELECT current_revision_id FROM knowledge_objects_v3
                WHERE knowledge_id = ?
                """,
                (knowledge_id,),
            ).fetchone()
            if row is None or row["current_revision_id"] is None:
                raise RuntimeError("compiled relation input revision is unavailable")
            input_revision_ids.append(row["current_revision_id"])
        input_revision_ids = sorted(set(input_revision_ids))
        input_set_sha256 = sha256_bytes(
            canonical_json(
                {
                    "knowledge_revision_ids": input_revision_ids,
                    "compilation_run_ids": [compilation_run_id],
                }
            ).encode("utf-8")
        )
        for input_id in input_revision_ids:
            cls._insert_revision_dependency(
                store,
                consumer_kind="relation_revision",
                consumer_object_id=value["relation_key"],
                consumer_revision_id=value["relation_revision_id"],
                input_kind="knowledge_revision",
                input_id=input_id,
                input_set_sha256=input_set_sha256,
                recorded_at=value["recorded_at"],
            )
        cls._insert_revision_dependency(
            store,
            consumer_kind="relation_revision",
            consumer_object_id=value["relation_key"],
            consumer_revision_id=value["relation_revision_id"],
            input_kind="compilation_run",
            input_id=compilation_run_id,
            input_set_sha256=input_set_sha256,
            recorded_at=value["recorded_at"],
        )

    @staticmethod
    def _insert_revision_dependency(
        store: AutonomousKnowledgeStore,
        *,
        consumer_kind: str,
        consumer_object_id: str,
        consumer_revision_id: str,
        input_kind: str,
        input_id: str,
        input_set_sha256: str,
        recorded_at: str,
    ) -> None:
        dependency_id = stable_id(
            "revisiondependency",
            consumer_kind,
            consumer_revision_id,
            input_kind,
            input_id,
        )
        store.connection.execute(
            """
            INSERT INTO revision_dependencies_v1(
                dependency_id, consumer_kind, consumer_object_id,
                consumer_revision_id, input_kind, input_id,
                input_set_sha256, freshness, reason, recorded_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'fresh',
                      'source_compilation_commit', ?, ?)
            """,
            (
                dependency_id,
                consumer_kind,
                consumer_object_id,
                consumer_revision_id,
                input_kind,
                input_id,
                input_set_sha256,
                recorded_at,
                recorded_at,
            ),
        )

    @staticmethod
    def _finish_materialization(
        store: AutonomousKnowledgeStore,
        *,
        compilation_run_id: str,
        revision_ids: list[str],
    ) -> None:
        try:
            for revision_id in revision_ids:
                store._materialize_pending(revision_id)
        except BaseException as error:
            failure_sha256 = sha256_bytes(f"{type(error).__name__}:{error}".encode())
            failed_at = store._next_transaction_time()
            store.connection.execute(
                """
                UPDATE source_compilation_runs_v1
                SET status = 'projection_pending', resumable = 1,
                    failure_stage = 'materialization',
                    failure_sha256 = ?, updated_at = ?
                WHERE compilation_run_id = ?
                """,
                (failure_sha256, failed_at, compilation_run_id),
            )
            store.connection.commit()
            raise
        materialized_at = store._next_transaction_time()
        store.connection.execute(
            """
            UPDATE source_compilation_runs_v1
            SET status = 'projection_pending', resumable = 1,
                failure_stage = NULL, failure_sha256 = NULL,
                updated_at = ?
            WHERE compilation_run_id = ?
              AND status IN ('committed', 'projection_pending')
            """,
            (materialized_at, compilation_run_id),
        )
        store.connection.commit()

    def status(self, compilation_run_id: str) -> dict[str, Any]:
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            run = self._run(store, compilation_run_id)
            return self._run_response(store, run, idempotent_replay=False)

    def explain(self, compilation_run_id: str) -> dict[str, Any]:
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            run = self._run(store, compilation_run_id)
            packets = [
                {
                    "packet_id": row["packet_id"],
                    "ordinal": row["ordinal"],
                    "fragment_count": row["fragment_count"],
                    "state": row["state"],
                    "plan_sha256": row["plan_sha256"],
                }
                for row in store.connection.execute(
                    """
                    SELECT packet_id, ordinal, fragment_count, state, plan_sha256
                    FROM source_compilation_packets_v1
                    WHERE compilation_run_id = ?
                    ORDER BY ordinal
                    """,
                    (compilation_run_id,),
                )
            ]
            counts = {
                "staged_objects": store.connection.execute(
                    """
                    SELECT COUNT(*) FROM source_compilation_staged_objects_v1
                    WHERE compilation_run_id = ?
                    """,
                    (compilation_run_id,),
                ).fetchone()[0],
                "staged_relations": store.connection.execute(
                    """
                    SELECT COUNT(*) FROM source_compilation_staged_relations_v1
                    WHERE compilation_run_id = ?
                    """,
                    (compilation_run_id,),
                ).fetchone()[0],
                "outputs": store.connection.execute(
                    """
                    SELECT COUNT(*) FROM source_compilation_outputs_v1
                    WHERE compilation_run_id = ?
                    """,
                    (compilation_run_id,),
                ).fetchone()[0],
                "dependencies": store.connection.execute(
                    """
                    SELECT COUNT(*) FROM knowledge_dependencies_v1
                    WHERE compilation_run_id = ?
                    """,
                    (compilation_run_id,),
                ).fetchone()[0],
            }
            result = {
                "schema_version": "deeplaw.source-compilation-explanation/v1",
                "run": self._run_response(store, run, idempotent_replay=False),
                "packets": packets,
                "counts": counts,
                "canonical_visible": run["status"]
                in {"committed", "projection_pending", "succeeded"},
                "projection_required": run["status"]
                in {"committed", "projection_pending"},
                "audit_head": store.audit_head,
            }
            _validate_contract("source-compilation-explanation.v1.schema.json", result)
            return result

    def refresh(
        self,
        *,
        grant_id: str,
        source_revision_id: str,
        replacement_source_revision_id: str | None = None,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        from .freshness import FreshnessService

        return FreshnessService(self.root).refresh(
            grant_id=grant_id,
            source_revision_id=source_revision_id,
            replacement_source_revision_id=replacement_source_revision_id,
            confirm_no_case_data=confirm_no_case_data,
        )

    def abort(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        reason: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "source compilation requires confirmation that no case data is present"
            )
        reason = _bounded(reason, field="compilation abort reason", maximum=2000)
        request = {
            "operation": "abort_compilation",
            "compilation_run_id": compilation_run_id,
            "reason_sha256": sha256_bytes(reason.encode("utf-8")),
        }
        request_bytes = _canonical_bytes(request)
        request_sha256 = sha256_bytes(request_bytes)
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            run = self._run(store, compilation_run_id)
            if run["grant_id"] != grant_id:
                raise PermissionError("source compilation run is bound to another grant")
            store._grant(
                grant_id,
                operation="abort_compilation",
                request_bytes=len(request_bytes),
            )
            if run["status"] == "aborted":
                response = {
                    "schema_version": "deeplaw.source-compilation-abort/v1",
                    "compilation_run_id": compilation_run_id,
                    "status": "aborted",
                    "idempotent_replay": True,
                }
                _validate_contract("source-compilation-abort.v1.schema.json", response)
                return response
            if run["status"] in {"committed", "projection_pending", "succeeded"}:
                raise RuntimeError("committed source compilation cannot be aborted")
            aborted_at = store._next_transaction_time(strictly_after_event=True)
            with store._file_lease("canonical-mutation"):
                try:
                    store.connection.execute("BEGIN IMMEDIATE")
                    locked = self._run(store, compilation_run_id)
                    if locked["status"] in {
                        "committed",
                        "projection_pending",
                        "succeeded",
                    }:
                        raise RuntimeError("committed source compilation cannot be aborted")
                    store._append_event(
                        event_type="source_compilation_aborted",
                        object_id=compilation_run_id,
                        payload={
                            "source_revision_id": locked["source_revision_id"],
                            "grant_id": grant_id,
                            "reason_sha256": request["reason_sha256"],
                        },
                        recorded_at=aborted_at,
                    )
                    store.connection.execute(
                        """
                        UPDATE source_compilation_runs_v1
                        SET status = 'aborted', resumable = 0,
                            failure_stage = 'aborted',
                            failure_sha256 = ?, updated_at = ?,
                            completed_at = ?
                        WHERE compilation_run_id = ?
                        """,
                        (
                            request["reason_sha256"],
                            aborted_at,
                            aborted_at,
                            compilation_run_id,
                        ),
                    )
                    store.connection.execute(
                        """
                        UPDATE source_compilation_run_metadata_v1
                        SET elapsed_ms = ?
                        WHERE compilation_run_id = ?
                        """,
                        (
                            self._elapsed_ms(
                                created_at=locked["created_at"],
                                ended_at=aborted_at,
                            ),
                            compilation_run_id,
                        ),
                    )
                    self._record_usage(
                        store,
                        grant_id=grant_id,
                        operation="abort_compilation",
                        request_sha256=request_sha256,
                        recorded_at=aborted_at,
                        discriminator=compilation_run_id,
                    )
                    store.connection.commit()
                except BaseException:
                    store.connection.rollback()
                    raise
            response = {
                "schema_version": "deeplaw.source-compilation-abort/v1",
                "compilation_run_id": compilation_run_id,
                "status": "aborted",
                "idempotent_replay": False,
            }
            _validate_contract("source-compilation-abort.v1.schema.json", response)
            return response

    def resume(
        self,
        *,
        grant_id: str,
        compilation_run_id: str,
        confirm_no_case_data: bool,
        project: bool = False,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError(
                "source compilation requires confirmation that no case data is present"
            )
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            run = self._run(store, compilation_run_id)
            if run["grant_id"] != grant_id:
                raise PermissionError("source compilation run is bound to another grant")
            store._grant(
                grant_id,
                operation="resume_compilation",
                request_bytes=0,
            )
            if run["status"] in {"failed", "aborted"}:
                raise RuntimeError("terminal source compilation cannot be resumed")
            if run["status"] == "succeeded":
                return self._run_response(
                    store,
                    run,
                    idempotent_replay=True,
                )
            metadata = store.connection.execute(
                """
                SELECT retry_count FROM source_compilation_run_metadata_v1
                WHERE compilation_run_id = ?
                """,
                (compilation_run_id,),
            ).fetchone()
            if metadata is None:
                raise RuntimeError("source compilation run metadata is unavailable")
            resumed_at = store._next_transaction_time()
            next_retry_count = metadata["retry_count"] + 1
            store.connection.execute(
                """
                UPDATE source_compilation_run_metadata_v1
                SET retry_count = ?
                WHERE compilation_run_id = ?
                """,
                (next_retry_count, compilation_run_id),
            )
            self._record_usage(
                store,
                grant_id=grant_id,
                operation="resume_compilation",
                request_sha256=sha256_bytes(
                    canonical_json(
                        {
                            "compilation_run_id": compilation_run_id,
                            "project": project,
                            "retry_count": next_retry_count,
                        }
                    ).encode("utf-8")
                ),
                recorded_at=resumed_at,
                discriminator=f"{compilation_run_id}:{next_retry_count}",
            )
            store.connection.commit()
            if run["status"] == "ready_to_commit":
                pass
            elif run["status"] in {"committed", "projection_pending"}:
                receipt = _decoded_artifact(store, run["receipt_sha256"], role="receipt")
                self._finish_materialization(
                    store,
                    compilation_run_id=compilation_run_id,
                    revision_ids=receipt["knowledge_revision_ids"],
                )
                if project:
                    return self._project_and_complete(
                        store,
                        compilation_run_id=compilation_run_id,
                    )
                return self._run_response(
                    store,
                    self._run(store, compilation_run_id),
                    idempotent_replay=False,
                )
            else:
                response = self._run_response(store, run, idempotent_replay=False)
                packet = self.next_packet(compilation_run_id)
                response["next_packet"] = packet
                return response
        return self.commit(
            grant_id=grant_id,
            compilation_run_id=compilation_run_id,
            confirm_no_case_data=True,
        )

    def _project_and_complete(
        self,
        store: AutonomousKnowledgeStore,
        *,
        compilation_run_id: str,
    ) -> dict[str, Any]:
        try:
            projection = store.rebuild_derived(
                run_status_overrides={compilation_run_id: "succeeded"}
            )
        except BaseException as error:
            failed_at = store._next_transaction_time()
            store.connection.execute(
                """
                UPDATE source_compilation_runs_v1
                SET status = 'projection_pending', resumable = 1,
                    failure_stage = 'projection', failure_sha256 = ?,
                    updated_at = ?
                WHERE compilation_run_id = ?
                """,
                (
                    sha256_bytes(f"{type(error).__name__}:{error}".encode()),
                    failed_at,
                    compilation_run_id,
                ),
            )
            store.connection.commit()
            raise
        projection_receipt = self._projection_receipt(projection)
        completed_at = store._next_transaction_time()
        run = self._run(store, compilation_run_id)
        store.connection.execute(
            """
            UPDATE source_compilation_run_metadata_v1
            SET projection_manifest_sha256 = ?, elapsed_ms = ?
            WHERE compilation_run_id = ?
            """,
            (
                projection_receipt["living_wiki"]["manifest_sha256"],
                self._elapsed_ms(
                    created_at=run["created_at"],
                    ended_at=completed_at,
                ),
                compilation_run_id,
            ),
        )
        store.connection.execute(
            """
            UPDATE source_compilation_runs_v1
            SET status = 'succeeded', resumable = 0,
                failure_stage = NULL, failure_sha256 = NULL,
                updated_at = ?, completed_at = ?
            WHERE compilation_run_id = ?
            """,
            (completed_at, completed_at, compilation_run_id),
        )
        store.connection.commit()
        return {
            **self._run_response(
                store,
                self._run(store, compilation_run_id),
                idempotent_replay=False,
            ),
            "projection": projection_receipt,
        }

    @staticmethod
    def _projection_receipt(projection: dict[str, Any]) -> dict[str, Any]:
        files = projection.get("files")
        living_wiki = projection.get("living_wiki")
        if not isinstance(files, list) or not isinstance(living_wiki, dict):
            raise RuntimeError("derived projection result is invalid")
        living_files = living_wiki.get("files")
        if not isinstance(living_files, list):
            raise RuntimeError("Living Wiki projection file inventory is invalid")
        receipt = {
            "schema_version": "deeplaw.source-compilation-projection/v1",
            "derived_manifest_sha256": projection["manifest_sha256"],
            "derived_file_count": len(files),
            "derived_file_inventory_sha256": sha256_bytes(
                canonical_json(files).encode("utf-8")
            ),
            "input_audit_head": projection["input_audit_head"],
            "living_wiki": {
                "schema_version": living_wiki["schema_version"],
                "manifest_sha256": living_wiki["manifest_sha256"],
                "knowledge_count": living_wiki["knowledge_count"],
                "relation_count": living_wiki["relation_count"],
                "source_count": living_wiki["source_count"],
                "file_count": living_wiki["file_count"],
                "file_inventory_sha256": sha256_bytes(
                    canonical_json(living_files).encode("utf-8")
                ),
                "index_shard_count": living_wiki["index_shard_count"],
                "canvas_count": living_wiki["canvas_count"],
                "community_count": living_wiki["community_count"],
                "input_audit_head": living_wiki["input_audit_head"],
            },
        }
        _validate_contract("source-compilation-projection.v1.schema.json", receipt)
        return receipt
