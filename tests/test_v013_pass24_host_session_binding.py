from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from deeplaw import cli
from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_mcp_server import _resolve_provider_host_route
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_continuity import (
    bind_host_session,
    checkpoint_task,
    forget_task,
    resolve_host_continuity_capsule,
    resolve_host_session,
    start_task,
)
from deeplaw.util import sha256_bytes

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA_V1 = json.loads(
    (REPOSITORY / "contracts/host-session-route-result.v1.schema.json").read_text(
        encoding="utf-8"
    )
)
SCHEMA_V2 = json.loads(
    (REPOSITORY / "contracts/host-session-route-result.v2.schema.json").read_text(
        encoding="utf-8"
    )
)
CONTINUITY_CAPSULE_SCHEMA = json.loads(
    (REPOSITORY / "contracts/host-continuity-capsule.v1.schema.json").read_text(
        encoding="utf-8"
    )
)

FROZEN_V1_ROUTE_GAP = {
    "schema_version": "deeplaw.host-session-route-result/v1",
    "operation": "resolve",
    "status": "gap",
    "host": "codex",
    "session_sha256": "a" * 64,
    "write_performed": False,
    "transcript_copied": False,
    "gap": {"code": "route_unbound"},
}


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
                operations=("record_run", "remember", "forget"),
            )["grant_id"]
        )
    return vault, grant_id


def _validate(value: dict[str, object]) -> None:
    schema = (
        SCHEMA_V2
        if value.get("schema_version") == "deeplaw.host-session-route-result/v2"
        else SCHEMA_V1
    )
    Draft202012Validator(schema).validate(value)


def _validate_continuity_capsule(value: dict[str, object]) -> None:
    Draft202012Validator(CONTINUITY_CAPSULE_SCHEMA).validate(value)


def test_host_session_route_v1_compatibility_fixture_remains_valid() -> None:
    Draft202012Validator(SCHEMA_V1).validate(FROZEN_V1_ROUTE_GAP)


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


