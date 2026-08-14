from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from benchmarks.release.evidence import (
    canonical_json,
    environment_manifest,
    repository_binding,
    sha256_bytes,
    sha256_file,
    write_report,
)

SCHEMA_VERSION = "deeplaw.no-model-host-acceptance/v1"
BASELINE_COMMIT = "0b7d21bfaadaa2143381b1c585f34ab4e3322999"
BASELINE_VERSION = "0.5.0"
VERSION = tomllib.loads(
    (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["version"]
PLUGIN_IDS = ("deeplaw@deeplaw", "deeplaw-knowledge-os@deeplaw")
LOCAL_GIT_URL = "https://local.invalid/deeplaw.git"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class HostAcceptanceError(RuntimeError):
    pass


def _json_output(value: bytes, *, operation: str) -> Any:
    try:
        return json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HostAcceptanceError(f"{operation} did not return JSON") from error


class CommandSession:
    def __init__(
        self,
        *,
        host: str,
        executable: Path,
        environment: dict[str, str],
        cwd: Path,
    ) -> None:
        self.host = host
        self.executable = executable
        self.environment = environment
        self.cwd = cwd
        self.evidence: list[dict[str, Any]] = []

    def run(
        self,
        operation: str,
        *arguments: str,
        cwd: Path | None = None,
        timeout: int = 180,
    ) -> bytes:
        process = subprocess.run(
            [str(self.executable), *arguments],
            cwd=cwd or self.cwd,
            env=self.environment,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
        self.evidence.append(
            {
                "sequence": len(self.evidence) + 1,
                "operation": operation,
                "returncode": process.returncode,
                "stdout_sha256": sha256_bytes(process.stdout),
                "stdout_bytes": len(process.stdout),
                "stderr_sha256": sha256_bytes(process.stderr),
                "stderr_bytes": len(process.stderr),
            }
        )
        if process.returncode != 0:
            stderr = process.stderr.decode("utf-8", errors="replace")[-4000:]
            stdout = process.stdout.decode("utf-8", errors="replace")[-2000:]
            raise HostAcceptanceError(f"{self.host} {operation} failed: {stdout}{stderr}")
        return process.stdout

    def json(self, operation: str, *arguments: str, cwd: Path | None = None) -> Any:
        return _json_output(self.run(operation, *arguments, cwd=cwd), operation=operation)

    def text(self, operation: str, *arguments: str, cwd: Path | None = None) -> str:
        try:
            return self.run(operation, *arguments, cwd=cwd).decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise HostAcceptanceError(f"{self.host} {operation} returned non-UTF-8") from error


def _resolve_executable(value: str) -> Path:
    found = shutil.which(value) if not Path(value).is_absolute() else value
    if found is None:
        raise HostAcceptanceError(f"host executable is unavailable: {value}")
    path = Path(found).resolve(strict=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise HostAcceptanceError(f"host executable is not executable: {value}")
    return path


def _base_environment(root: Path) -> dict[str, str]:
    directories = {
        "HOME": root / "home",
        "USERPROFILE": root / "home",
        "XDG_CONFIG_HOME": root / "xdg-config",
        "XDG_DATA_HOME": root / "xdg-data",
        "XDG_CACHE_HOME": root / "xdg-cache",
        "TMPDIR": root / "tmp",
        "TMP": root / "tmp",
        "TEMP": root / "tmp",
    }
    for path in set(directories.values()):
        path.mkdir(parents=True, mode=0o700)
    environment = {key: str(value) for key, value in directories.items()}
    environment.update(
        {
            "PATH": os.environ.get("PATH", os.defpath),
            "NO_COLOR": "1",
            "CI": "true",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
        if name in os.environ:
            environment[name] = os.environ[name]
    return environment


def _git(repository: Path, *arguments: str, environment: dict[str, str] | None = None) -> bytes:
    process = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        timeout=120,
    )
    if process.returncode != 0:
        raise HostAcceptanceError(
            "git host-fixture operation failed: "
            + process.stderr.decode("utf-8", errors="replace")[-4000:]
        )
    return process.stdout


def _prepare_git_marketplace(
    repository: Path,
    *,
    root: Path,
    current_commit: str,
) -> tuple[Path, Path]:
    mirror = root / "market.git"
    _git(repository, "clone", "--bare", str(repository), str(mirror))
    _git(
        repository,
        f"--git-dir={mirror}",
        "update-ref",
        "refs/heads/release-lifecycle",
        BASELINE_COMMIT,
    )
    _git(repository, f"--git-dir={mirror}", "symbolic-ref", "HEAD", "refs/heads/release-lifecycle")
    gitconfig = root / "gitconfig"
    _git(
        repository,
        "config",
        "--file",
        str(gitconfig),
        f"url.file://{mirror}.insteadOf",
        LOCAL_GIT_URL,
    )
    if current_commit == BASELINE_COMMIT:
        raise HostAcceptanceError("current candidate cannot equal the upgrade baseline")
    return mirror, gitconfig


def _advance_marketplace(repository: Path, mirror: Path, current_commit: str) -> None:
    _git(
        repository,
        f"--git-dir={mirror}",
        "update-ref",
        "refs/heads/release-lifecycle",
        current_commit,
    )


def _reset_marketplace(repository: Path, mirror: Path) -> None:
    _git(
        repository,
        f"--git-dir={mirror}",
        "update-ref",
        "refs/heads/release-lifecycle",
        BASELINE_COMMIT,
    )


def _installed_state(payload: Any, *, claude: bool = False) -> dict[str, dict[str, Any]]:
    records = payload if claude else payload.get("installed") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise HostAcceptanceError("host plugin list has an invalid shape")
    state: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise HostAcceptanceError("host plugin list contains a non-object")
        plugin_id = record.get("id" if claude else "pluginId")
        if plugin_id in state or not isinstance(plugin_id, str):
            raise HostAcceptanceError("host plugin list contains an invalid identity")
        state[plugin_id] = record
    return state


def _assert_plugins(
    state: dict[str, dict[str, Any]],
    *,
    expected: set[str],
    version: str,
    claude: bool = False,
    enabled: dict[str, bool] | None = None,
) -> None:
    if set(state) != expected:
        raise HostAcceptanceError(f"plugin isolation differs: {set(state)} != {expected}")
    for plugin_id, record in state.items():
        observed = str(record.get("version", ""))
        if not (observed == version or observed.startswith(version + "+")):
            raise HostAcceptanceError(f"{plugin_id} has version {observed}, expected {version}")
        expected_enabled = True if enabled is None else enabled[plugin_id]
        if record.get("enabled") is not expected_enabled:
            raise HostAcceptanceError(f"{plugin_id} enabled state is invalid")
        if claude and record.get("scope") != "user":
            raise HostAcceptanceError(f"{plugin_id} is not isolated to the test user scope")


def _set_codex_enabled(config: Path, plugin_id: str, enabled: bool) -> None:
    marker = f'[plugins."{plugin_id}"]\nenabled = '
    content = config.read_text(encoding="utf-8")
    start = content.find(marker)
    if start < 0:
        raise HostAcceptanceError(f"Codex config has no plugin state for {plugin_id}")
    value_start = start + len(marker)
    value_end = content.find("\n", value_start)
    current = content[value_start:value_end]
    if current not in {"true", "false"}:
        raise HostAcceptanceError("Codex plugin enabled state is not canonical")
    replacement = "true" if enabled else "false"
    config.write_text(
        content[:value_start] + replacement + content[value_end:],
        encoding="utf-8",
    )


def _codex_acceptance(
    repository: Path,
    *,
    executable: Path,
    root: Path,
    mirror: Path,
    gitconfig: Path,
    current_commit: str,
    expected_version: str,
) -> dict[str, Any]:
    _reset_marketplace(repository, mirror)
    environment = _base_environment(root)
    codex_home = root / "codex"
    codex_home.mkdir(mode=0o700)
    environment.update(
        {
            "CODEX_HOME": str(codex_home),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_ALLOW_PROTOCOL": "file:https",
        }
    )
    session = CommandSession(
        host="codex", executable=executable, environment=environment, cwd=repository
    )
    cli_version = session.text("version", "--version")
    if expected_version not in cli_version:
        raise HostAcceptanceError(f"Codex CLI version differs: {cli_version}")
    session.json(
        "marketplace_add_baseline",
        "plugin",
        "marketplace",
        "add",
        LOCAL_GIT_URL,
        "--ref",
        "release-lifecycle",
        "--json",
    )
    available = session.json("discover_baseline", "plugin", "list", "--available", "--json")
    available_ids = {
        item.get("pluginId")
        for item in available.get("available", [])
        if isinstance(item, dict)
    }
    if available_ids != set(PLUGIN_IDS):
        raise HostAcceptanceError("Codex marketplace discovery is incomplete")
    for plugin_id in PLUGIN_IDS:
        session.json("install_" + plugin_id.split("@")[0], "plugin", "add", plugin_id, "--json")
    baseline = _installed_state(session.json("list_baseline", "plugin", "list", "--json"))
    _assert_plugins(baseline, expected=set(PLUGIN_IDS), version=BASELINE_VERSION)

    _advance_marketplace(repository, mirror, current_commit)
    upgraded = session.json(
        "marketplace_upgrade", "plugin", "marketplace", "upgrade", "deeplaw", "--json"
    )
    if upgraded.get("errors") != [] or upgraded.get("selectedMarketplaces") != ["deeplaw"]:
        raise HostAcceptanceError("Codex marketplace upgrade did not complete cleanly")
    current = _installed_state(session.json("list_upgraded", "plugin", "list", "--json"))
    _assert_plugins(current, expected=set(PLUGIN_IDS), version=VERSION)

    config = codex_home / "config.toml"
    _set_codex_enabled(config, PLUGIN_IDS[0], False)
    disabled = _installed_state(session.json("list_legal_disabled", "plugin", "list", "--json"))
    _assert_plugins(
        disabled,
        expected=set(PLUGIN_IDS),
        version=VERSION,
        enabled={PLUGIN_IDS[0]: False, PLUGIN_IDS[1]: True},
    )
    _set_codex_enabled(config, PLUGIN_IDS[0], True)
    enabled = _installed_state(session.json("list_legal_enabled", "plugin", "list", "--json"))
    _assert_plugins(enabled, expected=set(PLUGIN_IDS), version=VERSION)

    session.json("remove_knowledge", "plugin", "remove", PLUGIN_IDS[1], "--json")
    legal_only = _installed_state(session.json("list_legal_only", "plugin", "list", "--json"))
    _assert_plugins(legal_only, expected={PLUGIN_IDS[0]}, version=VERSION)
    session.json("reinstall_knowledge", "plugin", "add", PLUGIN_IDS[1], "--json")
    session.json("remove_legal", "plugin", "remove", PLUGIN_IDS[0], "--json")
    knowledge_only = _installed_state(
        session.json("list_knowledge_only", "plugin", "list", "--json")
    )
    _assert_plugins(knowledge_only, expected={PLUGIN_IDS[1]}, version=VERSION)
    session.json("reinstall_legal", "plugin", "add", PLUGIN_IDS[0], "--json")
    session.json("remove_knowledge_final", "plugin", "remove", PLUGIN_IDS[1], "--json")
    session.json("remove_legal_final", "plugin", "remove", PLUGIN_IDS[0], "--json")
    final = _installed_state(session.json("list_empty", "plugin", "list", "--json"))
    _assert_plugins(final, expected=set(), version=VERSION)
    session.json("marketplace_remove", "plugin", "marketplace", "remove", "deeplaw", "--json")
    return {
        "cli_version": cli_version,
        "executable_sha256": sha256_file(executable),
        "lifecycle": {
            "manifest_discovery": True,
            "install": True,
            "disable_enable": True,
            "upgrade": True,
            "remove": True,
            "dual_product_isolation": True,
            "final_state_empty": True,
        },
        "upgrade": {"from": BASELINE_VERSION, "to": VERSION, "transport": "local_git_rewrite"},
        "command_evidence": session.evidence,
        "passed": True,
    }


def _claude_acceptance(
    repository: Path,
    *,
    executable: Path,
    root: Path,
    mirror: Path,
    gitconfig: Path,
    current_commit: str,
    expected_version: str,
) -> dict[str, Any]:
    _reset_marketplace(repository, mirror)
    environment = _base_environment(root)
    claude_config = root / "claude"
    claude_config.mkdir(mode=0o700)
    environment.update(
        {
            "CLAUDE_CONFIG_DIR": str(claude_config),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_ALLOW_PROTOCOL": "file:https",
        }
    )
    session = CommandSession(
        host="claude", executable=executable, environment=environment, cwd=repository
    )
    cli_version = session.text("version", "--version")
    if expected_version not in cli_version:
        raise HostAcceptanceError(f"Claude Code version differs: {cli_version}")
    session.text("validate_marketplace", "plugin", "validate", "--strict", str(repository))
    for name in ("deeplaw", "deeplaw-knowledge-os"):
        session.text(
            "validate_" + name,
            "plugin",
            "validate",
            "--strict",
            str(repository / "plugins" / name),
        )
    session.text(
        "marketplace_add_baseline",
        "plugin",
        "marketplace",
        "add",
        LOCAL_GIT_URL,
        "--scope",
        "user",
    )
    available = session.json(
        "discover_baseline", "plugin", "list", "--available", "--json"
    )
    available_ids = {
        item.get("pluginId")
        for item in available.get("available", [])
        if isinstance(item, dict)
    }
    if available_ids != set(PLUGIN_IDS):
        raise HostAcceptanceError("Claude Code marketplace discovery is incomplete")
    for plugin_id in PLUGIN_IDS:
        session.text(
            "install_" + plugin_id.split("@")[0],
            "plugin",
            "install",
            plugin_id,
            "--scope",
            "user",
        )
    baseline = _installed_state(
        session.json("list_baseline", "plugin", "list", "--json"), claude=True
    )
    _assert_plugins(
        baseline, expected=set(PLUGIN_IDS), version=BASELINE_VERSION, claude=True
    )

    _advance_marketplace(repository, mirror, current_commit)
    session.text("marketplace_update", "plugin", "marketplace", "update", "deeplaw")
    for plugin_id in PLUGIN_IDS:
        session.text(
            "update_" + plugin_id.split("@")[0],
            "plugin",
            "update",
            plugin_id,
            "--scope",
            "user",
        )
    current = _installed_state(
        session.json("list_upgraded", "plugin", "list", "--json"), claude=True
    )
    _assert_plugins(current, expected=set(PLUGIN_IDS), version=VERSION, claude=True)

    session.text("disable_legal", "plugin", "disable", PLUGIN_IDS[0], "--scope", "user")
    disabled = _installed_state(
        session.json("list_legal_disabled", "plugin", "list", "--json"), claude=True
    )
    _assert_plugins(
        disabled,
        expected=set(PLUGIN_IDS),
        version=VERSION,
        claude=True,
        enabled={PLUGIN_IDS[0]: False, PLUGIN_IDS[1]: True},
    )
    session.text("enable_legal", "plugin", "enable", PLUGIN_IDS[0], "--scope", "user")
    enabled = _installed_state(
        session.json("list_legal_enabled", "plugin", "list", "--json"), claude=True
    )
    _assert_plugins(enabled, expected=set(PLUGIN_IDS), version=VERSION, claude=True)

    session.text("remove_knowledge", "plugin", "uninstall", PLUGIN_IDS[1], "--scope", "user")
    legal_only = _installed_state(
        session.json("list_legal_only", "plugin", "list", "--json"), claude=True
    )
    _assert_plugins(legal_only, expected={PLUGIN_IDS[0]}, version=VERSION, claude=True)
    session.text(
        "reinstall_knowledge", "plugin", "install", PLUGIN_IDS[1], "--scope", "user"
    )
    session.text("remove_legal", "plugin", "uninstall", PLUGIN_IDS[0], "--scope", "user")
    knowledge_only = _installed_state(
        session.json("list_knowledge_only", "plugin", "list", "--json"), claude=True
    )
    _assert_plugins(knowledge_only, expected={PLUGIN_IDS[1]}, version=VERSION, claude=True)
    session.text("reinstall_legal", "plugin", "install", PLUGIN_IDS[0], "--scope", "user")
    session.text(
        "remove_knowledge_final", "plugin", "uninstall", PLUGIN_IDS[1], "--scope", "user"
    )
    session.text(
        "remove_legal_final", "plugin", "uninstall", PLUGIN_IDS[0], "--scope", "user"
    )
    final = _installed_state(session.json("list_empty", "plugin", "list", "--json"), claude=True)
    _assert_plugins(final, expected=set(), version=VERSION, claude=True)
    session.text("marketplace_remove", "plugin", "marketplace", "remove", "deeplaw")
    return {
        "cli_version": cli_version,
        "executable_sha256": sha256_file(executable),
        "lifecycle": {
            "strict_manifest_validation": True,
            "manifest_discovery": True,
            "install": True,
            "disable_enable": True,
            "upgrade": True,
            "remove": True,
            "dual_product_isolation": True,
            "final_state_empty": True,
        },
        "upgrade": {"from": BASELINE_VERSION, "to": VERSION, "transport": "local_git_rewrite"},
        "command_evidence": session.evidence,
        "passed": True,
    }


def _opencode_source(repository: Path) -> dict[str, dict[str, Any]]:
    manifest = json.loads(
        (repository / "adapters/opencode/manifest.json").read_text(encoding="utf-8")
    )
    if (
        manifest.get("schema_version") != "deeplaw.opencode-adapter-manifest/v1"
        or manifest.get("version") != VERSION
        or manifest.get("host") != "opencode"
        or manifest.get("model_or_api_call_required_for_lifecycle") is not False
    ):
        raise HostAcceptanceError("OpenCode adapter manifest is invalid")
    legal = json.loads(
        (repository / "adapters/opencode/opencode.jsonc").read_text(encoding="utf-8")
    )
    knowledge = json.loads(
        (repository / "adapters/opencode/knowledge-os.jsonc").read_text(encoding="utf-8")
    )
    return {"legal": legal, "knowledge": knowledge}


def _opencode_config(
    source: dict[str, dict[str, Any]],
    *,
    products: set[str],
    deeplaw_executable: Path,
    disabled: set[str] | None = None,
) -> dict[str, Any]:
    disabled = disabled or set()
    result: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {},
        "permission": {},
    }
    for product in sorted(products):
        config = json.loads(canonical_json(source[product]))
        for name, server in config["mcp"].items():
            server["command"][0] = str(deeplaw_executable)
            server["enabled"] = product not in disabled
            result["mcp"][name] = server
        for key, value in config.get("permission", {}).items():
            if isinstance(value, dict):
                result["permission"].setdefault(key, {}).update(value)
            else:
                result["permission"][key] = value
    return result


def _install_opencode_projection(
    repository: Path,
    project: Path,
    *,
    products: set[str],
) -> None:
    root = project / ".opencode"
    if root.exists():
        shutil.rmtree(root)
    (root / "agents").mkdir(parents=True)
    (root / "skills").mkdir(parents=True)
    mappings = {
        "legal": (
            repository / "adapters/opencode/agents/deeplaw.md",
            repository / "plugins/deeplaw/skills/research-chinese-law",
            "deeplaw.md",
            "research-chinese-law",
        ),
        "knowledge": (
            repository / "adapters/opencode/agents/deeplaw-knowledge.md",
            repository / "plugins/deeplaw-knowledge-os/skills/use-knowledge-assets",
            "deeplaw-knowledge.md",
            "use-knowledge-assets",
        ),
    }
    for product in sorted(products):
        agent, skill, agent_name, skill_name = mappings[product]
        shutil.copy2(agent, root / "agents" / agent_name)
        shutil.copytree(skill, root / "skills" / skill_name)


def _write_opencode_config(project: Path, config: dict[str, Any]) -> None:
    (project / "opencode.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _assert_opencode_config(payload: Any, *, servers: set[str], enabled: dict[str, bool]) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("mcp"), dict):
        raise HostAcceptanceError("OpenCode resolved config is invalid")
    observed = payload["mcp"]
    if set(observed) != servers:
        raise HostAcceptanceError(f"OpenCode MCP isolation differs: {set(observed)} != {servers}")
    for name in servers:
        if observed[name].get("enabled") is not enabled[name]:
            raise HostAcceptanceError(f"OpenCode MCP enabled state is invalid for {name}")


def _assert_mcp_list(text: str, *, connected: set[str], disabled: set[str] | None = None) -> None:
    normalized = ANSI_ESCAPE.sub("", text)
    disabled = disabled or set()
    for name in connected:
        if name not in normalized or "connected" not in normalized:
            raise HostAcceptanceError(f"OpenCode did not connect MCP server {name}")
    for name in disabled:
        if name not in normalized or "disabled" not in normalized:
            raise HostAcceptanceError(f"OpenCode did not report disabled MCP server {name}")


def _opencode_acceptance(
    repository: Path,
    *,
    executable: Path,
    deeplaw_executable: Path,
    root: Path,
    expected_version: str,
) -> dict[str, Any]:
    environment = _base_environment(root)
    config_dir = root / "opencode-config"
    config_dir.mkdir(mode=0o700)
    environment["OPENCODE_CONFIG_DIR"] = str(config_dir)
    project = root / "project"
    project.mkdir(mode=0o700)
    session = CommandSession(
        host="opencode", executable=executable, environment=environment, cwd=project
    )
    cli_version = session.text("version", "--version")
    if cli_version != expected_version:
        raise HostAcceptanceError(f"OpenCode version differs: {cli_version}")
    source = _opencode_source(repository)

    _install_opencode_projection(repository, project, products={"legal", "knowledge"})
    both = _opencode_config(
        source,
        products={"legal", "knowledge"},
        deeplaw_executable=deeplaw_executable,
    )
    _write_opencode_config(project, both)
    resolved = session.json("discover_both_config", "debug", "config")
    _assert_opencode_config(
        resolved,
        servers={"deeplaw", "deeplaw_knowledge"},
        enabled={"deeplaw": True, "deeplaw_knowledge": True},
    )
    _assert_mcp_list(
        session.text("discover_both_mcp", "mcp", "list"),
        connected={"deeplaw", "deeplaw_knowledge"},
    )
    agents = session.text("discover_agents", "agent", "list")
    skills = session.text("discover_skills", "debug", "skill")
    for marker in ("deeplaw", "deeplaw-knowledge"):
        if marker not in agents:
            raise HostAcceptanceError(f"OpenCode agent discovery missed {marker}")
    for marker in ("research-chinese-law", "use-knowledge-assets"):
        if marker not in skills:
            raise HostAcceptanceError(f"OpenCode skill discovery missed {marker}")

    disabled_config = _opencode_config(
        source,
        products={"legal", "knowledge"},
        deeplaw_executable=deeplaw_executable,
        disabled={"legal"},
    )
    _write_opencode_config(project, disabled_config)
    disabled = session.json("validate_legal_disabled", "debug", "config")
    _assert_opencode_config(
        disabled,
        servers={"deeplaw", "deeplaw_knowledge"},
        enabled={"deeplaw": False, "deeplaw_knowledge": True},
    )
    _assert_mcp_list(
        session.text("list_legal_disabled", "mcp", "list"),
        connected={"deeplaw_knowledge"},
        disabled={"deeplaw"},
    )
    _write_opencode_config(project, both)
    enabled = session.json("validate_legal_enabled", "debug", "config")
    _assert_opencode_config(
        enabled,
        servers={"deeplaw", "deeplaw_knowledge"},
        enabled={"deeplaw": True, "deeplaw_knowledge": True},
    )

    _install_opencode_projection(repository, project, products={"legal"})
    legal = _opencode_config(source, products={"legal"}, deeplaw_executable=deeplaw_executable)
    _write_opencode_config(project, legal)
    legal_only = session.json("validate_legal_only", "debug", "config")
    _assert_opencode_config(legal_only, servers={"deeplaw"}, enabled={"deeplaw": True})
    _assert_mcp_list(session.text("list_legal_only", "mcp", "list"), connected={"deeplaw"})

    _install_opencode_projection(repository, project, products={"knowledge"})
    knowledge = _opencode_config(
        source, products={"knowledge"}, deeplaw_executable=deeplaw_executable
    )
    _write_opencode_config(project, knowledge)
    knowledge_only = session.json("validate_knowledge_only", "debug", "config")
    _assert_opencode_config(
        knowledge_only,
        servers={"deeplaw_knowledge"},
        enabled={"deeplaw_knowledge": True},
    )
    _assert_mcp_list(
        session.text("list_knowledge_only", "mcp", "list"), connected={"deeplaw_knowledge"}
    )

    _install_opencode_projection(repository, project, products={"legal", "knowledge"})
    _write_opencode_config(project, both)
    upgraded = session.json("validate_adapter_upgrade", "debug", "config")
    _assert_opencode_config(
        upgraded,
        servers={"deeplaw", "deeplaw_knowledge"},
        enabled={"deeplaw": True, "deeplaw_knowledge": True},
    )
    shutil.rmtree(project / ".opencode")
    _write_opencode_config(project, {"$schema": "https://opencode.ai/config.json"})
    empty = session.json("validate_removed", "debug", "config")
    if empty.get("mcp") not in ({}, None):
        raise HostAcceptanceError("OpenCode adapter removal left MCP state behind")
    return {
        "cli_version": cli_version,
        "executable_sha256": sha256_file(executable),
        "lifecycle": {
            "resolved_config_validation": True,
            "agent_skill_discovery": True,
            "mcp_stdio_discovery": True,
            "install": True,
            "disable_enable": True,
            "upgrade": True,
            "remove": True,
            "dual_product_isolation": True,
            "final_state_empty": True,
        },
        "upgrade": {
            "from": BASELINE_VERSION,
            "to": VERSION,
            "transport": "version-bound local adapter projection",
        },
        "official_cli_boundary": (
            "OpenCode 1.18.16 validates native config, agent, skill, and MCP states; "
            "adapter files are installed, upgraded, disabled, and removed locally because "
            "OpenCode exposes no marketplace remove command for this configuration form."
        ),
        "command_evidence": session.evidence,
        "passed": True,
    }


def run(
    repository: Path,
    *,
    codex: str,
    claude: str,
    opencode: str,
    deeplaw: str,
    codex_version: str,
    claude_version: str,
    opencode_version: str,
) -> dict[str, Any]:
    binding = repository_binding(repository)
    if binding["package_version"] != VERSION or not binding["worktree_clean"]:
        raise HostAcceptanceError("host acceptance requires a clean release commit")
    executables = {
        "codex": _resolve_executable(codex),
        "claude": _resolve_executable(claude),
        "opencode": _resolve_executable(opencode),
        "deeplaw": _resolve_executable(deeplaw),
    }
    with tempfile.TemporaryDirectory(prefix="deeplaw-no-model-hosts-") as temporary:
        root = Path(temporary)
        fixture_root = root / "marketplace-fixture"
        fixture_root.mkdir()
        mirror, gitconfig = _prepare_git_marketplace(
            repository,
            root=fixture_root,
            current_commit=binding["commit"],
        )
        hosts = {
            "codex": _codex_acceptance(
                repository,
                executable=executables["codex"],
                root=root / "codex-host",
                mirror=mirror,
                gitconfig=gitconfig,
                current_commit=binding["commit"],
                expected_version=codex_version,
            ),
            "claude_code": _claude_acceptance(
                repository,
                executable=executables["claude"],
                root=root / "claude-host",
                mirror=mirror,
                gitconfig=gitconfig,
                current_commit=binding["commit"],
                expected_version=claude_version,
            ),
            "opencode": _opencode_acceptance(
                repository,
                executable=executables["opencode"],
                deeplaw_executable=executables["deeplaw"],
                root=root / "opencode-host",
                expected_version=opencode_version,
            ),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "binding": binding,
        "environment": environment_manifest(),
        "host_cli_pins": {
            "codex": codex_version,
            "claude_code": claude_version,
            "opencode": opencode_version,
        },
        "isolation": {
            "temporary_home_roots": True,
            "ambient_user_configuration_forwarded": False,
            "ambient_credentials_forwarded": False,
            "model_or_api_call_attempted": False,
            "large_model_api_key_required": False,
            "marketplace_git_transport_rewritten_to_local_fixture": True,
            "remote_network_required_for_host_lifecycle": False,
        },
        "hosts": hosts,
        "acceptance_scope": (
            "configuration-manifest-plugin-lifecycle-and-mcp-handshake-without-model"
        ),
        "model_task_acceptance": False,
        "model_task_results_claimed": False,
        "passed": all(host["passed"] for host in hosts.values()),
    }


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Run real Codex, Claude Code, and OpenCode lifecycle checks without a model."
    )
    parser.add_argument("--repository", type=Path, default=repository)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--opencode", default="opencode")
    parser.add_argument("--deeplaw", default="deeplaw")
    parser.add_argument("--codex-version", default="0.145.0")
    parser.add_argument("--claude-version", default="2.1.220")
    parser.add_argument("--opencode-version", default="1.18.16")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(
            args.repository.resolve(),
            codex=args.codex,
            claude=args.claude,
            opencode=args.opencode,
            deeplaw=args.deeplaw,
            codex_version=args.codex_version,
            claude_version=args.claude_version,
            opencode_version=args.opencode_version,
        )
        write_report(args.output.resolve(), report)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
