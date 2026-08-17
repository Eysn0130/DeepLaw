"""Shared, path-free configuration and vault resolution for Host MCP entry points."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from . import __version__
from .knowledge_autonomy import _atomic_owner_write
from .knowledge_store import (
    _load_manifest,
    default_knowledge_vault,
    knowledge_vault_permission_report,
)
from .store import default_home
from .task_context import normalize_task_context_binding
from .util import canonical_json, sha256_bytes, strict_json_loads

HostMCPSurface = Literal["knowledge_support", "knowledge_sink", "law_support"]

_SURFACES = frozenset({"knowledge_support", "knowledge_sink", "law_support"})
_VAULT_ID = re.compile(r"^vault_[0-9a-f]{24}$")
_GRANT_ID = re.compile(r"^grant_[0-9a-f]{24}$")
_BINDINGS_SCHEMA = "deeplaw.owner-host-vault-bindings/v1"
_BINDINGS_FILENAME = "vault-bindings.v1.json"
_MAX_BINDINGS = 128
_MAX_BINDINGS_BYTES = 64 * 1024

_HOST_READINESS_PROFILES: dict[str, dict[str, Any]] = {
    "codex": {
        "plugin_id": "deeplaw-knowledge-os@deeplaw",
        "load_options": ["direct_mcp_config", "codex_plugin"],
        "host_version_for_current_qualification": "codex-cli 0.148.0-alpha.9",
        "verification_command": ["codex", "mcp", "list"],
    },
    "claude-code": {
        "plugin_id": "deeplaw-knowledge-os@deeplaw",
        "load_options": ["project_mcp_config", "claude_plugin"],
        "host_version_for_current_qualification": None,
        "verification_command": ["claude", "mcp", "list"],
    },
    "opencode": {
        "plugin_id": "deeplaw-native",
        "load_options": ["project_mcp_config", "project_native_plugin"],
        "host_version_for_current_qualification": "1.18.16",
        "verification_command": ["opencode", "mcp", "list"],
    },
}


def host_product_readiness(
    *,
    autonomous_vault_ready: bool,
    host: str | None = None,
) -> dict[str, Any]:
    """Describe exact current Host/MCP prerequisites without claiming observation."""

    if host is not None and host not in _HOST_READINESS_PROFILES:
        raise ValueError("host must be codex, claude-code, or opencode")
    selected_hosts = (host,) if host is not None else tuple(_HOST_READINESS_PROFILES)
    profiles: list[dict[str, Any]] = []
    for selected in selected_hosts:
        profile = _HOST_READINESS_PROFILES[selected]
        gaps: list[dict[str, str]] = []
        if not autonomous_vault_ready:
            gaps.append(
                {
                    "code": "autonomous_vault_not_ready",
                    "action": "repair knowledge doctor failures before Host connection",
                }
            )
        gaps.extend(
            (
                {
                    "code": "host_plugin_load_unverified",
                    "action": (
                        f"load {profile['plugin_id']} version {__version__} or merge the "
                        "generated direct MCP configuration"
                    ),
                },
                {
                    "code": "mcp_registration_unverified",
                    "action": "run " + " ".join(profile["verification_command"]),
                },
                {
                    "code": "closed_environment_unverified",
                    "action": (
                        "verify the knowledge_support child receives only the closed "
                        "DeepLaw allowlist"
                    ),
                },
            )
        )
        profiles.append(
            {
                "host": selected,
                "status": (
                    "owner_verification_required"
                    if autonomous_vault_ready
                    else "blocked"
                ),
                "plugin_id": profile["plugin_id"],
                "plugin_version": __version__,
                "load_options": list(profile["load_options"]),
                "host_version_for_current_qualification": profile[
                    "host_version_for_current_qualification"
                ],
                "required_child_environment": ["DEEPLAW_KNOWLEDGE_VAULT"],
                "forbidden_child_environment": [
                    "CODEX_HOME",
                    "DEEPLAW_OPENCODE_DOTENV",
                    "OPENAI_API_KEY",
                    "DEEPSEEK_API_KEY",
                ],
                "verification_command": list(profile["verification_command"]),
                "gaps": gaps,
            }
        )
    return {
        "schema_version": "deeplaw.host-product-readiness/v1",
        "autonomous_vault_ready": autonomous_vault_ready,
        "mcp": {
            "mode": "compact_current_with_internal_compatibility",
            "input_schema": "deeplaw.knowledge-support-input/v7",
            "output_schema": "deeplaw.knowledge-support-output/v6",
            "advertised_operations": ["query", "context", "explain"],
            "compatibility_inputs": ["v1", "v2", "v3", "v4", "v5", "v6"],
            "compatibility_outputs": ["v1", "v2", "v3", "v4", "v5"],
        },
        "hosts": profiles,
    }


def closed_mcp_surface(value: str) -> HostMCPSurface:
    if value not in _SURFACES:
        raise ValueError("DeepLaw MCP surface is invalid")
    return cast(HostMCPSurface, value)


def safe_existing_path(
    value: str | Path,
    *,
    directory: bool,
    label: str,
) -> Path:
    """Resolve one path while rejecting linked or reparse-point ancestors."""

    selected = Path(value).expanduser().absolute()
    try:
        resolved = selected.resolve(strict=True)
    except (OSError, RuntimeError):
        raise RuntimeError(f"selected {label} is unavailable") from None
    if os.path.normcase(str(selected)) != os.path.normcase(str(resolved)):
        raise RuntimeError(f"selected {label} is unsafe")
    for candidate in (selected, *selected.parents):
        try:
            stat_result = candidate.stat(follow_symlinks=False)
        except OSError:
            raise RuntimeError(f"selected {label} is unavailable") from None
        reparse_flag = getattr(stat_result, "st_file_attributes", 0) & 0x400
        if candidate.is_symlink() or reparse_flag:
            raise RuntimeError(f"selected {label} is unsafe")
    if (directory and not selected.is_dir()) or (
        not directory and not selected.is_file()
    ):
        raise RuntimeError(f"selected {label} is unsafe")
    return resolved


def safe_directory_path(
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
        return safe_existing_path(selected, directory=True, label=label)
    if require_existing:
        raise RuntimeError(f"selected {label} is unavailable")
    return selected


def _owner_matches_current_process(vault: Path) -> bool:
    if os.name == "nt":
        return True
    expected_owner = os.geteuid()
    protected = [
        vault,
        vault / "vault.json",
        vault / "sources",
        vault / ".deeplaw",
        vault / ".deeplaw" / "ledger.sqlite3",
        vault / "vault.sqlite3",
    ]
    return all(
        path.stat(follow_symlinks=False).st_uid == expected_owner
        for path in protected
        if path.exists()
    )


def _validate_vault_security(vault: Path) -> None:
    report = knowledge_vault_permission_report(vault)
    if report.get("permissions_verified") is not True or not _owner_matches_current_process(
        vault
    ):
        raise RuntimeError("selected Knowledge Vault owner or permissions are unsafe")


def _validate_empty_discovery_directory(vault: Path) -> None:
    if os.name == "nt":
        return
    stat_result = vault.stat(follow_symlinks=False)
    if stat_result.st_uid != os.geteuid() or stat_result.st_mode & 0o077:
        raise RuntimeError("selected Knowledge Vault owner or permissions are unsafe")


def observed_knowledge_vault_id(vault: Path) -> str:
    """Read the protected manifest identity without opening SQLite sidecars."""

    try:
        return cast(str, _load_manifest(vault)["vault_id"])
    except Exception:
        raise RuntimeError("selected Knowledge Vault is invalid") from None


def _owner_bindings_path(owner_home: str | Path | None) -> Path:
    root = safe_directory_path(
        owner_home or default_home(),
        label="DeepLaw owner home",
        require_existing=False,
    )
    return root / "host" / _BINDINGS_FILENAME


def _empty_bindings() -> dict[str, Any]:
    return {"schema_version": _BINDINGS_SCHEMA, "bindings": {}}


def _load_owner_bindings(owner_home: str | Path | None) -> dict[str, Any]:
    path = _owner_bindings_path(owner_home)
    if not path.exists():
        return _empty_bindings()
    selected = safe_existing_path(path, directory=False, label="owner Host binding")
    if selected.stat().st_size > _MAX_BINDINGS_BYTES:
        raise RuntimeError("owner Host binding is oversized")
    if os.name != "nt":
        stat_result = selected.stat(follow_symlinks=False)
        if stat_result.st_uid != os.geteuid() or stat_result.st_mode & 0o077:
            raise RuntimeError("owner Host binding permissions are unsafe")
    else:
        from .windows_acl import native_windows_path_acl_report

        if native_windows_path_acl_report(selected).get("permissions_verified") is not True:
            raise RuntimeError("owner Host binding permissions are unsafe")
    value = strict_json_loads(selected.read_bytes())
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "bindings"}
        or value.get("schema_version") != _BINDINGS_SCHEMA
        or not isinstance(value.get("bindings"), dict)
        or len(value["bindings"]) > _MAX_BINDINGS
    ):
        raise RuntimeError("owner Host binding is invalid")
    for vault_id, binding in value["bindings"].items():
        if (
            not isinstance(vault_id, str)
            or not _VAULT_ID.fullmatch(vault_id)
            or not isinstance(binding, dict)
            or set(binding) != {"vault_path"}
            or not isinstance(binding.get("vault_path"), str)
            or not binding["vault_path"]
            or "\x00" in binding["vault_path"]
        ):
            raise RuntimeError("owner Host binding is invalid")
    return value


def bind_owner_vault(
    vault_path: str | Path,
    *,
    owner_home: str | Path | None = None,
) -> dict[str, Any]:
    """Bind an opaque vault identity to one private owner-local path."""

    selected = resolve_knowledge_vault(
        vault_path,
        expected_vault_id=None,
        require_existing=True,
        owner_home=owner_home,
        use_owner_binding=False,
    )
    vault_id = observed_knowledge_vault_id(selected)
    bindings = _load_owner_bindings(owner_home)
    entries = dict(bindings["bindings"])
    if vault_id not in entries and len(entries) >= _MAX_BINDINGS:
        raise RuntimeError("owner Host binding capacity is exhausted")
    entries[vault_id] = {"vault_path": str(selected)}
    payload = canonical_json(
        {"schema_version": _BINDINGS_SCHEMA, "bindings": entries}
    ).encode("utf-8")
    if len(payload) > _MAX_BINDINGS_BYTES:
        raise RuntimeError("owner Host binding capacity is exhausted")
    path = _owner_bindings_path(owner_home)
    _atomic_owner_write(path, payload)
    if os.name == "nt":
        from .windows_acl import harden_windows_private_file

        harden_windows_private_file(path)
    return {
        "schema_version": "deeplaw.owner-host-vault-binding-receipt/v1",
        "vault_id": vault_id,
        "owner_local_binding_written": True,
        "path_included": False,
        "binding_store_sha256": sha256_bytes(payload),
    }


def resolve_knowledge_vault(
    vault_path: str | Path | None,
    *,
    expected_vault_id: str | None,
    require_existing: bool,
    owner_home: str | Path | None = None,
    use_owner_binding: bool = True,
) -> Path:
    """Resolve and validate one Knowledge Vault through the sole Host rule set."""

    if expected_vault_id is not None and not _VAULT_ID.fullmatch(expected_vault_id):
        raise ValueError("expected Knowledge Vault identity is invalid")
    configured: str | Path | None = vault_path or os.environ.get(
        "DEEPLAW_KNOWLEDGE_VAULT"
    )
    if configured is None and expected_vault_id is not None and use_owner_binding:
        binding = _load_owner_bindings(owner_home)["bindings"].get(expected_vault_id)
        if binding is not None:
            configured = binding["vault_path"]
    selected = safe_directory_path(
        configured or default_knowledge_vault(),
        label="Knowledge Vault",
        require_existing=require_existing or expected_vault_id is not None,
    )
    if selected.exists():
        if not (selected / "vault.json").is_file():
            if require_existing or expected_vault_id is not None:
                raise RuntimeError("selected Knowledge Vault is invalid")
            _validate_empty_discovery_directory(selected)
            return selected
        _validate_vault_security(selected)
        observed_vault_id = observed_knowledge_vault_id(selected)
        if expected_vault_id is not None and observed_vault_id != expected_vault_id:
            raise RuntimeError("selected Knowledge Vault identity does not match")
    return selected


def build_closed_mcp_argv(
    *,
    surface: str,
    executable: str = "deeplaw",
    expected_vault_id: str | None = None,
    task_binding: Mapping[str, Any] | None = None,
    task_handle: str | None = None,
    grant_id: str | None = None,
) -> list[str]:
    """Build one fixed closed-launcher argv without embedding a local data path."""

    selected_surface = closed_mcp_surface(surface)
    if not isinstance(executable, str) or not executable or "\x00" in executable:
        raise ValueError("DeepLaw executable is invalid")
    normalized_binding = normalize_task_context_binding(task_binding, allow_none=True)
    if normalized_binding is not None and task_handle is not None:
        raise ValueError("task binding and task handle are mutually exclusive")
    if expected_vault_id is not None and not _VAULT_ID.fullmatch(expected_vault_id):
        raise ValueError("expected Knowledge Vault identity is invalid")
    if selected_surface == "knowledge_support":
        if grant_id is not None:
            raise ValueError("knowledge_support does not accept a grant identity")
        argv = [executable, "knowledge", "mcp", "--closed-environment", "--stdio"]
    elif selected_surface == "knowledge_sink":
        if normalized_binding is not None or task_handle is not None:
            raise ValueError("knowledge_sink does not accept a task binding")
        if not isinstance(grant_id, str) or not _GRANT_ID.fullmatch(grant_id):
            raise ValueError("knowledge_sink requires a valid owner grant identity")
        argv = [
            executable,
            "knowledge",
            "sink",
            "mcp",
            "--closed-environment",
            "--grant-id",
            grant_id,
            "--stdio",
        ]
    else:
        if (
            expected_vault_id is not None
            or normalized_binding is not None
            or task_handle is not None
        ):
            raise ValueError("law_support does not accept a Knowledge Vault binding")
        if grant_id is not None:
            raise ValueError("law_support does not accept a grant identity")
        return [executable, "mcp", "--closed-environment", "--stdio"]
    if expected_vault_id is not None:
        argv.extend(("--expected-vault-id", expected_vault_id))
    if normalized_binding is not None:
        argv.extend(("--task-binding", canonical_json(normalized_binding)))
    if task_handle is not None:
        from .task_continuity import decode_task_handle

        decoded = decode_task_handle(
            task_handle,
            expected_vault_id=expected_vault_id,
        )
        del decoded
        argv.extend(("--task-handle", task_handle))
    return argv


__all__ = [
    "bind_owner_vault",
    "build_closed_mcp_argv",
    "closed_mcp_surface",
    "observed_knowledge_vault_id",
    "resolve_knowledge_vault",
    "safe_directory_path",
    "safe_existing_path",
]
