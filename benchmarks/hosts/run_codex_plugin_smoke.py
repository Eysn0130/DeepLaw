from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from deeplaw import __version__
from deeplaw.bounded_subprocess import (
    BoundedProcessResult,
    BoundedSubprocessError,
    run_bounded_subprocess,
)
from deeplaw.util import canonical_json, sha256_bytes, sha256_file, strict_json_loads

SCHEMA_VERSION = "deeplaw.codex-plugin-host-smoke/v1"
SCOPE = "plugin-lifecycle-only"
MARKETPLACE_NAME = "deeplaw"
MARKETPLACE_PATH = Path(".agents/plugins/marketplace.json")
SCHEMA_PATH = Path("contracts/codex-plugin-host-smoke.v1.schema.json")
IMPLEMENTATION_PATHS = (
    Path("benchmarks/hosts/run_codex_plugin_smoke.py"),
    SCHEMA_PATH,
    MARKETPLACE_PATH,
)
_MAX_OUTPUT_BYTES = 256 * 1024
_MAX_PLUGIN_FILES = 256
_MAX_PLUGIN_BYTES = 8 * 1024 * 1024
_VERSION_PATTERN = re.compile(r"^codex-cli [A-Za-z0-9][A-Za-z0-9.+_-]{0,99}$")
UNRESOLVED_CHECKS = (
    "frozen_wheel_runtime",
    "explicit_task_model_activation",
    "session_tool_discovery",
    "recall",
    "context",
    "verify",
    "explain_boundary",
    "restricted_exclusion",
    "read_only_behavior",
    "proposal_inbox_post_run_feedback",
    "inactive_zero_impact_model_session",
)


class HostSmokeError(RuntimeError):
    """Raised when a real-host lifecycle observation violates the closed contract."""


@dataclass(frozen=True, slots=True)
class PluginSpec:
    plugin_id: str
    name: str
    version: str
    relative_root: str
    source_root: Path


def _bounded_text(value: Any, *, field: str, maximum: int = 500) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise HostSmokeError(f"{field} is not bounded canonical text")
    return value


