"""Host-neutral task handle and deterministic continuity driver.

The driver stores no Host transcript, reasoning, session, path, or authentication
material.  It derives the existing task binding from one opaque handle and the
current Git snapshot, reads through KnowledgeOS, and writes only through the
independent knowledge_sink contract with an owner grant.
"""

from __future__ import annotations

import base64
import re
import shutil
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from .api import KnowledgeOS
from .bounded_subprocess import BoundedSubprocessError, run_bounded_subprocess
from .host_runtime import resolve_knowledge_vault, safe_directory_path
from .knowledge_autonomy import AutonomousKnowledgeStore
from .knowledge_sink_mcp_server import handle_knowledge_sink
from .knowledge_store import KnowledgeVault
from .task_context import build_task_context_binding
from .util import canonical_json, sha256_bytes, strict_json_loads

TASK_HANDLE_SCHEMA_VERSION = "deeplaw.task-handle/v1"
TASK_CONTINUITY_SCHEMA_VERSION = "deeplaw.task-continuity-result/v1"

_HANDLE_PREFIX = "taskh_"
_VAULT_ID = re.compile(r"^vault_[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HANDLE_FIELDS = (
    "schema_version",
    "vault_id",
    "project_sha256",
    "task_lineage_sha256",
    "parent_task_lineage_sha256",
    "repository_sha256",
    "worktree_sha256",
    "task_handle_sha256",
)
_MAX_HANDLE_BYTES = 2048
_MAX_GIT_STATUS_BYTES = 4 * 1024 * 1024
_MAX_UNTRACKED_PATHS = 4096
_RESUME_TASK = "Restore the exact admitted working checkpoint for this task handle."

ForkMode = Literal["continue-parent", "child-task"]
ReadOperation = Literal["resume", "compaction"]


def _normalized_label(value: str, *, label: str, maximum: int = 5000) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized or len(normalized.encode("utf-8")) > maximum:
        raise ValueError(f"{label} is empty or exceeds its bound")
    return normalized


def _identity_digest(domain: str, value: str) -> str:
    return sha256_bytes(f"{domain}\0{value}".encode())


def _git(
    workspace: Path,
    *arguments: str,
    max_stdout_bytes: int = 8192,
) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("Git is required for bounded task continuity")
    try:
        completed = run_bounded_subprocess(
            [
                executable,
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                *arguments,
            ],
            cwd=workspace,
            timeout_seconds=30,
            max_stdout_bytes=max_stdout_bytes,
            max_stderr_bytes=8192,
        )
    except BoundedSubprocessError as error:
        raise RuntimeError("task workspace Git inspection failed closed") from error
    if completed.returncode != 0:
        raise RuntimeError("task workspace is not a supported Git worktree")
    return completed.stdout


def _git_text(workspace: Path, *arguments: str) -> str:
    try:
        value = _git(workspace, *arguments).decode("utf-8", errors="strict").strip()
    except UnicodeError:
        raise RuntimeError("task workspace Git identity is invalid") from None
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise RuntimeError("task workspace Git identity is invalid")
    return value


def _dirty_state_sha256(worktree: Path) -> str:
    status = _git(
        worktree,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignore-submodules=none",
        max_stdout_bytes=_MAX_GIT_STATUS_BYTES,
    )
    tracked_diff = _git(
        worktree,
        "diff",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
        "--",
        ".",
        ":(exclude).env",
        ":(exclude,glob)**/.env",
        max_stdout_bytes=_MAX_GIT_STATUS_BYTES,
    )
    untracked = _git(
        worktree,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        max_stdout_bytes=_MAX_GIT_STATUS_BYTES,
    )
    raw_paths = [item for item in untracked.split(b"\0") if item]
    if len(raw_paths) > _MAX_UNTRACKED_PATHS:
        raise RuntimeError("task workspace untracked-file inventory exceeds its bound")
    metadata: list[dict[str, Any]] = []
    for raw_path in raw_paths:
        try:
            relative_text = raw_path.decode("utf-8", errors="strict")
        except UnicodeError:
            raise RuntimeError("task workspace path identity is invalid") from None
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts or "\x00" in relative_text:
            raise RuntimeError("task workspace path identity is invalid")
        selected = worktree / relative
        try:
            stat_result = selected.stat(follow_symlinks=False)
        except OSError:
            raise RuntimeError("task workspace changed during inspection") from None
        metadata.append(
            {
                "path_sha256": _identity_digest(
                    "deeplaw-untracked-path/v1", relative.as_posix()
                ),
                "mode": stat_result.st_mode,
                "size": stat_result.st_size,
                "mtime_ns": stat_result.st_mtime_ns,
                "inode": getattr(stat_result, "st_ino", 0),
            }
        )
    return sha256_bytes(
        canonical_json(
            {
                "status_sha256": sha256_bytes(status),
                "tracked_diff_sha256": sha256_bytes(tracked_diff),
                "untracked_metadata": metadata,
            }
        ).encode()
    )


