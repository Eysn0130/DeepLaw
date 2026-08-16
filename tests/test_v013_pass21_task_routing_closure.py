from __future__ import annotations

import json
import os
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


def _vault(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="pass21-task-routing", scope="project")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant_id = str(
            store.enable_grant(
                writer_id="pass21-task-routing",
                operations=("record_run", "remember", "forget"),
            )["grant_id"]
        )
    return vault, grant_id


def _validate_result(value: dict[str, object]) -> None:
    repository = Path(__file__).resolve().parents[1]
    schema = json.loads(
        (repository / "contracts/task-continuity-result.v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(value)


def test_untracked_content_digest_changes_when_metadata_is_restored(tmp_path: Path) -> None:
    from deeplaw.task_continuity import workspace_snapshot_receipt

    repository = _repository(tmp_path)
    untracked = repository / "untracked.txt"
    untracked.write_bytes(b"alpha\n")
    before_stat = untracked.stat()
    before = workspace_snapshot_receipt(repository)

    untracked.write_bytes(b"bravo\n")
    os.utime(
        untracked,
        ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns),
    )
    after_stat = untracked.stat()
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert after_stat.st_ino == before_stat.st_ino

    after = workspace_snapshot_receipt(repository)
    assert before["status"] == after["status"] == "ready"
    assert before["dirty_state_sha256"] != after["dirty_state_sha256"]
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "contracts/workspace-snapshot-receipt.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(before)
    Draft202012Validator(schema).validate(after)


@pytest.mark.parametrize("relative", [".env", "credentials.json"])
@pytest.mark.parametrize("tracked", [False, True])
def test_secret_looking_workspace_candidates_fail_closed_without_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: str,
    tracked: bool,
) -> None:
    from deeplaw import task_continuity

    repository = _repository(tmp_path)
    candidate = repository / relative
    candidate.write_text("DO_NOT_READ_THIS_VALUE\n", encoding="utf-8")
    if tracked:
        _git(repository, "add", relative)
        _git(
            repository,
            "-c",
            "user.name=DeepLaw Test",
            "-c",
            "user.email=deeplaw@example.invalid",
            "commit",
            "-q",
            "-m",
            "secret candidate fixture",
        )

    original_git = task_continuity._git
    observed: list[tuple[str, ...]] = []

    def audit_git(workspace: Path, *arguments: str, **kwargs: object) -> bytes:
        observed.append(arguments)
        assert arguments[0] not in {"status", "diff"}, (
            "content-sensitive Git inspection ran after a secret candidate existed"
        )
        return original_git(workspace, *arguments, **kwargs)

    monkeypatch.setattr(task_continuity, "_git", audit_git)
    receipt = task_continuity.workspace_snapshot_receipt(repository)

    assert receipt == {
        "schema_version": "deeplaw.workspace-snapshot-receipt/v1",
        "status": "gap",
        "gap": {
            "code": "workspace_secret_unverifiable",
            "candidate_count": 1,
        },
    }
    assert observed
    assert relative not in str(receipt)
    assert "DO_NOT_READ_THIS_VALUE" not in str(receipt)
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "contracts/workspace-snapshot-receipt.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(receipt)

    if relative == ".env" and tracked is False:
        bounded_root = tmp_path / "bounded"
        bounded_root.mkdir()
        bounded_repository = _repository(bounded_root)
        (bounded_repository / "ordinary.txt").write_text("bounded\n", encoding="utf-8")
        monkeypatch.setattr(task_continuity, "_git", original_git)
        monkeypatch.setattr(task_continuity, "_MAX_UNTRACKED_PATHS", 0)
        bounded = task_continuity.workspace_snapshot_receipt(bounded_repository)
        assert bounded == {
            "schema_version": "deeplaw.workspace-snapshot-receipt/v1",
            "status": "gap",
            "gap": {"code": "workspace_snapshot_bound", "candidate_count": 1},
        }