def _json_file(path: Path, *, field: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 1024 * 1024:
        raise HostSmokeError(f"{field} must be a bounded regular non-symlink file")
    try:
        value = strict_json_loads(path.read_bytes())
    except (UnicodeDecodeError, ValueError) as error:
        raise HostSmokeError(f"{field} must contain strict JSON") from error
    if not isinstance(value, dict):
        raise HostSmokeError(f"{field} must contain a JSON object")
    return value


def _same_path(value: Any, expected: Path, *, field: str) -> None:
    supplied = Path(_bounded_text(value, field=field, maximum=4_096))
    try:
        same = supplied.resolve(strict=True) == expected.resolve(strict=True)
    except OSError as error:
        raise HostSmokeError(f"{field} cannot be resolved") from error
    if not same:
        raise HostSmokeError(f"{field} does not identify the expected local source")


def _load_plugin_specs(repository: Path) -> tuple[PluginSpec, ...]:
    marketplace = _json_file(repository / MARKETPLACE_PATH, field="Codex marketplace")
    if set(marketplace) != {"name", "interface", "plugins"}:
        raise HostSmokeError("Codex marketplace has an unexpected top-level contract")
    if marketplace.get("name") != MARKETPLACE_NAME:
        raise HostSmokeError("Codex marketplace name is invalid")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 2:
        raise HostSmokeError("Codex marketplace must expose exactly two plugins")
    specs: list[PluginSpec] = []
    for index, item in enumerate(plugins):
        if not isinstance(item, dict) or set(item) != {
            "name",
            "source",
            "policy",
            "category",
        }:
            raise HostSmokeError(f"Codex marketplace plugin {index} has an invalid contract")
        name = _bounded_text(
            item.get("name"),
            field=f"marketplace plugin {index} name",
            maximum=100,
        )
        source = item.get("source")
        policy = item.get("policy")
        if (
            not isinstance(source, dict)
            or set(source) != {"source", "path"}
            or source.get("source") != "local"
            or not isinstance(policy, dict)
            or policy != {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}
            or item.get("category") != "Productivity"
        ):
            raise HostSmokeError(f"Codex marketplace plugin {name} is not local and explicit")
        relative_root = _bounded_text(
            source.get("path"),
            field=f"marketplace plugin {name} path",
            maximum=200,
        )
        expected_relative = f"./plugins/{name}"
        if relative_root != expected_relative:
            raise HostSmokeError(f"Codex marketplace plugin {name} has an unexpected root")
        source_root = repository / relative_root.removeprefix("./")
        codex_root = source_root / ".codex-plugin"
        if codex_root.is_symlink() or not codex_root.is_dir():
            raise HostSmokeError(f"Codex plugin entry directory is invalid for {name}")
        if sorted(path.name for path in codex_root.iterdir()) != ["plugin.json"]:
            raise HostSmokeError(
                f"Codex plugin entry directory must contain only plugin.json for {name}"
            )
        manifest = _json_file(
            codex_root / "plugin.json",
            field=f"Codex plugin manifest {name}",
        )
        if manifest.get("name") != name:
            raise HostSmokeError(f"Codex plugin manifest identity differs for {name}")
        if manifest.get("mcpServers") != "./.mcp.json":
            raise HostSmokeError(f"Codex plugin MCP manifest path is invalid for {name}")
        mcp = _json_file(source_root / ".mcp.json", field=f"Codex MCP config {name}")
        expected_server = "deeplaw" if name == "deeplaw" else "deeplaw-knowledge"
        expected_args = (
            ["mcp", "--stdio"]
            if name == "deeplaw"
            else ["knowledge", "mcp", "--stdio"]
        )
        if mcp != {
            "mcpServers": {
                expected_server: {
                    "command": "deeplaw",
                    "args": expected_args,
                }
            }
        }:
            raise HostSmokeError(f"Codex plugin MCP config is invalid for {name}")
        version = _bounded_text(
            manifest.get("version"),
            field=f"Codex plugin {name} version",
            maximum=100,
        )
        specs.append(
            PluginSpec(
                plugin_id=f"{name}@{MARKETPLACE_NAME}",
                name=name,
                version=version,
                relative_root=relative_root.removeprefix("./"),
                source_root=source_root,
            )
        )
    if {spec.name for spec in specs} != {"deeplaw", "deeplaw-knowledge-os"}:
        raise HostSmokeError("Codex marketplace plugin identities are incomplete")
    return tuple(sorted(specs, key=lambda spec: spec.plugin_id))


def plugin_inventory(root: Path) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise HostSmokeError("plugin inventory root must be a regular non-symlink directory")
    files: list[dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise HostSmokeError("plugin inventory contains a symbolic link")
        if path.is_dir():
            continue
        if not path.is_file():
            raise HostSmokeError("plugin inventory contains a non-regular entry")
        size = path.stat().st_size
        total_bytes += size
        if len(files) >= _MAX_PLUGIN_FILES or total_bytes > _MAX_PLUGIN_BYTES:
            raise HostSmokeError("plugin inventory exceeds its file or byte bound")
        relative = path.relative_to(root).as_posix()
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            raise HostSmokeError("plugin inventory contains an unsafe relative path")
        files.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "byte_size": size,
            }
        )
    if not files:
        raise HostSmokeError("plugin inventory is empty")
    return {
        "inventory_sha256": sha256_bytes(canonical_json(files).encode("utf-8")),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
    }


def _cached_plugin_root(value: Any, *, codex_home: Path, field: str) -> Path:
    raw = Path(_bounded_text(value, field=field, maximum=4_096))
    if not raw.is_absolute() or raw.is_symlink():
        raise HostSmokeError(f"{field} must be an absolute non-symlink path")
    cache_root = codex_home / "plugins" / "cache"
    try:
        resolved_cache = cache_root.resolve(strict=True)
        resolved = raw.resolve(strict=True)
        resolved.relative_to(resolved_cache)
    except (OSError, ValueError) as error:
        raise HostSmokeError(f"{field} escapes the isolated Codex plugin cache") from error
    current = resolved_cache
    for part in resolved.relative_to(resolved_cache).parts:
        current = current / part
        if current.is_symlink():
            raise HostSmokeError(f"{field} traverses a symbolic link")
    return resolved


