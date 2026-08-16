from __future__ import annotations

from pathlib import Path

from jsonschema import Draft202012Validator

from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_mcp_server import knowledge_tool_definition
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.util import canonical_json


def _development_vault(tmp_path: Path) -> tuple[Path, str, list[dict[str, object]]]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="run-timeline-reproduction", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="timeline-development-owner",
            allowed_scope="project",
            operations=("record_run",),
        )

    run_requests = (
        {
            "run_id": "run-orion-release",
            "idempotency_key": "timeline-orion-release",
            "task": "Prepare Orion release artifact",
            "status": "succeeded",
            "started_at": "2025-01-01T09:00:00Z",
            "ended_at": "2025-01-01T09:05:00Z",
            "run_metadata": {
                "task_kind": "release",
                "artifact_ids": ["artifact-A"],
            },
        },
        {
            "run_id": "run-orion-rollback",
            "idempotency_key": "timeline-orion-rollback",
            "task": "Prepare Orion rollback artifact",
            "status": "failed",
            "started_at": "2025-01-02T09:00:00Z",
            "ended_at": "2025-01-02T09:05:00Z",
            "run_metadata": {
                "task_kind": "rollback",
                "artifact_ids": ["artifact-B"],
            },
        },
    )
    responses = [
        handle_knowledge_sink(
            {
                "operation": "record_run",
                "confirm_no_case_data": True,
                "host_id": "development-host",
                "model_id": "development-model",
                "scope": "project",
                "sensitivity": "private",
                **request,
            },
            grant_id=grant["grant_id"],
            vault_path=root,
        )
        for request in run_requests
    ]
    return root, grant["grant_id"], responses


def test_development_characterization_reproduced_missing_public_seam(
    tmp_path: Path,
) -> None:
    """Freeze the v0.13 gap; this is not a Timeline implementation or release claim."""

    root, _grant_id, responses = _development_vault(tmp_path)

    assert [response["operation"] for response in responses] == [
        "record_run",
        "record_run",
    ]
    results = [response["result"] for response in responses]
    assert [result["run_id"] for result in results] == [
        "run-orion-release",
        "run-orion-rollback",
    ]
    assert [result["status"] for result in results] == ["succeeded", "failed"]
    assert [result["metadata"]["artifact_ids"] for result in results] == [
        ["artifact-A"],
        ["artifact-B"],
    ]

    # The sink returns a bounded, content-minimized receipt: task text is not
    # copied into the response or provider-visible mutation result.
    serialized = canonical_json(responses)
    assert "Prepare Orion release artifact" not in serialized
    assert "Prepare Orion rollback artifact" not in serialized
    assert all("task_sha256" in result for result in results)

    # Development characterization: the stable Python facade has no owner read
    # seam for locating a historical Run by semantic task, time, status, or
    # artifact. Do not fall back to AutonomousKnowledgeStore internals or SQL.
    with KnowledgeOS.open(root) as knowledge_os:
        assert not any(
            hasattr(knowledge_os, attribute)
            for attribute in ("runs", "run_receipts", "timeline", "search_runs")
        )

    schema = knowledge_tool_definition(autonomous=True).inputSchema
    validator = Draft202012Validator(schema)
    for operation in ("run_timeline", "run_list"):
        errors = list(
            validator.iter_errors(
                {"operation": operation, "query": "find the older Orion run"}
            )
        )
        assert errors, f"unsupported operation unexpectedly accepted: {operation}"

    # release_gate_passed=false; claim_eligible=false; this test only freezes
    # the missing public seam as a development finding.
