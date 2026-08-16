"""Host-neutral task routing and deterministic continuity driver.

The driver stores no Host transcript, reasoning, session, path, or authentication
material. It derives the existing task binding from owner-visible task/workspace
identity or an optional opaque handle, reads through KnowledgeOS, and writes only
through the independent knowledge_sink contract with an owner grant.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import stat
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from .api import KnowledgeOS
from .bounded_subprocess import BoundedSubprocessError, run_bounded_subprocess
from .host_runtime import resolve_knowledge_vault, safe_directory_path
from .knowledge_autonomy import AutonomousKnowledgeStore, normalize_run_artifact_ids
from .knowledge_sink_mcp_server import handle_knowledge_sink
from .knowledge_store import KnowledgeVault
from .task_context import (
    build_task_context_binding,
    task_route_sha256,
    task_snapshot_sha256,
)
from .util import canonical_json, sha256_bytes, strict_json_loads

TASK_HANDLE_SCHEMA_VERSION = "deeplaw.task-handle/v1"
TASK_CONTINUITY_SCHEMA_VERSION = "deeplaw.task-continuity-result/v2"
WORKSPACE_SNAPSHOT_SCHEMA_VERSION = "deeplaw.workspace-snapshot-receipt/v1"

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
_MAX_UNTRACKED_FILE_BYTES = 4 * 1024 * 1024
_MAX_UNTRACKED_TOTAL_BYTES = 16 * 1024 * 1024
_MAX_TIMELINE_ENTRIES = 100
_RESUME_TASK = "Restore the exact admitted working checkpoint for this task route."

ForkMode = Literal["continue-parent", "child-task"]
ReadOperation = Literal["resume", "compaction"]


class WorkspaceSnapshotGap(RuntimeError):
    """A content-minimized, fail-closed workspace verification result."""

    def __init__(self, code: str, *, candidate_count: int | None = None) -> None:
        super().__init__(code)
        gap: dict[str, Any] = {"code": code}
        if candidate_count is not None:
            gap["candidate_count"] = candidate_count
        self.receipt = {
            "schema_version": WORKSPACE_SNAPSHOT_SCHEMA_VERSION,
            "status": "gap",
            "gap": gap,
        }


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
        if error.stdout_truncated:
            raise WorkspaceSnapshotGap("workspace_snapshot_bound") from None
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


def _workspace_paths(raw: bytes) -> list[tuple[str, Path]]:
    selected: list[tuple[str, Path]] = []
    for raw_path in (item for item in raw.split(b"\0") if item):
        try:
            relative_text = raw_path.decode("utf-8", errors="strict")
        except UnicodeError:
            raise RuntimeError("task workspace path identity is invalid") from None
        relative = Path(relative_text)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\x00" in relative_text
            or not relative.parts
        ):
            raise RuntimeError("task workspace path identity is invalid")
        selected.append((relative_text, relative))
    return selected


def _secret_looking_workspace_path(relative_text: str) -> bool:
    name = Path(relative_text).name.casefold()
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    stem = name.split(".", 1)[0]
    if stem in {
        "auth",
        "credential",
        "credentials",
        "secret",
        "secrets",
        "password",
        "passwd",
        "token",
        "private_key",
        "api_key",
    }:
        return True
    return name.endswith((".pem", ".key", ".p12", ".pfx"))


def _bounded_file_sha256(selected: Path, expected: os.stat_result) -> str:
    if expected.st_size > _MAX_UNTRACKED_FILE_BYTES:
        raise WorkspaceSnapshotGap("workspace_snapshot_bound")
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(selected, flags)
    except OSError:
        raise RuntimeError("task workspace changed during inspection") from None
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_dev != expected.st_dev
            or observed.st_ino != expected.st_ino
        ):
            raise RuntimeError("task workspace changed during inspection")
        chunks: list[bytes] = []
        remaining = _MAX_UNTRACKED_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > _MAX_UNTRACKED_FILE_BYTES:
            raise WorkspaceSnapshotGap("workspace_snapshot_bound")
        return sha256_bytes(content)
    finally:
        os.close(descriptor)


def _dirty_state_sha256(worktree: Path) -> str:
    tracked = _workspace_paths(
        _git(
            worktree,
            "ls-files",
            "--cached",
            "-z",
            max_stdout_bytes=_MAX_GIT_STATUS_BYTES,
        )
    )
    untracked = _workspace_paths(
        _git(
            worktree,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            max_stdout_bytes=_MAX_GIT_STATUS_BYTES,
        )
    )
    secret_candidates = {
        relative_text
        for relative_text, _relative in (*tracked, *untracked)
        if _secret_looking_workspace_path(relative_text)
    }
    if secret_candidates:
        raise WorkspaceSnapshotGap(
            "workspace_secret_unverifiable",
            candidate_count=len(secret_candidates),
        )
    if len(untracked) > _MAX_UNTRACKED_PATHS:
        raise WorkspaceSnapshotGap(
            "workspace_snapshot_bound",
            candidate_count=len(untracked),
        )

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
        max_stdout_bytes=_MAX_GIT_STATUS_BYTES,
    )
    total_bytes = 0
    content_identities: list[dict[str, Any]] = []
    for _relative_text, relative in untracked:
        selected = worktree / relative
        try:
            stat_result = selected.stat(follow_symlinks=False)
        except OSError:
            raise RuntimeError("task workspace changed during inspection") from None
        total_bytes += int(stat_result.st_size)
        if total_bytes > _MAX_UNTRACKED_TOTAL_BYTES:
            raise WorkspaceSnapshotGap(
                "workspace_snapshot_bound",
                candidate_count=len(untracked),
            )
        if stat.S_ISLNK(stat_result.st_mode):
            try:
                content_sha256 = sha256_bytes(os.readlink(selected).encode("utf-8"))
            except (OSError, UnicodeError):
                raise RuntimeError("task workspace changed during inspection") from None
        elif stat.S_ISREG(stat_result.st_mode):
            content_sha256 = _bounded_file_sha256(selected, stat_result)
        else:
            raise WorkspaceSnapshotGap("workspace_snapshot_bound")
        content_identities.append(
            {
                "path_sha256": _identity_digest(
                    "deeplaw-untracked-path/v1", relative.as_posix()
                ),
                "mode": stat_result.st_mode,
                "content_sha256": content_sha256,
            }
        )
    return sha256_bytes(
        canonical_json(
            {
                "status_sha256": sha256_bytes(status),
                "tracked_diff_sha256": sha256_bytes(tracked_diff),
                "untracked_content": content_identities,
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


def workspace_snapshot_receipt(workspace: str | Path) -> dict[str, Any]:
    """Return a bounded, path-free workspace identity or a structured Gap."""

    try:
        snapshot = _workspace_snapshot(workspace)
    except WorkspaceSnapshotGap as error:
        return error.receipt
    return {
        "schema_version": WORKSPACE_SNAPSHOT_SCHEMA_VERSION,
        "status": "ready",
        **snapshot,
    }


def _workspace_route_identity(workspace: str | Path) -> dict[str, str]:
    """Resolve only repository/worktree identity without inspecting file contents."""

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
    return {
        "repository_sha256": _identity_digest(
            "deeplaw-git-repository/v1", str(common_directory)
        ),
        "worktree_sha256": _identity_digest(
            "deeplaw-git-worktree/v1", str(worktree_root)
        ),
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


def _project_task_identities(project: str, task: str) -> tuple[str, str, str]:
    project_text = _normalized_label(project, label="project", maximum=500)
    task_text = _normalized_label(task, label="task")
    project_sha256 = _identity_digest("deeplaw-project/v1", project_text)
    task_lineage_sha256 = _identity_digest(
        "deeplaw-task-lineage/v1",
        f"{project_sha256}\0{task_text}",
    )
    return project_sha256, task_lineage_sha256, task_text


def _workspace_gap_result(*, operation: str, error: WorkspaceSnapshotGap) -> dict[str, Any]:
    return {
        "schema_version": TASK_CONTINUITY_SCHEMA_VERSION,
        "operation": operation,
        "status": "gap",
        "write_performed": False,
        "transcript_copied": False,
        "native_host_lifecycle_observed": False,
        "workspace_snapshot": error.receipt,
        "gap": error.receipt["gap"],
    }


def _handle_from_binding(*, vault_id: str, binding: dict[str, Any]) -> tuple[dict[str, Any], str]:
    payload = _handle_payload(
        vault_id=vault_id,
        project_sha256=binding["project_sha256"],
        task_lineage_sha256=binding["task_lineage_sha256"],
        parent_task_lineage_sha256=binding["parent_task_lineage_sha256"],
        repository_sha256=binding["repository_sha256"],
        worktree_sha256=binding["worktree_sha256"],
    )
    return payload, encode_task_handle(payload)


def start_task(
    *,
    vault_path: str | Path | None,
    project: str,
    task: str,
    workspace: str | Path,
) -> dict[str, Any]:
    selected, vault_id = _vault(vault_path, expected_vault_id=None)
    del selected
    project_sha256, task_lineage_sha256, _task_text = _project_task_identities(
        project, task
    )
    try:
        snapshot = _workspace_snapshot(workspace)
    except WorkspaceSnapshotGap as error:
        return _workspace_gap_result(operation="start", error=error)
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
    }


def locate_task(
    *,
    vault_path: str | Path | None,
    project: str,
    task: str,
    workspace: str | Path,
    task_handle: str | None = None,
) -> dict[str, Any]:
    """Locate one exact checkpoint route without requiring an internal handle."""

    expected_vault_id = decode_task_handle(task_handle)["vault_id"] if task_handle else None
    selected, vault_id = _vault(vault_path, expected_vault_id=expected_vault_id)
    project_sha256, _start_lineage, task_text = _project_task_identities(project, task)
    try:
        snapshot = _workspace_snapshot(workspace)
    except WorkspaceSnapshotGap as error:
        return _workspace_gap_result(operation="locate", error=error)
    current_binding = build_task_context_binding(
        project_sha256,
        _start_lineage,
        repository_sha256=snapshot["repository_sha256"],
        worktree_sha256=snapshot["worktree_sha256"],
        base_revision=snapshot["base_revision"],
        dirty_state_sha256=snapshot["dirty_state_sha256"],
    )
    with AutonomousKnowledgeStore(selected, read_only=True) as store:
        lookup = store.locate_checkpoint_task_projection(
            task_sha256=sha256_bytes(task_text.encode("utf-8")),
            project_sha256=project_sha256,
            repository_sha256=snapshot["repository_sha256"],
            worktree_sha256=snapshot["worktree_sha256"],
            snapshot_sha256=task_snapshot_sha256(current_binding),
            scope=store.vault_scope,
            max_sensitivity="private",
        )
        if lookup.get("status") == "not_found":
            lookup = store.locate_checkpoint_route_projection(
                route_sha256=task_route_sha256(current_binding),
                task_lineage_sha256=_start_lineage,
                project_sha256=project_sha256,
                repository_sha256=snapshot["repository_sha256"],
                worktree_sha256=snapshot["worktree_sha256"],
                snapshot_sha256=task_snapshot_sha256(current_binding),
                scope=store.vault_scope,
                max_sensitivity="private",
            )
    if lookup.get("status") != "exact":
        lookup_status = str(lookup.get("status", "invalid"))
        gap_code = "task_line_ambiguous" if lookup_status == "ambiguous" else lookup_status
        return {
            "schema_version": TASK_CONTINUITY_SCHEMA_VERSION,
            "operation": "locate",
            "status": lookup_status,
            "write_performed": False,
            "transcript_copied": False,
            "native_host_lifecycle_observed": False,
            "gap": {"code": gap_code},
        }
    binding = lookup.get("canonical_binding")
    if not isinstance(binding, dict):
        raise RuntimeError("task route locate returned no canonical binding")
    handle_payload, located_handle = _handle_from_binding(vault_id=vault_id, binding=binding)
    if task_handle is not None:
        supplied = decode_task_handle(task_handle, expected_vault_id=vault_id)
        if supplied["task_handle_sha256"] != handle_payload["task_handle_sha256"]:
            return {
                "schema_version": TASK_CONTINUITY_SCHEMA_VERSION,
                "operation": "locate",
                "status": "ambiguous",
                "write_performed": False,
                "transcript_copied": False,
                "native_host_lifecycle_observed": False,
                "gap": {"code": "task_handle_route_mismatch"},
            }
    return {
        **_base_result(
            handle_payload,
            task_handle=located_handle,
            operation="locate",
            write_performed=False,
        ),
        "status": "exact",
        "binding_sha256": binding["binding_sha256"],
        "checkpoint_identity": lookup["revision_ids"][0],
        "knowledge_identity": lookup["knowledge_ids"][0],
    }


def fork_task(
    *,
    vault_path: str | Path | None,
    task_handle: str,
    workspace: str | Path,
    child_workspace: str | Path | None = None,
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
        if child_task is not None or child_workspace is not None:
            raise ValueError("continue-parent does not accept child task or workspace")
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
    if child_workspace is None:
        raise ValueError("child-task requires an explicit child workspace")
    child_text = _normalized_label(child_task, label="child task")
    try:
        child_snapshot = _workspace_snapshot(child_workspace)
    except WorkspaceSnapshotGap as error:
        return _workspace_gap_result(operation="fork", error=error)
    if child_snapshot["repository_sha256"] != parent["repository_sha256"]:
        raise PermissionError("child task workspace belongs to another Git repository")
    if child_snapshot["worktree_sha256"] == parent["worktree_sha256"]:
        raise PermissionError("child task requires a different Git worktree")
    child_lineage = _identity_digest(
        "deeplaw-child-task-lineage/v1",
        f"{parent['task_lineage_sha256']}\0{child_text}",
    )
    child = _handle_payload(
        vault_id=vault_id,
        project_sha256=parent["project_sha256"],
        task_lineage_sha256=child_lineage,
        parent_task_lineage_sha256=parent["task_lineage_sha256"],
        repository_sha256=child_snapshot["repository_sha256"],
        worktree_sha256=child_snapshot["worktree_sha256"],
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
        "workspace_independent": child["worktree_sha256"] != parent["worktree_sha256"],
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
    task_handle: str | None = None,
    project: str | None = None,
    task: str | None = None,
    workspace: str | Path,
    operation: ReadOperation = "resume",
) -> dict[str, Any]:
    if operation not in {"resume", "compaction"}:
        raise ValueError("continuity read operation is invalid")
    if task_handle is None:
        if project is None or task is None:
            raise ValueError("resume requires a task handle or project and task text")
        located = locate_task(
            vault_path=vault_path,
            project=project,
            task=task,
            workspace=workspace,
        )
        if located.get("status") != "exact":
            return {**located, "operation": operation}
        task_handle = str(located["task_handle"])
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
    with KnowledgeOS.open(selected) as knowledge_os:
        context = knowledge_os.context.compile(
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


def inspect_task(
    *,
    vault_path: str | Path | None,
    project: str,
    task: str,
    workspace: str | Path,
    task_handle: str | None = None,
) -> dict[str, Any]:
    """Inspect the exact current route without returning checkpoint content."""

    located = locate_task(
        vault_path=vault_path,
        project=project,
        task=task,
        workspace=workspace,
        task_handle=task_handle,
    )
    return {**located, "operation": "inspect"}


def timeline_task(
    *,
    vault_path: str | Path | None,
    project: str,
    task: str,
    workspace: str | Path,
    task_handle: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return a bounded, content-minimized task identity timeline."""

    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= _MAX_TIMELINE_ENTRIES
    ):
        raise ValueError("task timeline limit is invalid")
    expected_vault_id = decode_task_handle(task_handle)["vault_id"] if task_handle else None
    selected, vault_id = _vault(vault_path, expected_vault_id=expected_vault_id)
    project_sha256, start_lineage, task_text = _project_task_identities(project, task)
    route_identity = _workspace_route_identity(workspace)
    snapshot_gap: WorkspaceSnapshotGap | None = None
    snapshot_sha256: str | None = None
    try:
        snapshot = _workspace_snapshot(workspace)
        current_binding = build_task_context_binding(
            project_sha256,
            start_lineage,
            repository_sha256=snapshot["repository_sha256"],
            worktree_sha256=snapshot["worktree_sha256"],
            base_revision=snapshot["base_revision"],
            dirty_state_sha256=snapshot["dirty_state_sha256"],
        )
        snapshot_sha256 = task_snapshot_sha256(current_binding)
    except WorkspaceSnapshotGap as error:
        snapshot_gap = error
    with AutonomousKnowledgeStore(selected, read_only=True) as store:
        history = store.locate_task_route_history(
            task_sha256=sha256_bytes(task_text.encode("utf-8")),
            fallback_task_lineage_sha256=start_lineage,
            project_sha256=project_sha256,
            repository_sha256=route_identity["repository_sha256"],
            worktree_sha256=route_identity["worktree_sha256"],
            snapshot_sha256=snapshot_sha256,
            scope=store.vault_scope,
            max_sensitivity="private",
        )
        binding = history.get("canonical_binding")
        if isinstance(binding, dict):
            timeline = store.task_identity_timeline(task_binding=binding, limit=limit)
        else:
            timeline = None
    history_status = str(history.get("status", "invalid"))
    if not isinstance(binding, dict) or timeline is None:
        gap_code = (
            "task_line_ambiguous" if history_status == "ambiguous" else history_status
        )
        return {
            "schema_version": TASK_CONTINUITY_SCHEMA_VERSION,
            "operation": "timeline",
            "status": history_status,
            "write_performed": False,
            "transcript_copied": False,
            "native_host_lifecycle_observed": False,
            "gap": {"code": gap_code},
            "entries": [],
        }
    handle_payload, located_handle = _handle_from_binding(vault_id=vault_id, binding=binding)
    if task_handle is not None:
        supplied = decode_task_handle(task_handle, expected_vault_id=vault_id)
        if supplied["task_handle_sha256"] != handle_payload["task_handle_sha256"]:
            return {
                "schema_version": TASK_CONTINUITY_SCHEMA_VERSION,
                "operation": "timeline",
                "status": "ambiguous",
                "write_performed": False,
                "transcript_copied": False,
                "native_host_lifecycle_observed": False,
                "gap": {"code": "task_handle_route_mismatch"},
                "entries": [],
            }
    base = _base_result(
        handle_payload,
        task_handle=located_handle,
        operation="timeline",
        write_performed=False,
    )
    if timeline.get("status") != "exact":
        return {
            **base,
            "status": "gap",
            "entries": [],
            "gap": {"code": str(timeline.get("status", "invalid"))},
        }
    gap_code: str | None = None
    if history_status == "forgotten":
        gap_code = "forgotten"
    elif snapshot_gap is not None:
        gap_code = str(snapshot_gap.receipt["gap"]["code"])
    elif history_status != "exact":
        gap_code = history_status
    return {
        **base,
        "status": "exact" if gap_code is None else "gap",
        "entries": timeline["entries"],
        "timeline_truncated": timeline["truncated"],
        **({"gap": {"code": gap_code}} if gap_code is not None else {}),
    }


