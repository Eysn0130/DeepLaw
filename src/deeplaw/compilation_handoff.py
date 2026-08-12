from __future__ import annotations

from pathlib import Path
from typing import Any

from .compilation import compiler_profile
from .compilation.status import summarize_source_compilation
from .knowledge_autonomy import AutonomousKnowledgeStore, _validate_contract


def build_compilation_handoff(
    vault_path: str | Path,
    *,
    source_revision_id: str,
) -> dict[str, Any]:
    """Describe the existing split read/sink saga without performing a write."""

    profile = compiler_profile("living-wiki-agent", "3")
    with AutonomousKnowledgeStore(vault_path, read_only=True) as store:
        source = store.connection.execute(
            """
            SELECT source_revisions_v2.source_revision_id,
                   source_lifecycle.status AS lifecycle_status
            FROM source_revisions_v2
            JOIN source_revision_bindings_v2 USING(source_revision_id)
            JOIN source_lifecycle
              ON source_lifecycle.source_id =
                 source_revision_bindings_v2.legacy_source_id
            WHERE source_revisions_v2.source_revision_id = ?
            """,
            (source_revision_id,),
        ).fetchone()
        if source is None:
            raise KeyError(f"Source Revision is unavailable: {source_revision_id}")
        runs = store.connection.execute(
            """
            SELECT compilation_run_id, status
            FROM source_compilation_runs_v1
            WHERE source_revision_id = ?
            ORDER BY updated_at, compilation_run_id
            """,
            (source_revision_id,),
        ).fetchall()
        audit_head = store.audit_head

    summary = summarize_source_compilation(
        source_revision_id=source_revision_id,
        lifecycle_status=str(source["lifecycle_status"]),
        runs=[dict(row) for row in runs],
    )

    sink_operations = [
        "begin_compilation",
        "stage_semantic_observations",
        "freeze_semantic_inventory",
        "finalize_semantic_compilation",
        "validate_compilation",
        "commit_compilation",
        "resume_compilation",
        "abort_compilation",
    ]
    step_specs = (
        ("knowledge_support", "semantic.profile", False),
        ("knowledge_sink", "begin_compilation", True),
        ("knowledge_support", "semantic.next_packet", False),
        ("knowledge_sink", "stage_semantic_observations", True),
        ("knowledge_sink", "freeze_semantic_inventory", True),
        ("knowledge_support", "semantic.inventory_and_finalization", False),
        ("knowledge_sink", "finalize_semantic_compilation", True),
        ("knowledge_sink", "validate_compilation", True),
        ("knowledge_sink", "commit_compilation", True),
        ("knowledge_sink", "resume_compilation", True),
        ("knowledge_support", "verify_query_wiki", False),
    )
    result = {
        "schema_version": "deeplaw.compilation-handoff/v1",
        "source_revision_id": source_revision_id,
        "source_status": summary["source_status"],
        "canonical_knowledge_committed": summary[
            "canonical_knowledge_committed"
        ],
        "canonical_knowledge_admissible": summary[
            "canonical_knowledge_admissible"
        ],
        "latest_compilation_attempts": summary["latest_compilation_attempts"],
        "wiki_projection_status": summary["wiki_projection_status"],
        "wiki_projection_pending": summary["wiki_projection_pending"],
        "wiki_projection_ready": summary["wiki_projection_ready"],
        "compiler_profile": {
            "compiler_profile": profile["compiler_profile"],
            "compiler_profile_version": profile["compiler_profile_version"],
            "prompt_template_id": profile["prompt_template_id"],
            "prompt_config_sha256": profile["prompt_config_sha256"],
            "plan_configuration_sha256": profile["plan_configuration_sha256"],
        },
        "boundaries": {
            "read_leaf": "knowledge_support",
            "write_leaf": "knowledge_sink",
            "grant_required": True,
            "grant_included": False,
            "model_invoked": False,
            "write_performed": False,
        },
        "sink_operations": sink_operations,
        "steps": [
            {
                "ordinal": ordinal,
                "leaf": leaf,
                "operation": operation,
                "write": write,
            }
            for ordinal, (leaf, operation, write) in enumerate(step_specs, start=1)
        ],
        "recommended_skill": "deeplaw-compile-source",
        "compatibility_skill": "compile-living-wiki",
        "audit_head": audit_head,
        "write_performed": False,
    }
    _validate_contract("compilation-handoff.v1.schema.json", result)
    return result