def test_host_session_route_v2_keeps_route_identity_across_edit_and_blocks_old_checkpoint(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    task = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Pass 26 stable Host route",
        workspace=repository,
    )
    session_sha256 = sha256_bytes(b"pass26-codex-session")
    bind_host_session(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        task_handle=str(task["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="pass26-bind-route",
        confirm_no_case_data=True,
    )
    checkpoint = checkpoint_task(
        vault_path=vault,
        task_handle=str(task["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="pass26-checkpoint",
        summary="Retain the exact task checkpoint.",
        next_action="Continue the original Host task.",
        expires_at="2099-01-01T00:00:00Z",
        confirm_no_case_data=True,
    )

    before = resolve_host_session(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        workspace=repository,
    )
    _validate(before)
    assert before["status"] == "exact"
    continuity_before = resolve_host_continuity_capsule(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        workspace=repository,
    )
    _validate_continuity_capsule(continuity_before)
    assert continuity_before["status"] == "admitted"
    serialized_continuity = json.dumps(continuity_before, ensure_ascii=False)
    assert "Retain the exact task checkpoint" in serialized_continuity
    assert len(serialized_continuity.encode("utf-8")) <= 1400
    assert str(repository) not in serialized_continuity
    assert "taskh_" not in serialized_continuity
    assert "receipt" not in serialized_continuity
    assert re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", serialized_continuity) is None

    (repository / "tracked.txt").write_text("edited by the same Host\n", encoding="utf-8")
    after = resolve_host_session(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        workspace=repository,
    )
    _validate(after)
    assert after["status"] == "exact"
    assert after["task_handle"] == task["task_handle"]
    assert after["route_identity"] == before["route_identity"]
    assert after["workspace_snapshot"] != before["workspace_snapshot"]
    continuity_after = resolve_host_continuity_capsule(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        workspace=repository,
    )
    _validate_continuity_capsule(continuity_after)
    assert continuity_after == {
        "schema_version": "deeplaw.host-continuity-capsule/v1",
        "status": "gap",
        "statements": [],
        "gaps": [{"code": "workspace_diverged"}, {"code": "stale_checkpoint"}],
        "conflicts": [],
        "write_performed": False,
    }
    assert checkpoint["knowledge_id"] not in json.dumps(continuity_after)

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
        "same Host edit",
    )
    committed = resolve_host_session(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        workspace=repository,
    )
    _validate(committed)
    assert committed["status"] == "exact"
    assert committed["route_identity"] == before["route_identity"]
    assert committed["workspace_snapshot"] != after["workspace_snapshot"]
    assert committed["checkpoint_status"] == "workspace_diverged"

    route_binding, route_gap = _resolve_provider_host_route(
        {
            "operation": "context",
            "host_route": {"host": "codex", "session_sha256": session_sha256},
        },
        vault_path=vault,
        workspace=repository,
        fixed_task_binding=None,
    )
    assert route_gap is None
    assert route_binding is not None
    with KnowledgeOS.open(vault) as knowledge_os:
        context = knowledge_os.context.compile(
            task="Restore the exact admitted working checkpoint for this task route.",
            purpose="answer",
            task_binding=route_binding,
            confirm_no_case_data=True,
        )
    provider = context["provider_capsule"]["capsule"]
    gap_codes = {
        item["code"]
        for item in provider["gaps"]
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }
    selected_ids = {
        item["knowledge_id"]
        for item in provider["statements"]
        if isinstance(item, dict) and isinstance(item.get("knowledge_id"), str)
    }
    assert gap_codes.intersection({"workspace_diverged", "stale_checkpoint"})
    assert checkpoint["knowledge_id"] not in selected_ids


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


@pytest.mark.parametrize(
    "summary",
    [
        "Continue from /Users/private/checkpoint.txt.",
        "Continue from /custom/private/checkpoint.txt.",
        "Continue from /tmp.",
        r"Continue from C:\private.",
        "Use Authorization: secret-material-value.",
        "Use Bearer secret-material-value.",
    ],
)
def test_host_continuity_capsule_blocks_sensitive_checkpoint_text(
    tmp_path: Path,
    summary: str,
) -> None:
    repository = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    task = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Sensitive projection boundary",
        workspace=repository,
    )
    session_sha256 = sha256_bytes(b"sensitive-projection-session")
    bind_host_session(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        task_handle=str(task["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="bind-sensitive-projection",
        confirm_no_case_data=True,
    )
    checkpoint_task(
        vault_path=vault,
        task_handle=str(task["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="checkpoint-sensitive-projection",
        summary=summary,
        next_action="Do not disclose the local path.",
        expires_at="2099-01-01T00:00:00Z",
        confirm_no_case_data=True,
    )
    capsule = resolve_host_continuity_capsule(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        workspace=repository,
    )
    _validate_continuity_capsule(capsule)
    assert capsule["status"] == "gap"
    assert capsule["statements"] == []
    assert capsule["gaps"]
    assert str(repository) not in json.dumps(capsule)
    assert "/Users/private/checkpoint.txt" not in json.dumps(capsule)
    assert summary not in json.dumps(capsule)


def test_host_session_resolution_rejects_stale_and_forgotten_routes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    task = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Stale and forgotten native route",
        workspace=repository,
    )
    session_sha256 = sha256_bytes(b"opaque-stale-session")
    bind_host_session(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        task_handle=str(task["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="bind-stale-route",
        confirm_no_case_data=True,
    )

    (repository / "tracked.txt").write_text("drifted\n", encoding="utf-8")
    stale = resolve_host_session(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        workspace=repository,
    )
    _validate(stale)
    assert stale["status"] == "exact"
    assert stale["checkpoint_status"] == "index_unavailable"

    (repository / "tracked.txt").write_text("stable\n", encoding="utf-8")
    checkpoint_task(
        vault_path=vault,
        task_handle=str(task["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="checkpoint-before-forget",
        summary="Keep the route governed before selective forget.",
        next_action="Forget this exact checkpoint.",
        expires_at="2099-01-01T00:00:00Z",
        confirm_no_case_data=True,
    )
    forget_task(
        vault_path=vault,
        task_handle=str(task["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="forget-native-route",
        reason="Owner requested selective task forget.",
        confirm_no_case_data=True,
    )
    forgotten = resolve_host_session(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        workspace=repository,
    )
    _validate(forgotten)
    assert forgotten["gap"] == {
        "code": "route_forgotten",
        "candidate_count": 1,
    }


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


def test_host_session_route_commands_are_explicit_cli_seams() -> None:
    parser = cli._parser()
    bind = parser.parse_args(
        [
            "knowledge",
            "task",
            "bind-host-session",
            "--vault",
            "vault",
            "--host",
            "codex",
            "--session-sha256",
            "a" * 64,
            "--task-handle",
            "taskh_opaque",
            "--grant-id",
            "grant-owner",
            "--idempotency-key",
            "bind-route",
            "--confirm-no-case-data",
        ]
    )
    assert bind.task_command == "bind-host-session"
    assert bind.host == "codex"
    assert bind.confirm_no_case_data is True

    resolve = parser.parse_args(
        [
            "knowledge",
            "task",
            "resolve-host-session",
            "--vault",
            "vault",
            "--host",
            "opencode",
            "--session-sha256",
            "b" * 64,
        ]
    )
    assert resolve.task_command == "resolve-host-session"
    assert resolve.host == "opencode"
    assert not hasattr(resolve, "grant_id")

    continuity = parser.parse_args(
        [
            "knowledge",
            "task",
            "resolve-host-continuity",
            "--vault",
            "vault",
            "--host",
            "codex",
            "--session-sha256",
            "c" * 64,
        ]
    )
    assert continuity.task_command == "resolve-host-continuity"
    assert not hasattr(continuity, "grant_id")


def test_provider_host_route_recomputes_binding_or_returns_gap(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    task = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Provider native route",
        workspace=repository,
    )
    session_sha256 = sha256_bytes(b"provider-native-session")
    bind_host_session(
        vault_path=vault,
        host="codex",
        session_sha256=session_sha256,
        task_handle=str(task["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="provider-native-route",
        confirm_no_case_data=True,
    )

    binding, gap = _resolve_provider_host_route(
        {
            "operation": "context",
            "host_route": {"host": "codex", "session_sha256": session_sha256},
        },
        vault_path=vault,
        workspace=repository,
        fixed_task_binding=None,
    )
    assert gap is None
    assert binding is not None
    assert binding["project_sha256"] == task["project_sha256"]
    assert binding["task_lineage_sha256"] == task["task_lineage_sha256"]

    missing_binding, missing_gap = _resolve_provider_host_route(
        {
            "operation": "query",
            "host_route": {
                "host": "codex",
                "session_sha256": sha256_bytes(b"missing-native-session"),
            },
        },
        vault_path=vault,
        workspace=repository,
        fixed_task_binding=None,
    )
    assert missing_binding is None
    assert missing_gap == {
        "schema_version": "deeplaw.host-route-gap/v1",
        "status": "gap",
        "host": "codex",
        "session_sha256": sha256_bytes(b"missing-native-session"),
        "write_performed": False,
        "gaps": [{"code": "route_unbound"}],
    }
