from __future__ import annotations

from pathlib import Path
from typing import Any

from benchmarks.hosts.deterministic_fake_agent import compile_with_fake_mcp_agent
from deeplaw.compilation.models import SEMANTIC_COMPILER_GRANT_OPERATIONS
from deeplaw.compilation_handoff import build_compilation_handoff
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_service import source_knowledge_status
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault


def _clone_compilation_attempt(
    store: AutonomousKnowledgeStore,
    *,
    base_run_id: str,
    run_id: str,
    source_revision_id: str,
    status: str,
    updated_at: str,
) -> None:
    columns = [
        str(row["name"])
        for row in store.connection.execute(
            "PRAGMA table_info(source_compilation_runs_v1)"
        )
    ]
    row = store.connection.execute(
        "SELECT * FROM source_compilation_runs_v1 WHERE compilation_run_id = ?",
        (base_run_id,),
    ).fetchone()
    assert row is not None
    values = dict(row)
    source = store.connection.execute(
        """
        SELECT source_revisions_v2.source_key, compilations_v2.compilation_id
        FROM source_revisions_v2
        JOIN compilations_v2 USING(source_revision_id)
        WHERE source_revisions_v2.source_revision_id = ?
        """,
        (source_revision_id,),
    ).fetchone()
    assert source is not None
    values.update(
        {
            "compilation_run_id": run_id,
            "source_revision_id": source_revision_id,
            "source_key": source["source_key"],
            "source_ir_compilation_id": source["compilation_id"],
            "prompt_template_id": f"pass14-{run_id}",
            "request_sha256": run_id.removeprefix("compilationrun_").ljust(64, "0"),
            "status": status,
            "resumable": int(status in {"projection_pending", "failed"}),
            "output_set_sha256": None,
            "receipt_sha256": None,
            "failure_stage": "model" if status == "failed" else None,
            "failure_sha256": "f" * 64 if status == "failed" else None,
            "created_at": updated_at,
            "updated_at": updated_at,
            "committed_at": updated_at
            if status in {"committed", "projection_pending", "succeeded"}
            else None,
            "completed_at": updated_at if status in {"succeeded", "failed"} else None,
        }
    )
    placeholders = ", ".join("?" for _ in columns)
    store.connection.execute(
        f"INSERT INTO source_compilation_runs_v1 ({', '.join(columns)}) "
        f"VALUES ({placeholders})",
        tuple(values[column] for column in columns),
    )
    store.connection.commit()


def _public_statuses(vault: Path, source_revision_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with KnowledgeVault(vault, read_only=True) as legacy:
        status = source_knowledge_status(
            legacy,
            source_revision_ids=(source_revision_id,),
        )
    return status, build_compilation_handoff(
        vault,
        source_revision_id=source_revision_id,
    )


def _assert_shared_state(
    status: dict[str, Any],
    handoff: dict[str, Any],
    *,
    source_state: str,
    committed: bool,
    admissible: bool,
    latest: list[dict[str, str]],
    projection: str,
) -> None:
    assert status["state"] == handoff["source_status"] == source_state
    assert status["canonical_knowledge_committed"] is committed
    assert handoff["canonical_knowledge_committed"] is committed
    assert status["canonical_knowledge_admissible"] is admissible
    assert handoff["canonical_knowledge_admissible"] is admissible
    assert status["latest_compilation_attempts"] == latest
    assert handoff["latest_compilation_attempts"] == latest
    assert status["wiki_projection_status"] == projection
    assert handoff["wiki_projection_status"] == projection


def test_public_compilation_status_seams_agree_across_mixed_history(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    first_source = tmp_path / "first.md"
    second_source = tmp_path / "second.md"
    first_source.write_text("# First\nThe canonical fact remains governed.\n", encoding="utf-8")
    second_source.write_text("# Second\nThis attempt fails before commit.\n", encoding="utf-8")
    initialize_knowledge_vault(vault, name="mixed-history", scope="project")
    initialize_autonomous_core(vault)
    with KnowledgeVault(vault, read_only=False) as legacy:
        first = compile_source(
            legacy,
            first_source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        first_manifest = legacy.source_review_manifest(first["source"]["source_id"])
        legacy.approve_source_assets(
            first["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=first_manifest["review_manifest_sha256"],
        )
        second = compile_source(
            legacy,
            second_source,
            source_kind="document",
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="pass14-mixed-history",
            operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
        )["grant_id"]

    succeeded = compile_with_fake_mcp_agent(
        vault=vault,
        grant_id=grant_id,
        source_revision_id=first["identity"]["source_revision_id"],
    )
    successful_run_id = succeeded["compilation_run_id"]
    failed_after_success = "compilationrun_fa11ed000000000000000001"
    failed_without_success = "compilationrun_fa11ed000000000000000002"
    first_revision = first["identity"]["source_revision_id"]
    second_revision = second["identity"]["source_revision_id"]

    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        _clone_compilation_attempt(
            store,
            base_run_id=successful_run_id,
            run_id=failed_after_success,
            source_revision_id=first_revision,
            status="failed",
            updated_at="2099-01-01T00:00:01Z",
        )
        _clone_compilation_attempt(
            store,
            base_run_id=successful_run_id,
            run_id=failed_without_success,
            source_revision_id=second_revision,
            status="failed",
            updated_at="2099-01-01T00:00:02Z",
        )

    status, handoff = _public_statuses(vault, first_revision)
    _assert_shared_state(
        status,
        handoff,
        source_state="stale_or_blocked",
        committed=True,
        admissible=True,
        latest=[
            {
                "source_revision_id": first_revision,
                "compilation_run_id": failed_after_success,
                "status": "failed",
            }
        ],
        projection="blocked",
    )

    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        store.connection.execute(
            """
            UPDATE source_compilation_runs_v1
            SET status = 'projection_pending', failure_stage = NULL,
                failure_sha256 = NULL, committed_at = updated_at,
                completed_at = NULL
            WHERE compilation_run_id = ?
            """,
            (failed_after_success,),
        )
        store.connection.commit()
    status, handoff = _public_statuses(vault, first_revision)
    _assert_shared_state(
        status,
        handoff,
        source_state="compiled",
        committed=True,
        admissible=True,
        latest=[
            {
                "source_revision_id": first_revision,
                "compilation_run_id": failed_after_success,
                "status": "projection_pending",
            }
        ],
        projection="pending",
    )

    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        store.connection.execute(
            """
            UPDATE source_compilation_runs_v1
            SET status = 'succeeded', completed_at = updated_at
            WHERE compilation_run_id = ?
            """,
            (failed_after_success,),
        )
        store.connection.execute(
            """
            UPDATE source_lifecycle SET status = 'removed'
            WHERE source_id = (
                SELECT legacy_source_id FROM source_revision_bindings_v2
                WHERE source_revision_id = ?
            )
            """,
            (first_revision,),
        )
        store.connection.commit()
    status, handoff = _public_statuses(vault, first_revision)
    _assert_shared_state(
        status,
        handoff,
        source_state="stale_or_blocked",
        committed=True,
        admissible=False,
        latest=[
            {
                "source_revision_id": first_revision,
                "compilation_run_id": failed_after_success,
                "status": "succeeded",
            }
        ],
        projection="ready",
    )

    status, handoff = _public_statuses(vault, second_revision)
    _assert_shared_state(
        status,
        handoff,
        source_state="stale_or_blocked",
        committed=False,
        admissible=False,
        latest=[
            {
                "source_revision_id": second_revision,
                "compilation_run_id": failed_without_success,
                "status": "failed",
            }
        ],
        projection="blocked",
    )
