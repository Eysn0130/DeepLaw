"""Run the installed-wheel Codex App Server Pass 13 qualification candidate.

The runner owns only the Host-side lifecycle and evidence boundary.  It starts a
fresh temporary Vault through the installed ``deeplaw`` executable, keeps all
raw sink/MCP/Host values in memory, and retains only schema-validated hashes,
bounded counters, and scalar client event projections.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts.codex_app_server_client import CodexAppServerClient
from benchmarks.hosts.pass13_evidence import (
    EvidenceValidationError,
    analyze_safe_read_calls,
    build_bundle_manifest,
    canonical_json,
    metric_evidence_sha256,
    validate_host_report_consistency,
    write_retained_artifact,
)

REPORT_SCHEMA_VERSION = "deeplaw.host-continuity-qualification/v1"
PACKAGE_VERSION = "0.12.0"
MODEL = "gpt-5.6-luna"
REASONING_EFFORT = "max"
RUN_COUNT = 3
TIMEOUT_SECONDS = 300.0
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_MCP_STATUS_LIMIT = 1000

_SCENARIOS = ("cold_start", "resume_fork", "compaction_forget")
_SCENARIO_METHODS = {
    "cold_start": ("thread/start",),
    "resume_fork": ("thread/start", "thread/resume", "thread/fork"),
    "compaction_forget": (
        "thread/start",
        "thread/compact/start",
        "thread/compacted",
    ),
}
_SAFE_READ_OPERATIONS = frozenset({"context", "query"})
_QUALIFICATION_MARKERS = {
    "cold_start": {
        "decision": "Keep release_ready=false until the Host qualification gates pass.",
        "next_action": "Record the bounded Codex Host receipts before any release decision.",
    },
    "resume_fork": {
        "decision": (
            "Preserve the owner-approved read-only qualification route across resume and fork."
        ),
        "next_action": "Compare each Host lifecycle receipt and keep the route unchanged.",
    },
    "compaction_forget": {
        "decision": (
            "Forget the owner-directed working checkpoint before continuing the qualification."
        ),
        "next_action": "Report the post-forget gap and do not reuse the forgotten checkpoint.",
    },
}
_DISTRACTOR_MARKERS = {
    "wrong_task": "PASS13-FORBIDDEN-WRONG-TASK",
    "wrong_worktree": "PASS13-FORBIDDEN-WRONG-WORKTREE",
    "stale_snapshot": "PASS13-FORBIDDEN-STALE-SNAPSHOT",
}
_FORGOTTEN_MARKER = "PASS13-FORGOTTEN-CHECKPOINT"
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
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TMP",
    "TEMP",
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
    rb'(?:^|[\s=:"\'])/(?:Users|home|tmp|private|var)(?:[\s/"\']|$)|[A-Za-z]:[\\/]'
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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repository: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env={"PATH": os.defpath, "LC_ALL": "C"},
    )
    if process.returncode != 0:
        raise QualificationFailure("Git binding could not be verified")
    return process.stdout.strip()


def _git_binding(repository: Path) -> dict[str, Any]:
    pyproject = repository / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    version = match.group(1) if match else None
    status = _git(repository, "status", "--porcelain=v1", "--untracked-files=all")
    return {
        "commit": _git(repository, "rev-parse", "HEAD"),
        "tree": _git(repository, "rev-parse", "HEAD^{tree}"),
        "worktree_clean": not bool(status),
        "package_version": version,
    }


def _candidate_output_directory(path: Path, *, repository: Path) -> Path:
    if path.is_symlink():
        raise ValueError("qualification output directory must not be a symlink")
    selected = path.resolve(strict=False)
    repository = repository.resolve(strict=True)
    if selected == repository or repository in selected.parents:
        raise ValueError("qualification output must be outside the repository")
    if selected.exists() or selected.is_symlink():
        raise ValueError("qualification output directory must not already exist")
    return selected


def _require_regular(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise QualificationFailure(f"{label} is not a regular file")
    selected = path.resolve(strict=True)
    if selected.is_symlink() or not selected.is_file():
        raise QualificationFailure(f"{label} is not a regular file")
    return selected


def _runtime_contract_script(contract_names: Sequence[str]) -> str:
    return (
        "import hashlib, importlib.resources, importlib.util, json, pathlib\n"
        "spec = importlib.util.find_spec('deeplaw')\n"
        "origin = getattr(spec, 'origin', None) if spec else None\n"
        "if not isinstance(origin, str) or 'site-packages' not in origin:\n"
        "    raise SystemExit(4)\n"
        f"names = {list(contract_names)!r}\n"
        "root = importlib.resources.files('deeplaw').joinpath('contracts')\n"
        "digests = {}\n"
        "for name in names:\n"
        "    data = root.joinpath(name).read_bytes()\n"
        "    digests[name] = hashlib.sha256(data).hexdigest()\n"
        "package_root = pathlib.Path(origin).parent\n"
        "files = {str(path.relative_to(package_root)): "
        "hashlib.sha256(path.read_bytes()).hexdigest() "
        "for path in package_root.rglob('*') if path.is_file() and '__pycache__' not in path.parts "
        "and path.suffix != '.pyc'}\n"
        "print(json.dumps({'import_path_class': 'isolated_site_packages', "
        "'contracts': digests, 'files': files}, sort_keys=True, separators=(',', ':')))\n"
    )


def _installed_runtime_binding(candidate_wheel: Path, deeplaw_executable: Path) -> dict[str, Any]:
    wheel = _require_regular(candidate_wheel, label="candidate wheel")
    if not wheel.name.startswith(f"deeplaw-{PACKAGE_VERSION}-") or not wheel.name.endswith(".whl"):
        raise QualificationFailure("candidate wheel must be the exact 0.12.0 DeepLaw wheel")
    executable = _require_regular(deeplaw_executable, label="installed deeplaw executable")
    runtime_python = executable.parent / "python"
    if not runtime_python.exists() or not runtime_python.resolve(strict=True).is_file():
        raise QualificationFailure("installed deeplaw executable has no adjacent Python")
    contract_names = (
        "host-continuity-qualification.v1.schema.json",
        "host-qualification-bundle-manifest.v1.schema.json",
        "knowledge-support.input.v6.schema.json",
        "knowledge-support.output.v6.schema.json",
        "knowledge-sink.input.v2.schema.json",
        "provider-knowledge-capsule.v2.schema.json",
    )
    try:
        completed = subprocess.run(
            [str(runtime_python), "-I", "-c", _runtime_contract_script(contract_names)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={"PATH": str(runtime_python.parent), "PYTHONNOUSERSITE": "1"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QualificationFailure("installed wheel import verification failed") from exc
    if completed.returncode != 0:
        raise QualificationFailure("installed wheel did not provide isolated DeepLaw contracts")
    try:
        observed = json.loads(completed.stdout)
    except (TypeError, ValueError) as exc:
        raise QualificationFailure("installed wheel contract receipt was invalid") from exc
    if (
        not isinstance(observed, Mapping)
        or observed.get("import_path_class") != "isolated_site_packages"
        or not isinstance(observed.get("contracts"), Mapping)
        or set(observed["contracts"]) != set(contract_names)
        or not isinstance(observed.get("files"), Mapping)
    ):
        raise QualificationFailure("installed wheel contract identity was incomplete")
    repository_contracts = _repository() / "contracts"
    for name, digest in observed["contracts"].items():
        path = repository_contracts / name
        if not path.is_file() or _sha256_file(path) != digest:
            raise QualificationFailure("installed wheel contract differs from the exact HEAD")
    try:
        with zipfile.ZipFile(wheel) as archive:
            wheel_files = {
                name.removeprefix("deeplaw/"): _sha256(archive.read(name))
                for name in archive.namelist()
                if name.startswith("deeplaw/") and not name.endswith("/")
            }
    except (OSError, zipfile.BadZipFile) as exc:
        raise QualificationFailure("candidate wheel package inventory is invalid") from exc
    if not wheel_files or dict(observed["files"]) != wheel_files:
        raise QualificationFailure("installed DeepLaw package does not match the candidate wheel")
    return {
        "wheel_name": wheel.name,
        "wheel_sha256": _sha256_file(wheel),
        "wheel_bytes": wheel.stat().st_size,
        "runtime_executable_sha256": _sha256_file(executable),
        "import_path_class": "isolated_site_packages",
        "contract_digests": {name: value for name, value in observed["contracts"].items()},
        "_executable": executable,
        "_runtime_python": runtime_python,
    }


def _host_environment(codex_binary: Path, canaries: Mapping[str, str] = ()) -> dict[str, str]:
    environment = {name: value for name in _HOST_ENV_NAMES if (value := os.environ.get(name))}
    environment["PATH"] = os.pathsep.join((str(codex_binary.parent), os.defpath))
    environment["NO_COLOR"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment.update(canaries)
    return environment


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


def _make_binding(scenario: str) -> dict[str, Any]:
    def digest(label: str) -> str:
        return _sha256(label.encode("utf-8"))

    value: dict[str, Any] = {
        "schema_version": "deeplaw.task-context-binding/v1",
        "project_sha256": digest("pass13-project"),
        "task_lineage_sha256": digest(f"pass13-task-{scenario}"),
        "parent_task_lineage_sha256": None,
        "repository_sha256": digest("pass13-repository"),
        "worktree_sha256": digest(f"pass13-worktree-{scenario}"),
        "base_revision": digest("pass13-base")[:40],
        "dirty_state_sha256": digest(f"pass13-dirty-{scenario}"),
    }
    value["binding_sha256"] = _sha256(canonical_json(value).encode("utf-8"))
    return value


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
) -> dict[str, Any]:
    _run_installed_cli(
        executable,
        [
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "pass13-codex",
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
            "pass13-codex-runner",
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
    for scenario, binding in bindings.items():
        seed_before = _ledger_head(executable, vault, work_dir=work_dir)
        seed_receipts: list[Mapping[str, Any]] = []
        run_id = f"run-pass13-{scenario}"
        recorded = _write_sink_request(
            executable,
            vault,
            grant_id,
            {
                "operation": "record_run",
                "idempotency_key": f"pass13-{scenario}-run",
                "confirm_no_case_data": True,
                "run_id": run_id,
                "task": f"Pass 13 owner qualification run for {scenario}.",
                "host_id": "codex-app-server-pass13",
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
                "idempotency_key": f"pass13-{scenario}-stale-checkpoint",
                "confirm_no_case_data": True,
                "title": f"Pass 13 {scenario} stale checkpoint",
                "body": _checkpoint_body(
                    scenario,
                    decision="Do not use the stale checkpoint decision.",
                    next_action=f"Do not reuse {_FORGOTTEN_MARKER}.",
                    verified=f"The stale marker is {_FORGOTTEN_MARKER}.",
                    gap="The current route must replace this stale revision.",
                    artifact=f"pass13-{scenario}-stale",
                ),
                "kind": "memory",
                "memory_type": "working",
                "semantic_key": f"checkpoint:pass13:{scenario}",
                "expires_at": expires_at,
                "scope": "project",
                "sensitivity": "private",
                "run_id": run_id,
                "model_id": MODEL,
                "tool_id": "codex-app-server-pass13",
                "tags": ["pass13", "qualification", scenario, "stale"],
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
                "idempotency_key": f"pass13-{scenario}-checkpoint",
                "confirm_no_case_data": True,
                "title": f"Pass 13 {scenario} working checkpoint",
                "body": _checkpoint_body(
                    scenario,
                    decision=_QUALIFICATION_MARKERS[scenario]["decision"],
                    next_action=_QUALIFICATION_MARKERS[scenario]["next_action"],
                    verified="The owner-authorized qualification route is current.",
                    gap="Independent holdout and blind evidence remain unexecuted.",
                    artifact=f"pass13-{scenario}-checkpoint",
                ),
                "kind": "memory",
                "memory_type": "working",
                "semantic_key": f"checkpoint:pass13:{scenario}",
                "expires_at": expires_at,
                "knowledge_id": stale_knowledge_id,
                "expected_revision_id": stale_revision_id,
                "scope": "project",
                "sensitivity": "private",
                "run_id": run_id,
                "model_id": MODEL,
                "tool_id": "codex-app-server-pass13",
                "tags": ["pass13", "qualification", scenario],
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
            "expected_decision": _QUALIFICATION_MARKERS[scenario]["decision"],
            "expected_next_action": _QUALIFICATION_MARKERS[scenario]["next_action"],
            "forbidden_markers": list(_DISTRACTOR_MARKERS.values()),
            "forgotten_marker": _FORGOTTEN_MARKER,
        }
        for dimension, marker in _DISTRACTOR_MARKERS.items():
            distractor_run_id = f"run-pass13-{scenario}-{dimension}"
            distractor_run = _write_sink_request(
                executable,
                vault,
                grant_id,
                {
                    "operation": "record_run",
                    "idempotency_key": f"pass13-{scenario}-{dimension}-run",
                    "confirm_no_case_data": True,
                    "run_id": distractor_run_id,
                    "task": f"Pass 13 isolated {dimension} route for {scenario}.",
                    "host_id": "codex-app-server-pass13",
                    "model_id": MODEL,
                    "status": "succeeded",
                    "scope": "project",
                    "sensitivity": "private",
                    "run_metadata": {"task_binding": _make_binding(f"{scenario}-{dimension}")},
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
                    "idempotency_key": f"pass13-{scenario}-{dimension}",
                    "confirm_no_case_data": True,
                    "title": f"Pass 13 {scenario} route distractor",
                    "body": _checkpoint_body(
                        scenario,
                        decision=f"Use {marker} only for an unrelated route.",
                        next_action=f"Never admit {marker} into the current route.",
                        verified=f"The route marker is {marker}.",
                        gap="The route does not match the current task binding.",
                        artifact=f"pass13-{scenario}-{dimension}",
                    ),
                    "kind": "memory",
                    "memory_type": "working",
                    "semantic_key": f"checkpoint:pass13:{scenario}:{dimension}",
                    "expires_at": expires_at,
                    "scope": "project",
                    "sensitivity": "private",
                    "run_id": distractor_run_id,
                    "model_id": MODEL,
                    "tool_id": "codex-app-server-pass13",
                    "tags": ["pass13", "qualification", scenario, dimension],
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


def _checkpoint_body(
    scenario: str,
    *,
    decision: str,
    next_action: str,
    verified: str,
    gap: str,
    artifact: str,
) -> str:
    return "\n".join(
        (
            f"GOAL: Complete the Pass 13 {scenario} owner qualification.",
            f"CONFIRMED_DECISION: {decision}",
            "CONSTRAINT: Use only governed read-only context and do not publish.",
            f"VERIFIED_FACT: {verified}",
            f"OPEN_GAP: {gap}",
            f"NEXT_ACTION: {next_action}",
            f"ARTIFACT_REF: {artifact}.",
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
            "idempotency_key": "pass13-compaction-forget",
            "confirm_no_case_data": True,
            "knowledge_id": checkpoint["knowledge_id"],
            "expected_revision_id": checkpoint["revision_id"],
            "reason": "Owner-directed Pass 13 checkpoint forgetting.",
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


def _prompt(scenario: str, binding: Mapping[str, Any], *, post_forget: bool = False) -> str:
    state = " after the owner-forget operation" if post_forget else ""
    return (
        f"Continue the no-case-data Pass 13 {scenario} qualification task{state}. "
        "Use exactly one read-only knowledge_support context call; retry once only "
        "if the first safe read is insufficient. Never use another tool or operation. "
        "The canonical task binding is "
        f"{canonical_json(dict(binding))}. confirm_no_case_data=true. "
        "Return only bounded JSON with summary, next_step, preserved_decisions, and open_gaps."
    )


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
    forgotten_marker: str | None = None,
    expected_task_binding: Mapping[str, Any],
    post_forget_phase: bool = False,
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
        or observation.get("argument_confirm_no_case_data") is not True
        or observation.get("argument_task_binding_sha256") != expected_binding_sha256
        for observation in observations
    ):
        raise QualificationFailure(
            "safe read did not bind context, no-case-data confirmation, and the exact task"
        )
    try:
        safe_read = analyze_safe_read_calls(observations, outputs)
    except EvidenceValidationError as exc:
        raise QualificationFailure("safe read observation failed validation") from exc
    if _result_value(result, "status") != "completed":
        raise QualificationFailure("App Server turn did not complete successfully")
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
            event.get("item_type") == "disallowed"
            or (isinstance(tool_name, str) and tool_name != "knowledge_support")
            or any(
                label in method
                for label in ("web_search", "websearch", "shell", "command", "browser", "computer")
            )
        ):
            raise QualificationFailure("App Server observed a prohibited capability event")
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
    marker_payload = {
        "expected_decision": (
            bool(
                expected_decision
                and any(_contains_marker(output, expected_decision) for output in outputs)
            )
            and expected_decision in final.get("preserved_decisions", [])
        ),
        "expected_next_action": bool(
            expected_next_action
            and any(_contains_marker(output, expected_next_action) for output in outputs)
            and final.get("next_step") == expected_next_action
        ),
        "forbidden_absent": not any(
            _contains_marker(output, marker) for output in outputs for marker in forbidden_markers
        ),
        "stale_absent": not any(
            _contains_marker(output, _DISTRACTOR_MARKERS["stale_snapshot"]) for output in outputs
        ),
        "forgotten_absent": not bool(
            forgotten_marker
            and any(_contains_marker(output, forgotten_marker) for output in outputs)
        ),
        "expected_state_absent": not post_forget_phase
        or not any(
            _contains_marker(output, marker)
            for output in outputs
            for marker in (expected_decision, expected_next_action)
            if marker
        ),
        "gap_observed": any(
            payload.get("gap_count", 0) > 0 for payload in safe_read.get("provider_payloads", [])
        ),
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


def _run_scenario(
    *,
    client: Any,
    scenario: str,
    task_binding: Mapping[str, Any],
    prompt: str,
    ledger_head: Callable[[], str],
    forget_checkpoint: Callable[[], Any] | None,
    expectations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if scenario not in _SCENARIOS:
        raise ValueError("unsupported Pass 13 scenario")
    turns: list[dict[str, Any]] = []
    methods: list[str] = []
    marker_values: list[dict[str, Any]] = []
    expectations = expectations or {}
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
        before = ledger_head()
        started = time.monotonic()
        result = client.turn_start(thread_id, [{"type": "text", "text": turn_prompt}])
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
            forgotten_marker=(
                expectations.get("forgotten_marker") if scenario == "compaction_forget" else None
            ),
            expected_task_binding=task_binding,
            post_forget_phase=post_forget_phase,
        )
        record["host_elapsed_ms"] = round((time.monotonic() - started) * 1000)
        turns.append(record)
        marker_values.append(payload)

    started = client.thread_start(
        {
            "model": MODEL,
            "effort": REASONING_EFFORT,
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "ephemeral": True,
        }
    )
    thread_id = _thread_id(started)
    methods.append("thread/start")
    turn(thread_id, "thread/start", prompt)
    if scenario == "resume_fork":
        resumed = client.thread_resume(thread_id)
        methods.append("thread/resume")
        resumed_id = _thread_id(resumed)
        turn(resumed_id, "thread/resume", prompt)
        forked = client.thread_fork(resumed_id)
        methods.append("thread/fork")
        forked_id = _thread_id(forked)
        turn(forked_id, "thread/fork", prompt)
    elif scenario == "compaction_forget":
        client.thread_compact_start(thread_id)
        methods.extend(["thread/compact/start", "thread/compacted"])
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
            _prompt(scenario, task_binding, post_forget=True),
            post_forget_phase=True,
        )
    correct_action_values = (
        marker_values[:-1] if scenario == "compaction_forget" else marker_values
    )
    metrics = {
        "first_correct_action": all(
            payload["expected_next_action"] for payload in correct_action_values
        ),
        "decision_preservation": (
            all(payload["expected_decision"] for payload in marker_values)
            if scenario == "resume_fork"
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
        "provider_boundary_correct": all(
            payload.get("write_performed") is False
            for record in turns
            for payload in record["safe_read"].get("provider_payloads", [])
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
        or any(
            value.encode("utf-8") in combined
            for name, value in environment.items()
            if name in _CANARY_NAMES
        )
    ):
        raise QualificationFailure("Codex existing login was not confirmed")
    return {"checked": True, "raw_sha256": _sha256(combined), "raw_bytes": len(combined)}


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
    }


def _placeholder_run(index: int, scenario: str) -> dict[str, Any]:
    run = {
        "run_index": index,
        "scenario": scenario,
        "status": "failed",
        "failure_codes": ["not_executed"],
        "task_sha256": "0" * 64,
        "new_thread": False,
        "methods_observed": ["not_applicable"],
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


def _build_report(
    *,
    binding: Mapping[str, Any],
    environment: Mapping[str, Any],
    host_attestation: Mapping[str, Any],
    runs: Sequence[Mapping[str, Any]],
    lifecycle: Mapping[str, Any],
    security: Mapping[str, Any],
) -> dict[str, Any]:
    passed_runs = sum(run.get("status") == "passed" for run in runs)
    failed_runs = len(runs) - passed_runs
    aggregate_fields: dict[str, Any] = {}
    for field in (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    ):
        values = [turn.get("usage", {}).get(field) for run in runs for turn in run.get("turns", [])]
        aggregate_fields[field] = (
            sum(value for value in values if isinstance(value, int))
            if values and all(isinstance(value, int) for value in values)
            else "unreported"
        )
    provider_bytes = sum(
        payload.get("provider_bytes", 0)
        for run in runs
        for turn in run.get("turns", [])
        for payload in turn.get("safe_read", {}).get("provider_payloads", [])
        if isinstance(payload, Mapping)
    )
    elapsed = sum(
        turn.get("host_elapsed_ms", 0)
        for run in runs
        for turn in run.get("turns", [])
        if isinstance(turn.get("host_elapsed_ms"), int)
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "host": "codex",
        "status": "executed" if failed_runs == 0 else "partial" if passed_runs else "failed",
        "package_version": PACKAGE_VERSION,
        "release_ready": False,
        "claim_eligible": False,
        "binding": dict(binding),
        "environment": dict(environment),
        "host_attestation": dict(host_attestation),
        "lifecycle": dict(lifecycle),
        "security": dict(security),
        "runs": [dict(run) for run in runs],
        "aggregate": {
            "passed_runs": passed_runs,
            "failed_runs": failed_runs,
            "first_call_valid_runs": sum(
                run.get("turns", [{}])[0].get("safe_read", {}).get("first_call_valid") is True
                for run in runs
            ),
            "bounded_retry_runs": sum(
                run.get("turns", [{}])[0].get("safe_read", {}).get("bounded_retry_used") is True
                for run in runs
            ),
            "provider_bytes": provider_bytes,
            **aggregate_fields,
            "host_elapsed_ms": elapsed,
        },
        "not_executed": [
            "Human review",
            "Legal Pack qualification",
            "OpenCode host",
            "Desktop host",
            "scale qualification",
            "qualification holdout",
            "final blind",
            "release decision",
        ],
    }


def _validate_report(report: Mapping[str, Any]) -> None:
    schema_path = _repository() / "contracts/host-continuity-qualification.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)


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


def execute(
    *,
    candidate_wheel: Path,
    deeplaw_executable: Path,
    output_dir: Path,
    codex_command: str = "codex",
) -> dict[str, Any]:
    """Execute the three current Host lifecycle scenarios.

    This function intentionally performs no authentication-file access.  The
    Host process may use its existing login through the explicitly supplied
    environment; the MCP wrapper receives a closed environment instead.
    """

    repository = _repository()
    selected_output = _candidate_output_directory(output_dir, repository=repository)
    binding = _git_binding(repository)
    if not binding.get("worktree_clean"):
        raise QualificationFailure("qualification requires a clean exact worktree")
    if binding.get("package_version") != PACKAGE_VERSION:
        raise QualificationFailure("qualification requires package version 0.12.0")
    runtime = _installed_runtime_binding(candidate_wheel, deeplaw_executable)
    codex_text = shutil.which(codex_command)
    if codex_text is None:
        raise QualificationFailure("Codex command was not found")
    codex_binary = Path(codex_text).resolve(strict=True)
    canaries = {name: _sha256(f"pass13-{name}".encode()) for name in _CANARY_NAMES}
    host_environment = _host_environment(codex_binary, canaries)
    authentication_receipt = _codex_authentication_receipt(codex_binary, host_environment)

    version_process = subprocess.run(
        [str(codex_binary), "--version"],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
        env=host_environment,
    )
    version_bytes = (version_process.stdout + version_process.stderr).encode("utf-8")
    if (
        version_process.returncode != 0
        or not version_process.stdout.strip()
        or len(version_bytes) > MAX_OUTPUT_BYTES
        or any(value.encode("utf-8") in version_bytes for value in canaries.values())
    ):
        raise QualificationFailure("Codex version preflight failed")
    codex_version = version_process.stdout.strip().splitlines()[-1][:200]
    mcp_inventory_value, _mcp_inventory_raw = _run_codex_mcp_list(codex_binary, host_environment)
    ambient_names = [
        name for name in _configured_mcp_server_names(mcp_inventory_value) if name != "deeplaw"
    ]

    selected_output.mkdir(parents=True)
    bindings = {scenario: _make_binding(scenario) for scenario in _SCENARIOS}
    runs: list[dict[str, Any]] = []
    lifecycle_methods: set[str] = set()
    all_events: dict[str, bytes] = {}
    security = _placeholder_security()
    security.update(
        {
            "mcp_child_closed_environment": True,
            "only_knowledge_support_enabled": True,
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

    with tempfile.TemporaryDirectory(prefix="deeplaw-pass13-") as temporary:
        work_dir = Path(temporary)
        vault = work_dir / "vault"
        seeded = _seed_vault(runtime["_executable"], vault, bindings, work_dir=work_dir)
        wrapper = work_dir / "deeplaw-closed-mcp"
        wrapper.write_text(
            _closed_mcp_wrapper_source(runtime["_runtime_python"], runtime["_executable"], vault),
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        app_argv = _app_server_argv(
            codex_binary, mcp_wrapper=wrapper, ambient_servers=ambient_names
        )

        # Inventory is gathered from the same app-server protocol used by the
        # three lifecycle runs.  Raw pages are hashed in memory and discarded.
        inventory_client = CodexAppServerClient(
            app_argv,
            host_environment,
            cwd=work_dir,
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

        for index, scenario in enumerate(_SCENARIOS, 1):
            checkpoint = seeded["checkpoints"][scenario]
            client = CodexAppServerClient(
                app_argv,
                host_environment,
                cwd=work_dir,
                timeout_seconds=TIMEOUT_SECONDS,
                max_output_bytes=MAX_OUTPUT_BYTES,
                forbidden_output_values=tuple(canaries.values()),
            )
            try:
                client.initialize()
                before = _ledger_head(runtime["_executable"], vault, work_dir=work_dir)

                def forget(checkpoint: Mapping[str, Any] = checkpoint) -> Any:
                    return _forget_checkpoint(
                        runtime["_executable"],
                        vault,
                        seeded["grant_id"],
                        checkpoint,
                        work_dir=work_dir,
                    )

                run = _run_scenario(
                    client=client,
                    scenario=scenario,
                    task_binding=bindings[scenario],
                    prompt=_prompt(scenario, bindings[scenario]),
                    ledger_head=lambda: _ledger_head(
                        runtime["_executable"], vault, work_dir=work_dir
                    ),
                    forget_checkpoint=forget if scenario == "compaction_forget" else None,
                    expectations=checkpoint,
                )
                after = _ledger_head(runtime["_executable"], vault, work_dir=work_dir)
                if before != after and scenario != "compaction_forget":
                    raise QualificationFailure("read-only lifecycle changed the ledger")
            except Exception as exc:
                code = re.sub(r"[^a-z0-9]+", "_", type(exc).__name__.casefold()).strip("_")
                run = _placeholder_run(index, scenario)
                run["failure_codes"] = [f"host_{code or 'failure'}"]
            finally:
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
            run["scenario"] = scenario
            run["turns"] = run.get("turns", _placeholder_run(index, scenario)["turns"])
            for turn in run["turns"]:
                turn["sanitized_events"]["name"] = event_name
                turn["sanitized_events"]["bytes"] = len(event_bytes)
                turn["sanitized_events"]["sha256"] = _sha256(event_bytes)
            lifecycle_methods.update(run.get("methods_observed", []))
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
        "commit": binding["commit"],
        "tree": binding["tree"],
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
    report = _build_report(
        binding=report_binding,
        environment={
            "operating_system": platform.system(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
        },
        host_attestation=host_attestation,
        runs=runs,
        lifecycle={
            "host_owns_threads": True,
            "methods_observed": sorted(lifecycle_methods) or ["not_applicable"],
            "deeplaw_session_store_created": False,
        },
        security=security,
    )
    report_bytes = canonical_json(report).encode("utf-8") + b"\n"
    if _ABSOLUTE_PATH.search(report_bytes) or any(
        value.encode("utf-8") in report_bytes for value in canaries.values()
    ):
        raise QualificationFailure("qualification report leaked a path or secret canary")
    _validate_report(report)
    try:
        validate_host_report_consistency(report)
    except EvidenceValidationError as exc:
        raise QualificationFailure("qualification report cross-field validation failed") from exc
    artifacts = {"codex-continuity-qualification.json": report_bytes, **all_events}
    _write_artifacts(selected_output, artifacts, forbidden_values=tuple(canaries.values()))
    manifest = build_bundle_manifest(
        host="codex",
        commit=binding["commit"],
        tree=binding["tree"],
        artifacts={
            role: selected_output / name
            for role, name in (
                [("qualification_report", "codex-continuity-qualification.json")]
                + [
                    (
                        f"sanitized_events_run_{index}",
                        f"codex-run-{index}-events.sanitized.jsonl",
                    )
                    for index in range(1, RUN_COUNT + 1)
                ]
            )
        },
        output_root=selected_output,
        forbidden_values=tuple(canaries.values()),
    )
    manifest_schema = json.loads(
        (_repository() / "contracts/host-qualification-bundle-manifest.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(manifest_schema, format_checker=FormatChecker()).validate(manifest)
    _write_artifacts(
        selected_output,
        {"SHA256SUMS.json": canonical_json(manifest).encode("utf-8") + b"\n"},
        forbidden_values=tuple(canaries.values()),
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Pass 13 Codex continuity qualification")
    parser.add_argument("--candidate-wheel", required=True)
    parser.add_argument("--deeplaw-executable", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--codex-command", default="codex")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = execute(
        candidate_wheel=Path(args.candidate_wheel),
        deeplaw_executable=Path(args.deeplaw_executable),
        output_dir=Path(args.output_dir),
        codex_command=args.codex_command,
    )
    print(canonical_json(report))
    return 0 if report["status"] == "executed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