def test_ignored_env_candidate_fails_closed_without_content_or_tree_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeplaw import task_continuity

    repository = _repository(tmp_path)
    (repository / ".gitignore").write_text(".env\n.venv/\n", encoding="utf-8")
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
        "ignore local state",
    )
    (repository / ".env").write_text("DO_NOT_READ_IGNORED_VALUE\n", encoding="utf-8")
    generated = repository / ".venv" / "generated.txt"
    generated.parent.mkdir()
    generated.write_text("ordinary ignored state\n", encoding="utf-8")

    original_git = task_continuity._git
    observed: list[tuple[str, ...]] = []

    def audit_git(workspace: Path, *arguments: str, **kwargs: object) -> bytes:
        observed.append(arguments)
        assert "--directory" in arguments or "--ignored" not in arguments
        return original_git(workspace, *arguments, **kwargs)

    monkeypatch.setattr(task_continuity, "_git", audit_git)
    receipt = task_continuity.workspace_snapshot_receipt(repository)

    assert receipt == {
        "schema_version": "deeplaw.workspace-snapshot-receipt/v1",
        "status": "gap",
        "gap": {
            "code": "workspace_secret_unverifiable",
            "candidate_count": 1,
        },
    }
    assert any("--ignored" in arguments for arguments in observed)
    assert ".env" not in str(receipt)
    assert "DO_NOT_READ_IGNORED_VALUE" not in str(receipt)


def test_child_fork_selects_an_explicit_independent_worktree(tmp_path: Path) -> None:
    from deeplaw.task_continuity import decode_task_handle, fork_task, start_task

    repository = _repository(tmp_path)
    child_worktree = tmp_path / "child-worktree"
    _git(repository, "worktree", "add", "-q", "--detach", str(child_worktree), "HEAD")
    vault, _grant_id = _vault(tmp_path)
    parent = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Parent route",
        workspace=repository,
    )

    with pytest.raises(ValueError, match="explicit child workspace"):
        fork_task(
            vault_path=vault,
            task_handle=str(parent["task_handle"]),
            workspace=repository,
            mode="child-task",
            child_task="Child route",
        )

    child = fork_task(
        vault_path=vault,
        task_handle=str(parent["task_handle"]),
        workspace=repository,
        child_workspace=child_worktree,
        mode="child-task",
        child_task="Child route",
    )

    parent_payload = decode_task_handle(str(parent["task_handle"]))
    child_payload = decode_task_handle(str(child["task_handle"]))
    assert parent_payload["repository_sha256"] == child_payload["repository_sha256"]
    assert parent_payload["worktree_sha256"] != child_payload["worktree_sha256"]


