from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.subprocess_environment import _build_subprocess_environment

REPOSITORY = Path(__file__).resolve().parents[1]


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


def _vault(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="pass22-continuity", scope="project")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant_id = str(
            store.enable_grant(
                writer_id="pass22-continuity",
                operations=("record_run", "remember", "forget"),
            )["grant_id"]
        )
    return vault, grant_id


def test_ordinary_ignored_files_never_enter_workspace_snapshot_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeplaw import task_continuity

    repository = _repository(tmp_path)
    (repository / ".gitignore").write_text(
        "local.cache\n.venv/\nignored-tree/\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".gitignore")
    _git(
        repository,
        "-c",
        "user.name=DeepLaw Test",
        "-c",
        "user.email=deeplaw@example.invalid",
        "commit",
        "-q",
        "-m",
        "ignore generated state",
    )
    (repository / "local.cache").write_text("ignored=one\n", encoding="utf-8")
    credentials = repository / ".venv" / "credentials.py"
    credentials.parent.mkdir()
    credentials.write_text("TOKEN = 'ignored'\n", encoding="utf-8")
    ignored_tree = repository / "ignored-tree"
    ignored_tree.mkdir()
    for index in range(4200):
        (ignored_tree / f"generated-{index:04d}.txt").write_text("x", encoding="utf-8")

    original_git = task_continuity._git

    def audit_git(workspace: Path, *arguments: str, **kwargs: object) -> bytes:
        if "--ignored" in arguments:
            assert "--directory" in arguments
            assert "--no-empty-directory" in arguments
        return original_git(workspace, *arguments, **kwargs)

    monkeypatch.setattr(task_continuity, "_git", audit_git)
    before = task_continuity.workspace_snapshot_receipt(repository)
    assert before["status"] == "ready"

    (repository / "local.cache").write_text("ignored=two\n", encoding="utf-8")
    credentials.write_text("TOKEN = 'changed-but-ignored'\n", encoding="utf-8")
    (ignored_tree / "generated-0000.txt").write_text("changed", encoding="utf-8")
    after = task_continuity.workspace_snapshot_receipt(repository)
    assert after == before


def test_current_deeplaw_repository_snapshot_excludes_or_gaps_on_ignored_state() -> None:
    from deeplaw.task_continuity import workspace_snapshot_receipt

    receipt = workspace_snapshot_receipt(REPOSITORY)
    assert receipt["status"] in {"ready", "gap"}
    if receipt["status"] == "gap":
        assert receipt["gap"]["code"] == "workspace_secret_unverifiable"


def test_child_task_text_locates_after_checkpoint_and_conflicts_are_ambiguous(
    tmp_path: Path,
) -> None:
    from deeplaw.task_continuity import (
        checkpoint_task,
        fork_task,
        locate_task,
        resume_task,
        start_task,
    )

    repository = _repository(tmp_path)
    child_worktree = tmp_path / "child-worktree"
    conflict_worktree = tmp_path / "conflict-worktree"
    _git(repository, "worktree", "add", "-q", "--detach", str(child_worktree), "HEAD")
    _git(repository, "worktree", "add", "-q", "--detach", str(conflict_worktree), "HEAD")
    vault, grant_id = _vault(tmp_path)

    parent = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Parent route",
        workspace=repository,
    )
    child = fork_task(
        vault_path=vault,
        task_handle=str(parent["task_handle"]),
        workspace=repository,
        child_workspace=child_worktree,
        mode="child-task",
        child_task="Child qualification route",
    )
    checkpoint_task(
        vault_path=vault,
        task_handle=str(child["task_handle"]),
        workspace=child_worktree,
        grant_id=grant_id,
        idempotency_key="pass22-child-checkpoint",
        task="Child qualification route",
        summary="The child route owns an independent checkpoint.",
        next_action="Locate and resume by child task text.",
        expires_at="2099-01-01T00:00:00Z",
        confirm_no_case_data=True,
    )

    located = locate_task(
        vault_path=vault,
        project="DeepLaw",
        task="Child qualification route",
        workspace=child_worktree,
    )
    assert located["status"] == "exact"
    assert located["parent_task_lineage_sha256"] == parent["task_lineage_sha256"]
    resumed = resume_task(
        vault_path=vault,
        project="DeepLaw",
        task="Child qualification route",
        workspace=child_worktree,
    )
    assert resumed["status"] == "admitted"
    assert resume_task(
        vault_path=vault,
        project="DeepLaw",
        task="Child qualification route",
        workspace=repository,
    )["status"] == "not_found"

    for index, parent_task in enumerate(("Conflict parent A", "Conflict parent B")):
        conflict_parent = start_task(
            vault_path=vault,
            project="DeepLaw",
            task=parent_task,
            workspace=repository,
        )
        conflict_child = fork_task(
            vault_path=vault,
            task_handle=str(conflict_parent["task_handle"]),
            workspace=repository,
            child_workspace=conflict_worktree,
            mode="child-task",
            child_task="Same visible child text",
        )
        checkpoint_task(
            vault_path=vault,
            task_handle=str(conflict_child["task_handle"]),
            workspace=conflict_worktree,
            grant_id=grant_id,
            idempotency_key=f"pass22-conflict-{index}",
            task="Same visible child text",
            summary="This child has a distinct parent lineage.",
            next_action="Do not select newest on conflict.",
            expires_at="2099-01-01T00:00:00Z",
            confirm_no_case_data=True,
        )
    ambiguous = locate_task(
        vault_path=vault,
        project="DeepLaw",
        task="Same visible child text",
        workspace=conflict_worktree,
    )
    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["gap"] == {"code": "task_line_ambiguous"}