def _cache_check(
    *,
    phase: str,
    spec: PluginSpec,
    installed_path: Any,
    codex_home: Path,
    source_inventory: dict[str, Any],
) -> dict[str, Any]:
    installed_root = _cached_plugin_root(
        installed_path,
        codex_home=codex_home,
        field=f"{phase} installedPath",
    )
    installed = plugin_inventory(installed_root)
    if canonical_json(installed) != canonical_json(source_inventory):
        raise HostSmokeError(f"{phase} cache bytes differ from the plugin source")
    return {
        "phase": phase,
        "plugin_id": spec.plugin_id,
        "source_inventory_sha256": source_inventory["inventory_sha256"],
        "installed_inventory_sha256": installed["inventory_sha256"],
        "file_count": installed["file_count"],
        "total_bytes": installed["total_bytes"],
        "exact_match": True,
    }


def _command_evidence(
    *, sequence: int, operation: str, result: BoundedProcessResult
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "operation": operation,
        "returncode": result.returncode,
        "stdout_sha256": sha256_bytes(result.stdout),
        "stdout_bytes": len(result.stdout),
        "stderr_sha256": sha256_bytes(result.stderr),
        "stderr_bytes": len(result.stderr),
        "raw_output_retained": False,
    }


class _CodexSession:
    def __init__(
        self,
        *,
        executable: Path,
        environment: dict[str, str],
        repository: Path,
        timeout_seconds: float,
    ) -> None:
        self.executable = executable
        self.environment = environment
        self.repository = repository
        self.timeout_seconds = timeout_seconds
        self.evidence: list[dict[str, Any]] = []

    def _execute(self, operation: str, arguments: tuple[str, ...]) -> BoundedProcessResult:
        try:
            result = run_bounded_subprocess(
                (str(self.executable), *arguments),
                environment=self.environment,
                cwd=self.repository,
                timeout_seconds=self.timeout_seconds,
                max_stdout_bytes=_MAX_OUTPUT_BYTES,
                max_stderr_bytes=_MAX_OUTPUT_BYTES,
            )
        except BoundedSubprocessError as error:
            raise HostSmokeError(
                f"Codex operation {operation} violated its process bound"
            ) from error
        self.evidence.append(
            _command_evidence(
                sequence=len(self.evidence) + 1,
                operation=operation,
                result=result,
            )
        )
        if result.returncode != 0:
            raise HostSmokeError(f"Codex operation {operation} returned a nonzero status")
        return result

    def plain(self, operation: str, *arguments: str) -> str:
        result = self._execute(operation, tuple(arguments))
        try:
            value = result.stdout.decode("utf-8").strip()
            result.stderr.decode("utf-8")
        except UnicodeDecodeError as error:
            raise HostSmokeError(f"Codex operation {operation} emitted non-UTF-8 output") from error
        return _bounded_text(value, field=f"Codex operation {operation} output", maximum=200)

    def json(self, operation: str, *arguments: str) -> dict[str, Any]:
        result = self._execute(operation, tuple(arguments))
        try:
            value = strict_json_loads(result.stdout)
            result.stderr.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise HostSmokeError(f"Codex operation {operation} emitted invalid JSON") from error
        if not isinstance(value, dict):
            raise HostSmokeError(f"Codex operation {operation} did not emit a JSON object")
        return value


def _validate_plugin_entry(
    value: Any,
    *,
    spec_by_id: dict[str, PluginSpec],
    repository: Path,
    installed: bool,
) -> str:
    if not isinstance(value, dict):
        raise HostSmokeError("Codex plugin list contains a non-object entry")
    plugin_id = _bounded_text(value.get("pluginId"), field="listed plugin id", maximum=200)
    spec = spec_by_id.get(plugin_id)
    if spec is None:
        raise HostSmokeError("Codex plugin list contains an unexpected plugin")
    if (
        value.get("name") != spec.name
        or value.get("marketplaceName") != MARKETPLACE_NAME
        or value.get("version") != spec.version
        or value.get("installed") is not installed
        or value.get("enabled") is not installed
        or value.get("installPolicy") != "AVAILABLE"
        or value.get("authPolicy") != "ON_INSTALL"
    ):
        raise HostSmokeError(f"Codex plugin state is invalid for {plugin_id}")
    source = value.get("source")
    marketplace_source = value.get("marketplaceSource")
    if (
        not isinstance(source, dict)
        or source.get("source") != "local"
        or not isinstance(marketplace_source, dict)
        or marketplace_source.get("sourceType") != "local"
    ):
        raise HostSmokeError(f"Codex plugin source is not local for {plugin_id}")
    _same_path(source.get("path"), spec.source_root, field=f"{plugin_id} source path")
    _same_path(
        marketplace_source.get("source"),
        repository,
        field=f"{plugin_id} marketplace path",
    )
    return plugin_id


