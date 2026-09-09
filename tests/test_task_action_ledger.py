"""Action results survive a lost response without authorizing another invocation."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding


def _ready(tmp_path: Path) -> tuple[Path, str, dict]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="action-state", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="synthetic-action-host", operations=("record_run",),
        )["grant_id"]
    binding = build_task_context_binding("a" * 64, "b" * 64)
    return root, grant, binding


def _request(binding: dict, status: str, prior: str | None = None) -> dict:
    state = {
        "schema_version": "deeplaw.task-action-state/v1",
        "action_id": "synthetic_action",
        "request_sha256": "c" * 64,
        "status": status,
        "outcome_sha256": "d" * 64 if status in {"succeeded", "failed"} else None,
        "expected_prior_run_id": prior,
        "evidence_level": "host_reported",
    }
    request = {
        "operation": "record_run", "idempotency_key": f"action-{status}",
        "run_id": f"action-run-{status}", "confirm_no_case_data": True,
        "task": "Record one synthetic external action observation.",
        "host_id": "synthetic-host",
        "status": status if status in {"succeeded", "failed"} else "partial",
        "input_sha256": state["request_sha256"],
        "run_metadata": {"task_binding": binding, "action_state": state},
    }
    if state["outcome_sha256"] is not None:
        request["output_sha256"] = state["outcome_sha256"]
    return request


def _write(root: Path, grant: str, request: dict) -> dict:
    return handle_knowledge_sink(request, grant_id=grant, vault_path=root)["result"]


def test_action_unknown_survives_restart_and_terminal_state_is_not_repeated(tmp_path: Path) -> None:
    root, grant, binding = _ready(tmp_path)
    initial = _write(root, grant, _request(binding, "not_executed"))
    unknown_request = _request(binding, "initiated_unknown", initial["run_id"])
    unknown = _write(root, grant, unknown_request)
    actual_invocations = ["synthetic_action"]  # Independent Host side effect; response was lost.
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        observed = store.task_action_states(
            task_binding=binding, scope="project", max_sensitivity="private",
        )
        assert observed["entries"][0]["resume_requirement"] == "verify_external_state"
        assert store.verify()["valid"] is True
    assert len(actual_invocations) == 1
    # Verification of the Host oracle resolves the unknown result; it does not execute it again.
    succeeded = _write(root, grant, _request(binding, "succeeded", unknown["run_id"]))
    assert succeeded["schema_version"] == "deeplaw.knowledge-run-record/v2"
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        observed = store.task_action_states(
            task_binding=binding, scope="project", max_sensitivity="private",
        )
        assert observed["entries"][0]["resume_requirement"] == "do_not_repeat"
        assert store.verify()["valid"] is True
    with pytest.raises(ValueError, match=r"predecessor|terminal"):
        _write(root, grant, _request(binding, "failed", unknown["run_id"]))
    assert len(actual_invocations) == 1


def test_action_predecessor_cas_and_route_boundary_leave_no_partial_run(tmp_path: Path) -> None:
    root, grant, binding = _ready(tmp_path)
    initial = _write(root, grant, _request(binding, "not_executed"))
    wrong = _request(binding, "initiated_unknown", "run_missing")
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        before = store.audit_head
    with pytest.raises(ValueError, match="predecessor"):
        _write(root, grant, wrong)
    wrong_route = deepcopy(wrong)
    wrong_route["run_metadata"]["action_state"]["expected_prior_run_id"] = initial["run_id"]
    wrong_route["run_metadata"]["task_binding"] = build_task_context_binding("a" * 64, "e" * 64)
    with pytest.raises(PermissionError, match="boundary"):
        _write(root, grant, wrong_route)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.audit_head == before
        assert store.task_action_states(
            task_binding=wrong_route["run_metadata"]["task_binding"],
            scope="project", max_sensitivity="private",
        )["entries"] == []
        assert store.verify()["valid"] is True


def test_cli_action_resume_checkpoint_and_host_projection(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    from deeplaw.task_continuity import (
        bind_host_session,
        checkpoint_task,
        record_task_action,
        resolve_host_continuity_capsule,
        resume_task,
        start_task,
    )
    from deeplaw.util import canonical_json, sha256_bytes

    workspace = tmp_path / "repository"
    workspace.mkdir()
    (workspace / "tracked.txt").write_text("stable\n")
    for arguments in (
        ("init", "-q"), ("add", "tracked.txt"),
        ("-c", "user.name=DeepLaw Test", "-c", "user.email=deeplaw@example.invalid",
         "commit", "-q", "-m", "synthetic fixture"),
    ):
        subprocess.run(["git", *arguments], cwd=workspace, capture_output=True, check=True)
    root, _, _ = _ready(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="synthetic-continuity-host", operations=("record_run", "remember"),
        )["grant_id"]
    handle = start_task(
        vault_path=root, project="Synthetic", task="Keep the approved action from repeating.",
        workspace=workspace,
    )["task_handle"]
    common = {
        "vault_path": root, "task_handle": handle, "workspace": workspace,
        "grant_id": grant, "confirm_no_case_data": True,
    }
    checkpoint_task(
        **common, idempotency_key="before-action", summary="Preserve action state.",
        next_action="Inspect the action receipt before continuing.",
        expires_at="2099-01-01T00:00:00Z",
    )
    bind_host_session(
        **common, host="codex", session_sha256="f" * 64, idempotency_key="action-host",
    )
    completed = subprocess.run(
        [sys.executable, "-m", "deeplaw", "knowledge", "--format", "jsonl", "task", "record-action",
         "--vault", str(root), "--task-handle", handle, "--workspace", str(workspace),
         "--grant-id", grant, "--idempotency-key", "cli-action-start",
         "--action-id", "external_once",
         "--request-sha256", "c" * 64, "--status", "not_executed", "--host-id", "synthetic-host",
         "--confirm-no-case-data"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    initial = json.loads(completed.stdout)
    unknown = record_task_action(
        **common, idempotency_key="cli-action-unknown", action_id="external_once",
        request_sha256="c" * 64, status="initiated_unknown",
        expected_prior_run_id=initial["run_id"], host_id="synthetic-host",
    )
    invocations = []

    def synthetic_external_action() -> dict:
        invocations.append({"action_id": "external_once", "parameter": "approved-value"})
        return {"success": True, "invocations": invocations.copy()}

    oracle = synthetic_external_action()  # The Host loses the response after this side effect.
    # Explicit public-protocol handoff to a second synthetic Host identity.
    bind_host_session(
        **common, host="opencode", session_sha256="e" * 64, idempotency_key="action-host-b",
    )
    resumed = resume_task(vault_path=root, task_handle=handle, workspace=workspace)
    assert resumed["status"] == "gap"
    assert "action_outcome_unknown" in resumed["gap_codes"]
    assert resumed["action_states"][0]["resume_requirement"] == "verify_external_state"
    assert resumed["provider_capsule"]["capsule"]["statements"] == []
    native_unknown = resolve_host_continuity_capsule(
        vault_path=root, host="opencode", session_sha256="e" * 64, workspace=workspace,
    )
    assert native_unknown["schema_version"] == "deeplaw.host-continuity-capsule/v2"
    assert native_unknown["status"] == "gap"
    assert native_unknown["action_states"][0]["resume_requirement"] == "verify_external_state"
    # The synthetic Host verifies its existing oracle instead of invoking the action again.
    assert oracle["success"] is True
    final_arguments = {
        **common, "idempotency_key": "cli-action-succeeded", "action_id": "external_once",
        "request_sha256": "c" * 64, "status": "succeeded",
        "expected_prior_run_id": unknown["run_id"], "host_id": "synthetic-host-b",
        "outcome_sha256": sha256_bytes(canonical_json(oracle).encode()),
    }
    succeeded = record_task_action(**final_arguments)
    replay = record_task_action(**final_arguments)
    assert replay["run_id"] == succeeded["run_id"]
    assert replay["idempotent_replay"] is True
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.get_run(initial["run_id"])["host_id"] == "synthetic-host"
        assert store.get_run(succeeded["run_id"])["host_id"] == "synthetic-host-b"
        assert store.verify()["valid"]
    stale = resume_task(vault_path=root, task_handle=handle, workspace=workspace)
    assert stale["status"] == "gap"
    assert "action_state_changed" in stale["gap_codes"]
    checkpoint_task(
        **common, idempotency_key="after-action", summary="External outcome checked.",
        next_action="Continue to the next independent task.", expires_at="2099-01-01T00:00:00Z",
    )
    resumed = resume_task(vault_path=root, task_handle=handle, workspace=workspace)
    assert resumed["status"] == "admitted"
    assert resumed["action_states"][0]["resume_requirement"] == "do_not_repeat"
    native = resolve_host_continuity_capsule(
        vault_path=root, host="opencode", session_sha256="e" * 64, workspace=workspace,
    )
    assert native["schema_version"] == "deeplaw.host-continuity-capsule/v2", native
    assert native["action_states"][0]["resume_requirement"] == "do_not_repeat"
    assert len(canonical_json(native).encode()) <= 1400
    assert "receipt_sha256" not in canonical_json(native)
    assert invocations == [{"action_id": "external_once", "parameter": "approved-value"}]


@pytest.mark.parametrize("terminal", [None, "failed"])
def test_action_state_snapshot_and_projection_rebuild_preserve_outcome(
    tmp_path: Path, terminal: str | None,
) -> None:
    from deeplaw.knowledge_autonomy import create_autonomous_snapshot, restore_autonomous_snapshot

    root, grant, binding = _ready(tmp_path)
    initial = _write(root, grant, _request(binding, "not_executed"))
    unknown = _write(root, grant, _request(binding, "initiated_unknown", initial["run_id"]))
    if terminal is not None:
        _write(root, grant, _request(binding, terminal, unknown["run_id"]))
    snapshot = tmp_path / "snapshot"
    create_autonomous_snapshot(root, snapshot)
    restored = tmp_path / "restored"
    restore_autonomous_snapshot(restored, snapshot=snapshot, confirm=True)
    with AutonomousKnowledgeStore(restored, read_only=False) as store:
        before = store.audit_head
        store.rebuild_checkpoint_route_projection()
        states = store.task_action_states(
            task_binding=binding, scope="project", max_sensitivity="private",
        )
        assert states["entries"][0]["resume_requirement"] == (
            "review_failure" if terminal else "verify_external_state"
        )
        assert store.audit_head == before
        assert store.verify()["valid"] is True


def test_raw_sink_action_replay_retains_generated_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import deeplaw.knowledge_autonomy as autonomy

    root, grant, binding = _ready(tmp_path)
    request = _request(binding, "not_executed")
    request.pop("run_id")
    first = _write(root, grant, request)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        before = store.audit_head
    monkeypatch.setattr(autonomy, "utc_now", lambda: "2099-01-01T00:00:00Z")
    replay = _write(root, grant, request)
    assert replay["idempotent_replay"] is True
    assert replay["run_id"] == first["run_id"]
    assert replay["receipt_sha256"] == first["receipt_sha256"]
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.audit_head == before


def test_action_replay_recovers_generated_fields_after_concurrent_commit(tmp_path, monkeypatch):
    import deeplaw.knowledge_autonomy as autonomy

    root, grant, binding = _ready(tmp_path)
    request = _request(binding, "not_executed")
    request.pop("run_id")
    original = AutonomousKnowledgeStore._grant
    interleaved = []

    def grant_with_concurrent_commit(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if kwargs.get("operation") == "record_run" and not interleaved:
            interleaved.append(None)
            monkeypatch.setattr(autonomy, "utc_now", lambda: "2099-01-01T00:00:00Z")
            interleaved[0] = _write(root, grant, request)
        return result

    monkeypatch.setattr(AutonomousKnowledgeStore, "_grant", grant_with_concurrent_commit)
    replay = _write(root, grant, request)
    assert replay["idempotent_replay"] is True
    assert replay["receipt_sha256"] == interleaved[0]["receipt_sha256"]
    assert replay["started_at"] == "2099-01-01T00:00:00Z"
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.connection.execute(
            "SELECT count(*) FROM knowledge_run_records_v4"
        ).fetchone()[0] == 1
        assert store.verify()["valid"]


def test_host_reported_success_cannot_verify_a_working_checkpoint(tmp_path):
    root, grant, binding = _ready(tmp_path)
    initial = _write(root, grant, _request(binding, "not_executed"))
    unknown = _write(root, grant, _request(binding, "initiated_unknown", initial["run_id"]))
    success = _write(root, grant, _request(binding, "succeeded", unknown["run_id"]))
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="synthetic-action-host", operations=("remember",),
        )["grant_id"]
        before = store.audit_head
        with pytest.raises(ValueError, match="successful task-bound Run"):
            store.remember(
                grant_id=grant, idempotency_key="action-as-checkpoint",
                title="Synthetic checkpoint", body="The Host reported success.",
                memory_type="working", expires_at="2099-01-01T00:00:00Z",
                run_id=success["run_id"], confirm_no_case_data=True,
            )
        assert store.audit_head == before
        assert store.verify()["valid"]