def _workspace_snapshot(workspace: str | Path) -> dict[str, str]:
    selected = safe_directory_path(
        workspace,
        label="task workspace",
        require_existing=True,
    )
    worktree_root = safe_directory_path(
        _git_text(selected, "rev-parse", "--show-toplevel"),
        label="task Git worktree",
        require_existing=True,
    )
    common_text = _git_text(selected, "rev-parse", "--git-common-dir")
    common_path = Path(common_text)
    if not common_path.is_absolute():
        common_path = worktree_root / common_path
    common_directory = safe_directory_path(
        common_path,
        label="task Git repository",
        require_existing=True,
    )
    base_revision = _git_text(selected, "rev-parse", "HEAD")
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", base_revision) is None:
        raise RuntimeError("task workspace Git base revision is invalid")
    return {
        "repository_sha256": _identity_digest(
            "deeplaw-git-repository/v1", str(common_directory)
        ),
        "worktree_sha256": _identity_digest(
            "deeplaw-git-worktree/v1", str(worktree_root)
        ),
        "base_revision": base_revision,
        "dirty_state_sha256": _dirty_state_sha256(worktree_root),
    }


def _handle_payload(
    *,
    vault_id: str,
    project_sha256: str,
    task_lineage_sha256: str,
    parent_task_lineage_sha256: str | None,
    repository_sha256: str,
    worktree_sha256: str,
) -> dict[str, Any]:
    value = {
        "schema_version": TASK_HANDLE_SCHEMA_VERSION,
        "vault_id": vault_id,
        "project_sha256": project_sha256,
        "task_lineage_sha256": task_lineage_sha256,
        "parent_task_lineage_sha256": parent_task_lineage_sha256,
        "repository_sha256": repository_sha256,
        "worktree_sha256": worktree_sha256,
    }
    value["task_handle_sha256"] = sha256_bytes(canonical_json(value).encode("utf-8"))
    return value


def _validate_handle_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(_HANDLE_FIELDS):
        raise ValueError("task handle has an invalid closed payload")
    if value.get("schema_version") != TASK_HANDLE_SCHEMA_VERSION:
        raise ValueError("task handle schema is unsupported")
    if not isinstance(value.get("vault_id"), str) or not _VAULT_ID.fullmatch(
        value["vault_id"]
    ):
        raise ValueError("task handle vault identity is invalid")
    for field in (
        "project_sha256",
        "task_lineage_sha256",
        "repository_sha256",
        "worktree_sha256",
        "task_handle_sha256",
    ):
        if not isinstance(value.get(field), str) or not _SHA256.fullmatch(value[field]):
            raise ValueError("task handle digest is invalid")
    parent = value.get("parent_task_lineage_sha256")
    if parent is not None and (not isinstance(parent, str) or not _SHA256.fullmatch(parent)):
        raise ValueError("task handle parent identity is invalid")
    if parent == value["task_lineage_sha256"]:
        raise ValueError("task handle parent identity is invalid")
    unhashed = {field: value[field] for field in _HANDLE_FIELDS[:-1]}
    if value["task_handle_sha256"] != sha256_bytes(
        canonical_json(unhashed).encode("utf-8")
    ):
        raise ValueError("task handle checksum does not match")
    return {field: value[field] for field in _HANDLE_FIELDS}


def encode_task_handle(value: dict[str, Any]) -> str:
    normalized = _validate_handle_payload(value)
    payload = canonical_json(normalized).encode("utf-8")
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return _HANDLE_PREFIX + token