def _list_check(
    value: dict[str, Any],
    *,
    check_id: str,
    specs: tuple[PluginSpec, ...],
    repository: Path,
    expected_installed: set[str],
    expected_available: set[str],
) -> dict[str, Any]:
    if set(value) != {"installed", "available"}:
        raise HostSmokeError(f"Codex plugin list contract is invalid at {check_id}")
    installed = value.get("installed")
    available = value.get("available")
    if not isinstance(installed, list) or not isinstance(available, list):
        raise HostSmokeError(f"Codex plugin list arrays are invalid at {check_id}")
    spec_by_id = {spec.plugin_id: spec for spec in specs}
    installed_ids = [
        _validate_plugin_entry(
            item,
            spec_by_id=spec_by_id,
            repository=repository,
            installed=True,
        )
        for item in installed
    ]
    available_ids = [
        _validate_plugin_entry(
            item,
            spec_by_id=spec_by_id,
            repository=repository,
            installed=False,
        )
        for item in available
    ]
    if (
        len(installed_ids) != len(set(installed_ids))
        or len(available_ids) != len(set(available_ids))
        or set(installed_ids) != expected_installed
        or set(available_ids) != expected_available
    ):
        raise HostSmokeError(f"Codex plugin state differs from expectation at {check_id}")
    return {
        "check_id": check_id,
        "expected_installed_plugin_ids": sorted(expected_installed),
        "observed_installed_plugin_ids": sorted(installed_ids),
        "expected_available_plugin_ids": sorted(expected_available),
        "observed_available_plugin_ids": sorted(available_ids),
        "passed": True,
    }


def _validate_marketplace_add(value: dict[str, Any], repository: Path) -> None:
    if value.get("marketplaceName") != MARKETPLACE_NAME or value.get("alreadyAdded") is not False:
        raise HostSmokeError("isolated Codex marketplace was not newly registered")
    _same_path(value.get("installedRoot"), repository, field="installed marketplace root")


def _validate_add(value: dict[str, Any], spec: PluginSpec) -> Any:
    if (
        value.get("pluginId") != spec.plugin_id
        or value.get("name") != spec.name
        or value.get("marketplaceName") != MARKETPLACE_NAME
        or value.get("version") != spec.version
        or value.get("authPolicy") != "ON_INSTALL"
    ):
        raise HostSmokeError(f"Codex add result is invalid for {spec.plugin_id}")
    return value.get("installedPath")


def _validate_remove(value: dict[str, Any], spec: PluginSpec) -> None:
    if (
        value.get("pluginId") != spec.plugin_id
        or value.get("name") != spec.name
        or value.get("marketplaceName") != MARKETPLACE_NAME
    ):
        raise HostSmokeError(f"Codex remove result is invalid for {spec.plugin_id}")


def _git(repository: Path, *arguments: str) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise HostSmokeError("Git is required to bind the candidate")
    try:
        result = run_bounded_subprocess(
            (executable, *arguments),
            cwd=repository,
            timeout_seconds=20,
            max_stdout_bytes=2 * 1024 * 1024,
            max_stderr_bytes=64 * 1024,
        )
    except BoundedSubprocessError as error:
        raise HostSmokeError("Git candidate binding violated its process bound") from error
    if result.returncode != 0:
        raise HostSmokeError("Git candidate binding failed")
    return result.stdout


