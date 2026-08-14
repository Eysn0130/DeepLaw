from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    (repository / "tracked.txt").write_text("stable\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(
        repository,
        "-c",
        "user.name=DeepLaw Test",
        "-c",
        "user.email=deeplaw@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    return repository


def _selected_ids(result: dict[str, object]) -> set[str]:
    provider = result["provider_capsule"]
    assert isinstance(provider, dict)
    capsule = provider["capsule"]
    assert isinstance(capsule, dict)
    statements = capsule.get("statements", [])
    assert isinstance(statements, list)
    return {
        str(item["knowledge_id"])
        for item in statements
        if isinstance(item, dict) and isinstance(item.get("knowledge_id"), str)
    }


def _gap_codes(result: dict[str, object]) -> set[str]:
    provider = result["provider_capsule"]
    assert isinstance(provider, dict)
    capsule = provider["capsule"]
    assert isinstance(capsule, dict)
    gaps = capsule.get("gaps", [])
    assert isinstance(gaps, list)
    return {
        str(item["code"])
        for item in gaps
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }


def test_task_handle_drives_restart_fork_compaction_checkpoint_and_forget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeplaw.closed_mcp_launcher import closed_mcp_environment
    from deeplaw.host_connect import build_host_connect_plan
    from deeplaw.task_continuity import (
        checkpoint_task,
        decode_task_handle,
        forget_task,
        fork_task,
        resume_task,
        start_task,
    )

    repository = _repository(tmp_path)
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="task-driver", scope="project")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="task-driver-test",
            operations=("record_run", "remember", "forget"),
        )["grant_id"]

    started = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Finish the bounded task driver.",
        workspace=repository,
    )
    repeated = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Finish the bounded task driver.",
        workspace=repository,
    )
    handle = str(started["task_handle"])
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts/task-handle.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(decode_task_handle(handle))
    assert handle == repeated["task_handle"]
    assert started["write_performed"] is False
    assert str(repository) not in handle
    assert "Finish the bounded task driver" not in handle
    plan = build_host_connect_plan(
        host="codex",
        vault_path=vault,
        task_handle=handle,
        owner_home=tmp_path / "owner-home",
    )
    assert plan["task_binding_configured"] is False
    assert plan["task_handle_configured"] is True
    assert plan["task_handle_sha256"] == started["task_handle_sha256"]
    assert str(vault) not in str(plan)
    assert str(repository) not in str(plan)
    monkeypatch.chdir(repository)
    with closed_mcp_environment(
        surface="knowledge_support",
        vault_path=vault,
        task_handle=handle,
    ) as launch:
        assert "DEEPLAW_TASK_BINDING" in launch.environment

    checkpoint = checkpoint_task(
        vault_path=vault,
        task_handle=handle,
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="task-driver-checkpoint-1",
        summary="The shared closed launcher is the production seam.",
        next_action="Run the bounded no-model registration check.",
        expires_at="2099-01-01T00:00:00Z",
        decisions=("Keep task route and snapshot identity separate.",),
        gaps=("Native Host lifecycle qualification remains pending.",),
        artifact_refs=("pass20-driver",),
        confirm_no_case_data=True,
    )
    knowledge_id = str(checkpoint["knowledge_id"])
    assert checkpoint["write_performed"] is True
    assert checkpoint["sink_leaf"] == "knowledge_sink"
    later_checkpoint = checkpoint_task(
        vault_path=vault,
        task_handle=handle,
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="task-driver-checkpoint-2",
        summary="The retained artifact job now owns the candidate bytes.",
        next_action="Run full verification.",
        expires_at="2099-01-01T00:00:00Z",
        confirm_no_case_data=True,
    )
    assert later_checkpoint["knowledge_id"] == knowledge_id
    assert later_checkpoint["run_id"] != checkpoint["run_id"]

    resumed = resume_task(
        vault_path=vault,
        task_handle=handle,
        workspace=repository,
    )
    assert resumed["status"] == "admitted"
    assert _selected_ids(resumed) == {knowledge_id}
    assert resumed["native_host_lifecycle_observed"] is False

    continued = fork_task(
        vault_path=vault,
        task_handle=handle,
        workspace=repository,
        mode="continue-parent",
    )
    assert continued["task_handle"] == handle
    child = fork_task(
        vault_path=vault,
        task_handle=handle,
        workspace=repository,
        mode="child-task",
        child_task="Implement the child-only validation.",
    )
    assert child["task_handle"] != handle
    assert child["parent_task_lineage_sha256"] == started["task_lineage_sha256"]

    compacted = resume_task(
        vault_path=vault,
        task_handle=handle,
        workspace=repository,
        operation="compaction",
    )
    assert compacted["status"] == "admitted"
    assert compacted["transcript_copied"] is False
    assert _selected_ids(compacted) == {knowledge_id}

    (repository / "tracked.txt").write_text("checkpointed\n", encoding="utf-8")
    changed_checkpoint = checkpoint_task(
        vault_path=vault,
        task_handle=handle,
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="task-driver-checkpoint-3",
        summary="The current changed workspace reached another explicit success boundary.",
        next_action="Reject any later uncheckpointed snapshot.",
        expires_at="2099-01-01T00:00:00Z",
        confirm_no_case_data=True,
    )
    assert changed_checkpoint["knowledge_id"] == knowledge_id
    assert resume_task(
        vault_path=vault,
        task_handle=handle,
        workspace=repository,
    )["status"] == "admitted"

    wrong = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="A different task line.",
        workspace=repository,
    )
    wrong_resume = resume_task(
        vault_path=vault,
        task_handle=str(wrong["task_handle"]),
        workspace=repository,
    )
    assert wrong_resume["status"] == "gap"
    assert knowledge_id not in _selected_ids(wrong_resume)

    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    stale = resume_task(
        vault_path=vault,
        task_handle=handle,
        workspace=repository,
    )
    assert stale["status"] == "gap"
    assert _gap_codes(stale).intersection({"workspace_diverged", "stale_checkpoint"})
    assert knowledge_id not in _selected_ids(stale)

    (repository / "tracked.txt").write_text("checkpointed\n", encoding="utf-8")
    forgotten = forget_task(
        vault_path=vault,
        task_handle=handle,
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="task-driver-forget-1",
        reason="Owner requested removal of the working checkpoint.",
        confirm_no_case_data=True,
    )
    assert forgotten["write_performed"] is True
    assert forgotten["sink_leaf"] == "knowledge_sink"
    after_forget = resume_task(
        vault_path=vault,
        task_handle=handle,
        workspace=repository,
    )
    assert after_forget["status"] == "gap"
    assert knowledge_id not in _selected_ids(after_forget)