def decode_task_handle(
    task_handle: str,
    *,
    expected_vault_id: str | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(task_handle, str)
        or not task_handle.startswith(_HANDLE_PREFIX)
        or len(task_handle.encode("utf-8")) > _MAX_HANDLE_BYTES
    ):
        raise ValueError("task handle is invalid")
    encoded = task_handle[len(_HANDLE_PREFIX) :]
    if not encoded or re.fullmatch(r"[A-Za-z0-9_-]+", encoded) is None:
        raise ValueError("task handle is invalid")
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = base64.b64decode(padded, altchars=b"-_", validate=True)
        value = strict_json_loads(payload)
    except (TypeError, ValueError):
        raise ValueError("task handle is invalid") from None
    normalized = _validate_handle_payload(value)
    if canonical_json(normalized).encode("utf-8") != payload:
        raise ValueError("task handle is not canonical")
    if expected_vault_id is not None and normalized["vault_id"] != expected_vault_id:
        raise PermissionError("task handle belongs to another Knowledge Vault")
    return normalized


def _vault(vault_path: str | Path | None, *, expected_vault_id: str | None) -> tuple[Path, str]:
    selected = resolve_knowledge_vault(
        vault_path,
        expected_vault_id=expected_vault_id,
        require_existing=True,
    )
    with KnowledgeVault(selected, read_only=True) as store:
        return selected, store.vault_id