def _resolve_codex(value: str | Path | None) -> Path:
    if value is None:
        discovered = shutil.which("codex")
        if discovered is None:
            raise HostSmokeError("Codex CLI was not found")
        raw = Path(discovered)
    else:
        raw = Path(value).expanduser()
    try:
        executable = raw.resolve(strict=True)
    except OSError as error:
        raise HostSmokeError("Codex CLI path cannot be resolved") from error
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise HostSmokeError("Codex CLI is not an executable regular file")
    return executable


def _isolated_environment(root: Path) -> dict[str, str]:
    directories = {
        "CODEX_HOME": root / "codex",
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
        path.mkdir(mode=0o700)
    environment = {
        key: str(path)
        for key, path in directories.items()
    }
    environment.update(
        {
            "PATH": os.defpath,
            "NO_COLOR": "1",
            "LANG": "C",
            "LC_ALL": "C",
        }
    )
    if os.name == "nt":
        for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
            if key in os.environ:
                environment[key] = os.environ[key]
    return environment


def _schema(repository: Path) -> dict[str, Any]:
    return _json_file(repository / SCHEMA_PATH, field="Codex host smoke schema")


def _validate_report(report: dict[str, Any], *, repository: Path) -> None:
    schema = _schema(repository)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(report)
    except (SchemaError, ValidationError) as error:
        raise HostSmokeError("Codex host smoke report violates its published schema") from error
    body = {key: value for key, value in report.items() if key != "record_sha256"}
    if report.get("record_sha256") != sha256_bytes(canonical_json(body).encode("utf-8")):
        raise HostSmokeError("Codex host smoke report digest is invalid")


def _assert_sanitized(value: Any, *, forbidden_roots: tuple[Path, ...]) -> None:
    forbidden = tuple(str(path.resolve()) for path in forbidden_roots)

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                visit(key)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str) and any(root in item for root in forbidden):
            raise HostSmokeError("Codex host smoke report contains a local absolute path")

    visit(value)


