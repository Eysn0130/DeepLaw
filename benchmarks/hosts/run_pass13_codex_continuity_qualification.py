"""Run the installed-wheel Codex App Server Pass 16 qualification candidate.

The runner owns only the Host-side lifecycle and evidence boundary.  It starts a
fresh temporary Vault through the installed ``deeplaw`` executable, uses only an
owner-provided isolated Codex profile, keeps all raw sink/MCP/Host values in
memory, and retains only schema-validated hashes, bounded counters, and scalar
client event projections.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from benchmarks.hosts import pass16_continuity_cases, pass17_development_diagnostic
from benchmarks.hosts.codex_app_server_client import CodexAppServerClient
from benchmarks.hosts.pass13_evidence import (
    EvidenceValidationError,
    analyze_safe_read_calls,
    bind_relevant_chars,
    canonical_json,
    isolation_receipt,
    metric_evidence_sha256,
    native_lifecycle_receipt,
    write_retained_artifact,
)
from benchmarks.hosts.pass13_orchestrator import (
    QualificationOrchestrator,
    observe_knowledge_support_tools_list,
)
from benchmarks.hosts.pass13_orchestrator import (
    sha256_bytes as _sha256,
)
from benchmarks.hosts.pass13_orchestrator import (
    sha256_file as _sha256_file,
)

MODEL = "gpt-5.6-luna"
REASONING_EFFORT = "max"
CODEX_VERSION = "codex-cli 0.147.0-alpha.1.2"
RUN_COUNT = 3
TIMEOUT_SECONDS = 300.0
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
PROVIDER_HARD_LIMIT_BYTES = 65_536
MAX_MCP_STATUS_LIMIT = 1000

_SCENARIOS = pass16_continuity_cases.SCENARIOS
SCENARIOS = _SCENARIOS
_SCENARIO_METHODS = {
    "cold_start": ("thread/start",),
    "resume_fork": ("thread/start", "thread/resume", "thread/fork"),
    "compaction_forget": (
        "thread/start",
        "thread/compact/start",
        "item/started",
        "item/completed",
    ),
}
_SAFE_READ_OPERATIONS = frozenset({"context", "query"})
SCENARIO_TASKS = {
    scenario: pass16_continuity_cases.candidate_prompt(
        pass16_continuity_cases.task_case(scenario)
    )
    for scenario in SCENARIOS
}
_DISABLED_CAPABILITIES = (
    "shell_tool",
    "unified_exec",
    "shell_snapshot",
    "multi_agent",
    "browser_use",
    "computer_use",
    "apps",
    "plugins",
    "image_generation",
    "goals",
    "workspace_dependencies",
    "in_app_browser",
    "code_mode_host",
    "skill_search",
    "tool_suggest",
    "hooks",
)
_CANARY_NAMES = (
    "DEEPLAW_QUALIFICATION_SECRET_CANARY",
    "DEEPLAW_QUALIFICATION_PROVIDER_CANARY",
    "DEEPLAW_CREDENTIAL_PATH_CANARY",
)
_HOST_ENV_NAMES = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)
_ABSOLUTE_PATH = re.compile(
    rb'(?:^|[\s=:"\'])/(?!/)[A-Za-z0-9._~-]+(?:/[^\s"\'\\]*)?|'
    rb'(?:^|[\s="\'(])[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._$-]+[\\/]'
)
_CREDENTIAL_FIELD = re.compile(
    rb'"(?:[A-Za-z0-9_]*(?:api_key|authorization|cookie|credential|password|secret|'
    rb'capability_token)[A-Za-z0-9_]*|token)"\s*:',
    re.IGNORECASE,
)
_FINAL_RESPONSE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "next_step", "preserved_decisions", "open_gaps"],
    "properties": {
        "summary": {"type": "string", "maxLength": 1000},
        "next_step": {"type": "string", "maxLength": 500},
        "preserved_decisions": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 500},
        },
        "open_gaps": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 500},
        },
    },
}


class QualificationFailure(RuntimeError):
    """A Host qualification requirement failed closed."""


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _profile_roots(profile_root: Path) -> dict[str, Path]:
    return {
        "HOME": profile_root / "home",
        "CODEX_HOME": profile_root / "codex",
        "XDG_CONFIG_HOME": profile_root / "xdg-config",
        "XDG_DATA_HOME": profile_root / "xdg-data",
        "XDG_CACHE_HOME": profile_root / "xdg-cache",
        "XDG_STATE_HOME": profile_root / "xdg-state",
        "TMPDIR": profile_root / "tmp",
        "TMP": profile_root / "tmp",
        "TEMP": profile_root / "tmp",
        "USERPROFILE": profile_root / "home",
        "APPDATA": profile_root / "appdata",
        "LOCALAPPDATA": profile_root / "localappdata",
    }


def _resolved_path(value: str | Path) -> Path | None:
    try:
        return Path(value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _validate_profile_root(
    profile_root: Path,
    *,
    repository: Path | None = None,
    allow_create: bool = False,
) -> Path:
    """Validate an explicit, owner-created profile root without reading its contents."""

    profile = Path(profile_root)
    if not profile.is_absolute():
        raise QualificationFailure("Codex qualification profile root must be absolute")
    if profile.is_symlink():
        raise QualificationFailure("Codex qualification profile root must not be a symlink")
    exists = profile.exists()
    if exists and not profile.is_dir():
        raise QualificationFailure("Codex qualification profile root must already exist")
    if not exists and not allow_create:
        raise QualificationFailure("Codex qualification profile root must already exist")
    try:
        resolved = profile.resolve(strict=exists)
        repository_path = (repository or _repository()).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise QualificationFailure("Codex qualification profile root is unavailable") from exc
    try:
        resolved.relative_to(repository_path)
    except ValueError:
        pass
    else:
        raise QualificationFailure(
            "Codex qualification profile root must be outside the repository"
        )

    ambient_paths: set[Path] = set()
    for candidate in (
        Path.home(),
        Path.home() / ".codex",
        os.environ.get("HOME"),
        os.environ.get("CODEX_HOME"),
    ):
        if candidate:
            candidate_path = _resolved_path(candidate)
            if candidate_path is not None:
                ambient_paths.add(candidate_path)
    if resolved in ambient_paths:
        raise QualificationFailure(
            "Codex qualification profile root must differ from ambient HOME/CODEX_HOME"
        )

    # The profile itself may be outside the ambient roots while one of the
    # child roots aliases an ambient HOME/CODEX_HOME.  Reject that collision
    # before creating any missing non-authentication directories.
    for root in _profile_roots(resolved).values():
        if _resolved_path(root) in ambient_paths:
            raise QualificationFailure(
                "Codex qualification profile roots must differ from ambient HOME/CODEX_HOME"
            )
    if not exists:
        try:
            profile.mkdir(parents=True)
        except OSError as exc:
            raise QualificationFailure("Codex qualification profile root is unavailable") from exc
        return _validate_profile_root(profile, repository=repository)
    return resolved


def _host_environment(
    codex_binary: Path,
    profile_root: Path,
    canaries: Mapping[str, str] = (),
    *,
    inherit_existing_login: bool = False,
) -> dict[str, str]:
    # Keep this helper usable in the legacy unit seam while execute() performs
    # the strict owner-profile existence check before entering its work tempdir.
    profile_root = _validate_profile_root(profile_root, allow_create=True)
    roots = _profile_roots(profile_root)
    for root in set(roots.values()):
        if root.is_symlink():
            raise QualificationFailure("Codex qualification profile directory is unsafe")
        existed = root.exists()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise QualificationFailure(
                "Codex qualification profile directory is unavailable"
            ) from exc
        if root.is_symlink() or not root.is_dir():
            raise QualificationFailure("Codex qualification profile directory is unsafe")
        if not existed and os.name != "nt":
            root.chmod(0o700)
    environment = {name: value for name in _HOST_ENV_NAMES if (value := os.environ.get(name))}
    environment.update({name: str(root) for name, root in roots.items()})
    if inherit_existing_login:
        ambient_home = _resolved_path(os.environ.get("HOME") or Path.home())
        ambient_codex = _resolved_path(
            os.environ.get("CODEX_HOME")
            or ((ambient_home / ".codex") if ambient_home is not None else "")
        )
        if ambient_home is None or ambient_codex is None:
            raise QualificationFailure("Codex existing login location is unavailable")
        environment["HOME"] = str(ambient_home)
        environment["CODEX_HOME"] = str(ambient_codex)
        environment["USERPROFILE"] = str(ambient_home)
    environment["PATH"] = os.pathsep.join((str(codex_binary.parent), os.defpath))
    environment["NO_COLOR"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment.update(canaries)
    return environment


def _isolation_receipt(
    profile_root: Path,
    environment: Mapping[str, str],
    *,
    inherit_existing_login: bool = False,
) -> dict[str, Any]:
    expected = {
        "XDG_CONFIG_HOME": profile_root / "xdg-config",
        "XDG_DATA_HOME": profile_root / "xdg-data",
    }
    if not inherit_existing_login:
        expected.update(
            {
                "HOME": profile_root / "home",
                "CODEX_HOME": profile_root / "codex",
            }
        )
    if any(environment.get(name) != str(path) for name, path in expected.items()):
        raise QualificationFailure("Codex temporary profile isolation is inconsistent")
    if inherit_existing_login:
        ambient_home = _resolved_path(os.environ.get("HOME") or Path.home())
        ambient_codex = _resolved_path(
            os.environ.get("CODEX_HOME")
            or ((ambient_home / ".codex") if ambient_home is not None else "")
        )
        if (
            ambient_home is None
            or ambient_codex is None
            or environment.get("HOME") != str(ambient_home)
            or environment.get("CODEX_HOME") != str(ambient_codex)
        ):
            raise QualificationFailure("Codex existing login inheritance is inconsistent")
        return {
            "profile_kind": "temporary_closed_with_existing_login",
            "home_isolated": False,
            "codex_home_isolated": False,
            "xdg_config_home_isolated": True,
            "xdg_data_home_isolated": True,
            "ambient_host_state_inherited": True,
            "ambient_plugins_inherited": False,
            "ambient_apps_inherited": False,
            "ambient_hooks_inherited": False,
            "secret_values_retained": False,
            "auth_class": "chatgpt_login",
        }
    return isolation_receipt(host="codex")


def _closed_mcp_wrapper_source(runtime_python: Path, executable: Path, vault: Path) -> str:
    return f"""#!{runtime_python}
from __future__ import annotations
import os
import subprocess