def _binding(
    task_handle: str,
    *,
    vault_id: str,
    workspace: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    handle = decode_task_handle(task_handle, expected_vault_id=vault_id)
    snapshot = _workspace_snapshot(workspace)
    if (
        handle["repository_sha256"] != snapshot["repository_sha256"]
        or handle["worktree_sha256"] != snapshot["worktree_sha256"]
    ):
        raise PermissionError("task handle does not match the selected worktree")
    binding = build_task_context_binding(
        handle["project_sha256"],
        handle["task_lineage_sha256"],
        parent_task_lineage_sha256=handle["parent_task_lineage_sha256"],
        repository_sha256=snapshot["repository_sha256"],
        worktree_sha256=snapshot["worktree_sha256"],
        base_revision=snapshot["base_revision"],
        dirty_state_sha256=snapshot["dirty_state_sha256"],
    )
    return handle, binding


def binding_for_task_handle(
    task_handle: str,
    *,
    vault_path: str | Path | None,
    workspace: str | Path,
) -> dict[str, Any]:
    preliminary = decode_task_handle(task_handle)
    _selected, vault_id = _vault(
        vault_path,
        expected_vault_id=preliminary["vault_id"],
    )
    return _binding(
        task_handle,
        vault_id=vault_id,
        workspace=workspace,
    )[1]


def _base_result(
    handle: dict[str, Any],
    *,
    task_handle: str,
    operation: str,
    write_performed: bool,
) -> dict[str, Any]:
    return {
        "schema_version": TASK_CONTINUITY_SCHEMA_VERSION,
        "operation": operation,
        "task_handle": task_handle,
        "task_handle_sha256": handle["task_handle_sha256"],
        "project_sha256": handle["project_sha256"],
        "task_lineage_sha256": handle["task_lineage_sha256"],
        "parent_task_lineage_sha256": handle["parent_task_lineage_sha256"],
        "transcript_copied": False,
        "native_host_lifecycle_observed": False,
        "write_performed": write_performed,
    }


def start_task(
    *,
    vault_path: str | Path | None,
    project: str,
    task: str,
    workspace: str | Path,
) -> dict[str, Any]:
    selected, vault_id = _vault(vault_path, expected_vault_id=None)
    del selected
    project_text = _normalized_label(project, label="project", maximum=500)
    task_text = _normalized_label(task, label="task")
    snapshot = _workspace_snapshot(workspace)
    project_sha256 = _identity_digest("deeplaw-project/v1", project_text)
    task_lineage_sha256 = _identity_digest(
        "deeplaw-task-lineage/v1",
        f"{project_sha256}\0{task_text}",
    )
    payload = _handle_payload(
        vault_id=vault_id,
        project_sha256=project_sha256,
        task_lineage_sha256=task_lineage_sha256,
        parent_task_lineage_sha256=None,
        repository_sha256=snapshot["repository_sha256"],
        worktree_sha256=snapshot["worktree_sha256"],
    )
    handle = encode_task_handle(payload)
    return {
        **_base_result(
            payload,
            task_handle=handle,
            operation="start",
            write_performed=False,
        ),
        "status": "ready",
        "workspace_bound": True,
        "host_configuration_argument": ["--task-handle", handle],
    }


def fork_task(
    *,
    vault_path: str | Path | None,
    task_handle: str,
    workspace: str | Path,
    mode: ForkMode,
    child_task: str | None = None,
) -> dict[str, Any]:
    preliminary = decode_task_handle(task_handle)
    _selected, vault_id = _vault(
        vault_path,
        expected_vault_id=preliminary["vault_id"],
    )
    parent, _current_binding = _binding(
        task_handle,
        vault_id=vault_id,
        workspace=workspace,
    )
    if mode == "continue-parent":
        if child_task is not None:
            raise ValueError("continue-parent does not accept child_task")
        return {
            **_base_result(
                parent,
                task_handle=task_handle,
                operation="fork",
                write_performed=False,
            ),
            "status": "ready",
            "fork_mode": mode,
        }
    if mode != "child-task":
        raise ValueError("fork mode must be continue-parent or child-task")
    child_text = _normalized_label(child_task, label="child task")
    child_lineage = _identity_digest(
        "deeplaw-child-task-lineage/v1",
        f"{parent['task_lineage_sha256']}\0{child_text}",
    )
    child = _handle_payload(
        vault_id=vault_id,
        project_sha256=parent["project_sha256"],
        task_lineage_sha256=child_lineage,
        parent_task_lineage_sha256=parent["task_lineage_sha256"],
        repository_sha256=parent["repository_sha256"],
        worktree_sha256=parent["worktree_sha256"],
    )
    child_handle = encode_task_handle(child)
    return {
        **_base_result(
            child,
            task_handle=child_handle,
            operation="fork",
            write_performed=False,
        ),
        "status": "ready",
        "fork_mode": mode,
    }


def _provider_gap_codes(provider: dict[str, Any]) -> list[str]:
    capsule = provider.get("capsule")
    gaps = capsule.get("gaps", []) if isinstance(capsule, dict) else []
    return sorted(
        {
            str(item["code"])
            for item in gaps
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        }
    )


def resume_task(
    *,
    vault_path: str | Path | None,
    task_handle: str,
    workspace: str | Path,
    operation: ReadOperation = "resume",
) -> dict[str, Any]:
    if operation not in {"resume", "compaction"}:
        raise ValueError("continuity read operation is invalid")
    preliminary = decode_task_handle(task_handle)
    selected, vault_id = _vault(
        vault_path,
        expected_vault_id=preliminary["vault_id"],
    )
    handle, binding = _binding(
        task_handle,
        vault_id=vault_id,
        workspace=workspace,
    )
    with AutonomousKnowledgeStore(selected, read_only=True) as store:
        audit_before = store.audit_head
        scope = store.vault_scope
        route_lookup = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_RESUME_TASK.encode()),
            task_binding=binding,
            scope=scope,
            max_sensitivity="private",
        )
    query_target = {
        "text": _RESUME_TASK,
        "semantic_key": f"checkpoint:task-handle:{handle['task_handle_sha256']}",
        "kind": "memory",
    }
    expected_knowledge_id: str | None = None
    if route_lookup.get("status") == "exact":
        knowledge_ids = route_lookup.get("knowledge_ids")
        revision_ids = route_lookup.get("revision_ids")
        if (
            not isinstance(knowledge_ids, list)
            or len(knowledge_ids) != 1
            or not isinstance(revision_ids, list)
            or len(revision_ids) != 1
        ):
            raise RuntimeError("task checkpoint route returned an invalid exact head")
        expected_knowledge_id = str(knowledge_ids[0])
        query_target.update(
            {
                "knowledge_id": expected_knowledge_id,
                "revision_id": str(revision_ids[0]),
            }
        )
    context = KnowledgeOS.open(selected).context.compile(
        task=_RESUME_TASK,
        purpose="answer",
        scope=scope,
        max_sensitivity="private",
        limit=8,
        max_chars=8000,
        max_tokens=6000,
        max_sources=4,
        graph_hops=0,
        retrieval_mode="lexical",
        query_target=query_target,
        projection="standard",
        task_binding=binding,
        confirm_no_case_data=True,
    )
    provider = context["provider_capsule"]
    with AutonomousKnowledgeStore(selected, read_only=True) as store:
        if store.audit_head != audit_before:
            raise RuntimeError("task continuity read changed the Knowledge Ledger")
    statements = provider.get("capsule", {}).get("statements", [])
    selected_knowledge_ids = {
        str(item["knowledge_id"])
        for item in statements
        if isinstance(item, dict) and isinstance(item.get("knowledge_id"), str)
    } if isinstance(statements, list) else set()
    status = (
        "admitted"
        if expected_knowledge_id is not None
        and selected_knowledge_ids == {expected_knowledge_id}
        else "gap"
    )
    return {
        **_base_result(
            handle,
            task_handle=task_handle,
            operation=operation,
            write_performed=False,
        ),
        "status": status,
        "binding_sha256": binding["binding_sha256"],
        "checkpoint_route_status": route_lookup.get("status", "invalid"),
        "gap_codes": _provider_gap_codes(provider),
        "provider_capsule": provider,
        "deterministic_data_plane_recovery": True,
    }


