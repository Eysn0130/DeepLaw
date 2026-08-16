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
from deeplaw.task_continuity import (
    bind_host_session,
    resolve_host_session,
    start_task,
)
from deeplaw.util import sha256_bytes

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (REPOSITORY / "contracts/host-session-route-result.v1.schema.json").read_text(
        encoding="utf-8"
    )
)


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


def _vault(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="pass24-host-route", scope="project")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant_id = str(
            store.enable_grant(
                writer_id="pass24-host-route-owner",
                operations=("record_run",),
            )["grant_id"]
        )
    return vault, grant_id


def _validate(value: dict[str, object]) -> None:
    Draft202012Validator(SCHEMA).validate(value)


def test_host_session_binding_uses_governed_run_and_resolves_exactly(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    task = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Pass 24 exact native route",
        workspace=repository,
    )
    session_sha256 = sha256_bytes(b"opaque-codex-session")

    bound = bind_host_session(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        task_handle=str(task["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="bind-exact-route",
        confirm_no_case_data=True,
    )
    _validate(bound)
    assert bound["status"] == "bound"
    assert bound["write_performed"] is True
    assert bound["transcript_copied"] is False
    assert "opaque-codex-session" not in json.dumps(bound)

    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        audit_before = store.audit_head
    resolved = resolve_host_session(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        workspace=repository,
    )
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        assert store.audit_head == audit_before
        run = store.get_run(str(bound["run_id"]))
    _validate(resolved)
    assert resolved["status"] == "exact"
    assert resolved["task_handle"] == task["task_handle"]
    assert run["metadata"]["task_kind"] == "host_session_route"
    assert run["metadata"]["artifact_ids"] == [f"hostsession:{session_sha256}"]
    assert "opaque-codex-session" not in json.dumps(run)

    replay = bind_host_session(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        task_handle=str(task["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="bind-exact-route",
        confirm_no_case_data=True,
    )
    _validate(replay)
    assert replay["run_id"] == bound["run_id"]
    assert replay["idempotent_replay"] is True


def test_host_session_resolution_returns_closed_gaps(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    other_worktree = tmp_path / "other-worktree"
    _git(repository, "worktree", "add", "-q", "--detach", str(other_worktree), "HEAD")
    vault, grant_id = _vault(tmp_path)
    session_sha256 = sha256_bytes(b"opaque-opencode-session")

    unbound = resolve_host_session(
        vault_path=vault,
        host="opencode",
        session_sha256=session_sha256,
        workspace=repository,
    )
    _validate(unbound)
    assert unbound["gap"] == {"code": "route_unbound"}

    first = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="First route",
        workspace=repository,
    )
    bind_host_session(
        vault_path=vault,
        host="opencode",
        session_sha256=session_sha256,
        task_handle=str(first["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="bind-first-route",
        confirm_no_case_data=True,
    )
    wrong_worktree = resolve_host_session(
        vault_path=vault,
        host="opencode",
        session_sha256=session_sha256,
        workspace=other_worktree,
    )
    _validate(wrong_worktree)
    assert wrong_worktree["gap"] == {
        "code": "route_wrong_worktree",
        "candidate_count": 1,
    }

    second = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Second route",
        workspace=repository,
    )
    bind_host_session(
        vault_path=vault,
        host="opencode",
        session_sha256=session_sha256,
        task_handle=str(second["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="bind-second-route",
        confirm_no_case_data=True,
    )
    ambiguous = resolve_host_session(
        vault_path=vault,
        host="opencode",
        session_sha256=session_sha256,
        workspace=repository,
    )
    _validate(ambiguous)
    assert ambiguous["status"] == "gap"
    assert ambiguous["gap"] == {"code": "route_ambiguous", "candidate_count": 2}


@pytest.mark.parametrize("host", ["claude", "Codex", ""])
def test_host_session_binding_rejects_unfrozen_hosts(tmp_path: Path, host: str) -> None:
    repository = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    task = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Rejected Host route",
        workspace=repository,
    )
    with pytest.raises(ValueError, match="native Host"):
        bind_host_session(
            vault_path=vault,
            host=host,
            session_sha256=sha256_bytes(b"opaque-session"),
            task_handle=str(task["task_handle"]),
            workspace=repository,
            grant_id=grant_id,
            idempotency_key="reject-host",
            confirm_no_case_data=True,
        )
