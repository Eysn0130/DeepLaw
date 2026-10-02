"""Credential-free snapshots of the fixed public C6 fixture, never arbitrary Vaults.

The owner preparation entry point must revoke every grant before this read-only
export. A snapshot preserves logical Ledger history and registered revision bytes;
it does not establish Host execution or native qualification authority.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from benchmarks.hosts.maintenance_task_cases import (
    CONFIGURATION_ORDER,
    FROZEN_INPUT_SHA256,
    SCENARIO_ORDER,
    public_task_projection,
)
from benchmarks.release.qualification_artifact_safety import ABSOLUTE_PATH_RE, SECRET_MARKER_RE
from deeplaw import knowledge_autonomy as autonomy
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, parse_knowledge_markdown
from deeplaw.knowledge_models import canonical_timestamp
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes, stable_id, strict_json_loads

_WRITER = "owner-host-maintenance-fixture"
_NAME = "Public Host maintenance task"
_MAX_FILES = 128
_MAX_BYTES = 16 * 1024 * 1024
_MAX_ROWS = 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SNAPSHOT_SCHEMA = "deeplaw.maintenance-fixture-snapshot/v1"
_IDENTITY_FIELDS = {
    "vault_id", "legacy_revision", "legacy_audit_head", "autonomous_sequence",
    "autonomous_audit_head",
}
_EMPTY_TABLES = frozenset(
    [
        "asset_revision_bindings_v2",
        "assets",
        "collections_v2",
        "compilations_v2",
        "content_tombstones_v4",
        "evidence_bindings_v3",
        "feedback_records",
        "fragment_node_membership_v2",
        "fragments_v2",
        "governance_revisions_v2",
        "knowledge_capture_batches_v4",
        "knowledge_consolidation_runs_v4",
        "knowledge_dependencies_v1",
        "knowledge_duplicate_resolutions_v4",
        "knowledge_identity_resolutions_v4",
        "knowledge_lineage_v2",
        "knowledge_relation_revisions_v3",
        "knowledge_relations_v3",
        "knowledge_revisions_v2",
        "knowledge_statements_v1",
        "legacy_fragment_bindings_v2",
        "pending_materializations_v3",
        "proposal_membership_v2",
        "proposal_metadata_v2",
        "proposal_sets_v2",
        "proposal_source_refs_v2",
        "query_backfill_drafts_v1",
        "relation_revisions_v2",
        "relations",
        "review_receipts",
        "revision_dependencies_v1",
        "run_receipts",
        "semantic_compilation_runs_v2",
        "semantic_duty_reports_v1",
        "semantic_inventories_v1",
        "semantic_observation_batches_v2",
        "semantic_observation_dispositions_v1",
        "semantic_observations_v2",
        "semantic_quality_receipts_v1",
        "source_build_bindings_v2",
        "source_compilation_artifact_bundle_members_v1",
        "source_compilation_artifacts_v1",
        "source_compilation_batches_v1",
        "source_compilation_identity_candidates_v1",
        "source_compilation_mcp_replays_v1",
        "source_compilation_outputs_v1",
        "source_compilation_packets_v1",
        "source_compilation_run_metadata_v1",
        "source_compilation_runs_v1",
        "source_compilation_staged_objects_v1",
        "source_compilation_staged_relations_v1",
        "source_compilation_usage_v1",
        "source_fragments",
        "source_freshness_events_v1",
        "source_identities_v2",
        "source_ir_nodes_v2",
        "source_lifecycle",
        "source_locations_v2",
        "source_revision_bindings_v2",
        "source_revisions_v2",
        "sources",
        "statement_evidence_maps_v1",
        "statement_evidence_receipts_v1",
        "statement_evidence_refs_v1",
        "synthesis_input_sets_v1",
        "synthesis_refresh_runs_v1",
        "synthesis_refresh_tasks_v1",
        "workspace_conflicts_v3",
        "workspace_file_leases_v4",
        "asset_search",
        "autonomous_search_v3",
    ]
)
_POPULATED_TABLES = frozenset(
    [
        "autonomous_core_v3",
        "autonomous_events_v3",
        "autonomous_metadata_v3",
        "content_object_roles_v3",
        "content_objects_v3",
        "derived_rebuild_queue_v3",
        "events",
        "identity_v2",
        "knowledge_aliases_v4",
        "knowledge_checkpoint_routes_v1",
        "knowledge_feedback_v3",
        "knowledge_objects_v3",
        "knowledge_revisions_v3",
        "knowledge_run_records_v4",
        "knowledge_sink_grants_v3",
        "knowledge_sink_usage_v3",
        "metadata",
        "mutation_idempotency_v3",
        "semantic_compilation_core_v1",
        "source_compilation_core_v1",
        "statement_evidence_core_v1",
    ]
)
_EVENT_TYPES = frozenset(
    {
        "autonomous_core_initialized",
        "knowledge_sink_grant_enabled",
        "knowledge_sink_grant_revoked",
        "knowledge_revision_committed",
        "workspace_materialized",
        "knowledge_run_recorded",
        "knowledge_feedback_recorded",
    }
)
_FORGET_REASONS = {
    None,
    "Owner-directed correction of injected public wrong experience.",
    "Owner requested forgetting before the subsequent Host tasks.",
}
_FEEDBACK_NOTE = (
    "Independent finite environment check of an explicitly referenced "
    "experience; outcome association does not establish general causal benefit."
)


def _digest(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode())


def _relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or path.as_posix() != value
    ):
        raise ValueError("fixture file path is unsafe")
    return value


def _safe_bytes(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_mode & 0o077
            or before.st_size > _MAX_BYTES
        ):
            raise ValueError("fixture file is unsafe or exceeds its bound")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(_MAX_BYTES + 1)
        after = os.fstat(descriptor)
        current = path.lstat()

        def stable(value):
            return (
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_nlink,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )

        if (
            stable(before) != stable(after)
            or after.st_ino != current.st_ino
            or after.st_dev != current.st_dev
            or len(payload) != before.st_size
        ):
            raise ValueError("fixture file changed during its read")
        return payload
    finally:
        os.close(descriptor)


def _source_files(root: Path, *, owner_only_directories: bool = False) -> dict[str, bytes]:
    files = {}
    for scanned_directories, (directory, child_directories, child_files) in enumerate(
        os.walk(root, followlinks=False), start=1,
    ):
        if scanned_directories > _MAX_FILES * 4:
            raise ValueError("fixture directory inventory exceeds its bound")
        for name in child_directories:
            path = Path(directory) / name
            if (path.is_symlink() or not path.is_dir()
                    or (owner_only_directories and path.stat().st_mode & 0o077)):
                raise ValueError("fixture directory is unsafe")
        for name in child_files:
            path = Path(directory) / name
            relative = _relative(path.relative_to(root).as_posix())
            if relative.startswith(".deeplaw/capabilities/"):
                raise ValueError("active grant or capability material cannot be exported")
            files[relative] = _safe_bytes(path)
            if len(files) > _MAX_FILES or sum(map(len, files.values())) > _MAX_BYTES:
                raise ValueError("fixture file inventory exceeds its bound")
    return files


def _owner_parents(path: Path, root: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        current.mkdir(mode=0o700, exist_ok=True)
        if current.is_symlink() or not current.is_dir() or current.stat().st_mode & 0o077:
            raise ValueError("frozen fixture directory is unsafe")


def _empty_roots(root: Path) -> None:
    for relative in ("sources", ".deeplaw/capabilities", ".deeplaw/staging"):
        path = root / relative
        if path.is_symlink() or not path.is_dir():
            raise ValueError("fixture empty directory is unsafe")
        for child in path.iterdir():
            if (
                relative == ".deeplaw/staging"
                and child.name == "conflicts"
                and not child.is_symlink()
                and child.is_dir()
                and not any(child.iterdir())
            ):
                continue
            raise ValueError("fixture requires empty source, capability and staging roots")


def _public_knowledge() -> set[tuple[str, str, str]]:
    old = public_task_projection("frozen_unmaintained", "source_update")
    allowed = {
        (
            canonical_json(record),
            "Orchid archive Amber report experience",
            f"host-maintenance:{index}",
        )
        for index, record in enumerate(old["environment"]["experience"]["records"])
    }
    current = public_task_projection("governed_maintenance", "source_update")
    allowed.add(
        (
            canonical_json(current["environment"]["experience"]["records"][0]),
            "Orchid archive Amber report experience",
            "host-maintenance:0",
        )
    )
    allowed.add(
        (
            "Synthetic obsolete shortcut marker cobalt-before-forget.",
            "Orchid archive forgotten shortcut",
            "host-maintenance:forgettable",
        )
    )
    return allowed


def _validate_tables(store: AutonomousKnowledgeStore) -> None:
    connection = store.connection
    for row in connection.execute("SELECT type, sql FROM sqlite_schema"):
        if row["type"] not in {"table", "index"} or (
            row["sql"]
            and (ABSOLUTE_PATH_RE.search(row["sql"]) or SECRET_MARKER_RE.search(row["sql"]))
        ):
            raise ValueError("fixture Ledger schema is unsafe")
    shadow_tables = {
        prefix + suffix
        for prefix in ("asset_search", "autonomous_search_v3")
        for suffix in ("_config", "_content", "_data", "_docsize", "_idx")
    }
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")
    }
    if tables != _EMPTY_TABLES | _POPULATED_TABLES | shadow_tables:
        raise ValueError("fixture Ledger contains an unexpected table")
    for table in sorted(tables):
        rows = connection.execute(f'SELECT * FROM "{table}" LIMIT ?', (_MAX_ROWS + 1,)).fetchall()
        if len(rows) > _MAX_ROWS or (table in _EMPTY_TABLES and rows):
            raise ValueError("fixture contains raw source, evidence or non-fixture state")
        for row in rows:
            for value in row:
                if isinstance(value, str) and (
                    ABSOLUTE_PATH_RE.search(value) or SECRET_MARKER_RE.search(value)
                ):
                    raise ValueError("fixture Ledger contains unsafe retained text")
    for row in connection.execute("SELECT * FROM knowledge_sink_grants_v3"):
        if (
            row["writer_id"] != _WRITER
            or row["allowed_scope"] != "project"
            or row["max_sensitivity"] != "public"
            or set(strict_json_loads(row["operations_json"]))
            not in ({"remember", "forget"}, {"record_run", "record_feedback"})
            or strict_json_loads(row["evaluator_types_json"]) != ["external_check"]
        ):
            raise ValueError("fixture grant writer or policy differs")
        if row["revoked_at"] is None:
            raise ValueError("fixture has an active grant")
    if any(
        row[0] not in _EVENT_TYPES
        for row in connection.execute("SELECT event_type FROM autonomous_events_v3")
    ):
        raise ValueError("fixture Ledger contains a non-fixture event")
    for row in connection.execute("SELECT * FROM knowledge_aliases_v4"):
        if (
            row["writer_id"] != _WRITER
            or row["scope"] != "project"
            or row["alias_text"]
            not in {item for entry in _public_knowledge() for item in entry[1:]}
        ):
            raise ValueError("fixture knowledge alias differs")
    for table, schema, migration in (
        ("identity_v2", "deeplaw.knowledge-identity/v2", "new-vault"),
        ("autonomous_core_v3", "deeplaw.autonomous-knowledge-core/v2", "knowledge-sqlite/v1"),
        ("source_compilation_core_v1", "deeplaw.source-compilation-core/v1", "knowledge-sqlite/v1"),
        (
            "semantic_compilation_core_v1",
            "deeplaw.semantic-compilation-core/v1",
            "knowledge-sqlite/v1",
        ),
        ("statement_evidence_core_v1", "deeplaw.statement-evidence-core/v1", "knowledge-sqlite/v1"),
    ):
        rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
        if (
            len(rows) != 1
            or rows[0]["schema_version"] != schema
            or rows[0]["migration_source"] != migration
        ):
            raise ValueError("fixture initialization identity differs")
        canonical_timestamp(rows[0]["installed_at"], field="fixture installation timestamp")
    metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    if (
        set(metadata)
        != {
            "schema_version",
            "control_schema",
            "vault_id",
            "name",
            "scope",
            "created_at",
            "revision",
            "audit_head",
        }
        or metadata["name"] != _NAME
        or metadata["scope"] != "project"
    ):
        raise ValueError("fixture Ledger metadata differs")
    if {row[0] for row in connection.execute("SELECT key FROM autonomous_metadata_v3")} != {
        "schema_version",
        "vault_id",
        "sequence",
        "audit_head",
        "installed_at",
    }:
        raise ValueError("fixture autonomous metadata differs")


def _registered_files(store: AutonomousKnowledgeStore, files: dict[str, bytes]) -> set[str]:
    allowed = _public_knowledge()
    required = {"vault.json", ".deeplaw/manifest.json", ".deeplaw/ledger.sqlite3"}
    digests = set()
    for revision in store.connection.execute("SELECT * FROM knowledge_revisions_v3"):
        digest = revision["markdown_sha256"]
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("fixture registered digest is invalid")
        relative = f".deeplaw/objects/sha256/{digest[:2]}/{digest[2:]}"
        if relative not in files or sha256_bytes(files[relative]) != digest:
            raise ValueError("fixture registered object is missing")
        parsed = parse_knowledge_markdown(files[relative])
        frontmatter = parsed["frontmatter"]
        if (
            (parsed["body"], revision["title"], revision["semantic_key"]) not in allowed
            or revision["writer_id"] != _WRITER
            or revision["scope"] != "project"
            or revision["sensitivity"] != "public"
            or revision["source_free"] != 1
            or revision["kind"] != "experience"
            or revision["verification"] != "unverified"
            or revision["lifecycle"] not in {"active", "forgotten", "superseded"}
            or revision["epistemic_state"] != "tentative"
            or frontmatter["sources"] != []
            or frontmatter["tags"] != []
            or frontmatter["aliases"] != []
            or frontmatter["relations"] != []
            or frontmatter["assertion"] is not None
            or any(value is not None for value in frontmatter["generation"].values())
            or frontmatter["lifecycle_reason"] not in _FORGET_REASONS
            or frontmatter["quarantine_reasons"] != []
            or any(
                frontmatter.get(key) is not None
                for key in (
                    "valid_from",
                    "valid_to",
                    "expires_at",
                    "memory_type",
                    "preference_basis",
                    "skill",
                )
            )
        ):
            raise ValueError("fixture knowledge is not fixed public material")
        required.add(relative)
        digests.add(digest)
    objects = store.connection.execute("SELECT * FROM content_objects_v3").fetchall()
    if {row["object_sha256"] for row in objects} != digests or any(
        row["object_kind"] != "knowledge_revision"
        or row["media_type"] != "text/markdown; charset=utf-8"
        for row in objects
    ):
        raise ValueError("fixture contains an unreferenced or evidence CAS object")
    for row in store.connection.execute("""
        SELECT objects.workspace_path, revisions.lifecycle
        FROM knowledge_objects_v3 AS objects JOIN knowledge_revisions_v3 AS revisions
        ON objects.current_revision_id = revisions.revision_id
    """):
        if row["lifecycle"] == "active":
            required.add(_relative(row["workspace_path"]))
    return required


def _validate_outcomes(store: AutonomousKnowledgeStore) -> None:
    tasks: dict[str, set[str]] = {}
    for configuration in CONFIGURATION_ORDER:
        for scenario in SCENARIO_ORDER:
            task = public_task_projection(configuration, scenario)
            tasks.setdefault(sha256_bytes(task["task"].encode()), set()).add(
                _digest(
                    build_task_context_binding(_digest(configuration), _digest(task["task_id"]))
                )
            )
    for row in store.connection.execute("SELECT * FROM knowledge_run_records_v4"):
        metadata = strict_json_loads(row["metadata_json"])
        if (
            row["writer_id"] != _WRITER
            or row["scope"] != "project"
            or row["sensitivity"] != "public"
            or row["task_sha256"] not in tasks
            or row["host_id"] not in {"opencode", "synthetic-host"}
            or row["model_id"] not in {"deepseek-v4-flash", "synthetic-test"}
            or row["status"] not in {"succeeded", "partial"}
            or set(metadata) != {"task_kind", "artifact_ids", "task_binding"}
            or metadata["task_kind"] != "actual_host_public_maintenance"
            or not isinstance(row["input_sha256"], str)
            or not _SHA256.fullmatch(row["input_sha256"])
            or metadata["artifact_ids"]
            != [stable_id("capsule", store.vault_id, row["input_sha256"])]
            or _digest(metadata["task_binding"]) not in tasks[row["task_sha256"]]
        ):
            raise ValueError("fixture outcome writer, scope or task differs")
    for row in store.connection.execute("""
        SELECT feedback.*, grants.writer_id, grants.allowed_scope, grants.max_sensitivity,
               runs.scope AS run_scope, runs.sensitivity AS run_sensitivity
        FROM knowledge_feedback_v3 AS feedback
        JOIN knowledge_sink_grants_v3 AS grants USING (grant_id)
        JOIN knowledge_run_records_v4 AS runs USING (run_id)
    """):
        if (
            row["writer_id"] != _WRITER
            or row["allowed_scope"] != "project"
            or row["max_sensitivity"] != "public"
            or row["run_scope"] != "project"
            or row["run_sensitivity"] != "public"
            or row["evaluator_type"] != "external_check"
            or row["outcome"] != "helpful"
            or row["note_sha256"] != sha256_bytes(_FEEDBACK_NOTE.encode())
        ):
            raise ValueError("fixture feedback writer or scope differs")


def _validate_fixture_mutations(
    store: AutonomousKnowledgeStore, files: dict[str, bytes],
) -> None:
    """Close retained mutation keys, identities and responses without modifying history."""
    connection = store.connection
    old_records = public_task_projection(
        "frozen_unmaintained", "source_update",
    )["environment"]["experience"]["records"]
    current_record = public_task_projection(
        "governed_maintenance", "source_update",
    )["environment"]["experience"]["records"][0]
    marker = "Synthetic obsolete shortcut marker cobalt-before-forget."
    # key -> body, semantic key, parent key, initial lifecycle, operation
    setup = {
        "seed-0": (
            canonical_json(old_records[0]), "host-maintenance:0", None, "active", "remember",
        ),
        "seed-1": (
            canonical_json(old_records[1]), "host-maintenance:1", None, "active", "remember",
        ),
        "maintain-source": (
            canonical_json(current_record), "host-maintenance:0", "seed-0", "active", "remember",
        ),
        "reject-error-1": (
            canonical_json(old_records[1]), "host-maintenance:1", "seed-1", "forgotten", "forget",
        ),
        "seed-forgettable": (marker, "host-maintenance:forgettable", None, "active", "remember"),
        "forget-shortcut": (
            marker, "host-maintenance:forgettable", "seed-forgettable", "forgotten", "forget",
        ),
    }
    grants = {
        row["grant_id"]: set(strict_json_loads(row["operations_json"]))
        for row in connection.execute("SELECT * FROM knowledge_sink_grants_v3")
    }
    setup_grants = [identity for identity, operations in grants.items()
                    if operations == {"remember", "forget"}]
    if len(setup_grants) != 1:
        raise ValueError("fixture mutation setup grant differs")
    mutations = connection.execute(
        "SELECT * FROM mutation_idempotency_v3 ORDER BY recorded_at",
    ).fetchall()
    seeds = {
        row["idempotency_key"]: row for row in mutations
        if row["grant_id"] == setup_grants[0]
    }
    frozen_keys = {"seed-0", "seed-1", "seed-forgettable", "forget-shortcut"}
    if set(seeds) not in (set(), frozen_keys, set(setup)):
        raise ValueError("fixture mutation setup keys differ")
    revisions = {
        row["revision_id"]: row
        for row in connection.execute("SELECT * FROM knowledge_revisions_v3")
    }
    runs = {
        row["run_id"]: row for row in connection.execute("SELECT * FROM knowledge_run_records_v4")
    }
    feedback = {
        row["feedback_id"]: row
        for row in connection.execute("SELECT * FROM knowledge_feedback_v3")
    }
    contracts = {
        "knowledge_revision": (revisions, "knowledge_revision_committed"),
        "run_record": (runs, "knowledge_run_recorded"),
        "knowledge_feedback": (feedback, "knowledge_feedback_recorded"),
    }
    for kind, (results, _) in contracts.items():
        if {row["result_id"] for row in mutations if row["result_kind"] == kind} != set(results):
            raise ValueError("fixture mutation result inventory differs")
    events = {}
    for row in connection.execute("SELECT * FROM autonomous_events_v3"):
        identity = (row["event_type"], row["object_id"])
        if identity in events:
            raise ValueError("fixture mutation audit identity is duplicated")
        events[identity] = row
    for mutation in mutations:
        key, grant_id = mutation["idempotency_key"], mutation["grant_id"]
        request_sha256, result_id = mutation["request_sha256"], mutation["result_id"]
        kind = mutation["result_kind"]
        if kind not in contracts or grant_id not in grants:
            raise ValueError("fixture mutation kind or grant differs")
        results, event_type = contracts[kind]
        row, event = results.get(result_id), events.get((event_type, result_id))
        if row is None or event is None:
            raise ValueError("fixture mutation result or audit binding differs")
        audit = {
            "grant_id": grant_id,
            "idempotency_key_sha256": sha256_bytes(key.encode()),
            "request_sha256": request_sha256,
        }
        if kind == "knowledge_revision":
            if key not in setup or grant_id != setup_grants[0]:
                raise ValueError("fixture mutation knowledge key differs")
            body, semantic_key, parent_key, lifecycle, operation = setup[key]
            parent = revisions.get(seeds[parent_key]["result_id"]) if parent_key else None
            knowledge_id = (
                parent["knowledge_id"] if parent
                else stable_id("knowledge", store.vault_id, grant_id, key)
            )
            digest = row["markdown_sha256"]
            parsed = parse_knowledge_markdown(
                files[f".deeplaw/objects/sha256/{digest[:2]}/{digest[2:]}"]
            )
            frontmatter = parsed["frontmatter"]
            if (
                result_id != stable_id("knowledgerev", knowledge_id, grant_id, key, request_sha256)
                or row["knowledge_id"] != knowledge_id
                or row["parent_revision_id"] != (parent["revision_id"] if parent else None)
                or row["workspace_path"] != f"knowledge/experiences/{knowledge_id}.md"
                or row["semantic_key"] != semantic_key
                or parsed["body"] != body
                or frontmatter["lifecycle"] != lifecycle
            ):
                raise ValueError("fixture mutation knowledge identity differs")
            response = {
                "schema_version": autonomy.KNOWLEDGE_REVISION_SCHEMA,
                **{field: row[field] for field in (
                    "knowledge_id", "revision_id", "parent_revision_id", "markdown_sha256",
                    "workspace_path", "kind", "origin", "authority", "verification",
                    "epistemic_state", "scope", "sensitivity", "recorded_at",
                )},
                "legal_authority": False,
                "mutability": autonomy.AGENT_KNOWLEDGE_MUTABILITY,
                "writer_scope": row["scope"],
                "activation_policy": autonomy.AUTONOMOUS_ACTIVATION_POLICY,
                "lifecycle": lifecycle,
                "source_free": bool(row["source_free"]),
                "quarantine_reasons": [],
                "idempotent_replay": False,
                "current_revision_id": result_id,
            }
            audit.update({
                "operation": operation,
                **{field: row[field] for field in (
                    "knowledge_id", "parent_revision_id", "markdown_sha256", "epistemic_state",
                    "origin", "authority", "writer_id", "scope", "sensitivity",
                    "semantic_digest", "verification", "valid_from", "valid_to", "expires_at",
                )},
                "lifecycle": lifecycle,
                "source_free": bool(row["source_free"]),
                **{field + "_sha256": _digest(strict_json_loads(row[column]))
                   for field, column in (
                       ("source_refs", "source_refs_json"), ("generation", "generation_json"),
                       ("tags", "tags_json"), ("metadata", "metadata_json"),
                   )},
                "workspace_edit_sha256": None,
            })
            materialized = events.get(("workspace_materialized", result_id))
            materialization = {
                "workspace_path": row["workspace_path"], "markdown_sha256": digest,
                "action": "write" if lifecycle == "active" else "delete",
            }
            if (materialized is None
                    or canonical_json(strict_json_loads(materialized["payload_json"]))
                    != canonical_json(materialization)):
                raise ValueError("fixture mutation materialization binding differs")
            response["audit_head"] = materialized["event_hash"]
        elif kind == "run_record":
            if (
                not re.fullmatch(r"host-maintenance-run:[0-9a-f]{64}", key)
                or grants[grant_id] != {"record_run", "record_feedback"}
                or row["grant_id"] != grant_id
                or result_id != stable_id("run", store.vault_id, grant_id, key, request_sha256)
            ):
                raise ValueError("fixture mutation run key or generated identity differs")
            metadata = strict_json_loads(row["metadata_json"])
            request = {
                "operation": "record_run", "run_id": None, "metadata": metadata,
                **{field: row[field] for field in (
                    "task_sha256", "host_id", "model_id", "status", "scope", "sensitivity",
                    "input_sha256", "output_sha256", "tool_results_sha256",
                    "started_at", "ended_at",
                )},
            }
            if _digest(request) != request_sha256:
                raise ValueError("fixture mutation run request differs")
            receipt = {
                "schema_version": "deeplaw.knowledge-run-record/v1", "metadata": metadata,
                **{field: row[field] for field in (
                    "run_id", "writer_id", "host_id", "model_id", "task_sha256", "input_sha256",
                    "output_sha256", "tool_results_sha256", "scope", "sensitivity", "status",
                    "started_at", "ended_at", "recorded_at",
                )},
            }
            if _digest(receipt) != row["receipt_sha256"]:
                raise ValueError("fixture mutation run receipt differs")
            response = {
                **receipt, "receipt_sha256": row["receipt_sha256"], "idempotent_replay": False,
                "audit_head": event["event_hash"],
            }
            audit.update({
                "operation": "record_run",
                **{field: row[field] for field in (
                    "writer_id", "host_id", "model_id", "task_sha256", "scope", "sensitivity",
                    "status", "receipt_sha256",
                )},
                "task_binding_sha256": metadata["task_binding"]["binding_sha256"],
            })
        else:
            run, revision = runs.get(row["run_id"]), revisions.get(row["revision_id"])
            if (
                not re.fullmatch(r"host-maintenance-feedback:[0-9a-f]{64}", key)
                or grants[grant_id] != {"record_run", "record_feedback"}
                or row["grant_id"] != grant_id
                or run is None or run["grant_id"] != grant_id
                or revision is None or revision["knowledge_id"] != row["knowledge_id"]
                or result_id != stable_id(
                    "feedback", row["knowledge_id"], row["revision_id"],
                    grant_id, key, request_sha256,
                )
            ):
                raise ValueError("fixture mutation feedback key or identity differs")
            request = {
                "operation": "record_feedback", "feedback_note_sha256": row["note_sha256"],
                **{field: row[field] for field in (
                    "knowledge_id", "revision_id", "run_id", "outcome", "evaluator_type",
                )},
            }
            if _digest(request) != request_sha256:
                raise ValueError("fixture mutation feedback request differs")
            response = {
                "schema_version": "deeplaw.knowledge-feedback/v1",
                **{field: row[field] for field in (
                    "feedback_id", "knowledge_id", "revision_id", "run_id", "outcome",
                    "evaluator_type", "recorded_at",
                )},
                "feedback_note_sha256": row["note_sha256"],
                "task_success_authority": "external_evidence", "idempotent_replay": False,
                "audit_head": event["event_hash"],
            }
            audit.update(request)
        if (
            canonical_json(strict_json_loads(mutation["response_json"])) != canonical_json(response)
            or canonical_json(strict_json_loads(event["payload_json"])) != canonical_json(audit)
            or event["recorded_at"] != mutation["recorded_at"]
            or row["recorded_at"] != mutation["recorded_at"]
        ):
            raise ValueError("fixture mutation response or audit differs")


def _check_source_closure(files: dict[str, bytes], required: set[str], vault_id: str) -> None:
    defaults = {
        "AGENTS.md": autonomy._VAULT_AGENT_GUIDE.encode(),
        ".gitignore": autonomy._VAULT_GITIGNORE.encode(),
        "policies/default.yaml": autonomy._DEFAULT_POLICY.encode(),
    }
    for relative, payload in defaults.items():
        if relative in files and files[relative] != payload:
            raise ValueError("fixture workspace default differs")
    workspace = files.get(".deeplaw/workspace.json")
    if workspace is not None:
        profile = strict_json_loads(workspace)
        if profile != {
            "schema_version": "deeplaw.workspace-profile/v1",
            "vault_id": vault_id,
            "canonical_markdown_roots": ["knowledge", "memory", "skills"],
            "derived_roots": ["wiki", "canvas"],
            "stable_identity_field": "deeplaw_id",
            "wikilinks": True,
            "obsidian_compatible": True,
            "tolaria_compatible": True,
            "git_is_transaction_database": False,
        }:
            raise ValueError("fixture workspace identity differs")
    ignored = {
        *defaults,
        ".deeplaw/workspace.json",
        ".deeplaw/ledger.sqlite3-wal",
        ".deeplaw/ledger.sqlite3-shm",
    }
    if set(files) - required - ignored or not required.issubset(files):
        raise ValueError("fixture contains unreferenced files or missing registered files")


def _validated_fixture_state(
    legacy: KnowledgeVault, store: AutonomousKnowledgeStore, files: dict[str, bytes],
    expected_vault_id: str,
) -> tuple[dict[str, Any], set[str]]:
    """The shared read-only admission rules for a live fixture or frozen copy."""
    if (
        not isinstance(expected_vault_id, str)
        or legacy.vault_id != expected_vault_id
        or store.vault_id != expected_vault_id
        or legacy.manifest["name"] != _NAME
        or legacy.manifest["scope"] != "project"
    ):
        raise ValueError("fixture Vault identity differs")
    inspected = store.inspect()
    if inspected["counts"]["active_grants"] != 0:
        raise ValueError("fixture has an active grant")
    if inspected["verification"]["valid"] is not True:
        raise ValueError("fixture Vault integrity is invalid")
    _validate_tables(store)
    required = _registered_files(store, files)
    _validate_outcomes(store)
    _validate_fixture_mutations(store, files)
    _check_source_closure(files, required, expected_vault_id)
    return {
        "vault_id": store.vault_id,
        "legacy_revision": legacy.revision,
        "legacy_audit_head": legacy.audit_head,
        "autonomous_sequence": store.sequence,
        "autonomous_audit_head": store.audit_head,
    }, required


def _inventory(files: dict[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": relative, "sha256": sha256_bytes(payload), "size": len(payload)}
        for relative, payload in sorted(files.items())
    ]


def validate_public_fixture_snapshot(
    vault: Path, manifest: dict, *, expected_vault_id: str,
) -> dict[str, Any]:
    """Reconstruct an exact public snapshot manifest using read-only Vault opens.

    This validates fixed synthetic fixture bytes and Ledger identity, never native
    Host execution authority. It creates no files or grants and changes no modes.
    The caller must serialize writes to the snapshot during validation.
    """
    if os.name != "posix":
        raise ValueError("fixture snapshot requires verified POSIX owner-only file modes")
    if type(manifest) is not dict or set(manifest) != _IDENTITY_FIELDS | {
        "schema_version", "public_input_sha256", "inventory", "inventory_sha256",
    }:
        raise ValueError("fixture snapshot manifest shape differs")
    if (
        manifest["schema_version"] != _SNAPSHOT_SCHEMA
        or manifest["public_input_sha256"] != FROZEN_INPUT_SHA256
        or not isinstance(expected_vault_id, str) or not expected_vault_id
        or manifest["vault_id"] != expected_vault_id
        or any(type(manifest[field]) is not int or not 0 <= manifest[field] <= _MAX_ROWS
               for field in ("legacy_revision", "autonomous_sequence"))
        or any(not isinstance(manifest[field], str) or not _SHA256.fullmatch(manifest[field])
               for field in ("legacy_audit_head", "autonomous_audit_head", "inventory_sha256"))
    ):
        raise ValueError("fixture snapshot manifest identity differs")
    inventory = manifest["inventory"]
    if type(inventory) is not list or not 0 < len(inventory) <= _MAX_FILES:
        raise ValueError("fixture snapshot inventory exceeds its bound")
    paths, total = [], 0
    for item in inventory:
        if (
            type(item) is not dict or set(item) != {"path", "sha256", "size"}
            or not isinstance(item["path"], str) or len(item["path"]) > 4096
            or not isinstance(item["sha256"], str) or not _SHA256.fullmatch(item["sha256"])
            or type(item["size"]) is not int or not 0 <= item["size"] <= _MAX_BYTES
        ):
            raise ValueError("fixture snapshot inventory entry differs")
        paths.append(_relative(item["path"]))
        total += item["size"]
    if (paths != sorted(set(paths)) or total > _MAX_BYTES
            or manifest["inventory_sha256"] != _digest(inventory)):
        raise ValueError("fixture snapshot inventory order, bound or digest differs")
    root = Path(vault).absolute()
    if ".." in root.parts or any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("fixture snapshot path has an unsafe ancestor")
    if not root.is_dir() or root.stat().st_mode & 0o077:
        raise ValueError("fixture snapshot must be an owner-only Vault")
    files = _source_files(root, owner_only_directories=True)
    actual_inventory = _inventory(files)
    if actual_inventory != inventory:
        raise ValueError("fixture snapshot actual file inventory differs")
    _empty_roots(root)
    with (
        KnowledgeVault(root, read_only=True) as legacy,
        AutonomousKnowledgeStore(root, read_only=True, legacy_snapshot=legacy) as store,
    ):
        identity, required = _validated_fixture_state(legacy, store, files, expected_vault_id)
    if (set(files) != required
            or any(identity[field] != manifest[field] for field in _IDENTITY_FIELDS)):
        raise ValueError("fixture snapshot registered closure or audit binding differs")
    required_directories = {".", "sources", ".deeplaw/capabilities", ".deeplaw/staging"}
    for relative in required:
        required_directories.update(parent.as_posix() for parent in PurePosixPath(relative).parents)
    directories = set()
    for scanned, (directory, _, _) in enumerate(os.walk(root, followlinks=False), start=1):
        if scanned > _MAX_FILES * 4:
            raise ValueError("fixture snapshot directory inventory exceeds its bound")
        directories.add(Path(directory).relative_to(root).as_posix())
    if directories - {".deeplaw/staging/conflicts"} != required_directories:
        raise ValueError("fixture snapshot directory closure differs")
    if _source_files(root, owner_only_directories=True) != files:
        raise ValueError("fixture snapshot changed during read-only validation")
    return {
        "schema_version": _SNAPSHOT_SCHEMA, **identity,
        "public_input_sha256": FROZEN_INPUT_SHA256,
        "inventory": actual_inventory, "inventory_sha256": _digest(actual_inventory),
    }


def freeze_public_fixture_vault(
    vault: Path,
    destination: Path,
    *,
    expected_vault_id: str,
) -> dict[str, Any]:
    """Freeze an existing fixed fixture, with source read-only and no grant operations.

    Destination is the new Vault root. Inventory paths are relative to it. The
    caller must serialize owner writes during capture/export; any observed source
    change rejects the result. POSIX owner-only modes are required by this lane.
    """
    if os.name != "posix":
        raise ValueError("fixture snapshot requires verified POSIX owner-only file modes")
    root, output = Path(vault).absolute(), Path(destination).absolute()
    if ".." in root.parts or ".." in output.parts:
        raise ValueError("fixture snapshot paths must not traverse ancestors")
    if root == output or root in output.parents:
        raise ValueError("fixture snapshot must be outside the source Vault")
    if output.exists() or output.is_symlink():
        raise ValueError("fixture snapshot destination must be new")
    for path in (root, *root.parents, output.parent, *output.parent.parents):
        if path.is_symlink():
            raise ValueError("fixture snapshot path has an unsafe ancestor")
    if not root.is_dir() or root.stat().st_mode & 0o077:
        raise ValueError("fixture source must be an owner-only Vault")
    files = _source_files(root)
    _empty_roots(root)
    created = False
    try:
        with (
            KnowledgeVault(root, read_only=True) as legacy,
            AutonomousKnowledgeStore(
                root,
                read_only=True,
                legacy_snapshot=legacy,
            ) as store,
        ):
            identity, required = _validated_fixture_state(legacy, store, files, expected_vault_id)
            output.mkdir(mode=0o700)
            created = True
            for relative in ("sources", ".deeplaw/capabilities", ".deeplaw/staging"):
                _owner_parents(output / relative, output)
            for relative in sorted(required - {".deeplaw/ledger.sqlite3"}):
                target = output / relative
                _owner_parents(target.parent, output)
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(files[relative])
            database = output / ".deeplaw/ledger.sqlite3"
            descriptor = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            with sqlite3.connect(database) as copied:
                store.connection.backup(copied)
                copied.execute("PRAGMA journal_mode = DELETE")
                # Rebuild the two empty derived FTS stores only in the copy, so
                # deleted accelerator segments cannot retain unrelated bytes.
                for table in ("asset_search", "autonomous_search_v3"):
                    copied.execute(f"INSERT INTO {table} ({table}) VALUES ('rebuild')")
                copied.commit()
                # Remove freelist remnants in the copy, preserving every logical row.
                copied.execute("VACUUM")
        if _source_files(root) != files:
            raise ValueError("fixture source changed during snapshot")
        exported = _source_files(output)
        if set(exported) != required:
            raise ValueError("frozen fixture file closure differs")
        inventory = _inventory(exported)
        manifest = {
            "schema_version": _SNAPSHOT_SCHEMA,
            **identity,
            "public_input_sha256": FROZEN_INPUT_SHA256,
            "inventory": inventory,
            "inventory_sha256": _digest(inventory),
        }
        return validate_public_fixture_snapshot(
            output, manifest, expected_vault_id=expected_vault_id,
        )
    except BaseException:
        if created:
            shutil.rmtree(output)
        raise
