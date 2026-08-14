"""Fixed-target launcher for DeepLaw MCP children with a closed environment."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, cast

from .knowledge_autonomy import AutonomousKnowledgeStore
from .knowledge_store import default_knowledge_vault
from .store import default_home
from .subprocess_environment import _build_subprocess_environment
from .task_context import normalize_task_context_binding
from .util import canonical_json

ClosedMCPSurface = Literal["knowledge_support", "knowledge_sink", "law_support"]

_SURFACES = frozenset({"knowledge_support", "knowledge_sink", "law_support"})
_GRANT_ID = re.compile(r"^grant_[0-9a-f]{24}$")


@dataclass(frozen=True)
class ClosedMCPEnvironment:
    """One temporary, isolated process environment owned by the launcher."""

    cwd: str
    environment: dict[str, str]
    allowed_environment_names: frozenset[str]


def _closed_surface(value: str) -> ClosedMCPSurface:
    if value not in _SURFACES:
        raise ValueError("DeepLaw MCP surface is invalid")
    return cast(ClosedMCPSurface, value)


def _safe_existing_path(value: str | Path, *, directory: bool, label: str) -> Path:
    """Resolve one data path while rejecting links, junctions, and reparse points."""

    selected = Path(value).expanduser().absolute()
    try:
        resolved = selected.resolve(strict=True)
        stat_result = selected.stat(follow_symlinks=False)
    except (OSError, RuntimeError):
        raise RuntimeError(f"selected {label} is unavailable") from None
    reparse_flag = getattr(stat_result, "st_file_attributes", 0) & 0x400
    if (
        os.path.normcase(str(selected)) != os.path.normcase(str(resolved))
        or selected.is_symlink()
        or reparse_flag
        or (directory and not selected.is_dir())
        or (not directory and not selected.is_file())
    ):
        raise RuntimeError(f"selected {label} is unsafe")
    return resolved


def _safe_directory_path(
    value: str | Path,
    *,
    label: str,
    require_existing: bool,
) -> Path:
    selected = Path(value).expanduser().absolute()
    try:
        resolved = selected.resolve(strict=False)
    except (OSError, RuntimeError):
        raise RuntimeError(f"selected {label} is unavailable") from None
    if os.path.normcase(str(selected)) != os.path.normcase(str(resolved)):
        raise RuntimeError(f"selected {label} is unsafe")
    if selected.exists():
        return _safe_existing_path(selected, directory=True, label=label)
    if require_existing:
        raise RuntimeError(f"selected {label} is unavailable")
    return selected


def _knowledge_vault(
    vault_path: str | Path | None,
    *,
    expected_vault_id: str | None,
    require_existing: bool,
) -> Path:
    configured = vault_path or os.environ.get("DEEPLAW_KNOWLEDGE_VAULT")
    selected = _safe_directory_path(
        configured or default_knowledge_vault(),
        label="Knowledge Vault",
        require_existing=require_existing or expected_vault_id is not None,
    )
    if expected_vault_id is not None:
        try:
            with AutonomousKnowledgeStore(selected, read_only=True) as store:
                observed_vault_id = store.vault_id
        except Exception:
            raise RuntimeError("selected Knowledge Vault is invalid") from None
        if observed_vault_id != expected_vault_id:
            raise RuntimeError("selected Knowledge Vault identity does not match")
    return selected


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
        selected_home = _safe_existing_path(
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
                _safe_existing_path(value, directory=False, label=label)
            )
    if os.environ.get("DEEPLAW_LAW_FEDERATED_KNOWLEDGE") == "1":
        environment["DEEPLAW_LAW_FEDERATED_KNOWLEDGE"] = "1"
        environment["DEEPLAW_KNOWLEDGE_VAULT"] = str(
            _knowledge_vault(
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
) -> Iterator[ClosedMCPEnvironment]:
    """Yield the closed environment for one enumerated DeepLaw MCP surface."""

    selected_surface = _closed_surface(surface)
    explicit: dict[str, str]
    if selected_surface in {"knowledge_support", "knowledge_sink"}:
        selected_vault = _knowledge_vault(
            vault_path,
            expected_vault_id=expected_vault_id,
            require_existing=selected_surface == "knowledge_sink",
        )
        explicit = {"DEEPLAW_KNOWLEDGE_VAULT": str(selected_vault)}
    else:
        if vault_path is not None or expected_vault_id is not None:
            raise ValueError("law_support does not accept a Knowledge Vault argument")
        explicit = _explicit_law_environment()

    normalized_binding = normalize_task_context_binding(task_binding, allow_none=True)
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
        )


def launch_closed_mcp(
    *,
    surface: str,
    vault_path: str | Path | None = None,
    expected_vault_id: str | None = None,
    task_binding: Mapping[str, object] | None = None,
    grant_id: str | None = None,
) -> None:
    """Launch one fixed DeepLaw MCP child; arbitrary commands are not accepted."""

    selected_surface = _closed_surface(surface)
    if selected_surface == "knowledge_sink":
        if not isinstance(grant_id, str) or not _GRANT_ID.fullmatch(grant_id):
            raise ValueError("knowledge_sink requires a valid owner grant identity")
        child_arguments = ["knowledge", "sink", "mcp", "--grant-id", grant_id, "--stdio"]
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
    ) as launch:
        completed = subprocess.run(
            [sys.executable, "-m", "deeplaw", *child_arguments],
            cwd=launch.cwd,
            env=launch.environment,
            check=False,
        )
    if completed.returncode:
        raise SystemExit(completed.returncode)


__all__ = ["closed_mcp_environment", "launch_closed_mcp"]