def _bounded_items(values: Sequence[str], *, label: str, maximum: int = 8) -> list[str]:
    if isinstance(values, (str, bytes)) or len(values) > maximum:
        raise ValueError(f"{label} exceeds its item bound")
    return [
        _normalized_label(value, label=label, maximum=500)
        for value in values
    ]


def checkpoint_task(
    *,
    vault_path: str | Path | None,
    task_handle: str,
    workspace: str | Path,
    grant_id: str,
    idempotency_key: str,
    summary: str,
    next_action: str,
    expires_at: str,
    decisions: Sequence[str] = (),
    gaps: Sequence[str] = (),
    artifact_refs: Sequence[str] = (),
    confirm_no_case_data: bool,
) -> dict[str, Any]:
    if confirm_no_case_data is not True:
        raise PermissionError("task checkpoint requires explicit no-case-data confirmation")
    key = _normalized_label(idempotency_key, label="idempotency key", maximum=180)
    selected_summary = _normalized_label(summary, label="checkpoint summary", maximum=500)
    selected_next = _normalized_label(next_action, label="next action", maximum=500)
    selected_decisions = _bounded_items(decisions, label="checkpoint decision") or [
        "Continue only through this exact validated task handle."
    ]
    selected_gaps = _bounded_items(gaps, label="checkpoint gap") or [
        "No unresolved gap was recorded at this success boundary."
    ]
    selected_artifacts = _bounded_items(artifact_refs, label="artifact reference") or [
        "No external artifact reference was recorded."
    ]
    if not isinstance(expires_at, str) or not expires_at:
        raise ValueError("checkpoint expiry is required")
    preliminary = decode_task_handle(task_handle)
    selected, vault_id = _vault(
        vault_path,
        expected_vault_id=preliminary["vault_id"],
    )
    handle, binding = _binding(
        task_handle,
        vault_id=vault_id,
        workspace=workspace,
    )
    with AutonomousKnowledgeStore(selected, read_only=True) as store:
        prior_head = store.checkpoint_route_head_for_write(task_binding=binding)
    if prior_head["status"] not in {"exact", "not_found"}:
        raise RuntimeError(
            f"task checkpoint head is unavailable: {prior_head['status']}"
        )
    run_id = "taskrun_" + sha256_bytes(
        f"{handle['task_handle_sha256']}\0{key}".encode()
    )[:24]
    run_response = handle_knowledge_sink(
        {
            "operation": "record_run",
            "idempotency_key": f"{key}:run",
            "confirm_no_case_data": True,
            "run_id": run_id,
            "task": _RESUME_TASK,
            "host_id": "deeplaw-task-continuity-driver",
            "status": "succeeded",
            "run_metadata": {"task_binding": binding},
        },
        grant_id=grant_id,
        vault_path=selected,
    )
    recorded_run = run_response["result"]
    body_lines = [
        f"GOAL: {selected_summary}",
        *(f"CONFIRMED_DECISION: {item}" for item in selected_decisions),
        "CONSTRAINT: Resume only the exact task route and workspace snapshot.",
        (
            "VERIFIED_FACT: This is the exact admitted working checkpoint to restore "
            "for this task handle after a succeeded task-bound Run."
        ),
        *(f"OPEN_GAP: {item}" for item in selected_gaps),
        f"NEXT_ACTION: {selected_next}",
        *(f"ARTIFACT_REF: {item}" for item in selected_artifacts),
    ]
    checkpoint_request: dict[str, Any] = {
        "operation": "remember",
        "idempotency_key": f"{key}:checkpoint",
        "confirm_no_case_data": True,
        "title": "Task continuity checkpoint",
        "body": "\n".join(body_lines),
        "kind": "memory",
        "memory_type": "working",
        "expires_at": expires_at,
        "run_id": recorded_run["run_id"],
        "semantic_key": f"checkpoint:task-handle:{handle['task_handle_sha256']}",
        "tags": ["checkpoint", "task-continuity"],
    }
    if prior_head["status"] == "exact":
        checkpoint_request.update(
            {
                "knowledge_id": prior_head["knowledge_id"],
                "expected_revision_id": prior_head["revision_id"],
            }
        )
    checkpoint_response = handle_knowledge_sink(
        checkpoint_request,
        grant_id=grant_id,
        vault_path=selected,
    )
    checkpoint = checkpoint_response["result"]
    return {
        **_base_result(
            handle,
            task_handle=task_handle,
            operation="checkpoint",
            write_performed=True,
        ),
        "status": "checkpointed",
        "sink_leaf": "knowledge_sink",
        "run_id": recorded_run["run_id"],
        "knowledge_id": checkpoint["knowledge_id"],
        "revision_id": checkpoint["revision_id"],
    }