def test_task_text_locate_timeline_and_changed_workspace_forget(tmp_path: Path) -> None:
    from deeplaw.task_continuity import (
        checkpoint_task,
        forget_task,
        locate_task,
        resume_task,
        start_task,
        timeline_task,
    )

    repository = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    task_text = "Recover through project task text and current workspace."
    started = start_task(
        vault_path=vault,
        project="DeepLaw",
        task=task_text,
        workspace=repository,
    )
    checkpoint = checkpoint_task(
        vault_path=vault,
        task_handle=str(started["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="pass21-locate-checkpoint",
        summary="The exact current route has a governed checkpoint.",
        next_action="Locate without an internal handle.",
        expires_at="2099-01-01T00:00:00Z",
        artifact_refs=("artifact_pass21_route_001",),
        confirm_no_case_data=True,
    )

    located = locate_task(
        vault_path=vault,
        project="DeepLaw",
        task=task_text,
        workspace=repository,
    )
    assert located["status"] == "exact"
    assert located["task_handle"] == started["task_handle"]
    resumed = resume_task(
        vault_path=vault,
        project="DeepLaw",
        task=task_text,
        workspace=repository,
    )
    assert resumed["status"] == "admitted"
    assert str(repository) not in str(resumed["provider_capsule"])
    assert "credentials" not in str(resumed["provider_capsule"]).casefold()

    unrelated_started = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="An unrelated concurrent route.",
        workspace=repository,
    )
    unrelated = checkpoint_task(
        vault_path=vault,
        task_handle=str(unrelated_started["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="pass21-unrelated-checkpoint",
        task="An unrelated concurrent route.",
        summary="This checkpoint must not enter the original task timeline.",
        next_action="Remain isolated.",
        expires_at="2099-01-01T00:00:00Z",
        confirm_no_case_data=True,
    )

    timeline = timeline_task(
        vault_path=vault,
        project="DeepLaw",
        task=task_text,
        workspace=repository,
    )
    assert timeline["status"] == "exact"
    assert timeline["entries"]
    assert all(
        set(entry)
        <= {
            "entry_type",
            "identity",
            "status",
            "recorded_at",
            "related_identity",
            "artifact_identities",
            "gap",
        }
        for entry in timeline["entries"]
    )
    assert str(repository) not in str(timeline)
    assert unrelated["run_id"] not in str(timeline)
    assert unrelated["knowledge_id"] not in str(timeline)
    assert unrelated["revision_id"] not in str(timeline)
    for result in (started, checkpoint, located, resumed, unrelated_started, unrelated, timeline):
        _validate_result(result)

    (repository / "tracked.txt").write_text("workspace changed\n", encoding="utf-8")
    forgotten = forget_task(
        vault_path=vault,
        task_handle=str(started["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="pass21-changed-workspace-forget",
        reason="Owner forgot the exact original checkpoint after workspace drift.",
        confirm_no_case_data=True,
    )
    assert forgotten["knowledge_id"] == checkpoint["knowledge_id"]
    _validate_result(forgotten)
    assert resume_task(
        vault_path=vault,
        task_handle=str(started["task_handle"]),
        workspace=repository,
    )["status"] == "gap"


def test_checkpoint_partial_is_explicit_and_same_key_recovers_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeplaw import task_continuity

    repository = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    started = task_continuity.start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Recover a partial checkpoint write.",
        workspace=repository,
    )
    original = task_continuity.handle_knowledge_sink
    failed_once = False

    def fail_first_remember(
        request: dict[str, object],
        *,
        grant_id: str,
        vault_path: Path,
    ) -> dict[str, object]:
        nonlocal failed_once
        if request.get("operation") == "remember" and not failed_once:
            failed_once = True
            raise RuntimeError("injected remember interruption")
        return original(request, grant_id=grant_id, vault_path=vault_path)

    monkeypatch.setattr(task_continuity, "handle_knowledge_sink", fail_first_remember)
    arguments = {
        "vault_path": vault,
        "task_handle": str(started["task_handle"]),
        "workspace": repository,
        "grant_id": grant_id,
        "idempotency_key": "pass21-partial-recovery",
        "task": "Recover a partial checkpoint write.",
        "summary": "The run was recorded before an injected interruption.",
        "next_action": "Retry the same idempotency key.",
        "expires_at": "2099-01-01T00:00:00Z",
        "confirm_no_case_data": True,
    }

    partial = task_continuity.checkpoint_task(**arguments)
    assert partial["status"] == "partial"
    assert partial["checkpoint_status"] == "pending_idempotent_retry"
    assert partial["gap"]["code"] == "checkpoint_partial"
    recovered = task_continuity.checkpoint_task(**arguments)
    assert recovered["status"] == "checkpointed"
    assert recovered["run_id"] == partial["run_id"]
    _validate_result(partial)
    _validate_result(recovered)


@pytest.mark.parametrize(
    "artifact_ref",
    [
        "/Users/example/private/report.json",
        "../outside.json",
        "nested/secret.txt",
        "credentials-prod",
        "api_key_material",
        r"C:\\Users\\example\\private.json",
    ],
)
def test_checkpoint_rejects_path_and_secret_looking_artifact_refs(
    tmp_path: Path,
    artifact_ref: str,
) -> None:
    from deeplaw.task_continuity import checkpoint_task, start_task

    repository = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    started = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Reject unsafe artifact references.",
        workspace=repository,
    )

    with pytest.raises(ValueError, match="artifact reference"):
        checkpoint_task(
            vault_path=vault,
            task_handle=str(started["task_handle"]),
            workspace=repository,
            grant_id=grant_id,
            idempotency_key=f"unsafe-{abs(hash(artifact_ref))}",
            task="Reject unsafe artifact references.",
            summary="Reject unsafe references.",
            next_action="Keep local details out of Provider-visible knowledge.",
            expires_at="2099-01-01T00:00:00Z",
            artifact_refs=(artifact_ref,),
            confirm_no_case_data=True,
        )


def test_closed_launcher_task_binding_requires_explicit_host_workspace(tmp_path: Path) -> None:
    from deeplaw.closed_mcp_launcher import closed_mcp_environment
    from deeplaw.task_continuity import start_task

    repository = _repository(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    (other / "tracked.txt").write_text("other\n", encoding="utf-8")
    _git(other, "add", "tracked.txt")
    _git(
        other,
        "-c",
        "user.name=DeepLaw Test",
        "-c",
        "user.email=deeplaw@example.invalid",
        "commit",
        "-q",
        "-m",
        "other fixture",
    )
    vault, _grant_id = _vault(tmp_path)
    started = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Bind the Host-selected workspace explicitly.",
        workspace=repository,
    )

    with closed_mcp_environment(
        surface="knowledge_support",
        vault_path=vault,
        task_handle=str(started["task_handle"]),
        workspace=repository,
    ) as launch:
        assert "DEEPLAW_TASK_BINDING" in launch.environment

    with (
        pytest.raises(PermissionError, match="worktree"),
        closed_mcp_environment(
            surface="knowledge_support",
            vault_path=vault,
            task_handle=str(started["task_handle"]),
            workspace=other,
        ),
    ):
        pass