def test_timeline_retains_identity_only_history_after_drift_and_forget(
    tmp_path: Path,
) -> None:
    from deeplaw.task_continuity import (
        checkpoint_task,
        forget_task,
        start_task,
        timeline_task,
    )

    repository = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    task_text = "Audit continuity after workspace drift and forget."
    started = start_task(
        vault_path=vault,
        project="DeepLaw",
        task=task_text,
        workspace=repository,
    )
    checkpoints: list[dict[str, object]] = []
    for index in range(2):
        checkpoints.append(
            checkpoint_task(
                vault_path=vault,
                task_handle=str(started["task_handle"]),
                workspace=repository,
                grant_id=grant_id,
                idempotency_key=f"pass22-timeline-{index}",
                task=task_text,
                summary=f"Checkpoint {index} remains identity-auditable.",
                next_action="Keep content outside the timeline.",
                expires_at="2099-01-01T00:00:00Z",
                artifact_refs=(f"report_timeline_{index}",),
                confirm_no_case_data=True,
            )
        )

    (repository / "tracked.txt").write_text("drifted\n", encoding="utf-8")
    drifted = timeline_task(
        vault_path=vault,
        project="DeepLaw",
        task=task_text,
        workspace=repository,
        limit=3,
    )
    assert drifted["status"] == "gap"
    assert drifted["gap"] == {"code": "workspace_diverged"}
    assert 1 <= len(drifted["entries"]) <= 3
    assert drifted["timeline_truncated"] is True
    assert {entry["entry_type"] for entry in drifted["entries"]} <= {
        "run",
        "checkpoint",
        "ledger",
    }
    assert "report_timeline_0" in str(drifted) or "report_timeline_1" in str(drifted)
    assert str(repository) not in str(drifted)
    assert task_text not in str(drifted)

    forgotten = forget_task(
        vault_path=vault,
        task_handle=str(started["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="pass22-timeline-forget",
        reason="Owner requested forgetting after drift.",
        confirm_no_case_data=True,
    )
    after_forget = timeline_task(
        vault_path=vault,
        project="DeepLaw",
        task=task_text,
        workspace=repository,
        limit=20,
    )
    assert after_forget["status"] == "gap"
    assert after_forget["gap"] == {"code": "forgotten"}
    assert after_forget["entries"]
    assert checkpoints[-1]["revision_id"] in str(after_forget)
    assert forgotten["revision_id"] not in str(after_forget)
    assert str(repository) not in str(after_forget)

    wrong_route = timeline_task(
        vault_path=vault,
        project="DeepLaw",
        task="A different task route.",
        workspace=repository,
    )
    assert wrong_route["status"] == "not_found"
    assert wrong_route["entries"] == []


@pytest.mark.parametrize(
    "artifact_id",
    [
        "C:/private/report.json",
        "/absolute/private/report.json",
        "../outside",
        "deployment-secret",
        "provider_token",
        "signing-key",
    ],
)
def test_record_run_shared_commit_seam_rejects_unsafe_artifact_ids(
    tmp_path: Path,
    artifact_id: str,
) -> None:
    vault, grant_id = _vault(tmp_path)
    with (
        AutonomousKnowledgeStore(vault, read_only=False) as store,
        pytest.raises(ValueError, match="artifact"),
    ):
        store.record_run(
            grant_id=grant_id,
            idempotency_key=f"pass22-unsafe-{abs(hash(artifact_id))}",
            task="Reject unsafe direct Run artifact references.",
            host_id="pytest-pass22",
            status="succeeded",
            metadata={"artifact_ids": [artifact_id]},
            confirm_no_case_data=True,
        )


def test_record_run_shared_commit_seam_keeps_safe_opaque_artifact_ids(tmp_path: Path) -> None:
    from deeplaw.task_context import build_task_context_binding

    vault, grant_id = _vault(tmp_path)
    binding = build_task_context_binding(
        "1" * 64,
        "2" * 64,
        repository_sha256="3" * 64,
        worktree_sha256="4" * 64,
        base_revision="5" * 40,
        dirty_state_sha256="6" * 64,
    )
    response = handle_knowledge_sink(
        {
            "operation": "record_run",
            "idempotency_key": "pass22-safe-artifacts",
            "confirm_no_case_data": True,
            "task": "Retain safe opaque artifact identities.",
            "host_id": "pytest-pass22",
            "status": "succeeded",
            "run_metadata": {
                "task_binding": binding,
                "artifact_ids": [
                    "commit:fd9657d8",
                    "test_report_001",
                    "artifact_candidate_001",
                ],
            },
        },
        grant_id=grant_id,
        vault_path=vault,
    )
    assert response["result"]["status"] == "succeeded"


def test_partial_checkpoint_recovers_after_process_exit_and_restart(tmp_path: Path) -> None:
    from deeplaw.task_continuity import checkpoint_task, start_task

    repository = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    task_text = "Recover a checkpoint after the writer process exits."
    started = start_task(
        vault_path=vault,
        project="DeepLaw",
        task=task_text,
        workspace=repository,
    )
    script = r'''
import os
import sys
from pathlib import Path
from deeplaw import task_continuity

original = task_continuity.handle_knowledge_sink

def exit_after_run(request, *, grant_id, vault_path):
    if request.get("operation") == "remember":
        os._exit(91)
    return original(request, grant_id=grant_id, vault_path=vault_path)

task_continuity.handle_knowledge_sink = exit_after_run
task_continuity.checkpoint_task(
    vault_path=Path(sys.argv[1]),
    task_handle=sys.argv[2],
    workspace=Path(sys.argv[3]),
    grant_id=sys.argv[4],
    idempotency_key="pass22-process-restart",
    task=sys.argv[5],
    summary="The Run commits before this process exits.",
    next_action="Recover with the same idempotency key.",
    expires_at="2099-01-01T00:00:00Z",
    confirm_no_case_data=True,
)
'''
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(vault),
            str(started["task_handle"]),
            str(repository),
            grant_id,
            task_text,
        ],
        cwd=REPOSITORY,
        check=False,
        env={
            **_build_subprocess_environment(),
            "PATH": os.pathsep.join(
                [str(Path(shutil.which("git") or "git").parent), os.defpath]
            ),
            "PYTHONUTF8": "1",
        },
    )
    assert completed.returncode == 91

    recovered = checkpoint_task(
        vault_path=vault,
        task_handle=str(started["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="pass22-process-restart",
        task=task_text,
        summary="The Run commits before this process exits.",
        next_action="Recover with the same idempotency key.",
        expires_at="2099-01-01T00:00:00Z",
        confirm_no_case_data=True,
    )
    assert recovered["status"] == "checkpointed"
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        run = store.get_run(str(recovered["run_id"]))
    assert run["status"] == "succeeded"
