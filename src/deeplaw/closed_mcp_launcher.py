"""Fixed-target launcher for DeepLaw MCP children with a closed environment."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from .host_runtime import (
    closed_mcp_surface,
    observed_knowledge_vault_id,
    resolve_knowledge_vault,
    safe_existing_path,
)
from .store import default_home
from .subprocess_environment import _build_subprocess_environment
from .task_context import normalize_task_context_binding
from .util import canonical_json

ClosedMCPSurface = Literal["knowledge_support", "knowledge_sink", "law_support"]

@dataclass(frozen=True)
class ClosedMCPEnvironment:
    """One temporary, isolated process environment owned by the launcher."""

    cwd: str
    environment: dict[str, str]
    allowed_environment_names: frozenset[str]
    expected_vault_id: str | None
    native_acl_verified: bool


def _explicit_law_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    configured_home = os.environ.get("DEEPLAW_HOME")
    selected_home = (
        Path(configured_home).expanduser().absolute()
        if configured_home
        else default_home().absolute()
    )
    try:
        resolved_home = selected_home.resolve(strict=False)
    except (OSError, RuntimeError):
        raise RuntimeError("selected DeepLaw data home is unavailable") from None
    if os.path.normcase(str(selected_home)) != os.path.normcase(str(resolved_home)):
        raise RuntimeError("selected DeepLaw data home is unsafe")
    if selected_home.exists():
        selected_home = safe_existing_path(
            selected_home,
            directory=True,
            label="DeepLaw data home",
        )
    environment["DEEPLAW_HOME"] = str(selected_home)
    for name, label in (
        ("DEEPLAW_DB", "official release database"),
        ("DEEPLAW_PRIVATE_DB", "private legal database"),
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = str(
                safe_existing_path(value, directory=False, label=label)
            )
    if os.environ.get("DEEPLAW_LAW_FEDERATED_KNOWLEDGE") == "1":
        environment["DEEPLAW_LAW_FEDERATED_KNOWLEDGE"] = "1"
        environment["DEEPLAW_KNOWLEDGE_VAULT"] = str(
            resolve_knowledge_vault(
                None,
                expected_vault_id=None,
                require_existing=True,
            )
        )
    return environment


@contextmanager
def closed_mcp_environment(
    *,
    surface: str,
    vault_path: str | Path | None = None,
    expected_vault_id: str | None = None,
    task_binding: Mapping[str, object] | None = None,
    task_handle: str | None = None,
    workspace: str | Path | None = None,
) -> Iterator[ClosedMCPEnvironment]:
    """Yield the closed environment for one enumerated DeepLaw MCP surface."""

    selected_surface = closed_mcp_surface(surface)
    explicit: dict[str, str]
    child_expected_vault_id: str | None = None
    if selected_surface in {"knowledge_support", "knowledge_sink"}:
        selected_vault = resolve_knowledge_vault(
            vault_path,
            expected_vault_id=expected_vault_id,
            require_existing=selected_surface == "knowledge_sink",
        )
        explicit = {"DEEPLAW_KNOWLEDGE_VAULT": str(selected_vault)}
        if (selected_vault / "vault.json").is_file():
            child_expected_vault_id = observed_knowledge_vault_id(selected_vault)
    else:
        if vault_path is not None or expected_vault_id is not None:
            raise ValueError("law_support does not accept a Knowledge Vault argument")
        explicit = _explicit_law_environment()

    normalized_binding = normalize_task_context_binding(task_binding, allow_none=True)
    if normalized_binding is not None and task_handle is not None:
        raise ValueError("task binding and task handle are mutually exclusive")
    if task_handle is not None:
        if selected_surface != "knowledge_support":
            raise ValueError("task handle is accepted only by knowledge_support")
        from .task_continuity import binding_for_task_handle

        if workspace is None:
            raise ValueError("task handle requires explicit Host workspace metadata")

        normalized_binding = binding_for_task_handle(
            task_handle,
            vault_path=selected_vault,
            workspace=workspace,
        )
    elif workspace is not None:
        raise ValueError("workspace metadata is accepted only with a task handle")
    if normalized_binding is not None:
        if selected_surface != "knowledge_support":
            raise ValueError("task binding is accepted only by knowledge_support")
        explicit["DEEPLAW_TASK_BINDING"] = canonical_json(normalized_binding)

    with TemporaryDirectory(prefix="deeplaw-mcp-") as temporary:
        root = Path(temporary).resolve(strict=True)
        home = root / "home"
        work = root / "work"
        temporary_files = root / "tmp"
        for path in (home, work, temporary_files):
            path.mkdir(mode=0o700)
        native_acl_verified = os.name != "nt"
        if os.name == "nt":
            from .windows_acl import harden_windows_vault

            acl_hardening = harden_windows_vault(root)
            native_acl_verified = bool(
                acl_hardening.get("applied")
                and acl_hardening.get("verification", {}).get("permissions_verified")
            )
            if not native_acl_verified:
                raise RuntimeError("launcher temporary root ACL hardening failed closed")
        overrides = {
            "HOME": str(home),
            "TEMP": str(temporary_files),
            "TMP": str(temporary_files),
            "TMPDIR": str(temporary_files),
            "XDG_CACHE_HOME": str(home / "xdg-cache"),
            "XDG_CONFIG_HOME": str(home / "xdg-config"),
            "XDG_DATA_HOME": str(home / "xdg-data"),
            "XDG_STATE_HOME": str(home / "xdg-state"),
        }
        environment = _build_subprocess_environment(overrides=overrides)
        if sys.platform == "darwin":
            # CoreFoundation otherwise synthesizes this process-local setting.
            # Supply a fixed value so the observed child environment remains
            # closed and independent of the ambient user profile.
            environment["__CF_USER_TEXT_ENCODING"] = "0x0:0x0:0x0"
        environment.update(explicit)
        yield ClosedMCPEnvironment(
            cwd=str(work),
            environment=environment,
            allowed_environment_names=frozenset(environment),
            expected_vault_id=child_expected_vault_id,
            native_acl_verified=native_acl_verified,
        )


def launch_closed_mcp(
    *,
    surface: str,
    vault_path: str | Path | None = None,
    expected_vault_id: str | None = None,
    task_binding: Mapping[str, object] | None = None,
    task_handle: str | None = None,
    workspace: str | Path | None = None,
    grant_id: str | None = None,
) -> None:
    """Launch one fixed DeepLaw MCP child; arbitrary commands are not accepted."""

    selected_surface = closed_mcp_surface(surface)
    if selected_surface == "knowledge_sink":
        from .host_runtime import build_closed_mcp_argv

        closed_argv = build_closed_mcp_argv(
            surface=selected_surface,
            grant_id=grant_id,
        )
        child_arguments = [
            argument
            for argument in closed_argv[1:]
            if argument != "--closed-environment"
        ]
    elif selected_surface == "knowledge_support":
        if grant_id is not None:
            raise ValueError("knowledge_support does not accept a grant identity")
        child_arguments = ["knowledge", "mcp", "--stdio"]
    else:
        if grant_id is not None:
            raise ValueError("law_support does not accept a grant identity")
        child_arguments = ["mcp", "--stdio"]

    with closed_mcp_environment(
        surface=selected_surface,
        vault_path=vault_path,
        expected_vault_id=expected_vault_id,
        task_binding=task_binding,
        task_handle=task_handle,
        workspace=workspace,
    ) as launch:
        if launch.expected_vault_id is not None:
            child_arguments.extend(
                ("--expected-vault-id", launch.expected_vault_id)
            )
        completed = subprocess.run(
            [sys.executable, "-m", "deeplaw", *child_arguments],
            cwd=launch.cwd,
            env=launch.environment,
            check=False,
        )
    if completed.returncode:
        raise SystemExit(completed.returncode)


__all__ = ["closed_mcp_environment", "launch_closed_mcp"]
