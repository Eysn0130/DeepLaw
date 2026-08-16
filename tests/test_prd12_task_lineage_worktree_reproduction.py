"""Regression for the reproduced task-line/worktree defect.

The linked-worktree fixture first reproduced wrong-line admission. It now
requires exact binding and Provider redaction. This remains development
evidence, not qualification.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from deeplaw.api.knowledge_os import KnowledgeOS
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes

_TASK = "Continue the shared deployment task."
_WRITER_ID = "prd12-lineage-worktree-reproduction"
_MODEL_ID = "development-model"
_TOOL_ID = "development-tool"


def _git_env(tmp_path: Path) -> dict[str, str]:
    git = shutil.which("git")
    assert git is not None, "git is required for this development reproduction"
    return {
        "PATH": str(Path(git).parent),
        "HOME": str(tmp_path / "git-home"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "LC_ALL": "C",
        "LANG": "C",
        "GIT_AUTHOR_NAME": "DeepLaw Development Test",
        "GIT_AUTHOR_EMAIL": "deeplaw-development@example.invalid",
        "GIT_COMMITTER_NAME": "DeepLaw Development Test",
        "GIT_COMMITTER_EMAIL": "deeplaw-development@example.invalid",
    }


def _git(cwd: Path, env: dict[str, str], *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _working_checkpoint_body(
    *,
    opaque_line: str,
) -> str:
    return "\n".join(
        (
            f"GOAL: {_TASK}",
            f"CONFIRMED_DECISION: Deploy from opaque task line {opaque_line}.",
            "CONSTRAINT: keep the worktree isolated",
            f"VERIFIED_FACT: task-line={opaque_line}",
            "OPEN_GAP: wrong task-line state must not be admitted",
            "NEXT_ACTION: verify deployment receipt",
            f"ARTIFACT_REF: artifact:{opaque_line.lower()}",
        )
    )


def _sink(root: Path, grant_id: str, request: dict[str, object]) -> dict[str, object]:
    result = handle_knowledge_sink(request, grant_id=grant_id, vault_path=root)
    assert result.get("result") is not None, result
    return result["result"]


def test_linked_worktree_context_admits_only_the_exact_task_binding(
    tmp_path: Path,
) -> None:
    """Exact project/task/worktree/base/dirty binding excludes the other line."""

    repo = tmp_path / "git-repo"
    repo.mkdir()
    git_env = _git_env(tmp_path)
    _git(repo, git_env, "init", "--quiet", "--initial-branch=main")
    _git(repo, git_env, "config", "user.name", "DeepLaw Development Test")
    _git(repo, git_env, "config", "user.email", "deeplaw-development@example.invalid")

    (repo / "README.md").write_text("shared deployment base\n", encoding="utf-8")
    _git(repo, git_env, "add", "README.md")
    _git(repo, git_env, "commit", "--quiet", "-m", "initial development base")
    _git(repo, git_env, "branch", "feature-line")

    feature_worktree = tmp_path / "feature-worktree"
    _git(repo, git_env, "worktree", "add", "--quiet", str(feature_worktree), "feature-line")

    (repo / "main-base.txt").write_text("main base\n", encoding="utf-8")
    _git(repo, git_env, "add", "main-base.txt")
    _git(repo, git_env, "commit", "--quiet", "-m", "main line base")
    main_base = _git(repo, git_env, "rev-parse", "HEAD")

    (feature_worktree / "feature-base.txt").write_text("feature base\n", encoding="utf-8")
    _git(feature_worktree, git_env, "add", "feature-base.txt")
    _git(feature_worktree, git_env, "commit", "--quiet", "-m", "feature line base")
    feature_base = _git(feature_worktree, git_env, "rev-parse", "HEAD")
    assert main_base != feature_base

    (repo / "main-dirty.marker").write_text("main private dirty marker\n", encoding="utf-8")
    (feature_worktree / "feature-dirty.marker").write_text(
        "feature private dirty marker\n",
        encoding="utf-8",
    )
    main_status = _git(repo, git_env, "status", "--porcelain=v1", "--untracked-files=all")
    feature_status = _git(
        feature_worktree,
        git_env,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    main_dirty_digest = hashlib.sha256(main_status.encode("utf-8")).hexdigest()
    feature_dirty_digest = hashlib.sha256(feature_status.encode("utf-8")).hexdigest()
    assert main_dirty_digest != feature_dirty_digest

    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="prd12-lineage-worktree-reproduction", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id=_WRITER_ID,
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]

    checkpoint_ids: dict[str, str] = {}
    line_metadata = {"main": "LINE-A", "feature": "LINE-B"}
    project_sha256 = sha256_bytes(b"prd12-linked-worktree-project")
    repository_sha256 = sha256_bytes(b"prd12-linked-worktree-repository")
    task_bindings = {
        "main": build_task_context_binding(
            project_sha256,
            sha256_bytes(b"prd12-linked-worktree-task:main"),
            repository_sha256=repository_sha256,
            worktree_sha256=sha256_bytes(b"prd12-linked-worktree:main"),
            base_revision=main_base,
            dirty_state_sha256=main_dirty_digest,
        ),
        "feature": build_task_context_binding(
            project_sha256,
            sha256_bytes(b"prd12-linked-worktree-task:feature"),
            repository_sha256=repository_sha256,
            worktree_sha256=sha256_bytes(b"prd12-linked-worktree:feature"),
            base_revision=feature_base,
            dirty_state_sha256=feature_dirty_digest,
        ),
    }
    for line, opaque_line in line_metadata.items():
        run_id = f"run-{line}-development"
        _sink(
            root,
            grant_id,
            {
                "operation": "record_run",
                "idempotency_key": f"prd12-worktree-record-run-{line}",
                "confirm_no_case_data": True,
                "run_id": run_id,
                "task": _TASK,
                "host_id": f"host-{line}-development",
                "model_id": _MODEL_ID,
                "status": "succeeded",
                "scope": "project",
                "sensitivity": "internal",
                "run_metadata": {
                    "task_kind": "deployment",
                    "artifact_ids": [f"commit:{line}-development"],
                    "task_binding": task_bindings[line],
                },
            },
        )
        remember_result = _sink(
            root,
            grant_id,
            {
                "operation": "remember",
                "idempotency_key": f"prd12-worktree-record-checkpoint-{line}",
                "confirm_no_case_data": True,
                "title": "Shared deployment checkpoint",
                "body": _working_checkpoint_body(opaque_line=opaque_line),
                "kind": "memory",
                "memory_type": "working",
                "semantic_key": "checkpoint:shared-deployment:slot-0",
                "expires_at": "2099-01-01T00:00:00Z",
                "scope": "project",
                "sensitivity": "internal",
                "run_id": run_id,
                "model_id": _MODEL_ID,
                "tool_id": _TOOL_ID,
                "tags": ["checkpoint", "shared-deployment"],
            },
        )
        checkpoint_ids[line] = str(remember_result["knowledge_id"])
    assert checkpoint_ids["main"] != checkpoint_ids["feature"]

    # Stable public read seam only. The binding contains digests and revision
    # hashes, never private paths or branch names.
    with KnowledgeOS.open(root) as knowledge_os:
        local_capsule = knowledge_os.context.compile(
            task="Continue the shared deployment task from the feature worktree.",
            purpose="answer",
            task_binding=task_bindings["feature"],
            confirm_no_case_data=True,
        )
    provider_capsule = local_capsule["provider_capsule"]
    assert local_capsule["task_binding"] == task_bindings["feature"]
    assert local_capsule["query_plan"]["task_binding"] == task_bindings["feature"]
    payload = provider_capsule["capsule"]
    statements = payload.get("statements", [])
    selected_ids = {
        item.get("knowledge_id")
        for item in statements
        if isinstance(item, dict) and item.get("knowledge_id") in checkpoint_ids.values()
    }
    assert selected_ids == {checkpoint_ids["feature"]}

    gaps = payload.get("gaps", [])
    gap_codes = {
        gap.get("code")
        for gap in gaps
        if isinstance(gap, dict) and isinstance(gap.get("code"), str)
    }
    assert not gap_codes.intersection(
        {"task_binding_required", "task_binding_unbound", "task_binding_mismatch"}
    )

    provider_json = canonical_json(provider_capsule)
    assert len(provider_json.encode("utf-8")) <= 64 * 1024
    assert str(tmp_path) not in provider_json
    assert str(repo) not in provider_json
    assert str(feature_worktree) not in provider_json
    assert "feature-line" not in provider_json
    assert main_base not in provider_json
    assert feature_base not in provider_json
    assert main_dirty_digest not in provider_json
    assert feature_dirty_digest not in provider_json
    assert all(binding["binding_sha256"] not in provider_json for binding in task_bindings.values())
