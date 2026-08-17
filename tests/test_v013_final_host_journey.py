from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from deeplaw import cli
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.util import canonical_json, sha256_bytes


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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


def _run_cli(
    capsys: pytest.CaptureFixture[str],
    *arguments: str,
) -> dict[str, object]:
    cli.main(["knowledge", *arguments])
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert isinstance(result, dict)
    return result


def test_default_public_host_journey_fails_closed_across_lifecycle_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _repository(tmp_path)
    child_worktree = tmp_path / "child-worktree"
    _git(repository, "worktree", "add", "-q", "--detach", str(child_worktree), "HEAD")
    vault = tmp_path / "vault"
    monkeypatch.setenv("DEEPLAW_HOME", str(tmp_path / "owner-home"))

    initialized = _run_cli(
        capsys,
        "init",
        "--vault",
        str(vault),
        "--name",
        "v013-final-host-journey",
        "--scope",
        "project",
    )
    assert initialized["schema_version"] == "deeplaw.knowledge-vault-initialization/v2"
    doctor = _run_cli(capsys, "doctor", "--vault", str(vault))
    assert doctor["ready"] is True
    assert doctor["product_readiness"]["autonomous_vault_ready"] is True

    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant_id = str(
            store.enable_grant(
                writer_id="v013-final-host-journey",
                operations=("record_run", "remember", "forget"),
            )["grant_id"]
        )

    project = "DeepLaw"
    task_text = "Finish the final Host journey."
    started = _run_cli(
        capsys,
        "task",
        "start",
        "--vault",
        str(vault),
        "--project",
        project,
        "--task",
        task_text,
        "--workspace",
        str(repository),
    )
    task_handle = str(started["task_handle"])
    assert started["status"] == "ready"
    assert started["write_performed"] is False

    before_checkpoint = _run_cli(
        capsys,
        "task",
        "locate",
        "--vault",
        str(vault),
        "--project",
        project,
        "--task",
        task_text,
        "--workspace",
        str(repository),
    )
    assert before_checkpoint["status"] == "not_found"
    assert before_checkpoint["gap"] == {"code": "not_found"}

    connect = _run_cli(
        capsys,
        "host",
        "connect",
        "--host",
        "codex",
        "--vault",
        str(vault),
    )
    serialized_connect = canonical_json(connect)
    assert connect["task_handle_configured"] is False
    assert connect["task_binding_configured"] is False
    assert str(vault) not in serialized_connect
    assert str(repository) not in serialized_connect

    raw_session_id = "official-session-v013-final-journey"
    session_sha256 = sha256_bytes(raw_session_id.encode("utf-8"))
    unbound = _run_cli(
        capsys,
        "task",
        "resolve-host-continuity",
        "--vault",
        str(vault),
        "--host",
        "codex",
        "--session-sha256",
        session_sha256,
        "--workspace",
        str(repository),
    )
    assert unbound["status"] == "gap"
    assert unbound["statements"] == []
    assert unbound["gaps"] == [{"code": "route_unbound"}]

    monkeypatch.setattr("sys.stdin", io.StringIO(raw_session_id + "\n"))
    enrolled = _run_cli(
        capsys,
        "task",
        "enroll-host-session",
        "--vault",
        str(vault),
        "--host",
        "codex",
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
        "--grant-id",
        grant_id,
        "--idempotency-key",
        "v013-final-enroll",
        "--confirm-no-case-data",
    )
    assert enrolled["status"] == "bound"
    assert enrolled["session_sha256"] == session_sha256
    assert raw_session_id not in canonical_json(enrolled)

    checkpoint = _run_cli(
        capsys,
        "task",
        "checkpoint",
        "--vault",
        str(vault),
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
        "--grant-id",
        grant_id,
        "--idempotency-key",
        "v013-final-checkpoint",
        "--summary",
        "The exact Host route is enrolled.",
        "--next-action",
        "Run the exact frozen qualification task.",
        "--expires-at",
        "2099-01-01T00:00:00Z",
        "--decision",
        "Keep static Host Connect task-neutral.",
        "--gap",
        "Native Host qualification remains pending.",
        "--artifact-ref",
        "v013-final-host-journey",
        "--confirm-no-case-data",
    )
    knowledge_id = str(checkpoint["knowledge_id"])
    assert checkpoint["status"] == "checkpointed"

    located = _run_cli(
        capsys,
        "task",
        "locate",
        "--vault",
        str(vault),
        "--project",
        project,
        "--task",
        task_text,
        "--workspace",
        str(repository),
    )
    assert located["status"] == "exact"
    assert located["knowledge_identity"] == knowledge_id

    resumed = _run_cli(
        capsys,
        "task",
        "resume",
        "--vault",
        str(vault),
        "--project",
        project,
        "--task",
        task_text,
        "--workspace",
        str(repository),
    )
    assert resumed["status"] == "admitted"
    provider = resumed["provider_capsule"]
    provider_bytes = provider["delivery"]["provider_content_bytes"]
    assert 0 < provider_bytes <= 65_536
    statements = provider["capsule"]["statements"]
    assert len(statements) == 1
    checkpoint_content = statements[0]["statement_text"]
    assert "CONFIRMED_DECISION: Keep static Host Connect task-neutral." in checkpoint_content
    assert "NEXT_ACTION: Run the exact frozen qualification task." in checkpoint_content
    provider_serialized = canonical_json(provider)
    for forbidden in (
        raw_session_id,
        str(vault),
        str(repository),
        "transcript",
        "reasoning",
        "receipt_identity",
        "selection_identity",
    ):
        assert forbidden not in provider_serialized

    continued = _run_cli(
        capsys,
        "task",
        "fork",
        "--vault",
        str(vault),
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
        "--mode",
        "continue-parent",
    )
    assert continued["task_handle"] == task_handle
    child = _run_cli(
        capsys,
        "task",
        "fork",
        "--vault",
        str(vault),
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
        "--child-workspace",
        str(child_worktree),
        "--mode",
        "child-task",
        "--child-task",
        "Run child-only verification.",
    )
    assert child["workspace_independent"] is True
    assert child["task_handle"] != task_handle

    compacted = _run_cli(
        capsys,
        "task",
        "compaction",
        "--vault",
        str(vault),
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
    )
    assert compacted["status"] == "admitted"
    assert compacted["transcript_copied"] is False

    wrong_task = _run_cli(
        capsys,
        "task",
        "resume",
        "--vault",
        str(vault),
        "--project",
        project,
        "--task",
        "A different task line.",
        "--workspace",
        str(repository),
    )
    assert wrong_task["status"] == "not_found"
    assert wrong_task["gap"] == {"code": "not_found"}
    assert "provider_capsule" not in wrong_task

    wrong_worktree = _run_cli(
        capsys,
        "task",
        "resolve-host-continuity",
        "--vault",
        str(vault),
        "--host",
        "codex",
        "--session-sha256",
        session_sha256,
        "--workspace",
        str(child_worktree),
    )
    assert wrong_worktree["status"] == "gap"
    assert wrong_worktree["statements"] == []
    assert wrong_worktree["gaps"][0]["code"] == "route_wrong_worktree"

    (repository / "tracked.txt").write_text("uncheckpointed\n", encoding="utf-8")
    stale = _run_cli(
        capsys,
        "task",
        "resume",
        "--vault",
        str(vault),
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
    )
    assert stale["status"] == "gap"
    assert {"workspace_diverged", "stale_checkpoint"}.intersection(
        stale["gap_codes"]
    )
    assert stale["provider_capsule"]["capsule"]["statements"] == []

    (repository / "tracked.txt").write_text("stable\n", encoding="utf-8")
    forgotten = _run_cli(
        capsys,
        "task",
        "forget",
        "--vault",
        str(vault),
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
        "--grant-id",
        grant_id,
        "--idempotency-key",
        "v013-final-forget",
        "--reason",
        "Owner requested selective checkpoint removal.",
        "--confirm-no-case-data",
    )
    assert forgotten["status"] == "forgotten"
    assert forgotten["knowledge_id"] == knowledge_id
    after_forget = _run_cli(
        capsys,
        "task",
        "resume",
        "--vault",
        str(vault),
        "--project",
        project,
        "--task",
        task_text,
        "--workspace",
        str(repository),
    )
    assert after_forget["status"] == "not_found"
    assert after_forget["gap"] == {"code": "not_found"}
    assert "provider_capsule" not in after_forget
