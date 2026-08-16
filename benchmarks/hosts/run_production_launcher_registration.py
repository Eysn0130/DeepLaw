"""No-model registration and tools/list check for the production Host launcher.

The check never executes a model/provider turn.  Codex registration uses an
isolated official CLI config seam; OpenCode runs only when the exact frozen
binary is already present.  The direct MCP handshake verifies the same path-free
closed launcher command that those Host configs register.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from deeplaw.host_runtime import bind_owner_vault, build_closed_mcp_argv
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_bytes, strict_json_loads

SCHEMA_VERSION = "deeplaw.production-launcher-registration/v1"
FROZEN_OPENCODE_VERSION = "1.18.16"
_MAX_HOST_OUTPUT = 1024 * 1024
_ABSOLUTE_PATH = re.compile(
    r"(?:/(?:Users|home|private|tmp|var)(?:[/\\][^\s,;:()<>]+)*|[A-Za-z]:[\\/])"
)
_BLOCKED_CHILD_NAMES = frozenset(
    {
        "CODEX_HOME",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "DOTENV_CONFIG_PATH",
    }
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    argv: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if len(completed.stdout.encode("utf-8")) > _MAX_HOST_OUTPUT or len(
        completed.stderr.encode("utf-8")
    ) > _MAX_HOST_OUTPUT:
        raise RuntimeError("no-model Host command output exceeded its bound")
    return completed


def _portable_environment(root: Path, *, owner_home: Path) -> dict[str, str]:
    home = root / "host-home"
    temporary = root / "tmp"
    for path in (home, temporary):
        path.mkdir(mode=0o700)
    environment = {
        "PATH": os.defpath,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_STATE_HOME": str(home / "state"),
        "TMPDIR": str(temporary),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "DEEPLAW_HOME": str(owner_home),
        "NO_COLOR": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL"):
        if value := os.environ.get(name):
            environment[name] = value
    if _BLOCKED_CHILD_NAMES.intersection(environment):
        raise RuntimeError("no-model Host environment contains a blocked name")
    return environment


def _structured_result(result: Any) -> dict[str, Any]:
    if result.isError is True or not isinstance(result.structuredContent, dict):
        raise RuntimeError("production launcher MCP call failed")
    return result.structuredContent


async def _mcp_registration(
    *,
    executable: Path,
    argv: list[str],
    workspace: Path,
    environment: dict[str, str],
    expected_vault_id: str,
) -> dict[str, Any]:
    parameters = StdioServerParameters(
        command=str(executable),
        args=argv,
        cwd=workspace,
        env=environment,
    )
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        if len(tools.tools) != 1 or tools.tools[0].name != "knowledge_support":
            raise RuntimeError("production launcher exposed an unexpected tool inventory")
        input_schema = tools.tools[0].inputSchema
        rendered_input_schema = canonical_json(input_schema)
        advertised_operations = {
            branch.get("$ref", "").rsplit("/", maxsplit=1)[-1]
            for branch in input_schema.get("oneOf", [])
            if isinstance(branch, dict)
        }
        if (
            not isinstance(input_schema, dict)
            or input_schema.get("title") != "DeepLaw Knowledge Support Provider Input v7"
            or not isinstance(input_schema.get("oneOf"), list)
            or advertised_operations != {"query", "context", "explain"}
            or '"additionalProperties":false' not in rendered_input_schema
            or '"context"' not in rendered_input_schema
        ):
            raise RuntimeError("production launcher exposed an unexpected tool schema")
        response = _structured_result(
            await session.call_tool(
                "knowledge_support",
                {
                    "operation": "context",
                    "task": "Verify the no-model production launcher registration seam.",
                    "purpose": "verify",
                    "confirm_no_case_data": True,
                },
            )
        )
    result = response.get("result")
    if (
        response.get("schema_version") != "deeplaw.knowledge-support-output/v6"
        or not isinstance(result, dict)
        or result.get("schema_version") != "deeplaw.provider-knowledge-capsule/v2"
        or result.get("delivery", {}).get("write_performed") is not False
        or result.get("delivery", {}).get("provider_content_bytes", 65_537) > 65_536
    ):
        raise RuntimeError("production launcher returned an invalid bounded provider capsule")
    return {
        "status": "executed",
        "initialize": "passed",
        "tools_list": "passed",
        "tool_names": ["knowledge_support"],
        "input_schema_sha256": sha256_bytes(canonical_json(input_schema).encode("utf-8")),
        "provider_schema": "deeplaw.provider-knowledge-capsule/v2",
        "provider_hard_limit_bytes": 65_536,
        "vault_identity_verified": True,
        "expected_vault_id": expected_vault_id,
        "write_performed": False,
        "model_turn_executed": False,
    }


def _codex_registration(
    *,
    command: str,
    root: Path,
    executable: Path,
    argv: list[str],
    environment: dict[str, str],
) -> dict[str, Any]:
    binary = shutil.which(command)
    if binary is None and Path(command).is_file():
        binary = str(Path(command).resolve(strict=True))
    if binary is None:
        return {"status": "not_executed", "reason": "codex_binary_unavailable"}
    environment = {
        **environment,
        "PATH": os.pathsep.join((str(Path(binary).parent), os.defpath)),
    }
    version = _run([binary, "--version"], cwd=root, environment=environment)
    if version.returncode != 0 or not version.stdout.strip().startswith("codex-cli "):
        return {"status": "not_executed", "reason": "codex_version_unverified"}
    add = _run(
        [binary, "mcp", "add", "deeplaw-knowledge", "--", str(executable), *argv],
        cwd=root,
        environment=environment,
    )
    listed = _run(
        [binary, "mcp", "list", "--json"],
        cwd=root,
        environment=environment,
    )
    detail = _run(
        [binary, "mcp", "get", "deeplaw-knowledge", "--json"],
        cwd=root,
        environment=environment,
    )
    try:
        listing = strict_json_loads(listed.stdout)
        registered = strict_json_loads(detail.stdout)
    except (TypeError, ValueError):
        listing = registered = None
    if (
        add.returncode != 0
        or listed.returncode != 0
        or detail.returncode != 0
        or not isinstance(listing, list)
        or not isinstance(registered, dict)
        or not any(
            isinstance(item, dict) and item.get("name") == "deeplaw-knowledge"
            for item in listing
        )
    ):
        raise RuntimeError("Codex official MCP registration seam failed")
    return {
        "status": "executed",
        "version": version.stdout.strip(),
        "official_config_parse": "passed",
        "mcp_registration": "passed",
        "mcp_list": "passed",
        "model_turn_executed": False,
        "existing_login_read": False,
    }


def _opencode_config(argv: list[str], executable: Path) -> dict[str, Any]:
    return {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",
        "autoupdate": False,
        "plugin": [],
        "permission": {
            "*": "deny",
            "deeplaw_knowledge_knowledge_support": "allow",
        },
        "mcp": {
            "deeplaw_knowledge": {
                "type": "local",
                "command": [str(executable), *argv],
                "enabled": True,
                "timeout": 5000,
            }
        },
    }


def _opencode_registration(
    *,
    command: str,
    root: Path,
    executable: Path,
    argv: list[str],
    environment: dict[str, str],
    expected_version: str,
) -> dict[str, Any]:
    binary = shutil.which(command)
    if binary is None and Path(command).is_file():
        binary = str(Path(command).resolve(strict=True))
    if binary is None:
        return {"status": "not_executed", "reason": "exact_opencode_binary_unavailable"}
    environment = {
        **environment,
        "PATH": os.pathsep.join((str(Path(binary).parent), os.defpath)),
    }
    version = _run([binary, "--version"], cwd=root, environment=environment)
    if version.returncode != 0 or version.stdout.strip() != expected_version:
        return {"status": "not_executed", "reason": "exact_opencode_version_unavailable"}
    config = _opencode_config(argv, executable)
    config_path = root / "opencode.json"
    config_path.write_text(canonical_json(config) + "\n", encoding="utf-8")
    host_environment = {
        **environment,
        "OPENCODE_CONFIG": str(config_path),
        "OPENCODE_CONFIG_DIR": str(root / "opencode-config"),
    }
    resolved = _run(
        [binary, "--pure", "debug", "config"],
        cwd=root,
        environment=host_environment,
    )
    listed = _run(
        [binary, "--pure", "mcp", "list"],
        cwd=root,
        environment=host_environment,
    )
    try:
        resolved_config = strict_json_loads(resolved.stdout)
    except (TypeError, ValueError):
        resolved_config = None
    if (
        resolved.returncode != 0
        or listed.returncode != 0
        or not isinstance(resolved_config, dict)
        or set(resolved_config.get("mcp", {})) != {"deeplaw_knowledge"}
    ):
        raise RuntimeError("OpenCode exact no-model MCP registration seam failed")
    return {
        "status": "executed",
        "version": expected_version,
        "official_config_parse": "passed",
        "mcp_registration": "passed",
        "mcp_list": "passed",
        "model_turn_executed": False,
        "provider_environment_forwarded": False,
    }


def run_registration(
    *,
    deeplaw_executable: Path,
    codex_command: str = "codex",
    opencode_command: str = "opencode",
    expected_opencode_version: str = FROZEN_OPENCODE_VERSION,
) -> dict[str, Any]:
    executable = deeplaw_executable.expanduser().resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="deeplaw-registration-") as temporary:
        root = Path(temporary).resolve(strict=True)
        workspace = root / "workspace"
        owner_home = root / "owner-home"
        vault = root / "vault"
        workspace.mkdir(mode=0o700)
        initialize_knowledge_vault(vault, name="registration", scope="project")
        initialize_autonomous_core(vault)
        with AutonomousKnowledgeStore(vault, read_only=True) as store:
            vault_id = store.vault_id
            audit_before = store.audit_head
        binding = bind_owner_vault(vault, owner_home=owner_home)
        if binding["vault_id"] != vault_id:
            raise RuntimeError("owner Host binding changed vault identity")
        full_argv = build_closed_mcp_argv(
            surface="knowledge_support",
            executable=str(executable),
            expected_vault_id=vault_id,
        )
        argv = full_argv[1:]
        rendered_config = canonical_json({"command": full_argv[0], "args": argv})
        if str(vault) in rendered_config or "--vault" in rendered_config:
            raise RuntimeError("production launcher registration contains a vault path")
        environment = _portable_environment(root, owner_home=owner_home)
        direct = asyncio.run(
            _mcp_registration(
                executable=executable,
                argv=argv,
                workspace=workspace,
                environment=environment,
                expected_vault_id=vault_id,
            )
        )
        with AutonomousKnowledgeStore(vault, read_only=True) as store:
            if store.audit_head != audit_before:
                raise RuntimeError("no-model registration changed the Knowledge Ledger")
        codex = _codex_registration(
            command=codex_command,
            root=workspace,
            executable=executable,
            argv=argv,
            environment=environment,
        )
        opencode = _opencode_registration(
            command=opencode_command,
            root=workspace,
            executable=executable,
            argv=argv,
            environment=environment,
            expected_version=expected_opencode_version,
        )
    report = {
        "schema_version": SCHEMA_VERSION,
        "package_version": "0.12.0",
        "release_ready": False,
        "claim_eligible": False,
        "production_launcher": direct,
        "codex": codex,
        "opencode": opencode,
        "vault_path_absent_from_configuration": True,
        "knowledge_support_read_only": True,
        "knowledge_sink_registered": False,
        "law_support_registered": False,
        "blocked_environment_names_absent": True,
        "model_turn_executed": False,
        "runtime_executable_sha256": _sha256_file(executable),
    }
    rendered = canonical_json(report)
    if _ABSOLUTE_PATH.search(rendered):
        raise RuntimeError("no-model registration report contains an absolute path")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deeplaw-executable", type=Path, required=True)
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--opencode-command", default="opencode")
    parser.add_argument("--opencode-version", default=FROZEN_OPENCODE_VERSION)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_registration(
        deeplaw_executable=args.deeplaw_executable,
        codex_command=args.codex_command,
        opencode_command=args.opencode_command,
        expected_opencode_version=args.opencode_version,
    )
    output = args.output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError("registration output must be new")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_json(report) + "\n", encoding="utf-8")
    print(canonical_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