def forget_task(
    *,
    vault_path: str | Path | None,
    task_handle: str,
    workspace: str | Path,
    grant_id: str,
    idempotency_key: str,
    reason: str,
    confirm_no_case_data: bool,
) -> dict[str, Any]:
    if confirm_no_case_data is not True:
        raise PermissionError("task forget requires explicit no-case-data confirmation")
    key = _normalized_label(idempotency_key, label="idempotency key", maximum=190)
    selected_reason = _normalized_label(reason, label="forget reason", maximum=2000)
    preliminary = decode_task_handle(task_handle)
    selected, vault_id = _vault(
        vault_path,
        expected_vault_id=preliminary["vault_id"],
    )
    handle, binding = _binding(
        task_handle,
        vault_id=vault_id,
        workspace=workspace,
    )
    with AutonomousKnowledgeStore(selected, read_only=True) as store:
        lookup = store.lookup_checkpoint_route_projection(
            task_sha256=sha256_bytes(_RESUME_TASK.encode("utf-8")),
            task_binding=binding,
            scope=store.vault_scope,
            max_sensitivity="private",
        )
    if lookup.get("status") != "exact":
        raise RuntimeError(
            f"task checkpoint cannot be forgotten from state={lookup.get('status', 'invalid')}"
        )
    response = handle_knowledge_sink(
        {
            "operation": "forget",
            "idempotency_key": key,
            "confirm_no_case_data": True,
            "knowledge_id": lookup["knowledge_ids"][0],
            "expected_revision_id": lookup["revision_ids"][0],
            "reason": selected_reason,
        },
        grant_id=grant_id,
        vault_path=selected,
    )
    result = response["result"]
    return {
        **_base_result(
            handle,
            task_handle=task_handle,
            operation="forget",
            write_performed=True,
        ),
        "status": "forgotten",
        "sink_leaf": "knowledge_sink",
        "knowledge_id": result["knowledge_id"],
        "revision_id": result["revision_id"],
    }


__all__ = [
    "TASK_CONTINUITY_SCHEMA_VERSION",
    "TASK_HANDLE_SCHEMA_VERSION",
    "binding_for_task_handle",
    "checkpoint_task",
    "decode_task_handle",
    "encode_task_handle",
    "forget_task",
    "fork_task",
    "resume_task",
    "start_task",
]
