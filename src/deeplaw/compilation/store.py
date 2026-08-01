from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any

from ..knowledge_models import canonical_timestamp
from ..util import canonical_json, sha256_file, strict_json_loads
from .models import COMPILATION_CORE_SCHEMA, SEMANTIC_COMPILATION_CORE_SCHEMA


def compilation_tables_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS source_compilation_core_v1 (
            schema_version TEXT PRIMARY KEY,
            installed_at TEXT NOT NULL,
            migration_source TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS semantic_compilation_core_v1 (
            schema_version TEXT PRIMARY KEY,
            installed_at TEXT NOT NULL,
            migration_source TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS source_compilation_runs_v1 (
            compilation_run_id TEXT PRIMARY KEY,
            source_revision_id TEXT NOT NULL
                REFERENCES source_revisions_v2(source_revision_id),
            source_key TEXT NOT NULL REFERENCES source_identities_v2(source_key),
            source_ir_compilation_id TEXT NOT NULL
                REFERENCES compilations_v2(compilation_id),
            grant_id TEXT NOT NULL REFERENCES knowledge_sink_grants_v3(grant_id),
            compiler_profile TEXT NOT NULL,
            compiler_profile_version TEXT NOT NULL,
            host_identity TEXT NOT NULL,
            model_identity TEXT,
            prompt_template_id TEXT NOT NULL,
            prompt_config_sha256 TEXT NOT NULL,
            plan_configuration_sha256 TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            input_audit_head TEXT NOT NULL,
            input_legacy_audit_head TEXT NOT NULL,
            packet_max_fragments INTEGER NOT NULL
                CHECK(packet_max_fragments BETWEEN 1 AND 128),
            packet_count INTEGER NOT NULL CHECK(packet_count BETWEEN 1 AND 10000),
            status TEXT NOT NULL CHECK(status IN (
                'planned', 'staging', 'validating', 'ready_to_commit',
                'committed', 'projection_pending', 'succeeded', 'failed', 'aborted'
            )),
            resumable INTEGER NOT NULL CHECK(resumable IN (0, 1)),
            output_set_sha256 TEXT,
            receipt_sha256 TEXT,
            failure_stage TEXT,
            failure_sha256 TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            committed_at TEXT,
            completed_at TEXT,
            UNIQUE(
                source_revision_id, source_ir_compilation_id, compiler_profile,
                compiler_profile_version, prompt_template_id,
                prompt_config_sha256, plan_configuration_sha256,
                packet_max_fragments
            )
        ) STRICT;
        CREATE INDEX IF NOT EXISTS source_compilation_runs_v1_source
            ON source_compilation_runs_v1(source_revision_id, created_at);
        CREATE INDEX IF NOT EXISTS source_compilation_runs_v1_status
            ON source_compilation_runs_v1(status, updated_at);

        CREATE TABLE IF NOT EXISTS source_compilation_run_metadata_v1 (
            compilation_run_id TEXT PRIMARY KEY
                REFERENCES source_compilation_runs_v1(compilation_run_id),
            previous_source_revision_id TEXT
                REFERENCES source_revisions_v2(source_revision_id),
            source_ir_sha256 TEXT NOT NULL,
            expected_source_status TEXT NOT NULL,
            validation_sha256 TEXT,
            canonical_commit_sha256 TEXT,
            projection_manifest_sha256 TEXT,
            token_usage_json TEXT NOT NULL,
            elapsed_ms INTEGER CHECK(elapsed_ms IS NULL OR elapsed_ms >= 0),
            retry_count INTEGER NOT NULL CHECK(retry_count >= 0)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS source_compilation_artifacts_v1 (
            artifact_sha256 TEXT PRIMARY KEY,
            artifact_role TEXT NOT NULL CHECK(artifact_role IN (
                'packet', 'plan', 'batch', 'validation', 'receipt',
                'freshness', 'query_backfill', 'mcp_result',
                'observation_plan', 'semantic_inventory', 'finalization_packet',
                'publication_plan', 'semantic_receipt', 'synthesis_packet',
                'synthesis_plan', 'synthesis_receipt'
            )),
            byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
            media_type TEXT NOT NULL,
            created_at TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS source_compilation_usage_v1 (
            operation_id TEXT PRIMARY KEY,
            grant_id TEXT NOT NULL REFERENCES knowledge_sink_grants_v3(grant_id),
            operation TEXT NOT NULL CHECK(operation IN (
                'abort_compilation', 'begin_compilation', 'commit_compilation',
                'propose_knowledge_backfill',
                'refresh_compilation', 'resume_compilation',
                'stage_compilation_batch', 'stage_semantic_observations',
                'finalize_semantic_compilation', 'freeze_semantic_inventory',
                'abort_synthesis_refresh', 'begin_synthesis_refresh',
                'commit_synthesis_refresh', 'resume_synthesis_refresh',
                'stage_synthesis_refresh', 'validate_synthesis_refresh',
                'validate_compilation'
            )),
            request_sha256 TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS source_compilation_usage_v1_rate
            ON source_compilation_usage_v1(grant_id, recorded_at);

        CREATE TABLE IF NOT EXISTS source_compilation_mcp_replays_v1 (
            grant_id TEXT NOT NULL REFERENCES knowledge_sink_grants_v3(grant_id),
            idempotency_key TEXT NOT NULL,
            operation TEXT NOT NULL CHECK(operation IN (
                'abort_compilation', 'begin_compilation', 'commit_compilation',
                'promote_knowledge_draft', 'propose_knowledge_backfill',
                'refresh_compilation', 'resume_compilation',
                'stage_compilation_batch', 'stage_semantic_observations',
                'finalize_semantic_compilation', 'freeze_semantic_inventory',
                'abort_synthesis_refresh', 'begin_synthesis_refresh',
                'commit_synthesis_refresh', 'resume_synthesis_refresh',
                'stage_synthesis_refresh', 'validate_synthesis_refresh',
                'validate_compilation'
            )),
            request_sha256 TEXT NOT NULL,
            result_sha256 TEXT NOT NULL
                REFERENCES source_compilation_artifacts_v1(artifact_sha256),
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(grant_id, idempotency_key)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS source_compilation_packets_v1 (
            packet_id TEXT PRIMARY KEY,
            compilation_run_id TEXT NOT NULL
                REFERENCES source_compilation_runs_v1(compilation_run_id),
            ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
            fragment_start_ordinal INTEGER NOT NULL CHECK(fragment_start_ordinal >= 1),
            fragment_end_ordinal INTEGER NOT NULL
                CHECK(fragment_end_ordinal >= fragment_start_ordinal),
            fragment_count INTEGER NOT NULL CHECK(fragment_count >= 1),
            artifact_sha256 TEXT NOT NULL
                REFERENCES source_compilation_artifacts_v1(artifact_sha256),
            state TEXT NOT NULL CHECK(state IN ('pending', 'staged', 'validated')),
            plan_sha256 TEXT,
            staged_at TEXT,
            validated_at TEXT,
            UNIQUE(compilation_run_id, ordinal)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS source_compilation_batches_v1 (
            batch_id TEXT PRIMARY KEY,
            compilation_run_id TEXT NOT NULL
                REFERENCES source_compilation_runs_v1(compilation_run_id),
            packet_id TEXT NOT NULL UNIQUE
                REFERENCES source_compilation_packets_v1(packet_id),
            plan_sha256 TEXT NOT NULL
                REFERENCES source_compilation_artifacts_v1(artifact_sha256),
            batch_sha256 TEXT NOT NULL
                REFERENCES source_compilation_artifacts_v1(artifact_sha256),
            object_count INTEGER NOT NULL CHECK(object_count >= 0),
            relation_count INTEGER NOT NULL CHECK(relation_count >= 0),
            identity_count INTEGER NOT NULL CHECK(identity_count >= 0),
            unresolved_identity_count INTEGER NOT NULL
                CHECK(unresolved_identity_count >= 0),
            contradiction_count INTEGER NOT NULL CHECK(contradiction_count >= 0),
            skipped_fragment_count INTEGER NOT NULL
                CHECK(skipped_fragment_count >= 0),
            warning_count INTEGER NOT NULL CHECK(warning_count >= 0),
            coverage_ratio REAL NOT NULL CHECK(coverage_ratio BETWEEN 0.0 AND 1.0),
            recorded_at TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS source_compilation_staged_objects_v1 (
            compilation_run_id TEXT NOT NULL
                REFERENCES source_compilation_runs_v1(compilation_run_id),
            action_ordinal INTEGER NOT NULL CHECK(action_ordinal >= 1),
            packet_id TEXT NOT NULL
                REFERENCES source_compilation_packets_v1(packet_id),
            requested_action TEXT NOT NULL CHECK(requested_action IN (
                'create', 'revise', 'retain', 'archive', 'propose'
            )),
            resolved_action TEXT,
            kind TEXT NOT NULL,
            semantic_key TEXT NOT NULL,
            requested_knowledge_id TEXT,
            expected_revision_id TEXT,
            resolved_knowledge_id TEXT,
            resolved_parent_revision_id TEXT,
            prepared_revision_id TEXT,
            prepared_markdown_sha256 TEXT,
            prepared_json TEXT,
            action_json TEXT NOT NULL,
            validation_state TEXT NOT NULL CHECK(validation_state IN (
                'staged', 'valid', 'invalid'
            )),
            validation_error_sha256 TEXT,
            PRIMARY KEY(compilation_run_id, action_ordinal)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS source_compilation_staged_objects_v1_identity
            ON source_compilation_staged_objects_v1(
                compilation_run_id, kind, semantic_key
            );

        CREATE TABLE IF NOT EXISTS source_compilation_staged_relations_v1 (
            compilation_run_id TEXT NOT NULL
                REFERENCES source_compilation_runs_v1(compilation_run_id),
            action_ordinal INTEGER NOT NULL CHECK(action_ordinal >= 1),
            packet_id TEXT NOT NULL
                REFERENCES source_compilation_packets_v1(packet_id),
            predicate TEXT NOT NULL,
            expected_relation_revision_id TEXT,
            resolved_relation_key TEXT,
            resolved_parent_revision_id TEXT,
            prepared_relation_revision_id TEXT,
            prepared_json TEXT,
            action_json TEXT NOT NULL,
            validation_state TEXT NOT NULL CHECK(validation_state IN (
                'staged', 'valid', 'invalid'
            )),
            validation_error_sha256 TEXT,
            PRIMARY KEY(compilation_run_id, action_ordinal)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS source_compilation_identity_candidates_v1 (
            compilation_run_id TEXT NOT NULL
                REFERENCES source_compilation_runs_v1(compilation_run_id),
            packet_id TEXT NOT NULL
                REFERENCES source_compilation_packets_v1(packet_id),
            candidate_ordinal INTEGER NOT NULL CHECK(candidate_ordinal >= 1),
            candidate_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'proposed', 'ambiguous', 'resolved', 'rejected'
            )),
            PRIMARY KEY(compilation_run_id, packet_id, candidate_ordinal)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS source_compilation_outputs_v1 (
            compilation_run_id TEXT NOT NULL
                REFERENCES source_compilation_runs_v1(compilation_run_id),
            output_kind TEXT NOT NULL CHECK(output_kind IN (
                'knowledge_revision', 'relation_revision', 'identity_resolution'
            )),
            output_id TEXT NOT NULL,
            object_id TEXT NOT NULL,
            packet_id TEXT NOT NULL
                REFERENCES source_compilation_packets_v1(packet_id),
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(compilation_run_id, output_kind, output_id)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS knowledge_dependencies_v1 (
            dependency_id TEXT PRIMARY KEY,
            compilation_run_id TEXT NOT NULL
                REFERENCES source_compilation_runs_v1(compilation_run_id),
            consumer_kind TEXT NOT NULL CHECK(consumer_kind IN (
                'knowledge_revision', 'relation_revision'
            )),
            consumer_object_id TEXT NOT NULL,
            consumer_revision_id TEXT NOT NULL,
            source_revision_id TEXT NOT NULL
                REFERENCES source_revisions_v2(source_revision_id),
            fragment_id TEXT,
            dependency_kind TEXT NOT NULL CHECK(dependency_kind IN ('direct', 'transitive')),
            freshness TEXT NOT NULL CHECK(freshness IN (
                'fresh', 'stale', 'invalidated', 'unknown'
            )),
            reason TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(
                consumer_kind, consumer_revision_id, source_revision_id,
                fragment_id, dependency_kind
            )
        ) STRICT;
        CREATE INDEX IF NOT EXISTS knowledge_dependencies_v1_source
            ON knowledge_dependencies_v1(source_revision_id, freshness);
        CREATE INDEX IF NOT EXISTS knowledge_dependencies_v1_consumer
            ON knowledge_dependencies_v1(consumer_object_id, freshness);

        CREATE TABLE IF NOT EXISTS revision_dependencies_v1 (
            dependency_id TEXT PRIMARY KEY,
            consumer_kind TEXT NOT NULL CHECK(consumer_kind IN (
                'knowledge_revision', 'relation_revision'
            )),
            consumer_object_id TEXT NOT NULL,
            consumer_revision_id TEXT NOT NULL,
            input_kind TEXT NOT NULL CHECK(input_kind IN (
                'knowledge_revision', 'relation_revision', 'compilation_run'
            )),
            input_id TEXT NOT NULL,
            input_set_sha256 TEXT NOT NULL,
            freshness TEXT NOT NULL CHECK(freshness IN (
                'fresh', 'stale', 'invalidated', 'unknown'
            )),
            reason TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(consumer_revision_id, input_kind, input_id)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS revision_dependencies_v1_input
            ON revision_dependencies_v1(input_kind, input_id, freshness);
        CREATE INDEX IF NOT EXISTS revision_dependencies_v1_consumer
            ON revision_dependencies_v1(consumer_revision_id, freshness);

        CREATE TABLE IF NOT EXISTS synthesis_input_sets_v1 (
            synthesis_revision_id TEXT PRIMARY KEY
                REFERENCES knowledge_revisions_v3(revision_id),
            source_revision_ids_json TEXT NOT NULL,
            knowledge_revision_ids_json TEXT NOT NULL,
            relation_revision_ids_json TEXT NOT NULL,
            compilation_run_ids_json TEXT NOT NULL,
            input_set_sha256 TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS source_freshness_events_v1 (
            freshness_event_id TEXT PRIMARY KEY,
            target_kind TEXT NOT NULL CHECK(target_kind IN (
                'source_revision', 'knowledge_revision', 'relation_revision'
            )),
            target_id TEXT NOT NULL,
            previous_freshness TEXT,
            freshness TEXT NOT NULL CHECK(freshness IN (
                'fresh', 'stale', 'invalidated', 'unknown'
            )),
            reason TEXT NOT NULL,
            source_revision_id TEXT NOT NULL
                REFERENCES source_revisions_v2(source_revision_id),
            replacement_source_revision_id TEXT
                REFERENCES source_revisions_v2(source_revision_id),
            report_sha256 TEXT,
            recorded_at TEXT NOT NULL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS source_freshness_events_v1_target
            ON source_freshness_events_v1(target_kind, target_id, recorded_at);

        CREATE TABLE IF NOT EXISTS query_backfill_drafts_v1 (
            draft_id TEXT PRIMARY KEY,
            grant_id TEXT NOT NULL REFERENCES knowledge_sink_grants_v3(grant_id),
            idempotency_key TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            query_sha256 TEXT NOT NULL,
            draft_sha256 TEXT NOT NULL
                REFERENCES source_compilation_artifacts_v1(artifact_sha256),
            workspace_path TEXT NOT NULL,
            workspace_sha256 TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'proposed', 'validated', 'promoted', 'rejected'
            )),
            validation_sha256 TEXT,
            promoted_revision_id TEXT,
            promotion_receipt_sha256 TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(grant_id, idempotency_key)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS semantic_compilation_runs_v2 (
            compilation_run_id TEXT PRIMARY KEY
                REFERENCES source_compilation_runs_v1(compilation_run_id),
            semantic_status TEXT NOT NULL CHECK(semantic_status IN (
                'complete', 'partial', 'blocked', 'unknown'
            )),
            observation_packet_count INTEGER NOT NULL CHECK(observation_packet_count >= 0),
            observed_packet_count INTEGER NOT NULL CHECK(observed_packet_count >= 0),
            observation_count INTEGER NOT NULL CHECK(observation_count >= 0),
            inventory_sha256 TEXT,
            publication_plan_sha256 TEXT,
            quality_receipt_sha256 TEXT,
            source_summary_revision_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS semantic_observation_batches_v2 (
            compilation_run_id TEXT NOT NULL
                REFERENCES semantic_compilation_runs_v2(compilation_run_id),
            packet_id TEXT NOT NULL REFERENCES source_compilation_packets_v1(packet_id),
            observation_plan_sha256 TEXT NOT NULL
                REFERENCES source_compilation_artifacts_v1(artifact_sha256),
            observation_count INTEGER NOT NULL CHECK(observation_count >= 0),
            covered_fragment_ids_json TEXT NOT NULL,
            omitted_fragments_json TEXT NOT NULL,
            coverage_ratio REAL NOT NULL CHECK(coverage_ratio BETWEEN 0.0 AND 1.0),
            warnings_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(compilation_run_id, packet_id)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS semantic_observations_v2 (
            observation_id TEXT PRIMARY KEY,
            compilation_run_id TEXT NOT NULL
                REFERENCES semantic_compilation_runs_v2(compilation_run_id),
            packet_id TEXT NOT NULL REFERENCES source_compilation_packets_v1(packet_id),
            semantic_key_candidate TEXT,
            kind TEXT NOT NULL,
            normalized_aliases_json TEXT NOT NULL,
            source_refs_json TEXT NOT NULL,
            observation_json TEXT NOT NULL,
            observation_sha256 TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            UNIQUE(compilation_run_id, observation_sha256)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS semantic_observations_v2_run_key
            ON semantic_observations_v2(compilation_run_id, kind, semantic_key_candidate);

        CREATE TABLE IF NOT EXISTS semantic_inventories_v1 (
            artifact_sha256 TEXT PRIMARY KEY
                REFERENCES source_compilation_artifacts_v1(artifact_sha256),
            inventory_sha256 TEXT NOT NULL UNIQUE,
            inventory_id TEXT NOT NULL UNIQUE,
            compilation_run_id TEXT NOT NULL UNIQUE
                REFERENCES semantic_compilation_runs_v2(compilation_run_id),
            observation_count INTEGER NOT NULL CHECK(observation_count >= 0),
            packet_count INTEGER NOT NULL CHECK(packet_count >= 1),
            truncated INTEGER NOT NULL CHECK(truncated IN (0, 1)),
            recorded_at TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS semantic_observation_dispositions_v1 (
            compilation_run_id TEXT NOT NULL
                REFERENCES semantic_compilation_runs_v2(compilation_run_id),
            observation_id TEXT NOT NULL REFERENCES semantic_observations_v2(observation_id),
            disposition TEXT NOT NULL CHECK(disposition IN (
                'published', 'merged_into', 'retained_existing', 'proposed_only',
                'omitted_with_reason', 'unresolved'
            )),
            target_ref TEXT,
            reason TEXT NOT NULL,
            PRIMARY KEY(compilation_run_id, observation_id)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS semantic_duty_reports_v1 (
            compilation_run_id TEXT NOT NULL
                REFERENCES semantic_compilation_runs_v2(compilation_run_id),
            duty_id TEXT NOT NULL,
            duty_type TEXT NOT NULL,
            required INTEGER NOT NULL CHECK(required IN (0, 1)),
            status TEXT NOT NULL CHECK(status IN (
                'satisfied', 'not_applicable', 'unresolved', 'omitted_with_reason'
            )),
            report_json TEXT NOT NULL,
            PRIMARY KEY(compilation_run_id, duty_type),
            UNIQUE(compilation_run_id, duty_id)
        ) STRICT;

        CREATE TABLE IF NOT EXISTS semantic_quality_receipts_v1 (
            artifact_sha256 TEXT PRIMARY KEY
                REFERENCES source_compilation_artifacts_v1(artifact_sha256),
            receipt_sha256 TEXT NOT NULL UNIQUE,
            compilation_run_id TEXT NOT NULL UNIQUE
                REFERENCES semantic_compilation_runs_v2(compilation_run_id),
            recorded_at TEXT NOT NULL
        ) STRICT;

        CREATE TABLE IF NOT EXISTS synthesis_refresh_tasks_v1 (
            refresh_task_id TEXT PRIMARY KEY,
            target_knowledge_id TEXT NOT NULL
                REFERENCES knowledge_objects_v3(knowledge_id),
            target_revision_id TEXT NOT NULL
                REFERENCES knowledge_revisions_v3(revision_id),
            input_set_sha256 TEXT NOT NULL,
            triggering_freshness_event_ids_json TEXT NOT NULL,
            source_revision_ids_json TEXT NOT NULL,
            knowledge_revision_ids_json TEXT NOT NULL,
            relation_revision_ids_json TEXT NOT NULL,
            compilation_run_ids_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN (
                'planned', 'started', 'completed', 'superseded', 'blocked'
            )),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(target_revision_id, input_set_sha256)
        ) STRICT;
        CREATE INDEX IF NOT EXISTS synthesis_refresh_tasks_v1_status
            ON synthesis_refresh_tasks_v1(status, created_at);

        CREATE TABLE IF NOT EXISTS synthesis_refresh_runs_v1 (
            synthesis_refresh_run_id TEXT PRIMARY KEY,
            refresh_task_id TEXT NOT NULL UNIQUE
                REFERENCES synthesis_refresh_tasks_v1(refresh_task_id),
            compilation_run_id TEXT NOT NULL UNIQUE
                REFERENCES source_compilation_runs_v1(compilation_run_id),
            target_semantic_key TEXT NOT NULL,
            target_knowledge_id TEXT NOT NULL
                REFERENCES knowledge_objects_v3(knowledge_id),
            expected_revision_id TEXT NOT NULL
                REFERENCES knowledge_revisions_v3(revision_id),
            input_set_sha256 TEXT NOT NULL,
            source_revision_ids_json TEXT NOT NULL,
            knowledge_revision_ids_json TEXT NOT NULL,
            relation_revision_ids_json TEXT NOT NULL,
            compilation_run_ids_json TEXT NOT NULL,
            host_identity TEXT NOT NULL,
            model_identity TEXT,
            profile_id TEXT NOT NULL,
            prompt_sha256 TEXT NOT NULL,
            config_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        ) STRICT;
    """


def _upgrade_extended_compilation_constraints(connection: sqlite3.Connection) -> None:
    """Expand frozen v0.11 CHECK domains without losing existing rows or FKs."""
    required = {
        "source_compilation_artifacts_v1": "semantic_receipt",
        "source_compilation_usage_v1": "freeze_semantic_inventory",
        "source_compilation_mcp_replays_v1": "abort_synthesis_refresh",
    }
    definitions = {
        "source_compilation_artifacts_v1": """
            CREATE TABLE _source_compilation_artifacts_v1_next (
                artifact_sha256 TEXT PRIMARY KEY,
                artifact_role TEXT NOT NULL CHECK(artifact_role IN (
                    'packet', 'plan', 'batch', 'validation', 'receipt',
                    'freshness', 'query_backfill', 'mcp_result',
                    'observation_plan', 'semantic_inventory', 'finalization_packet',
                    'publication_plan', 'semantic_receipt', 'synthesis_packet',
                    'synthesis_plan', 'synthesis_receipt'
                )),
                byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
                media_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            ) STRICT
        """,
        "source_compilation_usage_v1": """
            CREATE TABLE _source_compilation_usage_v1_next (
                operation_id TEXT PRIMARY KEY,
                grant_id TEXT NOT NULL REFERENCES knowledge_sink_grants_v3(grant_id),
                operation TEXT NOT NULL CHECK(operation IN (
                    'abort_compilation', 'begin_compilation', 'commit_compilation',
                    'propose_knowledge_backfill',
                    'refresh_compilation', 'resume_compilation',
                    'stage_compilation_batch', 'stage_semantic_observations',
                    'finalize_semantic_compilation', 'freeze_semantic_inventory',
                    'abort_synthesis_refresh', 'begin_synthesis_refresh',
                    'commit_synthesis_refresh', 'resume_synthesis_refresh',
                    'stage_synthesis_refresh', 'validate_synthesis_refresh',
                    'validate_compilation'
                )),
                request_sha256 TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            ) STRICT
        """,
        "source_compilation_mcp_replays_v1": """
            CREATE TABLE _source_compilation_mcp_replays_v1_next (
                grant_id TEXT NOT NULL REFERENCES knowledge_sink_grants_v3(grant_id),
                idempotency_key TEXT NOT NULL,
                operation TEXT NOT NULL CHECK(operation IN (
                    'abort_compilation', 'begin_compilation', 'commit_compilation',
                    'promote_knowledge_draft', 'propose_knowledge_backfill',
                    'refresh_compilation', 'resume_compilation',
                    'stage_compilation_batch', 'stage_semantic_observations',
                    'finalize_semantic_compilation', 'freeze_semantic_inventory',
                    'abort_synthesis_refresh', 'begin_synthesis_refresh',
                    'commit_synthesis_refresh', 'resume_synthesis_refresh',
                    'stage_synthesis_refresh', 'validate_synthesis_refresh',
                    'validate_compilation'
                )),
                request_sha256 TEXT NOT NULL,
                result_sha256 TEXT NOT NULL
                    REFERENCES source_compilation_artifacts_v1(artifact_sha256),
                recorded_at TEXT NOT NULL,
                PRIMARY KEY(grant_id, idempotency_key)
            ) STRICT
        """,
    }
    selected = []
    for table, marker in required.items():
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"source compilation table is unavailable: {table}")
        if marker not in row[0]:
            selected.append(table)
    if not selected:
        return
    if connection.in_transaction:
        connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        for table in selected:
            temporary = f"_{table}_next"
            connection.execute(f"DROP TABLE IF EXISTS {temporary}")
            connection.execute(definitions[table])
            columns = {
                "source_compilation_artifacts_v1": (
                    "artifact_sha256, artifact_role, byte_size, media_type, created_at"
                ),
                "source_compilation_usage_v1": (
                    "operation_id, grant_id, operation, request_sha256, recorded_at"
                ),
                "source_compilation_mcp_replays_v1": (
                    "grant_id, idempotency_key, operation, request_sha256, "
                    "result_sha256, recorded_at"
                ),
            }[table]
            connection.execute(
                f"INSERT INTO {temporary}({columns}) SELECT {columns} FROM {table}"
            )
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE {temporary} RENAME TO {table}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS source_compilation_usage_v1_rate "
            "ON source_compilation_usage_v1(grant_id, recorded_at)"
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
    failure = connection.execute("PRAGMA foreign_key_check").fetchone()
    if failure is not None:
        raise RuntimeError("source compilation constraint migration broke a foreign key")


def install_compilation_schema(
    connection: sqlite3.Connection,
    *,
    installed_at: str,
    migration_source: str,
) -> None:
    canonical_timestamp(installed_at, field="source compilation installed_at")
    connection.executescript(compilation_tables_sql())
    _upgrade_extended_compilation_constraints(connection)
    connection.execute(
        """
        INSERT OR IGNORE INTO source_compilation_core_v1(
            schema_version, installed_at, migration_source
        ) VALUES (?, ?, ?)
        """,
        (COMPILATION_CORE_SCHEMA, installed_at, migration_source),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO semantic_compilation_core_v1(
            schema_version, installed_at, migration_source
        ) VALUES (?, ?, ?)
        """,
        (SEMANTIC_COMPILATION_CORE_SCHEMA, installed_at, migration_source),
    )
    row = connection.execute("SELECT * FROM source_compilation_core_v1").fetchone()
    if row is None or row["schema_version"] != COMPILATION_CORE_SCHEMA:
        raise RuntimeError("source compilation schema is unavailable")
    canonical_timestamp(row["installed_at"], field="source compilation installed_at")
    semantic_row = connection.execute(
        "SELECT * FROM semantic_compilation_core_v1"
    ).fetchone()
    if (
        semantic_row is None
        or semantic_row["schema_version"] != SEMANTIC_COMPILATION_CORE_SCHEMA
    ):
        raise RuntimeError("semantic compilation schema is unavailable")
    canonical_timestamp(
        semantic_row["installed_at"], field="semantic compilation installed_at"
    )
    connection.commit()


def verify_compilation_schema(
    connection: sqlite3.Connection,
    *,
    root: Path,
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    row = connection.execute(
        "SELECT schema_version, installed_at FROM source_compilation_core_v1"
    ).fetchone()
    if row is None or row["schema_version"] != COMPILATION_CORE_SCHEMA:
        return [{"code": "source_compilation_schema_invalid", "object_id": "core"}]
    try:
        canonical_timestamp(row["installed_at"], field="source compilation installed_at")
    except (TypeError, ValueError):
        failures.append({"code": "source_compilation_schema_invalid", "object_id": "core"})
    semantic_row = connection.execute(
        "SELECT schema_version, installed_at FROM semantic_compilation_core_v1"
    ).fetchone()
    if (
        semantic_row is None
        or semantic_row["schema_version"] != SEMANTIC_COMPILATION_CORE_SCHEMA
    ):
        failures.append({"code": "semantic_compilation_schema_invalid", "object_id": "core"})
    else:
        try:
            canonical_timestamp(
                semantic_row["installed_at"],
                field="semantic compilation installed_at",
            )
        except (TypeError, ValueError):
            failures.append(
                {"code": "semantic_compilation_schema_invalid", "object_id": "core"}
            )
    for artifact in connection.execute(
        """
        SELECT artifact_sha256, byte_size
        FROM source_compilation_artifacts_v1
        ORDER BY artifact_sha256
        """
    ):
        digest = artifact["artifact_sha256"]
        path = root / ".deeplaw" / "objects" / "sha256" / digest[:2] / digest[2:]
        if (
            len(digest) != 64
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != artifact["byte_size"]
            or sha256_file(path) != digest
        ):
            failures.append(
                {
                    "code": "source_compilation_artifact_invalid",
                    "object_id": digest,
                }
            )
    for replay in connection.execute(
        """
        SELECT grant_id, idempotency_key, operation, request_sha256,
               result_sha256, recorded_at
        FROM source_compilation_mcp_replays_v1
        ORDER BY grant_id, idempotency_key
        """
    ):
        try:
            if len(replay["request_sha256"]) != 64:
                raise ValueError("source compilation MCP request digest is invalid")
            bytes.fromhex(replay["request_sha256"])
            if (
                not replay["idempotency_key"]
                or len(replay["idempotency_key"]) > 200
                or canonical_timestamp(
                    replay["recorded_at"],
                    field="source compilation MCP replay time",
                )
                != replay["recorded_at"]
            ):
                raise ValueError("source compilation MCP replay binding is invalid")
            artifact = connection.execute(
                """
                SELECT artifact_role FROM source_compilation_artifacts_v1
                WHERE artifact_sha256 = ?
                """,
                (replay["result_sha256"],),
            ).fetchone()
            if artifact is None or artifact["artifact_role"] != "mcp_result":
                raise ValueError("source compilation MCP replay artifact is invalid")
            digest = replay["result_sha256"]
            result_path = (
                root / ".deeplaw" / "objects" / "sha256" / digest[:2] / digest[2:]
            )
            if result_path.is_symlink() or not result_path.is_file():
                raise ValueError("source compilation MCP replay path is unsafe")
            value = strict_json_loads(result_path.read_bytes())
            if (
                not isinstance(value, dict)
                or set(value) != {"schema_version", "operation", "result"}
                or value["schema_version"]
                != "deeplaw.source-compilation-mcp-result/v1"
                or value["operation"] != replay["operation"]
                or not isinstance(value["result"], dict)
            ):
                raise ValueError("source compilation MCP replay result is invalid")
        except (OSError, TypeError, ValueError):
            failures.append(
                {
                    "code": "source_compilation_mcp_replay_invalid",
                    "object_id": hashlib.sha256(
                        (
                            f"{replay['grant_id']}:"
                            f"{replay['idempotency_key']}"
                        ).encode()
                    ).hexdigest(),
                }
            )
    for run in connection.execute(
        """
        SELECT compilation_run_id, packet_count, status, output_set_sha256,
               receipt_sha256
        FROM source_compilation_runs_v1
        """
    ):
        packet_count = connection.execute(
            """
            SELECT COUNT(*) FROM source_compilation_packets_v1
            WHERE compilation_run_id = ?
            """,
            (run["compilation_run_id"],),
        ).fetchone()[0]
        outputs = [
            dict(item)
            for item in connection.execute(
                """
                SELECT output_kind, output_id, object_id, packet_id
                FROM source_compilation_outputs_v1
                WHERE compilation_run_id = ?
                ORDER BY output_kind, output_id
                """,
                (run["compilation_run_id"],),
            )
        ]
        output_digest = hashlib.sha256(
            canonical_json(outputs).encode("utf-8")
        ).hexdigest()
        if packet_count != run["packet_count"] or (
            run["status"] in {"committed", "projection_pending", "succeeded"}
            and (run["receipt_sha256"] is None or run["output_set_sha256"] != output_digest)
        ):
            failures.append(
                {
                    "code": "source_compilation_run_invalid",
                    "object_id": run["compilation_run_id"],
                }
            )
        if run["status"] in {"committed", "projection_pending", "succeeded"}:
            event = connection.execute(
                """
                SELECT payload_json FROM autonomous_events_v3
                WHERE event_type = 'source_compilation_committed'
                  AND object_id = ?
                """,
                (run["compilation_run_id"],),
            ).fetchone()
            try:
                payload = (
                    strict_json_loads(event["payload_json"])
                    if event is not None
                    else None
                )
                if (
                    not isinstance(payload, dict)
                    or payload.get("output_set_sha256") != run["output_set_sha256"]
                ):
                    raise ValueError("compilation event binding is invalid")
            except (TypeError, ValueError):
                failures.append(
                    {
                        "code": "source_compilation_event_invalid",
                        "object_id": run["compilation_run_id"],
                    }
                )
        metadata = connection.execute(
            """
            SELECT * FROM source_compilation_run_metadata_v1
            WHERE compilation_run_id = ?
            """,
            (run["compilation_run_id"],),
        ).fetchone()
        try:
            if metadata is None:
                raise ValueError("run metadata is missing")
            source_ir = connection.execute(
                """
                SELECT compilation_id, adapter, adapter_version,
                       configuration_sha256, source_ir_schema,
                       fragment_inventory_sha256
                FROM compilations_v2
                JOIN source_compilation_runs_v1
                  ON source_compilation_runs_v1.source_ir_compilation_id =
                     compilations_v2.compilation_id
                WHERE source_compilation_runs_v1.compilation_run_id = ?
                """,
                (run["compilation_run_id"],),
            ).fetchone()
            if source_ir is None:
                raise ValueError("run Source IR is missing")
            expected_source_ir_sha256 = hashlib.sha256(
                canonical_json(dict(source_ir)).encode("utf-8")
            ).hexdigest()
            token_usage = strict_json_loads(metadata["token_usage_json"])
            if (
                metadata["source_ir_sha256"] != expected_source_ir_sha256
                or not isinstance(token_usage, dict)
                or set(token_usage)
                != {
                    "status",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                }
                or token_usage["status"] not in {"reported", "unreported"}
                or (
                    run["status"]
                    in {
                        "ready_to_commit",
                        "committed",
                        "projection_pending",
                        "succeeded",
                    }
                    and metadata["validation_sha256"] is None
                )
                or (
                    run["status"] in {"committed", "projection_pending", "succeeded"}
                    and metadata["canonical_commit_sha256"] is None
                )
                or (
                    run["status"] == "succeeded"
                    and (
                        metadata["projection_manifest_sha256"] is None
                        or metadata["elapsed_ms"] is None
                    )
                )
            ):
                raise ValueError("run metadata binding is invalid")
            for digest, role in (
                (metadata["validation_sha256"], "validation"),
            ):
                if digest is not None and connection.execute(
                    """
                    SELECT 1 FROM source_compilation_artifacts_v1
                    WHERE artifact_sha256 = ? AND artifact_role = ?
                    """,
                    (digest, role),
                ).fetchone() is None:
                    raise ValueError("run metadata artifact is missing")
        except (TypeError, ValueError):
            failures.append(
                {
                    "code": "source_compilation_run_metadata_invalid",
                    "object_id": run["compilation_run_id"],
                }
            )
    for row in connection.execute(
        """
        SELECT prepared_json FROM source_compilation_staged_objects_v1
        WHERE prepared_json IS NOT NULL
        UNION ALL
        SELECT prepared_json FROM source_compilation_staged_relations_v1
        WHERE prepared_json IS NOT NULL
        """
    ):
        try:
            value: Any = strict_json_loads(row["prepared_json"])
            if not isinstance(value, dict):
                raise ValueError("prepared compilation value is not an object")
        except (TypeError, ValueError):
            failures.append({"code": "source_compilation_staging_invalid", "object_id": "prepared"})
            break
    for output in connection.execute(
        """
        SELECT output_kind, output_id FROM source_compilation_outputs_v1
        ORDER BY output_kind, output_id
        """
    ):
        if output["output_kind"] == "knowledge_revision":
            table = "knowledge_revisions_v3"
            column = "revision_id"
        elif output["output_kind"] == "relation_revision":
            table = "knowledge_relation_revisions_v3"
            column = "relation_revision_id"
        else:
            continue
        if (
            connection.execute(
                f"SELECT 1 FROM {table} WHERE {column} = ?",
                (output["output_id"],),
            ).fetchone()
            is None
        ):
            failures.append(
                {
                    "code": "source_compilation_output_invalid",
                    "object_id": output["output_id"],
                }
            )
    for dependency in connection.execute(
        """
        SELECT consumer_kind, consumer_revision_id
        FROM knowledge_dependencies_v1
        ORDER BY dependency_id
        """
    ):
        if dependency["consumer_kind"] == "knowledge_revision":
            table = "knowledge_revisions_v3"
            column = "revision_id"
        else:
            table = "knowledge_relation_revisions_v3"
            column = "relation_revision_id"
        if (
            connection.execute(
                f"SELECT 1 FROM {table} WHERE {column} = ?",
                (dependency["consumer_revision_id"],),
            ).fetchone()
            is None
        ):
            failures.append(
                {
                    "code": "knowledge_dependency_invalid",
                    "object_id": dependency["consumer_revision_id"],
                }
            )
    for dependency in connection.execute(
        """
        SELECT dependency_id, consumer_kind, consumer_revision_id,
               input_kind, input_id, input_set_sha256
        FROM revision_dependencies_v1
        ORDER BY dependency_id
        """
    ):
        consumer_table, consumer_column = (
            ("knowledge_revisions_v3", "revision_id")
            if dependency["consumer_kind"] == "knowledge_revision"
            else ("knowledge_relation_revisions_v3", "relation_revision_id")
        )
        input_table, input_column = {
            "knowledge_revision": ("knowledge_revisions_v3", "revision_id"),
            "relation_revision": (
                "knowledge_relation_revisions_v3",
                "relation_revision_id",
            ),
            "compilation_run": (
                "source_compilation_runs_v1",
                "compilation_run_id",
            ),
        }[dependency["input_kind"]]
        digest = dependency["input_set_sha256"]
        valid_digest = len(digest) == 64
        if valid_digest:
            try:
                bytes.fromhex(digest)
            except ValueError:
                valid_digest = False
        if (
            not valid_digest
            or connection.execute(
                f"SELECT 1 FROM {consumer_table} WHERE {consumer_column} = ?",
                (dependency["consumer_revision_id"],),
            ).fetchone()
            is None
            or connection.execute(
                f"SELECT 1 FROM {input_table} WHERE {input_column} = ?",
                (dependency["input_id"],),
            ).fetchone()
            is None
        ):
            failures.append(
                {
                    "code": "revision_dependency_invalid",
                    "object_id": dependency["dependency_id"],
                }
            )
    for input_set in connection.execute(
        """
        SELECT * FROM synthesis_input_sets_v1
        ORDER BY synthesis_revision_id
        """
    ):
        try:
            canonical_inputs = {
                "source_revision_ids": strict_json_loads(
                    input_set["source_revision_ids_json"]
                ),
                "knowledge_revision_ids": strict_json_loads(
                    input_set["knowledge_revision_ids_json"]
                ),
                "relation_revision_ids": strict_json_loads(
                    input_set["relation_revision_ids_json"]
                ),
                "compilation_run_ids": strict_json_loads(
                    input_set["compilation_run_ids_json"]
                ),
            }
            if any(
                not isinstance(values, list)
                or values != sorted(values)
                or len(values) != len(set(values))
                for values in canonical_inputs.values()
            ):
                raise ValueError("Synthesis input list is not canonical")
            if (
                hashlib.sha256(
                    canonical_json(canonical_inputs).encode("utf-8")
                ).hexdigest()
                != input_set["input_set_sha256"]
            ):
                raise ValueError("Synthesis input-set digest does not match")
            revision = connection.execute(
                """
                SELECT kind FROM knowledge_revisions_v3
                WHERE revision_id = ?
                """,
                (input_set["synthesis_revision_id"],),
            ).fetchone()
            if revision is None or revision["kind"] != "synthesis":
                raise ValueError("Synthesis input set has no Synthesis revision")
            for field, table, column in (
                (
                    "source_revision_ids",
                    "source_revisions_v2",
                    "source_revision_id",
                ),
                (
                    "knowledge_revision_ids",
                    "knowledge_revisions_v3",
                    "revision_id",
                ),
                (
                    "relation_revision_ids",
                    "knowledge_relation_revisions_v3",
                    "relation_revision_id",
                ),
                (
                    "compilation_run_ids",
                    "source_compilation_runs_v1",
                    "compilation_run_id",
                ),
            ):
                if any(
                    connection.execute(
                        f"SELECT 1 FROM {table} WHERE {column} = ?",
                        (value,),
                    ).fetchone()
                    is None
                    for value in canonical_inputs[field]
                ):
                    raise ValueError("Synthesis input target is unavailable")
        except (TypeError, ValueError):
            failures.append(
                {
                    "code": "synthesis_input_set_invalid",
                    "object_id": input_set["synthesis_revision_id"],
                }
            )
    for revision in connection.execute(
        """
        SELECT revision_id FROM knowledge_revisions_v3
        WHERE verification = 'revision_bound'
          AND NOT EXISTS (
            SELECT 1 FROM synthesis_input_sets_v1
            WHERE synthesis_revision_id = knowledge_revisions_v3.revision_id
          )
        ORDER BY revision_id
        """
    ):
        failures.append(
            {
                "code": "revision_bound_input_set_missing",
                "object_id": revision["revision_id"],
            }
        )
    for draft in connection.execute(
        """
        SELECT draft_id, workspace_path, workspace_sha256
        FROM query_backfill_drafts_v1
        ORDER BY draft_id
        """
    ):
        relative = PurePosixPath(draft["workspace_path"])
        path = root / relative
        if (
            relative.is_absolute()
            or not relative.parts
            or relative.parts[0] != "drafts"
            or ".." in relative.parts
            or path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != draft["workspace_sha256"]
        ):
            failures.append(
                {
                    "code": "knowledge_backfill_draft_invalid",
                    "object_id": draft["draft_id"],
                }
            )
    return failures
