from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from deeplaw.host_lifecycle import (
    HostLifecycleError,
    _load_json_file,
    handle_host_lifecycle_event,
)
from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_continuity import checkpoint_task, start_task


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, Path]:
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
    child = tmp_path / "child"
    _git(repository, "worktree", "add", "-q", "--detach", str(child), "HEAD")
    return repository, child


def _vault(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="host-lifecycle", scope="project")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant_id = str(
            store.enable_grant(
                writer_id="host-lifecycle-owner",
                operations=("record_run", "remember"),
            )["grant_id"]
        )
    return vault, grant_id


def _config(
    *, host: str, version: str, vault: Path, workspace: Path, child: Path
) -> dict[str, object]:
    return {
        "schema_version": "deeplaw.host-lifecycle-config/v1",
        "enabled": True,
        "host": host,
        "host_version": version,
        "vault": str(vault),
        "project": "DeepLaw",
        "task": "Registered continuity task",
        "workspace": str(workspace),
        "workspace_class": "project",
        "confirm_no_case_data": True,
        "fork": {
            "child_task": "Registered child task",
            "child_workspace": str(child),
        },
    }


def test_codex_events_delegate_to_continuity_and_emit_path_free_receipts(
    tmp_path: Path,
) -> None:
    repository, child = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    config = _config(
        host="codex",
        version="0.147.0-alpha.1.2",
        vault=vault,
        workspace=repository,
        child=child,
    )
    started = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Registered continuity task",
        workspace=repository,
    )
    checkpoint_task(
        vault_path=vault,
        task_handle=str(started["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="host-lifecycle-checkpoint",
        task="Registered continuity task",
        summary="The registered route has a checkpoint.",
        next_action="Resume through the read-only Host lifecycle seam.",
        expires_at="2099-01-01T00:00:00Z",
        confirm_no_case_data=True,
    )
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        audit_before = store.audit_head

    events = {
        "thread/start": "start",
        "thread/resume": "resume",
        "thread/compact/start": "compaction",
        "thread/fork": "fork",
    }
    receipts = []
    for event, operation in events.items():
        receipt = handle_host_lifecycle_event(
            config,
            {
                "event": event,
                "host_thread_or_session_id": "untrusted-host-thread-hint",
            },
            expected_host="codex",
        )
        assert receipt["operation"] == operation
        assert receipt["native_seam_received"] is True
        assert receipt["write_performed"] is False
        assert receipt["claim_eligible"] is False
        assert receipt["task_continuity_result_schema"] == (
            "deeplaw.task-continuity-result/v2"
        )
        receipts.append(receipt)
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        assert store.audit_head == audit_before
    rendered = str(receipts)
    assert str(repository) not in rendered
    assert str(vault) not in rendered
    assert "untrusted-host-thread-hint" not in rendered
    assert receipts[-1]["parent_task_lineage_sha256"] == started[
        "task_lineage_sha256"
    ]


def test_opencode_summarize_maps_to_compaction_without_changing_v2_claim(
    tmp_path: Path,
) -> None:
    repository, child = _repository(tmp_path)
    vault, grant_id = _vault(tmp_path)
    config = _config(
        host="opencode",
        version="1.18.16",
        vault=vault,
        workspace=repository,
        child=child,
    )
    started = start_task(
        vault_path=vault,
        project="DeepLaw",
        task="Registered continuity task",
        workspace=repository,
    )
    checkpoint_task(
        vault_path=vault,
        task_handle=str(started["task_handle"]),
        workspace=repository,
        grant_id=grant_id,
        idempotency_key="opencode-lifecycle-checkpoint",
        task="Registered continuity task",
        summary="OpenCode reads this registered checkpoint.",
        next_action="Map summarize to compaction.",
        expires_at="2099-01-01T00:00:00Z",
        confirm_no_case_data=True,
    )
    receipt = handle_host_lifecycle_event(
        config,
        {"event": "session/summarize"},
        expected_host="opencode",
    )
    assert receipt["operation"] == "compaction"
    assert receipt["status"] == "admitted"
    assert receipt["native_seam_received"] is True
    assert receipt["claim_eligible"] is False

    operations = {
        "cli.run": "start",
        "cli.run.session": "resume",
        "cli.run.fork": "fork",
        "session.compacted": "compaction",
    }
    for event, operation in operations.items():
        observed = handle_host_lifecycle_event(
            config,
            {"event": event},
            expected_host="opencode",
        )
        assert observed["operation"] == operation
        assert observed["claim_eligible"] is False


def test_lifecycle_is_disabled_by_default_and_rejects_client_case_workspace(
    tmp_path: Path,
) -> None:
    repository, child = _repository(tmp_path)
    vault, _grant_id = _vault(tmp_path)
    config = _config(
        host="codex",
        version="0.147.0-alpha.1.2",
        vault=vault,
        workspace=repository,
        child=child,
    )
    config["enabled"] = False
    with pytest.raises(HostLifecycleError, match="disabled"):
        handle_host_lifecycle_event(
            config,
            {"event": "thread/start"},
            expected_host="codex",
        )
    config["enabled"] = True
    config["workspace_class"] = "client_case"
    with pytest.raises(HostLifecycleError, match="client and case"):
        handle_host_lifecycle_event(
            config,
            {"event": "thread/start"},
            expected_host="codex",
        )


def test_read_lifecycle_rejects_checkpoint_or_unknown_event(tmp_path: Path) -> None:
    repository, child = _repository(tmp_path)
    vault, _grant_id = _vault(tmp_path)
    config = _config(
        host="opencode",
        version="1.18.16",
        vault=vault,
        workspace=repository,
        child=child,
    )
    with pytest.raises(HostLifecycleError, match="unsupported"):
        handle_host_lifecycle_event(
            config,
            {"event": "session/checkpoint"},
            expected_host="opencode",
        )


def test_codex_adapter_process_accepts_only_closed_event_and_emits_receipt(
    tmp_path: Path,
) -> None:
    repository, child = _repository(tmp_path)
    vault, _grant_id = _vault(tmp_path)
    config = _config(
        host="codex",
        version="0.147.0-alpha.1.2",
        vault=vault,
        workspace=repository,
        child=child,
    )
    config_path = tmp_path / "codex-lifecycle.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    if os.name != "nt":
        config_path.chmod(0o600)
    adapter = Path(__file__).resolve().parents[1] / "adapters/codex/lifecycle.py"
    completed = subprocess.run(
        [sys.executable, str(adapter), "--config", str(config_path)],
        input='{"event":"thread/start","host_thread_or_session_id":"opaque-hint"}',
        check=True,
        capture_output=True,
        text=True,
    )
    receipt = json.loads(completed.stdout)
    assert receipt["schema_version"] == "deeplaw.native-host-lifecycle-receipt/v1"
    assert receipt["native_seam_received"] is True
    assert receipt["write_performed"] is False
    assert receipt["claim_eligible"] is False
    assert str(repository) not in completed.stdout
    assert str(vault) not in completed.stdout
    assert "opaque-hint" not in completed.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-mode regression")
def test_lifecycle_config_requires_owner_only_non_symlink_file(tmp_path: Path) -> None:
    config_path = tmp_path / "lifecycle.json"
    config_path.write_text("{}", encoding="utf-8")
    config_path.chmod(0o640)
    with pytest.raises(HostLifecycleError, match="owner-only"):
        _load_json_file(config_path)

    config_path.chmod(0o600)
    linked = tmp_path / "linked-lifecycle.json"
    linked.symlink_to(config_path)
    with pytest.raises(HostLifecycleError, match="unsafe"):
        _load_json_file(linked)
