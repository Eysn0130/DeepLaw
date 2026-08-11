from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator

from .knowledge_maintenance import knowledge_doctor
from .util import strict_json_loads

HostName = Literal["codex", "claude-code", "opencode"]


def _contract() -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[2]
        / "contracts/host-connect-plan.v1.schema.json"
    )
    value = strict_json_loads(path.read_bytes())
    if not isinstance(value, dict):
        raise RuntimeError("Host Connect Plan contract is invalid")
    return value


def _server_argv(vault_path: Path) -> list[str]:
    return [
        "deeplaw",
        "knowledge",
        "mcp",
        "--stdio",
        "--vault",
        str(vault_path),
    ]


def build_host_connect_plan(
    *, host: str, vault_path: str | Path
) -> dict[str, Any]:
    """Build one read-only MCP configuration without changing Host state."""

    if host not in {"codex", "claude-code", "opencode"}:
        raise ValueError("host must be codex, claude-code, or opencode")
    selected_host = cast(HostName, host)
    selected_vault = Path(vault_path).expanduser().absolute()
    doctor = knowledge_doctor(selected_vault)
    autonomous = doctor.get("checks", {}).get("autonomous_core", {})
    preflight = {
        "vault_ready": doctor.get("ready") is True,
        "canonical_valid": doctor.get("canonical_valid") is True,
        "autonomous_core_installed": autonomous.get("installed") is True,
    }
    if not all(preflight.values()):
        raise RuntimeError("Host connect requires a ready autonomous Knowledge vault")

    argv = _server_argv(selected_vault)
    if selected_host == "opencode":
        configuration: dict[str, Any] = {
            "mcp": {
                "deeplaw_knowledge": {
                    "type": "local",
                    "command": argv,
                    "enabled": True,
                    "timeout": 5000,
                }
            },
            "permission": {
                "deeplaw_knowledge_*": "deny",
                "deeplaw_knowledge_knowledge_support": "allow",
            },
        }
    else:
        configuration = {
            "mcpServers": {
                "deeplaw-knowledge": {
                    "command": argv[0],
                    "args": argv[1:],
                }
            }
        }
    plan = {
        "schema_version": "deeplaw.host-connect-plan/v1",
        "host": selected_host,
        "server_leaf": "knowledge_support",
        "read_only": True,
        "configuration": configuration,
        "preflight": preflight,
        "merge_required": True,
        "authentication_managed": False,
        "host_runtime_managed": False,
        "install_performed": False,
        "write_performed": False,
    }
    schema = _contract()
    Draft202012Validator.check_schema(schema)
    error = next(Draft202012Validator(schema).iter_errors(plan), None)
    if error is not None:
        raise RuntimeError(f"Host Connect Plan is invalid: {error.message}")
    return plan