def run(
    repository: Path,
    *,
    codex: str | Path | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    if not timeout_seconds > 0:
        raise HostSmokeError("Codex command timeout must be positive")
    repository = repository.resolve(strict=True)
    if not repository.is_dir():
        raise HostSmokeError("repository must be a directory")
    executable = _resolve_codex(codex)
    specs = _load_plugin_specs(repository)
    source_inventories = {
        spec.plugin_id: plugin_inventory(spec.source_root)
        for spec in specs
    }
    spec_by_name = {spec.name: spec for spec in specs}
    legal = spec_by_name["deeplaw"]
    knowledge = spec_by_name["deeplaw-knowledge-os"]
    all_plugin_ids = {spec.plugin_id for spec in specs}
    implementation_files = {}
    for relative in IMPLEMENTATION_PATHS:
        path = repository / relative
        if path.is_symlink() or not path.is_file():
            raise HostSmokeError(f"host smoke implementation file is unavailable: {relative}")
        implementation_files[relative.as_posix()] = sha256_file(path)
    try:
        commit = _git(repository, "rev-parse", "HEAD").decode("ascii").strip()
        status = _git(repository, "status", "--porcelain=v1", "--untracked-files=all")
    except UnicodeDecodeError as error:
        raise HostSmokeError("Git candidate binding is not ASCII") from error
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise HostSmokeError("Git candidate commit is invalid")

    lifecycle_checks: list[dict[str, Any]] = []
    cache_checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="deeplaw-codex-host-smoke-") as temporary:
        temporary_root = Path(temporary)
        environment = _isolated_environment(temporary_root)
        codex_home = Path(environment["CODEX_HOME"])
        session = _CodexSession(
            executable=executable,
            environment=environment,
            repository=repository,
            timeout_seconds=timeout_seconds,
        )
        version = session.plain("host_version", "--version")
        if not _VERSION_PATTERN.fullmatch(version):
            raise HostSmokeError("Codex CLI version output has an unexpected contract")
        marketplace_result = session.json(
            "marketplace_add",
            "plugin",
            "marketplace",
            "add",
            str(repository),
            "--json",
        )
        _validate_marketplace_add(marketplace_result, repository)
        lifecycle_checks.append(
            _list_check(
                session.json(
                    "list_available",
                    "plugin",
                    "list",
                    "--available",
                    "--json",
                ),
                check_id="marketplace_discovery",
                specs=specs,
                repository=repository,
                expected_installed=set(),
                expected_available=all_plugin_ids,
            )
        )

        legal_path = _validate_add(
            session.json(
                "add_legal_initial",
                "plugin",
                "add",
                legal.plugin_id,
                "--json",
            ),
            legal,
        )
        cache_checks.append(
            _cache_check(
                phase="legal_initial",
                spec=legal,
                installed_path=legal_path,
                codex_home=codex_home,
                source_inventory=source_inventories[legal.plugin_id],
            )
        )
        lifecycle_checks.append(
            _list_check(
                session.json("list_legal_initial", "plugin", "list", "--json"),
                check_id="legal_initial",
                specs=specs,
                repository=repository,
                expected_installed={legal.plugin_id},
                expected_available=set(),
            )
        )

        knowledge_path = _validate_add(
            session.json(
                "add_knowledge_initial",
                "plugin",
                "add",
                knowledge.plugin_id,
                "--json",
            ),
            knowledge,
        )
        cache_checks.append(
            _cache_check(
                phase="knowledge_initial",
                spec=knowledge,
                installed_path=knowledge_path,
                codex_home=codex_home,
                source_inventory=source_inventories[knowledge.plugin_id],
            )
        )
        lifecycle_checks.append(
            _list_check(
                session.json("list_both_initial", "plugin", "list", "--json"),
                check_id="both_initial",
                specs=specs,
                repository=repository,
                expected_installed=all_plugin_ids,
                expected_available=set(),
            )
        )

        _validate_remove(
            session.json(
                "remove_knowledge",
                "plugin",
                "remove",
                knowledge.plugin_id,
                "--json",
            ),
            knowledge,
        )
        lifecycle_checks.append(
            _list_check(
                session.json(
                    "list_legal_survives_knowledge_remove",
                    "plugin",
                    "list",
                    "--json",
                ),
                check_id="legal_survives_knowledge_remove",
                specs=specs,
                repository=repository,
                expected_installed={legal.plugin_id},
                expected_available=set(),
            )
        )

        knowledge_path = _validate_add(
            session.json(
                "add_knowledge_readd",
                "plugin",
                "add",
                knowledge.plugin_id,
                "--json",
            ),
            knowledge,
        )
        cache_checks.append(
            _cache_check(
                phase="knowledge_readd",
                spec=knowledge,
                installed_path=knowledge_path,
                codex_home=codex_home,
                source_inventory=source_inventories[knowledge.plugin_id],
            )
        )
        lifecycle_checks.append(
            _list_check(
                session.json(
                    "list_both_after_knowledge_readd",
                    "plugin",
                    "list",
                    "--json",
                ),
                check_id="both_after_knowledge_readd",
                specs=specs,
                repository=repository,
                expected_installed=all_plugin_ids,
                expected_available=set(),
            )
        )

        _validate_remove(
            session.json(
                "remove_legal",
                "plugin",
                "remove",
                legal.plugin_id,
                "--json",
            ),
            legal,
        )
        lifecycle_checks.append(
            _list_check(
                session.json(
                    "list_knowledge_survives_legal_remove",
                    "plugin",
                    "list",
                    "--json",
                ),
                check_id="knowledge_survives_legal_remove",
                specs=specs,
                repository=repository,
                expected_installed={knowledge.plugin_id},
                expected_available=set(),
            )
        )

        legal_path = _validate_add(
            session.json(
                "add_legal_readd",
                "plugin",
                "add",
                legal.plugin_id,
                "--json",
            ),
            legal,
        )
        cache_checks.append(
            _cache_check(
                phase="legal_readd",
                spec=legal,
                installed_path=legal_path,
                codex_home=codex_home,
                source_inventory=source_inventories[legal.plugin_id],
            )
        )
        lifecycle_checks.append(
            _list_check(
                session.json(
                    "list_both_after_legal_readd",
                    "plugin",
                    "list",
                    "--json",
                ),
                check_id="both_after_legal_readd",
                specs=specs,
                repository=repository,
                expected_installed=all_plugin_ids,
                expected_available=set(),
            )
        )

        _validate_remove(
            session.json(
                "remove_knowledge_final",
                "plugin",
                "remove",
                knowledge.plugin_id,
                "--json",
            ),
            knowledge,
        )
        lifecycle_checks.append(
            _list_check(
                session.json(
                    "list_legal_after_knowledge_final",
                    "plugin",
                    "list",
                    "--json",
                ),
                check_id="legal_after_knowledge_final",
                specs=specs,
                repository=repository,
                expected_installed={legal.plugin_id},
                expected_available=set(),
            )
        )
        _validate_remove(
            session.json(
                "remove_legal_final",
                "plugin",
                "remove",
                legal.plugin_id,
                "--json",
            ),
            legal,
        )
        lifecycle_checks.append(
            _list_check(
                session.json("list_final_empty", "plugin", "list", "--json"),
                check_id="final_empty",
                specs=specs,
                repository=repository,
                expected_installed=set(),
                expected_available=set(),
            )
        )
        command_evidence = session.evidence
        environment_names = sorted(environment)
        temporary_forbidden = temporary_root
    if temporary_forbidden.exists():
        raise HostSmokeError("isolated Codex state was not removed")

    plugin_sources = []
    for spec in specs:
        inventory = source_inventories[spec.plugin_id]
        plugin_sources.append(
            {
                "plugin_id": spec.plugin_id,
                "name": spec.name,
                "version": spec.version,
                "relative_root": spec.relative_root,
                **inventory,
            }
        )
    blockers = [
        "plugin_lifecycle_only",
        "model_session_acceptance_not_run",
        "network_isolation_not_enforced",
    ]
    if status:
        blockers.insert(0, "candidate_worktree_not_frozen")
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "scope": SCOPE,
        "claim_eligible": False,
        "claim_ineligibility_reasons": blockers,
        "full_host_acceptance": False,
        "candidate": {
            "candidate_line": "0.7.0-unreleased",
            "package_version": __version__,
            "commit": commit,
            "worktree_dirty": bool(status),
            "implementation_files": implementation_files,
            "marketplace_sha256": sha256_file(repository / MARKETPLACE_PATH),
        },
        "host": {
            "kind": "codex",
            "cli_version": version,
            "executable_sha256": sha256_file(executable),
            "platform_system": platform.system(),
            "platform_release": platform.release(),
            "machine": platform.machine(),
        },
        "isolation": {
            "codex_home_temporary": True,
            "home_temporary": True,
            "xdg_roots_temporary": True,
            "ambient_user_environment_forwarded": False,
            "user_configuration_seeded": False,
            "user_credentials_seeded": False,
            "model_or_api_call_attempted": False,
            "network_isolation_enforced": False,
            "network_activity_measured": False,
            "network_claim": False,
            "temporary_state_removed": True,
            "environment_variable_names": environment_names,
        },
        "plugin_sources": plugin_sources,
        "command_evidence": command_evidence,
        "lifecycle_checks": lifecycle_checks,
        "cache_copy_checks": cache_checks,
        "result": {
            "success": True,
            "marketplace_discovery": True,
            "install_remove_readd": True,
            "plugin_lifecycle_isolation": True,
            "cache_copy_exact": True,
            "final_installed_plugin_ids": [],
        },
        "unresolved_checks": list(UNRESOLVED_CHECKS),
        "limitations": [
            "The smoke used a local source marketplace, not a frozen installed wheel.",
            "It did not start a model session or the bundled MCP processes.",
            "It did not enforce or measure operating-system network isolation.",
            "It covers one Codex CLI build on one operating-system instance only.",
        ],
    }
    report = {
        **body,
        "record_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }
    _assert_sanitized(report, forbidden_roots=(repository, temporary_forbidden, Path.home()))
    _validate_report(report, repository=repository)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a real Codex plugin marketplace/install/remove lifecycle in isolated local state."
        )
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--codex", help="Absolute path or command name for the Codex CLI.")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        report = run(
            arguments.repository,
            codex=arguments.codex,
            timeout_seconds=arguments.timeout_seconds,
        )
    except (HostSmokeError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