environment = {{
    "PATH": os.defpath,
    "HOME": "mcp-home",
    "XDG_CONFIG_HOME": "mcp-home/config",
    "PYTHONNOUSERSITE": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
    "NO_COLOR": "1",
    "GIT_TERMINAL_PROMPT": "0",
}}
completed = subprocess.run(
    [{str(executable)!r}, "knowledge", "mcp", "--stdio", "--vault", {str(vault)!r}],
    env=environment,
    check=False,
)
raise SystemExit(completed.returncode)
"""


def _app_server_argv(
    codex_binary: Path,
    *,
    mcp_wrapper: Path,
    ambient_servers: Sequence[str] = (),
) -> list[str]:
    argv = [
        str(codex_binary),
        "app-server",
        "--stdio",
        "--config",
        'approval_policy="never"',
        "--config",
        'model="gpt-5.6-luna"',
        "--config",
        'model_reasoning_effort="max"',
        "--config",
        'sandbox_mode="read-only"',
        "--config",
        'web_search="disabled"',
        "--config",
        "mcp_servers={}",
        "--config",
        f"mcp_servers.deeplaw.command={json.dumps(str(mcp_wrapper))}",
        "--config",
        "mcp_servers.deeplaw.args=[]",
        "--config",
        'mcp_servers.deeplaw.enabled_tools=["knowledge_support"]',
        "--config",
        "mcp_servers.deeplaw.required=true",
        "--config",
        "mcp_servers.deeplaw.startup_timeout_sec=20",
        "--config",
        "mcp_servers.deeplaw.tool_timeout_sec=60",
    ]
    for name in ambient_servers:
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 100
            or not re.fullmatch(r"[A-Za-z0-9_-]+", name)
            or name == "deeplaw"
        ):
            raise QualificationFailure("ambient MCP server name is unsafe")
        argv.extend(("--config", f"mcp_servers.{name}.enabled=false"))
    for feature in _DISABLED_CAPABILITIES:
        argv.extend(("--disable", feature))
    return argv


def _parse_json_output(stdout: str) -> dict[str, Any]:
    try:
        complete = json.loads(stdout)
    except (TypeError, ValueError):
        complete = None
    if isinstance(complete, dict):
        return complete
    candidates = [line.strip() for line in stdout.splitlines() if line.strip()]
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    raise QualificationFailure("installed DeepLaw CLI did not return one JSON object")


def _run_installed_cli(
    executable: Path,
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    env = dict(environment or {"PATH": str(executable.parent), "PYTHONNOUSERSITE": "1"})
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QualificationFailure("installed DeepLaw CLI failed to start") from exc
    if completed.returncode != 0:
        raise QualificationFailure("installed DeepLaw CLI returned a failure")
    return _parse_json_output(completed.stdout)


def _create_git_task_repository(
    root: Path,
    *,
    task_line: str,
    development: bool = False,
) -> tuple[Path, Path, dict[str, Any], dict[str, Any]]:
    """Create one real task repository and detached concurrent worktree."""

    repository = root / "task-repository"
    repository.mkdir(parents=True, exist_ok=True)

    def git(*arguments: str, cwd: Path = repository) -> str:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                cwd=cwd,
                env={"PATH": os.defpath, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"},
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise QualificationFailure("temporary Git task repository command failed") from exc
        if completed.returncode != 0 or completed.stderr:
            raise QualificationFailure("temporary Git task repository command failed")
        return completed.stdout.strip()

    git("init", "--quiet")
    git(
        "config",
        "user.email",
        "development@localhost" if development else "qualification@localhost",
    )
    git("config", "user.name", "DeepLaw Development" if development else "DeepLaw Qualification")
    (repository / "TASK.md").write_text(
        (
            "Pass 17 local source-free development diagnostic.\n"
            if development
            else "Pass 16 local no-case-data qualification task.\n"
        ),
        encoding="utf-8",
    )
    (repository / ".gitignore").write_text("vault/\n", encoding="utf-8")
    git("add", "TASK.md", ".gitignore")
    git(
        "commit",
        "--quiet",
        "-m",
        "initial development diagnostic" if development else "initial qualification task",
    )
    concurrent = root / "concurrent-worktree"
    git("worktree", "add", "--quiet", "--detach", str(concurrent))
    primary_binding = pass16_continuity_cases.git_binding(
        repository, task_line=task_line
    )
    concurrent_binding = pass16_continuity_cases.git_binding(
        repository, task_line=task_line, worktree=concurrent
    )
    if primary_binding["base_revision"] != concurrent_binding["base_revision"]:
        raise QualificationFailure("concurrent worktree does not bind the same base revision")
    if primary_binding["worktree_sha256"] == concurrent_binding["worktree_sha256"]:
        raise QualificationFailure("concurrent worktree binding is not independent")
    return repository, concurrent, primary_binding, concurrent_binding


def _make_binding(
    scenario: str,
    *,
    repository: Path | None = None,
    worktree: Path | None = None,
) -> dict[str, Any]:
    """Compatibility seam returning a binding derived from a real Git repository."""

    case = pass16_continuity_cases.task_case(scenario)
    if repository is not None:
        return pass16_continuity_cases.git_binding(
            repository,
            task_line=str(case["task_case"]),
            worktree=worktree,
        )
    with tempfile.TemporaryDirectory(prefix="deeplaw-pass16-binding-") as temporary:
        repository, _concurrent, _primary, _concurrent_binding = _create_git_task_repository(
            Path(temporary), task_line=str(case["task_case"])
        )
        return pass16_continuity_cases.git_binding(
            repository,
            task_line=str(case["task_case"]),
        )


def _extract(value: Any, *keys: str) -> Any:
    if isinstance(value, Mapping):
        for key in keys:
            if key in value:
                return value[key]
        for nested in value.values():
            selected = _extract(nested, *keys)
            if selected is not None:
                return selected
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            selected = _extract(nested, *keys)
            if selected is not None:
                return selected
    return None


def _write_sink_request(
    executable: Path,
    vault: Path,
    grant_id: str,
    request: Mapping[str, Any],
    *,
    work_dir: Path,
) -> dict[str, Any]:
    request_path = work_dir / "sink-request.json"
    request_path.write_text(canonical_json(dict(request)) + "\n", encoding="utf-8")
    try:
        return _run_installed_cli(
            executable,
            [
                "knowledge",
                "sink",
                "apply",
                "--vault",
                str(vault),
                "--grant-id",
                grant_id,
                "--request",
                str(request_path),
            ],
            cwd=work_dir,
        )
    finally:
        request_path.unlink(missing_ok=True)


def _seed_vault(
    executable: Path,
    vault: Path,
    bindings: Mapping[str, Mapping[str, Any]],
    *,
    work_dir: Path,
    cases: Mapping[str, Mapping[str, Any]] | None = None,
    challenge_bindings: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    _run_installed_cli(
        executable,
        [
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "pass16-codex",
            "--scope",
            "project",
        ],
        cwd=work_dir,
    )
    enabled = _run_installed_cli(
        executable,
        [
            "knowledge",
            "sink",
            "enable",
            "--vault",
            str(vault),
            "--writer-id",
            "pass16-codex-runner",
            "--scope",
            "project",
            "--max-sensitivity",
            "private",
            "--operation",
            "record_run",
            "--operation",
            "remember",
            "--operation",
            "forget",
        ],
        cwd=work_dir,
    )
    grant_id = _extract(enabled, "grant_id", "grantId")
    if not isinstance(grant_id, str) or not grant_id:
        raise QualificationFailure("owner sink enable did not return a grant")
    checkpoints: dict[str, dict[str, Any]] = {}
    expires_at = "2099-01-01T00:00:00Z"
    selected_cases = cases or {
        scenario: pass16_continuity_cases.task_case(scenario) for scenario in bindings
    }
    selected_challenge_bindings = challenge_bindings or {}
    for scenario, binding in bindings.items():
        case = selected_cases.get(scenario)
        if not isinstance(case, Mapping):
            raise QualificationFailure("Pass 16 task case is missing")
        current = case.get("current_checkpoint")
        stale = case.get("stale_checkpoint")
        challenges = case.get("wrong_state_challenges")
        if (
            not isinstance(current, Mapping)
            or not isinstance(stale, Mapping)
            or not isinstance(challenges, list)
        ):
            raise QualificationFailure("Pass 16 task case checkpoints are invalid")
        markers = pass16_continuity_cases.marker_values(case)
        include_case_details = cases is not None
        seed_before = _ledger_head(executable, vault, work_dir=work_dir)
        seed_receipts: list[Mapping[str, Any]] = []
        run_id = f"run-pass16-{scenario}"
        recorded = _write_sink_request(
            executable,
            vault,
            grant_id,
            {
                "operation": "record_run",
                "idempotency_key": f"pass16-{scenario}-run",
                "confirm_no_case_data": True,
                "run_id": run_id,
                "task": f"Pass 16 owner qualification run for {scenario}.",
                "host_id": "codex-app-server-pass16",
                "model_id": MODEL,
                "status": "succeeded",
                "scope": "project",
                "sensitivity": "private",
                "run_metadata": {"task_binding": dict(binding)},
            },
            work_dir=work_dir,
        )
        seed_receipts.append(recorded)
        stale = _write_sink_request(
            executable,
            vault,
            grant_id,
            {
                "operation": "remember",
                "idempotency_key": f"pass16-{scenario}-stale-checkpoint",
                "confirm_no_case_data": True,
                "title": f"Pass 16 {scenario} stale checkpoint",
                "body": _checkpoint_body(
                    scenario,
                    decision=str(stale["decision"]),
                    next_action=str(stale["next_action"]),
                    verified=str(stale["verified_fact"]),
                    gap=str(stale["open_gap"]),
                    artifact=f"pass16-{scenario}-stale",
                    marker=str(stale["marker"]) if include_case_details else None,
                    route="stale" if include_case_details else None,
                    binding=binding if include_case_details else None,
                ),
                "kind": "memory",
                "memory_type": "working",
                "semantic_key": f"checkpoint:pass16:{scenario}",
                "expires_at": expires_at,
                "scope": "project",
                "sensitivity": "private",
                "run_id": run_id,
                "model_id": MODEL,
                "tool_id": "codex-app-server-pass16",
                "tags": ["pass16", "qualification", scenario, "stale"],
            },
            work_dir=work_dir,
        )
        seed_receipts.append(stale)
        stale_knowledge_id = _extract(stale, "knowledge_id", "knowledgeId")
        stale_revision_id = _extract(stale, "revision_id", "revisionId")
        if not isinstance(stale_knowledge_id, str) or not isinstance(stale_revision_id, str):
            raise QualificationFailure("stale checkpoint response omitted CAS identity")
        remembered = _write_sink_request(
            executable,
            vault,
            grant_id,
            {
                "operation": "remember",
                "idempotency_key": f"pass16-{scenario}-checkpoint",
                "confirm_no_case_data": True,
                "title": f"Pass 16 {scenario} working checkpoint",
                "body": _checkpoint_body(
                    scenario,
                    decision=str(current["decision"]),
                    next_action=str(current["next_action"]),
                    verified=str(current["verified_fact"]),
                    gap=str(current["open_gap"]),
                    artifact=f"pass16-{scenario}-checkpoint",
                    marker=str(current["marker"]) if include_case_details else None,
                    forget_marker=(
                        markers.get("forgotten") if include_case_details else None
                    ),
                    route="current" if include_case_details else None,
                    binding=binding if include_case_details else None,
                ),
                "kind": "memory",
                "memory_type": "working",
                "semantic_key": f"checkpoint:pass16:{scenario}",
                "expires_at": expires_at,
                "knowledge_id": stale_knowledge_id,
                "expected_revision_id": stale_revision_id,
                "scope": "project",
                "sensitivity": "private",
                "run_id": run_id,
                "model_id": MODEL,
                "tool_id": "codex-app-server-pass16",
                "tags": ["pass16", "qualification", scenario],
            },
            work_dir=work_dir,
        )
        seed_receipts.append(remembered)
        knowledge_id = _extract(remembered, "knowledge_id", "knowledgeId")
        revision_id = _extract(remembered, "revision_id", "revisionId")
        if not isinstance(knowledge_id, str) or not isinstance(revision_id, str):
            raise QualificationFailure("owner checkpoint response omitted CAS identity")
        checkpoints[scenario] = {
            "knowledge_id": knowledge_id,
            "revision_id": revision_id,
            "run_id": run_id,
            "task_case": str(case.get("task_case", f"continuity_{scenario}_v1")),
            "current_marker": markers["current"],
            "stale_marker": markers["stale"],
            "expected_decision": str(current["decision"]),
            "expected_next_action": str(current["next_action"]),
            "forbidden_markers": list(pass16_continuity_cases.forbidden_markers(case)),
            "forgotten_marker": markers.get("forgotten"),
        }
        for challenge in challenges:
            if not isinstance(challenge, Mapping):
                raise QualificationFailure("Pass 16 wrong-state challenge is invalid")
            dimension = str(challenge["challenge"])
            # The stale checkpoint is already the superseded revision of the
            # current semantic identity. A second active stale route with the
            # current binding would make correct admission impossible.
            if dimension == "stale_checkpoint" and include_case_details:
                continue
            marker = str(challenge["marker"])
            distractor_run_id = f"run-pass16-{scenario}-{dimension}"
            route_bindings = selected_challenge_bindings.get(scenario, {})
            distractor_binding = route_bindings.get(dimension, binding)
            distractor_run = _write_sink_request(
                executable,
                vault,
                grant_id,
                {
                    "operation": "record_run",
                    "idempotency_key": f"pass16-{scenario}-{dimension}-run",
                    "confirm_no_case_data": True,
                    "run_id": distractor_run_id,
                    "task": f"Pass 16 isolated {dimension} route for {scenario}.",
                    "host_id": "codex-app-server-pass16",
                    "model_id": MODEL,
                    "status": "succeeded",
                    "scope": "project",
                    "sensitivity": "private",
                    "run_metadata": {"task_binding": dict(distractor_binding)},
                },
                work_dir=work_dir,
            )
            seed_receipts.append(distractor_run)
            distractor = _write_sink_request(
                executable,
                vault,
                grant_id,
                {
                    "operation": "remember",
                    "idempotency_key": f"pass16-{scenario}-{dimension}",
                    "confirm_no_case_data": True,
                    "title": f"Pass 16 {scenario} route distractor",
                    "body": _checkpoint_body(
                        scenario,
                        decision=f"Do not admit {marker} into this route.",
                        next_action=f"Reject unrelated {dimension} state.",
                        verified=f"The {dimension} route is unrelated to this task.",
                        gap="The current route remains owner-authorized.",
                        artifact=f"pass16-{scenario}-{dimension}",
                        marker=marker if include_case_details else None,
                        route=dimension if include_case_details else None,
                        binding=distractor_binding if include_case_details else None,
                    ),
                    "kind": "memory",
                    "memory_type": "working",
                    "semantic_key": f"checkpoint:pass16:{scenario}:{dimension}",
                    "expires_at": expires_at,
                    "scope": "project",
                    "sensitivity": "private",
                    "run_id": distractor_run_id,
                    "model_id": MODEL,
                    "tool_id": "codex-app-server-pass16",
                    "tags": ["pass16", "qualification", scenario, dimension],
                },
                work_dir=work_dir,
            )
            seed_receipts.append(distractor)
        seed_after = _ledger_head(executable, vault, work_dir=work_dir)
        checkpoints[scenario]["seed_boundary"] = {
            "kind": "seed_checkpoint",
            "owner_enabled": True,
            "read_mcp_write_performed": False,
            "audit_changed": seed_before != seed_after,
            "audit_head_before": seed_before,
            "audit_head_after": seed_after,
            "receipt_sha256": _sha256(canonical_json(seed_receipts).encode("utf-8")),
            "target_sha256": _sha256(knowledge_id.encode("utf-8")),
        }
    return {"grant_id": grant_id, "checkpoints": checkpoints}


def _seed_development_vault(
    executable: Path,
    vault: Path,
    fixture: Mapping[str, Any],
    *,
    work_dir: Path,
) -> dict[str, Any]:
    """Seed one source-free development checkpoint through the public CLI."""

    _run_installed_cli(
        executable,
        [
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "pass17-codex-development",
            "--scope",
            "project",
        ],
        cwd=work_dir,
    )
    enabled = _run_installed_cli(
        executable,
        [
            "knowledge",
            "sink",
            "enable",
            "--vault",
            str(vault),
            "--writer-id",
            "pass17-codex-development-runner",
            "--scope",
            "project",
            "--max-sensitivity",
            "private",
            "--operation",
            "record_run",
            "--operation",
            "remember",
        ],
        cwd=work_dir,
    )
    grant_id = _extract(enabled, "grant_id", "grantId")
    checkpoint = fixture.get("checkpoint")
    if not isinstance(grant_id, str) or not grant_id or not isinstance(checkpoint, Mapping):
        raise QualificationFailure("development fixture setup is invalid")
    before = _ledger_head(executable, vault, work_dir=work_dir)
    run_id = "run-pass17-development-diagnostic"
    receipts = [
        _write_sink_request(
            executable,
            vault,
            grant_id,
            {
                "operation": "record_run",
                "idempotency_key": "pass17-development-run",
                "confirm_no_case_data": True,
                "run_id": run_id,
                "task": "Source-free native Host development diagnostic.",
                "host_id": "codex-app-server-pass17-development",
                "model_id": MODEL,
                "status": "succeeded",
                "scope": "project",
                "sensitivity": "private",
            },
            work_dir=work_dir,
        )
    ]
    remembered = _write_sink_request(
        executable,
        vault,
        grant_id,
        {
            "operation": "remember",
            "idempotency_key": "pass17-development-checkpoint",
            "confirm_no_case_data": True,
            "title": "Pass 17 source-free development checkpoint",
            "body": "\n".join(
                [
                    "GOAL: Run the source-free native Host development diagnostic.",
                    f"CONFIRMED_DECISION: {checkpoint['decision']}",
                    "CONSTRAINT: Use governed read-only context and no case data.",
                    f"VERIFIED_FACT: {checkpoint['verified_fact']}",
                    f"OPEN_GAP: {checkpoint['open_gap']}",
                    f"NEXT_ACTION: {checkpoint['next_action']}",
                    f"ROUTE_MARKER: {checkpoint['marker']}",
                ]
            ),
            "kind": "memory",
            "memory_type": "working",
            "semantic_key": "checkpoint:pass17:development-diagnostic",
            "expires_at": "2099-01-01T00:00:00Z",
            "scope": "project",
            "sensitivity": "private",
            "run_id": run_id,
            "model_id": MODEL,
            "tool_id": "codex-app-server-pass17-development",
            "tags": ["pass17", "development", "diagnostic"],
        },
        work_dir=work_dir,
    )
    receipts.append(remembered)
    knowledge_id = _extract(remembered, "knowledge_id", "knowledgeId")
    revision_id = _extract(remembered, "revision_id", "revisionId")
    if not isinstance(knowledge_id, str) or not isinstance(revision_id, str):
        raise QualificationFailure("development checkpoint omitted CAS identity")
    after = _ledger_head(executable, vault, work_dir=work_dir)
    if before == after:
        raise QualificationFailure("development checkpoint did not change the Ledger")
    return {
        "grant_id": grant_id,
        "checkpoints": {
            "cold_start": {
                "knowledge_id": knowledge_id,
                "revision_id": revision_id,
                "run_id": run_id,
                "task_case": fixture["task_case"],
                "current_marker": checkpoint["marker"],
                "stale_marker": None,
                "expected_decision": checkpoint["decision"],
                "expected_next_action": checkpoint["next_action"],
                "forbidden_markers": [],
                "forgotten_marker": None,
                "seed_boundary": {
                    "kind": "seed_checkpoint",
                    "owner_enabled": True,
                    "read_mcp_write_performed": False,
                    "audit_changed": True,
                    "audit_head_before": before,
                    "audit_head_after": after,
                    "receipt_sha256": _sha256(
                        canonical_json(receipts).encode("utf-8")
                    ),
                    "target_sha256": _sha256(knowledge_id.encode("utf-8")),
                },
            }
        },
    }


def _checkpoint_body(
    scenario: str,
    *,
    decision: str,
    next_action: str,
    verified: str,
    gap: str,
    artifact: str,
    marker: str | None = None,
    forget_marker: str | None = None,
    route: str | None = None,
    binding: Mapping[str, Any] | None = None,
) -> str:
    return "\n".join(
        [
            f"GOAL: Complete the Pass 16 {scenario} owner qualification.",
            f"CONFIRMED_DECISION: {decision}",
            "CONSTRAINT: Use only governed read-only context and no case data.",
            f"VERIFIED_FACT: {verified}",
            f"OPEN_GAP: {gap}",
            f"NEXT_ACTION: {next_action}",
            f"ARTIFACT_REF: {artifact}.",
        ]
        + ([f"ROUTE_MARKER: {marker}"] if marker else [])
        + ([f"FORGET_MARKER: {forget_marker}"] if forget_marker else [])
        + ([f"ROUTE_KIND: {route}"] if route else [])
        + (
            [f"BINDING_DIGEST: {binding['binding_sha256']}"]
            if isinstance(binding, Mapping) and isinstance(binding.get("binding_sha256"), str)
            else []
        )
    )


def _forget_checkpoint(
    executable: Path,
    vault: Path,
    grant_id: str,
    checkpoint: Mapping[str, Any],
    *,
    work_dir: Path,
) -> dict[str, Any]:
    return _write_sink_request(
        executable,
        vault,
        grant_id,
        {
            "operation": "forget",
            "idempotency_key": "pass16-compaction-forget",
            "confirm_no_case_data": True,
            "knowledge_id": checkpoint["knowledge_id"],
            "expected_revision_id": checkpoint["revision_id"],
            "reason": "Owner-directed Pass 16 checkpoint forgetting.",
        },
        work_dir=work_dir,
    )


def _ledger_head(executable: Path, vault: Path, *, work_dir: Path) -> str:
    value = _run_installed_cli(
        executable,
        ["knowledge", "autonomy", "status", "--vault", str(vault)],
        cwd=work_dir,
    )
    head = _extract(value, "audit_head", "auditHead")
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{64}", head):
        raise QualificationFailure("Vault status omitted a valid audit head")
    return head


def _prompt(
    scenario: str,
    binding: Mapping[str, Any],
    *,
    post_forget: bool = False,
    case: Mapping[str, Any] | None = None,
) -> str:
    selected_case = pass16_continuity_cases.task_case(scenario) if case is None else case
    prompt = pass16_continuity_cases.candidate_prompt(
        selected_case,
        phase="post_forget" if post_forget else "current",
    )
    call = _context_call_arguments(
        task=str(selected_case["task_case"]),
        binding=binding,
    )
    return (
        f"{prompt} Call knowledge_support with exactly these arguments: "
        f"{canonical_json(call)}. Retry at most once only if the first bounded Provider "
        "Capsule is insufficient. Return only bounded JSON with summary, next_step, "
        "preserved_decisions, and open_gaps."
    )


def _context_call_arguments(
    *,
    task: str,
    binding: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not task or len(task) > 5000:
        raise QualificationFailure("knowledge_support context task is invalid")
    arguments: dict[str, Any] = {
        "operation": "context",
        "task": task,
        "confirm_no_case_data": True,
        "query_plan_version": "6",
    }
    if binding is not None:
        arguments["task_binding"] = dict(binding)
    return arguments


def _parse_final(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        candidate = dict(value)
    elif isinstance(value, str):
        try:
            candidate = json.loads(value)
        except (TypeError, ValueError):
            return None
    else:
        return None
    if not isinstance(candidate, dict):
        return None
    try:
        Draft202012Validator(_FINAL_RESPONSE_SCHEMA).validate(candidate)
    except Exception:
        return None
    return candidate


def _contains_marker(value: Any, marker: str) -> bool:
    if isinstance(value, str):
        return marker in value
    if isinstance(value, Mapping):
        return any(_contains_marker(item, marker) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_marker(item, marker) for item in value)
    return False


def _empty_usage() -> dict[str, Any]:
    return {
        "input_tokens": "unreported",
        "cached_input_tokens": "unreported",
        "cache_write_input_tokens": "unreported",
        "output_tokens": "unreported",
        "reasoning_output_tokens": "unreported",
        "total_tokens": "unreported",
    }


def _require_actual_usage(value: Mapping[str, Any]) -> dict[str, int]:
    """Require actual App Server provider accounting for every retained turn."""

    fields = tuple(_empty_usage())
    if any(
        not isinstance(value.get(field), int)
        or isinstance(value.get(field), bool)
        or int(value[field]) < 0
        for field in fields
    ):
        raise QualificationFailure("actual Codex provider token usage is missing")
    usage = {field: int(value[field]) for field in fields}
    if (
        usage["total_tokens"]
        != usage["input_tokens"] + usage["output_tokens"]
        or usage["cached_input_tokens"] > usage["input_tokens"]
        or usage["reasoning_output_tokens"] > usage["output_tokens"]
    ):
        raise QualificationFailure("actual Codex provider token usage is inconsistent")
    return usage


def _merge_actual_usage(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> dict[str, int]:
    left = _require_actual_usage(first)
    right = _require_actual_usage(second)
    return _require_actual_usage(
        {field: left[field] + right[field] for field in _empty_usage()}
    )


def _result_value(result: Any, name: str, default: Any = None) -> Any:
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def _safe_read_placeholder() -> dict[str, Any]:
    return {
        "call_count": 0,
        "first_call_valid": False,
        "bounded_retry_used": False,
        "safe_read_operations": [],
        "provider_payloads": [],
    }


def _turn_record(
    result: Any,
    *,
    lifecycle_method: str,
    prompt: str,
    ledger_before: str,
    ledger_after: str,
    expected_decision: str | None = None,
    expected_next_action: str | None = None,
    forbidden_markers: Sequence[str] = (),
    stale_marker: str | None = None,
    current_marker: str | None = None,
    forgotten_marker: str | None = None,
    expected_task_binding: Mapping[str, Any],
    post_forget_phase: bool = False,
    require_task_binding: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    observations = list(_result_value(result, "tool_call_observations", []) or [])
    outputs = list(_result_value(result, "tool_outputs", []) or [])
    if len(observations) not in {1, 2} or len(outputs) != len(observations):
        raise QualificationFailure("safe read requires exactly one or two MCP calls")
    if any(
        not isinstance(observation, Mapping)
        or observation.get("server") != "deeplaw"
        or observation.get("tool_name") != "knowledge_support"
        or observation.get("status") != "completed"
        for observation in observations
    ):
        raise QualificationFailure("safe read used an unexpected MCP server or tool")
    expected_binding_sha256 = _sha256(
        canonical_json(dict(expected_task_binding)).encode("utf-8")
    )
    if any(
        observation.get("argument_operation") != "context"
        or observation.get("argument_task_present") is not True
        or observation.get("argument_confirm_no_case_data") is not True
        or observation.get("argument_query_plan_version") != "6"
        or (
            observation.get("argument_task_binding_sha256") != expected_binding_sha256
            if require_task_binding
            else observation.get("argument_task_binding_sha256") is not None
        )
        for observation in observations
    ):
        raise QualificationFailure(
            "safe read did not bind context, v6, no-case-data confirmation, and the exact task"
        )
    if _result_value(result, "status") != "completed":
        raise QualificationFailure("App Server turn did not complete successfully")
    try:
        safe_read = analyze_safe_read_calls(observations, outputs)
        safe_read = bind_relevant_chars(
            safe_read,
            outputs,
            tuple(
                value
                for value in (
                    expected_decision,
                    expected_next_action,
                    current_marker,
                    forgotten_marker if post_forget_phase else None,
                )
                if isinstance(value, str) and value
            ),
        )
    except EvidenceValidationError as exc:
        raise QualificationFailure("safe read observation failed validation") from exc
    final = _parse_final(_result_value(result, "final_text", ""))
    if final is None:
        raise QualificationFailure("bounded final response schema was not satisfied")
    final_bytes = canonical_json(final).encode("utf-8")
    if _ABSOLUTE_PATH.search(final_bytes) or _CREDENTIAL_FIELD.search(final_bytes):
        raise QualificationFailure("bounded final response contains prohibited data")
    usage_value = _result_value(result, "usage", _empty_usage())
    usage = (
        {key: usage_value.get(key, "unreported") for key in _empty_usage()}
        if isinstance(usage_value, Mapping)
        else _empty_usage()
    )
    thread_id = _result_value(result, "thread_id")
    turn_id = _result_value(result, "turn_id")
    if not isinstance(thread_id, str) or not isinstance(turn_id, str):
        raise QualificationFailure("turn response omitted thread or turn identity")
    events = _result_value(result, "events", [])
    safe_events = [dict(event) for event in events if isinstance(event, Mapping)]
    for event in safe_events:
        method = str(event.get("method", "")).casefold()
        tool_name = event.get("tool_name")
        if (
            (isinstance(tool_name, str) and tool_name != "knowledge_support")
            or any(
                label in method
                for label in ("web_search", "websearch", "shell", "command", "browser", "computer")
            )
        ):
            raise QualificationFailure("App Server observed a prohibited capability event")
    usage = _require_actual_usage(usage)
    event_bytes = b"".join(canonical_json(event).encode("utf-8") + b"\n" for event in safe_events)
    if not event_bytes:
        event_bytes = b'{"method":"not_applicable"}\n'
    record = {
        "status": "passed",
        "lifecycle_method": lifecycle_method,
        "thread_id_sha256": _sha256(thread_id.encode("utf-8")),
        "turn_id_sha256": _sha256(turn_id.encode("utf-8")),
        "prompt_sha256": _sha256(prompt.encode("utf-8")),
        "final_response_sha256": _sha256(final_bytes),
        "final_response_bytes": len(final_bytes),
        "host_elapsed_ms": 0,
        "usage": usage,
        "ledger_audit_head_before": ledger_before,
        "ledger_audit_head_after": ledger_after,
        "ledger_unchanged": ledger_before == ledger_after,
        "safe_read": safe_read,
        "sanitized_events": {"name": "", "bytes": len(event_bytes), "sha256": _sha256(event_bytes)},
    }
    if ledger_before != ledger_after:
        raise QualificationFailure("read-only turn mutated the ledger")
    marker_values = [*outputs, final]
    marker_payload = {
        "expected_decision": (
            bool(
                expected_decision
                and any(_contains_marker(value, expected_decision) for value in marker_values)
            )
            and expected_decision in final.get("preserved_decisions", [])
        ),
        "expected_next_action": bool(
            expected_next_action
            and any(_contains_marker(value, expected_next_action) for value in marker_values)
            and final.get("next_step") == expected_next_action
        ),
        "forbidden_absent": not any(
            _contains_marker(value, marker)
            for value in marker_values
            for marker in forbidden_markers
        ),
        "stale_absent": not stale_marker
        or not any(_contains_marker(value, stale_marker) for value in marker_values),
        "forgotten_absent": not bool(
            forgotten_marker
            and any(_contains_marker(value, forgotten_marker) for value in marker_values)
        ),
        "expected_state_absent": not post_forget_phase
        or not any(
            _contains_marker(value, marker)
            for value in marker_values
            for marker in (current_marker, expected_decision)
            if marker
        ),
        "current_present": not current_marker
        or any(_contains_marker(value, current_marker) for value in marker_values),
        "gap_observed": any(
            payload.get("gap_count", 0) > 0 for payload in safe_read.get("provider_payloads", [])
        )
        and bool(final.get("open_gaps")),
    }
    return record, {
        "bytes": event_bytes,
        "final": final,
        "safe_read": safe_read,
        **marker_payload,
    }


def _thread_id(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("threadId", "thread_id"):
            if isinstance(value.get(key), str) and value[key]:
                return value[key]
        thread = value.get("thread")
        if isinstance(thread, Mapping) and isinstance(thread.get("id"), str):
            return thread["id"]
        if isinstance(value.get("id"), str):
            return value["id"]
    raise QualificationFailure("thread lifecycle response omitted identity")


def _thread_is_ephemeral(scenario: str, *, development: bool) -> bool:
    """Resume requires a persisted root under the official App Server API."""

    return scenario != "resume_fork" and not development


def _run_scenario(
    *,
    client: Any,
    scenario: str,
    task_binding: Mapping[str, Any],
    prompt: str,
    ledger_head: Callable[[], str],
    forget_checkpoint: Callable[[], Any] | None,
    expectations: Mapping[str, Any] | None = None,
    case: Mapping[str, Any] | None = None,
    task_family: str | None = None,
) -> dict[str, Any]:
    if scenario not in _SCENARIOS:
        raise ValueError("unsupported Pass 16 scenario")
    turns: list[dict[str, Any]] = []
    methods: list[str] = []
    native_receipts: list[dict[str, Any]] = []
    marker_values: list[dict[str, Any]] = []
    pending_compaction_usage: dict[str, int] | None = None
    expectations = expectations or {}
    semantic_task_family = task_family or scenario
    development = semantic_task_family == "development_diagnostic"
    seed_boundary = expectations.get("seed_boundary")
    if not isinstance(seed_boundary, Mapping):
        raise QualificationFailure("scenario omitted its owner seed boundary")
    mutation_boundaries = [dict(seed_boundary)]

    def turn(
        thread_id: str,
        method: str,
        turn_prompt: str,
        *,
        post_forget_phase: bool = False,
    ) -> None:
        nonlocal pending_compaction_usage
        before = ledger_head()
        started = time.monotonic()
        result = client.turn_start(
            thread_id,
            [{"type": "text", "text": turn_prompt}],
            params={"outputSchema": _FINAL_RESPONSE_SCHEMA},
        )
        after = ledger_head()
        record, payload = _turn_record(
            result,
            lifecycle_method=method,
            prompt=turn_prompt,
            ledger_before=before,
            ledger_after=after,
            expected_decision=(
                None if post_forget_phase else expectations.get("expected_decision")
            ),
            expected_next_action=(
                None if post_forget_phase else expectations.get("expected_next_action")
            ),
            forbidden_markers=expectations.get("forbidden_markers", ()),
            stale_marker=expectations.get("stale_marker"),
            current_marker=expectations.get("current_marker"),
            forgotten_marker=(
                expectations.get("forgotten_marker") if scenario == "compaction_forget" else None
            ),
            expected_task_binding=task_binding,
            post_forget_phase=post_forget_phase,
            require_task_binding=not development,
        )
        if pending_compaction_usage is not None:
            record["usage"] = _merge_actual_usage(
                pending_compaction_usage, record["usage"]
            )
            pending_compaction_usage = None
        record["host_elapsed_ms"] = round((time.monotonic() - started) * 1000)
        turns.append(record)
        marker_values.append(payload)

    def compact(active_thread_id: str) -> None:
        nonlocal pending_compaction_usage
        event_offset = len(client.sanitized_events)
        compacted = client.thread_compact_start(active_thread_id)
        compact_events = [
            event
            for event in client.sanitized_events[event_offset:]
            if event.get("method") in {"item/started", "item/completed"}
            and event.get("compaction_status") in {"started", "completed"}
        ]
        if [event.get("method") for event in compact_events] != [
            "item/started",
            "item/completed",
        ]:
            raise QualificationFailure("contextCompaction native events are incomplete")
        pending_compaction_usage = _require_actual_usage(
            _result_value(client, "last_compaction_usage", _empty_usage())
        )
        methods.extend(["thread/compact/start", "item/started", "item/completed"])
        native_receipts.append(
            native_lifecycle_receipt(
                semantic_task_family=semantic_task_family,
                transport="codex_app_server_jsonrpc",
                request_seam="thread/compact/start",
                requested_operation="thread/compact/start",
                sanitized_request={
                    "thread_id_sha256": _sha256(active_thread_id.encode("utf-8"))
                },
                observation_kind="native_response",
                methods_observed=["thread/compact/start"],
                sanitized_observation={
                    "response": "accepted",
                    "response_shape": sorted(compacted),
                },
                current_identity=active_thread_id,
                parent_identity=active_thread_id,
                root_identity=thread_id,
                relation="same_session",
                actual_provider_usage=None,
            )
        )
        for compact_event in compact_events:
            native_receipts.append(
                native_lifecycle_receipt(
                    semantic_task_family=semantic_task_family,
                    transport="codex_app_server_jsonrpc",
                    request_seam="thread/compact/start notifications",
                    requested_operation="thread/compact/start",
                    sanitized_request={
                        "thread_id_sha256": _sha256(active_thread_id.encode("utf-8"))
                    },
                    observation_kind="native_event",
                    methods_observed=[str(compact_event["method"])],
                    sanitized_observation={"event": compact_event},
                    current_identity=active_thread_id,
                    parent_identity=active_thread_id,
                    root_identity=thread_id,
                    relation="same_session",
                    actual_provider_usage=(
                        pending_compaction_usage
                        if compact_event["method"] == "item/completed"
                        else None
                    ),
                )
            )

    thread_ephemeral = _thread_is_ephemeral(scenario, development=development)
    started = client.thread_start(
        {
            "model": MODEL,
            "effort": REASONING_EFFORT,
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "ephemeral": thread_ephemeral,
        }
    )
    thread_id = _thread_id(started)
    methods.append("thread/start")
    turn(thread_id, "thread/start", prompt)
    native_receipts.append(
        native_lifecycle_receipt(
            semantic_task_family=semantic_task_family,
            transport="codex_app_server_jsonrpc",
            request_seam="thread/start",
            requested_operation="thread/start",
            sanitized_request={
                "model": MODEL,
                "effort": REASONING_EFFORT,
                "approval_policy": "never",
                "sandbox": "read-only",
                "ephemeral": thread_ephemeral,
            },
            observation_kind="native_response",
            methods_observed=["thread/start"],
            sanitized_observation={
                "response": "thread",
                "thread_id_sha256": _sha256(thread_id.encode("utf-8")),
            },
            current_identity=thread_id,
            parent_identity=None,
            root_identity=thread_id,
            relation="new",
            actual_provider_usage=turns[-1]["usage"],
        )
    )
    if scenario == "resume_fork" or development:
        resumed = client.thread_resume(thread_id)
        methods.append("thread/resume")
        resumed_id = _thread_id(resumed)
        turn(resumed_id, "thread/resume", prompt)
        native_receipts.append(
            native_lifecycle_receipt(
                semantic_task_family=semantic_task_family,
                transport="codex_app_server_jsonrpc",
                request_seam="thread/resume",
                requested_operation="thread/resume",
                sanitized_request={"thread_id_sha256": _sha256(thread_id.encode("utf-8"))},
                observation_kind="native_response",
                methods_observed=["thread/resume"],
                sanitized_observation={
                    "response": "thread",
                    "thread_id_sha256": _sha256(resumed_id.encode("utf-8")),
                },
                current_identity=resumed_id,
                parent_identity=thread_id,
                root_identity=thread_id,
                relation="resume",
                actual_provider_usage=turns[-1]["usage"],
            )
        )
        forked = client.thread_fork(resumed_id)
        methods.append("thread/fork")
        forked_id = _thread_id(forked)
        turn(forked_id, "thread/fork", prompt)
        native_receipts.append(
            native_lifecycle_receipt(
                semantic_task_family=semantic_task_family,
                transport="codex_app_server_jsonrpc",
                request_seam="thread/fork",
                requested_operation="thread/fork",
                sanitized_request={
                    "thread_id_sha256": _sha256(resumed_id.encode("utf-8"))
                },
                observation_kind="native_response",
                methods_observed=["thread/fork"],
                sanitized_observation={
                    "response": "thread",
                    "thread_id_sha256": _sha256(forked_id.encode("utf-8")),
                },
                current_identity=forked_id,
                parent_identity=resumed_id,
                root_identity=thread_id,
                relation="fork",
                actual_provider_usage=turns[-1]["usage"],
            )
        )
        if development:
            compact(forked_id)
            turn(forked_id, "thread/compact/start", prompt)
    elif scenario == "compaction_forget":
        compact(thread_id)
        turn(thread_id, "thread/compact/start", prompt)
        if forget_checkpoint is None:
            raise QualificationFailure("compaction_forget omitted owner forget callback")
        forget_before = ledger_head()
        forget_receipt = forget_checkpoint()
        forget_after = ledger_head()
        if (
            not isinstance(forget_receipt, Mapping)
            or forget_receipt.get("knowledge_id") != expectations.get("knowledge_id")
            or forget_receipt.get("parent_revision_id") != expectations.get("revision_id")
            or forget_receipt.get("lifecycle") != "forgotten"
            or not isinstance(forget_receipt.get("revision_id"), str)
            or forget_before == forget_after
        ):
            raise QualificationFailure("owner forget receipt did not bind the exact CAS mutation")
        mutation_boundaries.append(
            {
                "kind": "forget",
                "owner_enabled": True,
                "read_mcp_write_performed": False,
                "audit_changed": forget_before != forget_after,
                "audit_head_before": forget_before,
                "audit_head_after": forget_after,
                "receipt_sha256": _sha256(
                    canonical_json(forget_receipt).encode("utf-8")
                ),
                "target_sha256": _sha256(
                    str(expectations["knowledge_id"]).encode("utf-8")
                ),
            }
        )
        turn(
            thread_id,
            "thread/compact/start",
            _prompt(scenario, task_binding, post_forget=True, case=case),
            post_forget_phase=True,
        )
    preservation_values = (
        marker_values[:-1] if scenario == "compaction_forget" else marker_values
    )
    metrics = {
        "first_correct_action": all(
            turn_record["safe_read"].get("first_call_valid") is True
            for turn_record in turns
        ),
        "decision_preservation": (
            all(
                payload["expected_decision"] and payload["expected_next_action"]
                for payload in preservation_values
            )
            if preservation_values
            else None
        ),
        "wrong_state_admission": (
            0 if all(payload["forbidden_absent"] for payload in marker_values) else 1
        ),
        "stale_state_rejected": all(payload["stale_absent"] for payload in marker_values),
        "forgotten_state_admission": (
            0
            if scenario == "compaction_forget"
            and marker_values[-1]["forgotten_absent"]
            and marker_values[-1]["expected_state_absent"]
            and marker_values[-1]["gap_observed"]
            else (1 if scenario == "compaction_forget" else None)
        ),
        "gap_observed": (
            marker_values[-1]["gap_observed"] if scenario == "compaction_forget" else None
        ),
        "projection_state_correct": None,
        "retention_wording_correct": None,
        "provider_boundary_correct": bool(
            payloads := [
                payload
                for record in turns
                for payload in record["safe_read"].get("provider_payloads", [])
                if isinstance(payload, Mapping)
            ]
        )
        and all(
            payload.get("delivery_match") is True
            and payload.get("write_performed") is False
            and isinstance(payload.get("provider_bytes"), int)
            and 0 < payload["provider_bytes"] <= PROVIDER_HARD_LIMIT_BYTES
            for payload in payloads
        ),
    }
    failure_codes = []
    if metrics["first_correct_action"] is not True:
        failure_codes.append("first_correct_action_invalid")
    if metrics["wrong_state_admission"] != 0:
        failure_codes.append("wrong_state_admitted")
    if metrics["stale_state_rejected"] is not True:
        failure_codes.append("stale_state_admitted")
    if metrics["provider_boundary_correct"] is not True:
        failure_codes.append("provider_boundary_invalid")
    if scenario == "resume_fork" and metrics["decision_preservation"] is not True:
        failure_codes.append("decision_not_preserved")
    if scenario == "compaction_forget" and (
        metrics["forgotten_state_admission"] != 0 or metrics["gap_observed"] is not True
    ):
        failure_codes.append("forgotten_state_admitted")
    run = {
        "scenario": scenario,
        "status": "failed" if failure_codes else "passed",
        "failure_codes": failure_codes,
        "task_sha256": _sha256(canonical_json(dict(task_binding)).encode("utf-8")),
        "new_thread": True,
        "methods_observed": methods,
        "task_family": semantic_task_family,
        "native_receipts": native_receipts,
        "turns": turns,
        "metrics": {**metrics, "evidence_sha256": "0" * 64},
        "mutation_boundaries": mutation_boundaries,
    }
    run["metrics"]["evidence_sha256"] = metric_evidence_sha256(run)
    return run


def _run_codex_mcp_list(
    codex_binary: Path, environment: Mapping[str, str]
) -> tuple[dict[str, Any], bytes]:
    try:
        completed = subprocess.run(
            [str(codex_binary), "mcp", "list", "--json"],
            capture_output=True,
            check=False,
            timeout=60,
            env=dict(environment),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QualificationFailure("Codex MCP inventory failed to start") from exc
    if completed.returncode != 0:
        raise QualificationFailure("Codex MCP inventory failed")
    raw = completed.stdout
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    stderr = completed.stderr
    if isinstance(stderr, str):
        stderr = stderr.encode("utf-8")
    if (
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > MAX_OUTPUT_BYTES
        or not isinstance(stderr, bytes)
        or len(stderr) > MAX_OUTPUT_BYTES
        or any(
            value.encode("utf-8") in raw + stderr
            for name, value in environment.items()
            if name in _CANARY_NAMES
        )
    ):
        raise QualificationFailure("Codex MCP inventory was empty")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise QualificationFailure("Codex MCP inventory was not JSON") from exc
    if not isinstance(value, (Mapping, list)):
        raise QualificationFailure("Codex MCP inventory shape was invalid")
    return (dict(value) if isinstance(value, Mapping) else {"data": value}), raw


def _codex_authentication_receipt(
    codex_binary: Path, environment: Mapping[str, str]
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [str(codex_binary), "login", "status"],
            capture_output=True,
            check=False,
            timeout=30,
            env=dict(environment),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QualificationFailure("Codex login status failed to start") from exc
    stdout = completed.stdout if isinstance(completed.stdout, bytes) else completed.stdout.encode()
    stderr = completed.stderr if isinstance(completed.stderr, bytes) else completed.stderr.encode()
    combined = stdout + stderr
    if (
        completed.returncode != 0
        or not combined
        or len(combined) > MAX_OUTPUT_BYTES
        or b"logged in" not in combined.lower()
        or b"chatgpt" not in combined.lower()
        or any(
            value.encode("utf-8") in combined
            for name, value in environment.items()
            if name in _CANARY_NAMES
        )
    ):
        raise QualificationFailure("Codex existing login was not confirmed")
    return {"checked": True, "raw_sha256": _sha256(combined), "raw_bytes": len(combined)}


def _validate_codex_version(
    completed: Any,
    *,
    canaries: Mapping[str, str] = (),
) -> str:
    """Accept only the exact pinned Codex CLI version on stdout."""

    stdout_value = getattr(completed, "stdout", None)
    stderr_value = getattr(completed, "stderr", None)
    if isinstance(stdout_value, bytes):
        stdout = stdout_value
    elif isinstance(stdout_value, str):
        stdout = stdout_value.encode("utf-8")
    else:
        stdout = b""
    if isinstance(stderr_value, bytes):
        stderr = stderr_value
    elif isinstance(stderr_value, str):
        stderr = stderr_value.encode("utf-8")
    else:
        stderr = b""
    version_bytes = stdout + stderr
    expected_stdout = {
        CODEX_VERSION.encode("utf-8"),
        (CODEX_VERSION + "\n").encode("utf-8"),
        (CODEX_VERSION + "\r\n").encode("utf-8"),
    }
    canary_values = tuple(
        value
        for value in (canaries.values() if isinstance(canaries, Mapping) else ())
        if isinstance(value, str)
    )
    if (
        getattr(completed, "returncode", None) != 0
        or stdout not in expected_stdout
        or len(version_bytes) > MAX_OUTPUT_BYTES
        or any(value.encode("utf-8") in version_bytes for value in canary_values)
    ):
        raise QualificationFailure("Codex version preflight failed")
    return CODEX_VERSION


def _configured_mcp_server_names(value: Any) -> list[str]:
    names: set[str] = set()

    def row_name(row: Mapping[str, Any]) -> str | None:
        name = row.get("name", row.get("serverName", row.get("id")))
        if isinstance(name, str) and name:
            return name
        for nested_key in ("server", "serverInfo", "config"):
            nested = row.get(nested_key)
            if isinstance(nested, Mapping):
                nested_name = nested.get("name", nested.get("serverName", nested.get("id")))
                if isinstance(nested_name, str) and nested_name:
                    return nested_name
        return None

    def add_rows(rows: Any) -> None:
        if isinstance(rows, Mapping):
            # Codex has emitted both an array of status records and a map of
            # configured name -> server definition across CLI versions.
            for key, row in rows.items():
                if key in {"data", "servers", "mcpServers"} and isinstance(row, (Mapping, list)):
                    add_rows(row)
                    continue
                if isinstance(key, str) and key:
                    names.add(key)
                if isinstance(row, Mapping) and (name := row_name(row)):
                    names.add(name)
            return
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                if name := row_name(row):
                    names.add(name)

    if isinstance(value, Mapping):
        found_container = False
        for key in ("data", "servers", "mcpServers"):
            if key in value:
                found_container = True
                add_rows(value[key])
        if not found_container:
            add_rows(value)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        add_rows(value)
    return sorted(names)


def _inventory_receipt(raw: bytes, *, selected_present: bool) -> dict[str, Any]:
    return {
        "checked": True,
        "selected_present": selected_present,
        "raw_sha256": _sha256(raw),
        "raw_bytes": len(raw),
    }


def _selected_model_present(value: Mapping[str, Any]) -> bool:
    rows = value.get("data")
    return isinstance(rows, list) and any(
        isinstance(row, Mapping)
        and any(row.get(key) == MODEL for key in ("id", "model", "slug"))
        for row in rows
    )


def _mcp_status_valid(value: Mapping[str, Any]) -> bool:
    rows = value.get("data")
    if not isinstance(rows, list) or not rows:
        return False
    observed_deeplaw = False
    seen_names: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            return False
        name = row.get("name", row.get("serverName"))
        server_info = row.get("serverInfo")
        if not isinstance(name, str) and isinstance(server_info, Mapping):
            name = server_info.get("name")
        if not isinstance(name, str):
            return False
        if name in seen_names:
            return False
        seen_names.add(name)
        tools = row.get("tools")
        if tools is None and isinstance(server_info, Mapping):
            tools = server_info.get("tools")
        if isinstance(tools, Mapping):
            tool_names = {name for name in tools if isinstance(name, str) and name}
        elif isinstance(tools, list):
            if not all(
                isinstance(tool, Mapping)
                and isinstance(tool.get("name"), str)
                and bool(tool["name"])
                for tool in tools
            ):
                return False
            tool_names = {
                tool.get("name")
                for tool in tools
                if isinstance(tool, Mapping) and isinstance(tool.get("name"), str)
            }
        else:
            tool_names = set()
        if name == "deeplaw":
            if tool_names != {"knowledge_support"}:
                return False
            observed_deeplaw = True
        elif tool_names:
            return False
    return observed_deeplaw


def _collect_app_server_inventory(
    client: CodexAppServerClient, *, inventory: str
) -> dict[str, Any]:
    rows: list[Any] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(20):
        if inventory == "model":
            page = client.model_list(include_hidden=True, cursor=cursor)
        elif inventory == "mcp":
            page = client.mcp_server_status_list(
                detail="full", cursor=cursor, limit=MAX_MCP_STATUS_LIMIT
            )
        else:
            raise ValueError("unsupported App Server inventory")
        page_rows = page.get("data")
        if not isinstance(page_rows, list):
            raise QualificationFailure("App Server inventory page is invalid")
        rows.extend(page_rows)
        if len(rows) > MAX_MCP_STATUS_LIMIT:
            raise QualificationFailure("App Server inventory exceeds its bound")
        next_cursor = page.get("nextCursor")
        if next_cursor is None:
            return {"data": rows, "nextCursor": None}
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
            raise QualificationFailure("App Server inventory cursor is invalid")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise QualificationFailure("App Server inventory pagination exceeds its bound")


def _placeholder_attestation() -> dict[str, Any]:
    return {
        "binary_name": "codex",
        "binary_sha256": "0" * 64,
        "version": "not_observed",
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "authentication": {
            "status": "existing_login_confirmed",
            "source": "existing_codex_login",
            "auth_file_read": False,
            "checked": False,
            "raw_sha256": None,
            "raw_bytes": 0,
        },
        "model_inventory": {
            "checked": False,
            "selected_present": False,
            "raw_sha256": None,
            "raw_bytes": 0,
        },
        "mcp_inventory": {
            "checked": False,
            "selected_present": False,
            "raw_sha256": None,
            "raw_bytes": 0,
        },
    }


def _placeholder_security() -> dict[str, Any]:
    return {
        "mcp_child_closed_environment": False,
        "only_knowledge_support_enabled": False,
        "absolute_path_leak": False,
        "secret_leak": False,
        "raw_transcript_retained": False,
        "hidden_reasoning_retained": False,
        "authentication_material_retained": False,
        "cleanup_complete": False,
    }


def _placeholder_run(
    index: int, scenario: str, *, task_family: str | None = None
) -> dict[str, Any]:
    run = {
        "run_index": index,
        "scenario": scenario,
        "task_family": task_family or scenario,
        "status": "failed",
        "failure_codes": ["not_executed"],
        "task_sha256": "0" * 64,
        "new_thread": False,
        "methods_observed": [],
        "native_receipts": [],
        "turns": [
            {
                "status": "failed",
                "lifecycle_method": "not_applicable",
                "thread_id_sha256": None,
                "turn_id_sha256": None,
                "prompt_sha256": "0" * 64,
                "final_response_sha256": None,
                "final_response_bytes": 0,
                "host_elapsed_ms": 0,
                "usage": _empty_usage(),
                "ledger_audit_head_before": "0" * 64,
                "ledger_audit_head_after": "0" * 64,
                "ledger_unchanged": True,
                "safe_read": _safe_read_placeholder(),
                "sanitized_events": {"name": "placeholder.jsonl", "bytes": 1, "sha256": "0" * 64},
            }
        ],
        "metrics": {
            "first_correct_action": None,
            "decision_preservation": None,
            "wrong_state_admission": None,
            "stale_state_rejected": None,
            "forgotten_state_admission": None,
            "gap_observed": None,
            "projection_state_correct": None,
            "retention_wording_correct": None,
            "provider_boundary_correct": None,
            "evidence_sha256": "0" * 64,
        },
        "mutation_boundaries": [],
    }
    run["metrics"]["evidence_sha256"] = metric_evidence_sha256(run)
    return run


def _safe_failure_code(exc: BaseException) -> str:
    if not isinstance(exc, QualificationFailure):
        code = re.sub(r"[^a-z0-9]+", "_", type(exc).__name__.casefold()).strip("_")
        return f"host_{code or 'failure'}"
    message = str(exc)
    known = {
        "safe read requires exactly one or two MCP calls": "safe_read_call_count_invalid",
        "safe read used an unexpected MCP server or tool": "safe_read_tool_failed",
        "safe read did not bind context, v6, no-case-data confirmation, and the exact task": (
            "safe_read_task_binding_invalid"
        ),
        "App Server turn did not complete successfully": "host_turn_failed",
        "safe read observation failed validation": "safe_read_output_invalid",
        "bounded final response schema was not satisfied": "final_response_schema_invalid",
        "bounded final response contains prohibited data": "final_response_prohibited",
        "turn response omitted thread or turn identity": "native_identity_missing",
        "App Server observed a prohibited capability event": "prohibited_capability_observed",
        "actual Codex provider token usage is missing": "provider_usage_missing",
        "actual Codex provider token usage is inconsistent": "provider_usage_inconsistent",
        "read-only turn mutated the ledger": "ledger_changed",
    }
    return known.get(message, "host_qualification_failure")


def _bind_run_event_receipt(
    run: dict[str, Any],
    *,
    event_name: str,
    event_bytes: bytes,
) -> None:
    turns = run.get("turns")
    metrics = run.get("metrics")
    if not isinstance(turns, list) or not isinstance(metrics, dict):
        raise QualificationFailure("Host run omitted event-bound metrics")
    receipt = {
        "name": event_name,
        "bytes": len(event_bytes),
        "sha256": _sha256(event_bytes),
    }
    for turn in turns:
        if not isinstance(turn, dict):
            raise QualificationFailure("Host run turn is invalid")
        turn["sanitized_events"] = dict(receipt)
    metrics["evidence_sha256"] = metric_evidence_sha256(run)


def _write_artifacts(
    output_dir: Path,
    artifacts: Mapping[str, bytes],
    *,
    forbidden_values: Sequence[str] = (),
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for name, data in artifacts.items():
        path = output_dir / name
        records[name] = write_retained_artifact(
            path,
            data,
            output_root=output_dir,
            forbidden_values=forbidden_values,
        )
    return records


def _prepare_codex_scenario(
    *,
    run_root: Path,
    case: Mapping[str, Any],
    codex_binary: Path,
    runtime_executable: Path,
    runtime_python: Path,
    ambient_servers: Sequence[str],
) -> dict[str, Any]:
    """Prepare one isolated Pass 16 vault, Git task, worktree, and MCP route."""

    scenario = str(case.get("scenario", ""))
    if scenario not in SCENARIOS:
        raise QualificationFailure("unsupported Pass 16 scenario")
    run_root.mkdir(parents=True, exist_ok=True)
    repository, concurrent, primary_binding, concurrent_binding = _create_git_task_repository(
        run_root,
        task_line=str(case["task_case"]),
    )
    wrong_task_binding = pass16_continuity_cases.git_binding(
        repository,
        task_line=f"{case['task_case']}:wrong-task-line",
    )
    bindings = {scenario: primary_binding}
    challenge_bindings = {
        scenario: {
            "stale_checkpoint": primary_binding,
            "wrong_task_line": wrong_task_binding,
            "wrong_worktree": concurrent_binding,
        }
    }
    vault = repository / "vault"
    seeded = _seed_vault(
        runtime_executable,
        vault,
        bindings,
        work_dir=repository,
        cases={scenario: case},
        challenge_bindings=challenge_bindings,
    )
    wrapper = run_root / "deeplaw-closed-mcp"
    wrapper.write_text(
        _closed_mcp_wrapper_source(runtime_python, runtime_executable, vault),
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    app_argv = _app_server_argv(
        codex_binary,
        mcp_wrapper=wrapper,
        ambient_servers=ambient_servers,
    )
    return {
        "case": dict(case),
        "repository": repository,
        "concurrent": concurrent,
        "binding": primary_binding,
        "concurrent_binding": concurrent_binding,
        "vault": vault,
        "seeded": seeded,
        "wrapper": wrapper,
        "app_argv": app_argv,
    }


def _prepare_codex_diagnostic(
    *,
    run_root: Path,
    fixture: Mapping[str, Any],
    codex_binary: Path,
    runtime_executable: Path,
    runtime_python: Path,
    ambient_servers: Sequence[str],
) -> dict[str, Any]:
    run_root.mkdir(parents=True, exist_ok=True)
    repository, concurrent, binding, concurrent_binding = _create_git_task_repository(
        run_root,
        task_line=str(fixture["task_case"]),
        development=True,
    )
    vault = repository / "vault"
    seeded = _seed_development_vault(
        runtime_executable,
        vault,
        fixture,
        work_dir=repository,
    )
    wrapper = run_root / "deeplaw-closed-mcp"
    wrapper.write_text(
        _closed_mcp_wrapper_source(runtime_python, runtime_executable, vault),
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    return {
        "case": dict(fixture),
        "repository": repository,
        "concurrent": concurrent,
        "binding": binding,
        "concurrent_binding": concurrent_binding,
        "vault": vault,
        "seeded": seeded,
        "wrapper": wrapper,
        "app_argv": _app_server_argv(
            codex_binary,
            mcp_wrapper=wrapper,
            ambient_servers=ambient_servers,
        ),
    }


def execute(
    *,
    candidate_wheel: Path,
    deeplaw_executable: Path,
    output_dir: Path,
    profile_root: Path,
    human_gold_path: Path | None,
    codex_command: str = "codex",
    mode: str = "qualification",
) -> dict[str, Any]:
    """Execute current qualification or one claim-ineligible diagnostic.

    This function intentionally performs no authentication-file access.  The
    Host process may use its existing login through the explicitly supplied
    environment; the MCP wrapper receives a closed environment instead.
    """

    repository = _repository()
    profile_root = _validate_profile_root(profile_root, repository=repository)
    if mode not in {"qualification", "diagnostic"}:
        raise QualificationFailure("Codex execution mode is invalid")
    if mode == "diagnostic" and human_gold_path is not None:
        raise QualificationFailure("Codex diagnostic must not receive Human Gold")
    if mode == "qualification":
        # This structural check runs before candidate preparation or any Host/model
        # process. Human authorship and independence remain reviewer attestations.
        from benchmarks.evaluator.score_pass16_host_continuity import (
            HumanGoldValidationError,
            load_human_gold,
        )

        if human_gold_path is None:
            raise QualificationFailure(
                "Codex qualification requires frozen external Human Gold"
            )
        try:
            load_human_gold(
                Path(human_gold_path),
                repository=repository,
                candidate_wheel_path=candidate_wheel,
            )
        except HumanGoldValidationError as exc:
            raise QualificationFailure(
                "Codex qualification requires frozen external Human Gold"
            ) from exc
    orchestrator = QualificationOrchestrator(
        host="codex",
        repository=repository,
        candidate_wheel=candidate_wheel,
        deeplaw_executable=deeplaw_executable,
        output_dir=output_dir.resolve(strict=False),
        error_type=QualificationFailure,
        execution_mode=mode,
    )
    selected_output, candidate_binding, runtime = orchestrator.prepare_candidate()
    codex_text = shutil.which(codex_command)
    if codex_text is None:
        raise QualificationFailure("Codex command was not found")
    codex_binary = Path(codex_text).resolve(strict=True)
    canaries = {
        name: _sha256(f"pass17-{mode}-{name}".encode()) for name in _CANARY_NAMES
    }

    selected_output.mkdir(parents=True)
    runs: list[dict[str, Any]] = []
    lifecycle_methods: set[str] = set()
    lifecycle_requests: set[str] = set()
    lifecycle_transports: set[str] = set()
    all_events: dict[str, bytes] = {}
    tool_schema: dict[str, Any] | None = None
    security = _placeholder_security()
    security.update(
        {
            "mcp_child_closed_environment": True,
            "only_knowledge_support_enabled": True,
            "cleanup_complete": True,
        }
    )
    model_inventory = {
        "checked": False,
        "selected_present": False,
        "raw_sha256": None,
        "raw_bytes": 0,
    }
    status_inventory = {
        "checked": False,
        "selected_present": False,
        "raw_sha256": None,
        "raw_bytes": 0,
    }

    cases = pass16_continuity_cases.cases_by_scenario()
    diagnostic_fixture = (
        pass17_development_diagnostic.load_fixture() if mode == "diagnostic" else None
    )
    run_specs = (
        [(scenario, scenario) for scenario in _SCENARIOS]
        if mode == "qualification"
        else [("development_diagnostic", "cold_start")]
    )
    with tempfile.TemporaryDirectory(prefix="deeplaw-pass17-") as temporary:
        work_dir = Path(temporary)
        inherit_existing_login = mode == "diagnostic"
        host_environment = _host_environment(
            codex_binary,
            profile_root,
            canaries,
            inherit_existing_login=inherit_existing_login,
        )
        host_isolation = _isolation_receipt(
            profile_root,
            host_environment,
            inherit_existing_login=inherit_existing_login,
        )
        version_process = subprocess.run(
            [str(codex_binary), "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
            env=host_environment,
        )
        codex_version = _validate_codex_version(version_process, canaries=canaries)
        authentication_receipt = _codex_authentication_receipt(
            codex_binary,
            host_environment,
        )
        mcp_inventory_value, _mcp_inventory_raw = _run_codex_mcp_list(
            codex_binary,
            host_environment,
        )
        ambient_names = [
            name
            for name in _configured_mcp_server_names(mcp_inventory_value)
            if name != "deeplaw"
        ]
        states: dict[str, dict[str, Any]] = {}
        for index, (reported_scenario, engine_scenario) in enumerate(run_specs, 1):
            if mode == "qualification":
                states[reported_scenario] = _prepare_codex_scenario(
                    run_root=work_dir / f"run-{index}",
                    case=cases[engine_scenario],
                    codex_binary=codex_binary,
                    runtime_executable=runtime["_executable"],
                    runtime_python=runtime["_runtime_python"],
                    ambient_servers=ambient_names,
                )
            else:
                if not isinstance(diagnostic_fixture, Mapping):
                    raise QualificationFailure("development diagnostic fixture is missing")
                states[reported_scenario] = _prepare_codex_diagnostic(
                    run_root=work_dir / f"run-{index}",
                    fixture=diagnostic_fixture,
                    codex_binary=codex_binary,
                    runtime_executable=runtime["_executable"],
                    runtime_python=runtime["_runtime_python"],
                    ambient_servers=ambient_names,
                )

        # Inventory is gathered from the same app-server protocol used by the
        # three lifecycle runs.  Raw pages are hashed in memory and discarded.
        inventory_state = states[run_specs[0][0]]
        try:
            tool_schema = observe_knowledge_support_tools_list(
                command=inventory_state["wrapper"],
                args=(),
                cwd=inventory_state["repository"],
                environment={
                    "PATH": os.defpath,
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "NO_COLOR": "1",
                    "GIT_TERMINAL_PROMPT": "0",
                },
            )
        except Exception as exc:
            raise QualificationFailure("knowledge_support tools/list observation failed") from exc
        inventory_client = CodexAppServerClient(
            inventory_state["app_argv"],
            host_environment,
            cwd=inventory_state["repository"],
            timeout_seconds=TIMEOUT_SECONDS,
            max_output_bytes=MAX_OUTPUT_BYTES,
            forbidden_output_values=tuple(canaries.values()),
        )
        try:
            inventory_client.initialize()
            model_page = _collect_app_server_inventory(inventory_client, inventory="model")
            status_page = _collect_app_server_inventory(inventory_client, inventory="mcp")
            model_raw = canonical_json(model_page).encode("utf-8")
            status_raw = canonical_json(status_page).encode("utf-8")
            model_inventory = _inventory_receipt(
                model_raw,
                selected_present=_selected_model_present(model_page),
            )
            status_valid = _mcp_status_valid(status_page)
            status_inventory = _inventory_receipt(status_raw, selected_present=status_valid)
            if not model_inventory["selected_present"]:
                raise QualificationFailure("selected model was absent from model/list")
            if not status_valid:
                raise QualificationFailure("MCP status exposed an unexpected tool or server")
        finally:
            inventory_client.close()
            if inventory_client.secret_leak:
                security["secret_leak"] = True

        for index, (reported_scenario, engine_scenario) in enumerate(run_specs, 1):
            state = states[reported_scenario]
            checkpoint = state["seeded"]["checkpoints"][engine_scenario]
            repository = state["repository"]
            vault = state["vault"]
            binding = state["binding"]
            client = CodexAppServerClient(
                state["app_argv"],
                host_environment,
                cwd=repository,
                timeout_seconds=TIMEOUT_SECONDS,
                max_output_bytes=MAX_OUTPUT_BYTES,
                forbidden_output_values=tuple(canaries.values()),
            )
            try:
                client.initialize()
                before = _ledger_head(runtime["_executable"], vault, work_dir=repository)

                def forget(
                    checkpoint: Mapping[str, Any] = checkpoint,
                    state: Mapping[str, Any] = state,
                    vault: Path = vault,
                    repository: Path = repository,
                ) -> Any:
                    return _forget_checkpoint(
                        runtime["_executable"],
                        vault,
                        state["seeded"]["grant_id"],
                        checkpoint,
                        work_dir=repository,
                    )

                if mode == "qualification":
                    selected_prompt = _prompt(
                        engine_scenario,
                        binding,
                        case=state["case"],
                    )
                else:
                    diagnostic_call = _context_call_arguments(
                        task=str(state["case"]["task_case"]),
                        binding=None,
                    )
                    selected_prompt = (
                        pass17_development_diagnostic.candidate_prompt(state["case"])
                        + " Call knowledge_support with exactly these arguments: "
                        + canonical_json(diagnostic_call)
                        + ". Return only bounded JSON with "
                        "summary, next_step, preserved_decisions, and open_gaps."
                    )
                run = _run_scenario(
                    client=client,
                    scenario=engine_scenario,
                    task_binding=binding,
                    prompt=selected_prompt,
                    ledger_head=lambda vault=vault, repository=repository: _ledger_head(
                        runtime["_executable"], vault, work_dir=repository
                    ),
                    forget_checkpoint=(
                        forget if engine_scenario == "compaction_forget" else None
                    ),
                    expectations=checkpoint,
                    case=state["case"] if mode == "qualification" else None,
                    task_family=reported_scenario,
                )
                after = _ledger_head(runtime["_executable"], vault, work_dir=repository)
                if before != after and engine_scenario != "compaction_forget":
                    raise QualificationFailure("read-only lifecycle changed the ledger")
                if mode == "diagnostic":
                    run["scenario"] = "development_diagnostic"
                    run["task_family"] = "development_diagnostic"
                    run["metrics"] = {
                        "first_correct_action": None,
                        "decision_preservation": None,
                        "wrong_state_admission": None,
                        "stale_state_rejected": None,
                        "forgotten_state_admission": None,
                        "gap_observed": None,
                        "projection_state_correct": None,
                        "retention_wording_correct": None,
                        "provider_boundary_correct": None,
                        "evidence_sha256": "0" * 64,
                    }
                    run["metrics"]["evidence_sha256"] = metric_evidence_sha256(run)
            except Exception as exc:
                run = _placeholder_run(
                    index,
                    reported_scenario,
                    task_family=reported_scenario,
                )
                run["failure_codes"] = [_safe_failure_code(exc)]
                run["task_sha256"] = _sha256(
                    canonical_json(dict(binding)).encode("utf-8")
                )
            finally:
                cleanup_ok = client.cleanup_persisted_threads()
                if not cleanup_ok:
                    security["cleanup_complete"] = False
                    run["status"] = "failed"
                    run["failure_codes"] = sorted(
                        set(run.get("failure_codes", [])) | {"host_cleanup_incomplete"}
                    )
                events = client.sanitized_events
                event_bytes = (
                    b"".join(
                        canonical_json(event).encode("utf-8") + b"\n"
                        for event in events
                        if isinstance(event, Mapping)
                    )
                    or b'{"method":"not_applicable"}\n'
                )
                event_name = f"codex-run-{index}-events.sanitized.jsonl"
                all_events[event_name] = event_bytes
                client.close()
                leak_codes: list[str] = []
                if client.secret_leak:
                    security["secret_leak"] = True
                    leak_codes.append("host_secret_leak")
                if _ABSOLUTE_PATH.search(event_bytes):
                    security["absolute_path_leak"] = True
                    leak_codes.append("host_absolute_path_leak")
                if leak_codes:
                    run["status"] = "failed"
                    run["failure_codes"] = sorted(
                        set(run.get("failure_codes", [])) | set(leak_codes)
                    )
            run["run_index"] = index
            run["scenario"] = reported_scenario
            run["task_family"] = reported_scenario
            run["turns"] = run.get(
                "turns",
                _placeholder_run(
                    index,
                    reported_scenario,
                    task_family=reported_scenario,
                )["turns"],
            )
            _bind_run_event_receipt(
                run,
                event_name=event_name,
                event_bytes=event_bytes,
            )
            lifecycle_methods.update(run.get("methods_observed", []))
            for receipt in run.get("native_receipts", []):
                if isinstance(receipt, Mapping):
                    requested = receipt.get("requested_operation")
                    transport = receipt.get("transport")
                    if isinstance(requested, str):
                        lifecycle_requests.add(requested)
                    if isinstance(transport, str):
                        lifecycle_transports.add(transport)
            runs.append(run)

    security["only_knowledge_support_enabled"] = bool(status_inventory["selected_present"])
    host_attestation = {
        "binary_name": "codex",
        "binary_sha256": _sha256_file(codex_binary),
        "version": codex_version,
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "authentication": {
            "status": "existing_login_confirmed",
            "source": "existing_codex_login",
            "auth_file_read": False,
            **authentication_receipt,
        },
        "model_inventory": model_inventory,
        "mcp_inventory": status_inventory,
    }
    report_binding = {
        "commit": candidate_binding["commit"],
        "tree": candidate_binding["tree"],
        "worktree_clean": True,
        **{
            key: value
            for key, value in runtime.items()
            if key
            in {
                "wheel_name",
                "wheel_sha256",
                "wheel_bytes",
                "runtime_executable_sha256",
                "import_path_class",
                "contract_digests",
            }
        },
    }
    if not isinstance(tool_schema, Mapping):
        raise QualificationFailure("knowledge_support tools/list receipt is missing")
    report = orchestrator.build_report(
        binding=report_binding,
        environment={
            "operating_system": platform.system(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "isolation": host_isolation,
        },
        host_attestation=host_attestation,
        tool_schema=tool_schema,
        runs=runs,
        lifecycle={
            "host_owns_threads": True,
            "common_task_families": [item[0] for item in run_specs],
            "transport_seams": sorted(lifecycle_transports),
            "requested_operations": sorted(lifecycle_requests),
            "methods_observed": sorted(lifecycle_methods),
            "deeplaw_session_store_created": False,
        },
        security=security,
        not_executed=(
            [
                "Human review",
                "Legal Pack qualification",
                "OpenCode host",
                "Desktop host",
                "scale qualification",
                "qualification holdout",
                "final blind",
                "release decision",
            ]
            if mode == "qualification"
            else [
                "qualification",
                "Human Gold",
                "blind scoring",
                "release decision",
            ]
        ),
    )
    report_bytes = canonical_json(report).encode("utf-8") + b"\n"
    if _ABSOLUTE_PATH.search(report_bytes) or any(
        value.encode("utf-8") in report_bytes for value in canaries.values()
    ):
        raise QualificationFailure("Host receipt leaked a path or secret canary")
    report_name = (
        "codex-continuity-qualification.json"
        if mode == "qualification"
        else "codex-development-diagnostic.json"
    )
    artifacts = {report_name: report_bytes, **all_events}
    _write_artifacts(selected_output, artifacts, forbidden_values=tuple(canaries.values()))
    if mode == "qualification":
        orchestrator.finalize_bundle(
            commit=candidate_binding["commit"],
            tree=candidate_binding["tree"],
            artifacts={
                role: selected_output / name
                for role, name in (
                    [("qualification_report", report_name)]
                    + [
                        (
                            f"sanitized_events_run_{index}",
                            f"codex-run-{index}-events.sanitized.jsonl",
                        )
                        for index in range(1, RUN_COUNT + 1)
                    ]
                )
            },
            forbidden_values=tuple(canaries.values()),
        )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run current Codex Host receipt workflow")
    parser.add_argument("--mode", choices=("qualification", "diagnostic"), default="qualification")
    parser.add_argument("--candidate-wheel", required=True)
    parser.add_argument("--deeplaw-executable", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--profile-root", required=True)
    parser.add_argument("--human-gold")
    parser.add_argument("--codex-command", default="codex")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = execute(
        candidate_wheel=Path(args.candidate_wheel),
        deeplaw_executable=Path(args.deeplaw_executable),
        output_dir=Path(args.output_dir),
        profile_root=Path(args.profile_root),
        human_gold_path=Path(args.human_gold) if args.human_gold else None,
        codex_command=args.codex_command,
        mode=args.mode,
    )
    print(canonical_json(report))
    return 0 if report["status"] == "executed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