def _bounded_items(values: Sequence[str], *, label: str, maximum: int = 8) -> list[str]:
    if isinstance(values, (str, bytes)) or len(values) > maximum:
        raise ValueError(f"{label} exceeds its item bound")
    return [
        _normalized_label(value, label=label, maximum=500)
        for value in values
    ]


def _bounded_artifact_refs(values: Sequence[str]) -> list[str]:
    selected = _bounded_items(values, label="artifact reference")
    try:
        return normalize_run_artifact_ids(selected)
    except ValueError:
        raise ValueError(
            "artifact reference must be an opaque bounded safe identifier"
        ) from None


def checkpoint_task(
    *,
    vault_path: str | Path | None,
    task_handle: str,
    workspace: str | Path,
    grant_id: str,
    idempotency_key: str,
    task: str | None = None,
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
        "Continue only through this exact validated task route."
    ]
    selected_gaps = _bounded_items(gaps, label="checkpoint gap") or [
        "No unresolved gap was recorded at this success boundary."
    ]
    selected_artifacts = _bounded_artifact_refs(artifact_refs)
    body_artifacts = selected_artifacts or ["artifact_none"]
    selected_task = (
        _RESUME_TASK if task is None else _normalized_label(task, label="task")
    )
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
    run_request: dict[str, Any] = {
        "operation": "record_run",
        "idempotency_key": f"{key}:run",
        "confirm_no_case_data": True,
        "run_id": run_id,
        "task": selected_task,
        "host_id": "deeplaw-task-continuity-driver",
        "status": "succeeded",
        "run_metadata": {
            "task_binding": binding,
            "artifact_ids": selected_artifacts,
        },
    }
    with AutonomousKnowledgeStore(selected, read_only=True) as store:
        try:
            existing_run = store.get_run(run_id)
        except KeyError:
            existing_run = None
    if existing_run is not None:
        run_request.update(
            {
                "started_at": existing_run["started_at"],
                "ended_at": existing_run["ended_at"],
            }
        )
    run_response = handle_knowledge_sink(
        run_request,
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
            "for this task route after a succeeded task-bound Run."
        ),
        *(f"OPEN_GAP: {item}" for item in selected_gaps),
        f"NEXT_ACTION: {selected_next}",
        *(f"ARTIFACT_REF: {item}" for item in body_artifacts),
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
    try:
        checkpoint_response = handle_knowledge_sink(
            checkpoint_request,
            grant_id=grant_id,
            vault_path=selected,
        )
    except Exception as error:
        return {
            **_base_result(
                handle,
                task_handle=task_handle,
                operation="checkpoint",
                write_performed=True,
            ),
            "status": "partial",
            "sink_leaf": "knowledge_sink",
            "run_id": recorded_run["run_id"],
            "run_status": "succeeded",
            "checkpoint_status": "pending_idempotent_retry",
            "recovery_idempotency_key_sha256": sha256_bytes(key.encode("utf-8")),
            "gap": {
                "code": "checkpoint_partial",
                "cause_type": type(error).__name__,
            },
        }
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
    handle = decode_task_handle(task_handle, expected_vault_id=vault_id)
    route_identity = _workspace_route_identity(workspace)
    if (
        handle["repository_sha256"] != route_identity["repository_sha256"]
        or handle["worktree_sha256"] != route_identity["worktree_sha256"]
    ):
        raise PermissionError("task handle does not match the selected worktree")
    route_binding = build_task_context_binding(
        handle["project_sha256"],
        handle["task_lineage_sha256"],
        parent_task_lineage_sha256=handle["parent_task_lineage_sha256"],
        repository_sha256=handle["repository_sha256"],
        worktree_sha256=handle["worktree_sha256"],
        base_revision="0" * 40,
        dirty_state_sha256="0" * 64,
    )
    with AutonomousKnowledgeStore(selected, read_only=True) as store:
        lookup = store.checkpoint_route_head_for_write(task_binding=route_binding)
    if lookup.get("status") != "exact":
        raise RuntimeError(
            f"task checkpoint cannot be forgotten from state={lookup.get('status', 'invalid')}"
        )
    response = handle_knowledge_sink(
        {
            "operation": "forget",
            "idempotency_key": key,
            "confirm_no_case_data": True,
            "knowledge_id": lookup["knowledge_id"],
            "expected_revision_id": lookup["revision_id"],
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
    "WORKSPACE_SNAPSHOT_SCHEMA_VERSION",
    "WorkspaceSnapshotGap",
    "binding_for_task_handle",
    "checkpoint_task",
    "decode_task_handle",
    "encode_task_handle",
    "forget_task",
    "fork_task",
    "inspect_task",
    "locate_task",
    "resume_task",
    "start_task",
    "timeline_task",
    "workspace_snapshot_receipt",
]
