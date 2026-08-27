"""Current-installed-wheel OpenCode 1.18.16 Pass 16 qualification runner.

The runner is deliberately independent from the historical continuity runners.  It
attests the wheel and runtime selected for this invocation, starts every OpenCode
scenario in a fresh isolated state, and keeps only bounded hashes/counts and the
contracted Provider Capsule transport receipt.  Raw prompts, model output, MCP
payloads, credentials, and host paths remain in memory only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping, MutableSet, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from jsonschema import Draft202012Validator, ValidationError

from benchmarks.hosts import (
    host_preflight_receipt,
    host_process_receipt_v2,
    pass13_evidence,
    pass16_continuity_cases,
    pass17_development_diagnostic,
)
from benchmarks.hosts.pass13_orchestrator import (
    PACKAGE_VERSION,
    QualificationOrchestrator,
    observe_knowledge_support_tools_list,
)
from benchmarks.hosts.pass13_orchestrator import (
    sha256_bytes as _sha256,
)
from benchmarks.hosts.pass13_orchestrator import (
    sha256_file as _sha256_file,
)
from benchmarks.hosts.run_v013_host_task_qualification import (
    HostTaskQualificationError,
    load_exact_candidate_binding,
)

MODEL = "deepseek/deepseek-v4-flash"
VARIANT = "max"
# Compatibility-only fixture value used by non-executing unit seams. Formal
# qualification passes the version from the external frozen identity.
HISTORICAL_OPENCODE_VERSION_FIXTURE = "1.18.16"
OPENCODE_SOURCE_COMMIT = "a3647eb025c7615159d417dcc49fc39fdaeba65b"
TOOL_NAME = "deeplaw_knowledge_knowledge_support"
RUN_COUNT = 3
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
PROVIDER_HARD_LIMIT_BYTES = 65_536
TIMEOUT_SECONDS = 300
_ISOLATED_ROOT_PREFIX = "deeplaw-pass17-opencode-"
_PLUGIN_SOURCE_RELATIVE = Path(
    "deeplaw/opencode_adapter/plugins/deeplaw-native.ts"
)
_PLUGIN_RESOURCE_RELATIVE = Path("opencode_adapter/plugins/deeplaw-native.ts")
_PLUGIN_INSTALLED_RELATIVE = Path(".opencode/plugins/deeplaw-native.ts")
_OPENCODE_PLUGIN_API_PACKAGE = "@opencode-ai/plugin"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GAP_CODE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,99}$")
_GIT_OID = re.compile(r"^[0-9a-f]{40}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")
_CONTROL_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_CONTROL_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+:-]{0,99}$")
_ABSOLUTE_PATH = re.compile(
    rb'(?:^|[\s=:"\'])/(?!/)[A-Za-z0-9._~-]+(?:/[^\s"\'\\]*)?|'
    rb'(?:^|[\s="\'(])[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._$-]+[\\/]'
)
_MAX_BROKER_CONTROL_BYTES = 256 * 1024
_BROKER_CONTROL_READ_CHUNK_BYTES = 16 * 1024
_BROKER_SOURCE_MAX_BYTES = 256 * 1024
_OPENCODE_PACKAGE_MAX_BYTES = 128 * 1024 * 1024
OPENCODE_BROKER_CONTROL_SCHEMA_VERSION = (
    "deeplaw.opencode-owner-external-broker-control/v2"
)
OPENCODE_BROKER_CONTROL_ARGUMENT = "deeplaw-opencode-zero-model-preflight-v2"
OPENCODE_ZERO_MODEL_REQUIRED_SEQUENCE = (
    "GET /global/health",
    "POST /session {}",
    "POST /session/:parent/fork {}",
)
OPENCODE_ZERO_MODEL_OPTIONAL_SEQUENCE = (
    *OPENCODE_ZERO_MODEL_REQUIRED_SEQUENCE,
    "GET /session/:child",
)
OPENCODE_ZERO_MODEL_ALLOWED_ROUTES = (
    "GET /global/health",
    "POST /session",
    "POST /session/:parent/fork",
    "GET /session/:child",
)
_ZERO_MODEL_CONSTRAINTS = {
    "ambient_plugin_allowed": False,
    "event_barrier_timeout_seconds": 30,
    "fork_request_body_sha256": hashlib.sha256(b"{}").hexdigest(),
    "mcp_route_allowed": False,
    "message_route_allowed": False,
    "model_invocation_allowed": False,
    "model_route_allowed": False,
    "provider_request_allowed": False,
    "provider_route_allowed": False,
    "remote_workspace_forwarding_allowed": False,
    "response_release_requires_child_event": True,
    "session_create_body_sha256": hashlib.sha256(b"{}").hexdigest(),
    "share_request_allowed": False,
}
_CONTROL_REQUEST_KEYS = {
    "schema_version",
    "operation",
    "host",
    "task_case",
    "run_id",
    "candidate_binding",
    "run_binding",
    "host_binary",
    "broker_source_sha256",
    "host_identity_sha256",
    "host_identity_source_sha256",
    "challenge",
    "required_sequence",
    "optional_sequence",
    "allowed_routes",
    "zero_model_constraints",
}
_CONTROL_RESPONSE_KEYS = {
    "schema_version",
    "operation",
    "status",
    "observed_sequence",
    "forbidden_route_count",
    "message_route_count",
    "provider_route_count",
    "model_route_count",
    "mcp_route_count",
    "model_invocation_count",
    "provider_request_count",
    "remote_workspace_forward_count",
    "share_request_count",
    "ambient_plugin_count",
    "event_barrier",
    "host_process_receipt",
}
_FORBIDDEN_FIELDS = (
    b'"auth_file"',
    b'"authentication_file"',
    b'"capability_token"',
    b'"grant_id"',
    b'"hidden_reasoning"',
    b'"query_trace"',
    b'"route_identity"',
    b'"task_binding"',
    b'"transcript"',
)
_PROVIDER_ENV_NAME = "DEEPSEEK_API_KEY"
_OWNER_DOTENV_ENV_NAME = "DEEPLAW_OWNER_DOTENV"
MAX_OWNER_DOTENV_BYTES = 64 * 1024
_MODEL_RECEIPT_ENV_NAME = "DEEPLAW_OPENCODE_MODEL_RECEIPT"
_CANARY_NAMES = (
    "DEEPLAW_QUALIFICATION_SECRET_CANARY",
    "DEEPLAW_QUALIFICATION_PATH_CANARY",
    "DEEPLAW_QUALIFICATION_PROVIDER_CANARY",
    "DEEPLAW_CREDENTIAL_PATH_CANARY",
)
EXPECTED_HOST_ENVIRONMENT_NAMES = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "HOME",
        "USERPROFILE",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "APPDATA",
        "LOCALAPPDATA",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
        "OPENCODE_DISABLE_AUTOUPDATE",
        "OPENCODE_DISABLE_CLAUDE_CODE",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS",
        "NO_COLOR",
        "GIT_TERMINAL_PROMPT",
        "DEEPLAW_KNOWLEDGE_VAULT",
        _OWNER_DOTENV_ENV_NAME,
        _MODEL_RECEIPT_ENV_NAME,
        "CI",
        *_CANARY_NAMES,
    }
)
EXPECTED_MCP_ENVIRONMENT_NAMES = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "HOME",
        "USERPROFILE",
        "XDG_CONFIG_HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "NO_COLOR",
        "GIT_TERMINAL_PROMPT",
    }
)
SCENARIOS = pass16_continuity_cases.SCENARIOS
_FINAL_RESPONSE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "next_step", "preserved_decisions", "open_gaps"],
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
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

# The task-case file is frozen before any model output.  Keep the prompt map as
# a compatibility seam for tests, while all runtime lifecycle code obtains the
# complete case (markers, checkpoint text, and binding) from the loader below.
SCENARIO_TASKS = pass16_continuity_cases.lazy_candidate_prompts()


def _context_call_arguments(
    case: Mapping[str, Any], task_binding: Mapping[str, Any]
) -> dict[str, Any]:
    task = case.get("task_case")
    if not isinstance(task, str) or not task:
        raise QualificationError("Host fixture task case is invalid")
    return {
        "operation": "context",
        "task": task,
        "confirm_no_case_data": True,
        "query_plan_version": "6",
        "task_binding": dict(task_binding),
    }


def _candidate_prompt(
    case: Mapping[str, Any],
    _legacy_binding: Mapping[str, Any] | None = None,
    *,
    phase: str = "current",
) -> str:
    """Build the formal Host prompt without route or evaluator material.

    The native OpenCode plugin receives a provider-safe continuity capsule from
    the local Host hook.  The model therefore must not be handed a task
    binding, session identity, route digest, MCP argument object, or path in
    its user prompt.  ``_legacy_binding`` is retained only so old development
    callers do not fail at import time; it is intentionally ignored.
    """

    del _legacy_binding
    task = case.get("task_prompt")
    if not isinstance(task, str) or not task.strip():
        raise QualificationError("Host fixture task prompt is invalid")
    if phase not in {"current", "post_forget"}:
        raise QualificationError("unsupported candidate prompt phase")
    suffix = (
        " After the owner-directed forget, report the resulting gap explicitly."
        if phase == "post_forget"
        else ""
    )
    return (
        f"{task.strip()} Use only the continuity capsule supplied by the native Host "
        "context; do not invoke any tool or request additional context. Return exactly one "
        "JSON object and no Markdown: "
        '{"summary":"string","next_step":"string","preserved_decisions":["string"],'
        '"open_gaps":["string"]}. Use no other keys. Keep every string non-empty and at most '
        f"200 characters; keep each array to one through three items.{suffix}"
    )


class QualificationError(ValueError):
    """Qualification evidence is incomplete, inconsistent, or unsafe."""


def _canonical(value: Any) -> str:
    return pass13_evidence.canonical_json(value)


def _encoded(value: Any) -> bytes:
    return _canonical(value).encode("utf-8")


def _require_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise QualificationError(f"{field} must be one SHA-256 digest")
    return value


def _forbid_sensitive(
    data: bytes,
    forbidden_values: Sequence[str] = (),
    *,
    allow_task_binding: bool = False,
) -> None:
    if _ABSOLUTE_PATH.search(data):
        raise QualificationError("evidence contains an absolute path")
    lowered = data.lower()
    forbidden_fields = (
        tuple(field for field in _FORBIDDEN_FIELDS if field != b'"task_binding"')
        if allow_task_binding
        else _FORBIDDEN_FIELDS
    )
    if any(field in lowered for field in forbidden_fields):
        raise QualificationError("evidence contains a forbidden field")
    _forbid_values(data, forbidden_values)


def _forbid_values(data: bytes, forbidden_values: Sequence[str] = ()) -> None:
    """Reject exact Secret/canary values where URL syntax is otherwise expected."""

    for value in forbidden_values:
        if isinstance(value, str) and value and value.encode("utf-8") in data:
            raise QualificationError("evidence contains a forbidden value")


def build_permission() -> dict[str, str]:
    return {"*": "deny", TOOL_NAME: "allow"}


def build_opencode_config(*, agent_name: str = "qualification") -> dict[str, Any]:
    if agent_name not in {"qualification", "development"}:
        raise QualificationError("OpenCode agent mode is invalid")
    permission = build_permission()
    agent_prompt = (
        "Use only the bounded continuity capsule supplied by the native Host context; "
        "do not invoke any tool or request additional context. Return only the requested "
        "bare JSON response object. Keep every response string non-empty and at most 200 "
        "characters, and each response array to one through three items."
        if agent_name == "qualification"
        else (
            "When the user supplies a complete JSON object for knowledge_support "
            "arguments, copy every key and value unchanged. Do not add, remove, "
            "rename, infer, or rewrite fields. Invoke only knowledge_support, and "
            "return only the requested bare JSON response object. Keep every response "
            "string non-empty and at most 200 characters, and each response array to "
            "one through three items."
        )
    )
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": MODEL,
        "small_model": MODEL,
        "default_agent": agent_name,
        "subagent_depth": 0,
        "enabled_providers": ["deepseek"],
        "provider": {"deepseek": {"options": {"apiKey": "{env:DEEPSEEK_API_KEY}"}}},
        "share": "disabled",
        "autoupdate": False,
        "snapshot": False,
        "instructions": [],
        "permission": permission,
        "agent": {
            agent_name: {
                "description": (
                    "Pass 16 read-only qualification"
                    if agent_name == "qualification"
                    else "Pass 17 source-free development diagnostic"
                ),
                "mode": "primary",
                "model": MODEL,
                "variant": VARIANT,
                "steps": 4,
                "permission": permission,
                "prompt": agent_prompt,
            }
        },
        "mcp": {
            "deeplaw_knowledge": {
                "type": "local",
                "command": [
                    "./deeplaw-closed-mcp",
                ],
                "enabled": True,
                "timeout": 60_000,
            }
        },
    }


def build_host_environment(
    *,
    root: Path,
    opencode_binary: Path,
    node_binary: Path,
    canaries: Mapping[str, str] | None = None,
    owner_dotenv: Path | None = None,
    repository: Path | None = None,
) -> dict[str, str]:
    """Build an allowlisted host environment; ambient values never flow through."""

    root = root.resolve()
    path = os.pathsep.join(
        dict.fromkeys((str(opencode_binary.parent), str(node_binary.parent), os.defpath))
    )
    values = {
        "PATH": path,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(root / "host-home"),
        "USERPROFILE": str(root / "host-home"),
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "XDG_STATE_HOME": str(root / "xdg-state"),
        "TMPDIR": str(root / "tmp"),
        "TMP": str(root / "tmp"),
        "TEMP": str(root / "tmp"),
        "APPDATA": str(root / "appdata"),
        "LOCALAPPDATA": str(root / "localappdata"),
        "OPENCODE_CONFIG": str(root / "opencode.json"),
        "OPENCODE_CONFIG_DIR": str(root / "opencode-config"),
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
        "NO_COLOR": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "DEEPLAW_KNOWLEDGE_VAULT": "vault",
        "CI": "1",
    }
    if owner_dotenv is not None:
        values[_OWNER_DOTENV_ENV_NAME] = str(
            _validate_owner_dotenv(
                owner_dotenv,
                repository=repository or Path(__file__).resolve().parents[2],
            )
        )
    if canaries:
        if set(canaries) != set(_CANARY_NAMES) or not all(
            isinstance(value, str) and value for value in canaries.values()
        ):
            raise QualificationError("qualification canaries are incomplete")
        values.update(canaries)
    if set(values) - EXPECTED_HOST_ENVIRONMENT_NAMES:
        raise QualificationError("host environment contains an unallowlisted name")
    return values


def _validate_owner_dotenv(path: Path | None, *, repository: Path) -> Path:
    """Validate an owner dotenv path using metadata only.

    The owner-only broker, rather than this runner, reads the returned path.
    In particular, this function must never open, read, or hash the file.
    """

    if path is None or not isinstance(path, Path):
        raise QualificationError("OpenCode owner dotenv path is required")
    if not path.is_absolute():
        raise QualificationError("OpenCode owner dotenv path must be absolute")
    try:
        details = path.lstat()
    except OSError as exc:
        raise QualificationError("OpenCode owner dotenv is unavailable") from exc
    parent = path.parent
    while True:
        try:
            parent_details = parent.lstat()
        except OSError as exc:
            raise QualificationError("OpenCode owner dotenv is unavailable") from exc
        if stat.S_ISLNK(parent_details.st_mode):
            raise QualificationError("OpenCode owner dotenv parent must not be a symlink")
        if parent.parent == parent:
            break
        parent = parent.parent
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise QualificationError("OpenCode owner dotenv must be a regular non-symlink file")
    if details.st_nlink != 1:
        raise QualificationError("OpenCode owner dotenv must have one link")
    if details.st_size > MAX_OWNER_DOTENV_BYTES:
        raise QualificationError("OpenCode owner dotenv exceeds its size bound")
    mode = stat.S_IMODE(details.st_mode)
    if (
        os.name != "nt"
        and (
            mode & 0o077
            or not mode & stat.S_IRUSR
            or (hasattr(os, "geteuid") and details.st_uid != os.geteuid())
        )
    ):
        raise QualificationError("OpenCode owner dotenv is not owner-only")
    try:
        resolved = path.resolve(strict=True)
        repository_path = Path(repository).resolve(strict=False)
        resolved_details = resolved.lstat()
    except (OSError, RuntimeError, ValueError) as exc:
        raise QualificationError("OpenCode owner dotenv is unavailable") from exc
    if (
        stat.S_ISLNK(resolved_details.st_mode)
        or not stat.S_ISREG(resolved_details.st_mode)
        or details.st_dev != resolved_details.st_dev
        or details.st_ino != resolved_details.st_ino
        or details.st_nlink != resolved_details.st_nlink
        or details.st_uid != resolved_details.st_uid
        or details.st_size != resolved_details.st_size
        or stat.S_IMODE(details.st_mode) != stat.S_IMODE(resolved_details.st_mode)
    ):
        raise QualificationError("OpenCode owner dotenv changed during validation")
    try:
        resolved.relative_to(repository_path)
    except ValueError:
        return resolved
    raise QualificationError("OpenCode owner dotenv must be outside the repository")


def _build_mcp_environment(root: Path, *, node_binary: Path) -> dict[str, str]:
    path = os.pathsep.join(dict.fromkeys((str(node_binary.parent), os.defpath)))
    values = {
        "PATH": path,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(root / "mcp-home"),
        "USERPROFILE": str(root / "mcp-home"),
        "XDG_CONFIG_HOME": str(root / "mcp-config"),
        "TMPDIR": str(root / "mcp-tmp"),
        "TMP": str(root / "mcp-tmp"),
        "TEMP": str(root / "mcp-tmp"),
        "NO_COLOR": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    if set(values) - EXPECTED_MCP_ENVIRONMENT_NAMES:
        raise QualificationError("MCP environment contains an unallowlisted name")
    return values


def validate_mcp_receipt(
    receipt: Mapping[str, Any],
    *,
    wrapper_path: Path | None = None,
    deeplaw_executable: Path | None = None,
    host_environment: Mapping[str, str] | None = None,
) -> bool:
    """Validate the wrapper's path-free child-environment receipt."""

    names = receipt.get("environment_names")
    blocked = receipt.get("blocked_child_names_present")
    blocked_host = receipt.get("blocked_host_names_present")
    argv = receipt.get("child_argv")
    if (
        not isinstance(names, list)
        or not all(isinstance(name, str) for name in names)
        or len(names) != len(set(names))
        or set(names) != set(EXPECTED_MCP_ENVIRONMENT_NAMES)
        or not isinstance(blocked, list)
        or blocked
        or not isinstance(blocked_host, list)
        or not (
            {
                _PROVIDER_ENV_NAME,
                *_CANARY_NAMES,
                *(
                    (_OWNER_DOTENV_ENV_NAME,)
                    if host_environment is not None
                    and _OWNER_DOTENV_ENV_NAME in host_environment
                    else ()
                ),
            }
            <= set(blocked_host)
        )
        or argv
        != [
            "deeplaw",
            "knowledge",
            "mcp",
            "--closed-environment",
            "--stdio",
        ]
    ):
        raise QualificationError("MCP child environment receipt is not closed")
    for field in ("wrapper_sha256", "child_executable_sha256", "environment_sha256"):
        _require_sha(receipt.get(field), field)
    if wrapper_path is not None and receipt["wrapper_sha256"] != _sha256_file(wrapper_path):
        raise QualificationError("MCP wrapper receipt does not bind its exact executable")
    if (
        deeplaw_executable is not None
        and receipt["child_executable_sha256"] != _sha256_file(deeplaw_executable)
    ):
        raise QualificationError("MCP wrapper receipt does not bind installed DeepLaw")
    if host_environment is not None:
        expected_environment = {
            name: host_environment[name]
            for name in sorted(EXPECTED_MCP_ENVIRONMENT_NAMES)
            if name in host_environment
        }
        if receipt["environment_sha256"] != _sha256(_encoded(expected_environment)):
            raise QualificationError("MCP child environment hash is inconsistent")
    return True


def _write_mcp_wrapper(
    path: Path,
    *,
    deeplaw_executable: Path,
    receipt_path: Path,
    node_binary: Path,
) -> None:
    """Create a transient wrapper that strips provider/auth material."""

    child_environment = sorted(EXPECTED_MCP_ENVIRONMENT_NAMES)
    blocked_names = sorted(
        {
            _PROVIDER_ENV_NAME,
            _OWNER_DOTENV_ENV_NAME,
            *_CANARY_NAMES,
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "GITHUB_TOKEN",
        }
    )
    script = f"""#!/usr/bin/env python3
import hashlib
import json
import os
import pathlib

allow = {child_environment!r}
blocked = {blocked_names!r}
receipt_path = pathlib.Path({str(receipt_path)!r})
child_executable = pathlib.Path({str(deeplaw_executable)!r})
child_environment = {{name: os.environ[name] for name in allow if name in os.environ}}
blocked_host_present = sorted(name for name in blocked if name in os.environ)
blocked_child_present = sorted(name for name in blocked if name in child_environment)
receipt_path.write_text(json.dumps({{
    "environment_names": sorted(child_environment),
    "blocked_host_names_present": blocked_host_present,
    "blocked_child_names_present": blocked_child_present,
    "child_argv": [
        "deeplaw", "knowledge", "mcp", "--closed-environment", "--stdio"
    ],
    "wrapper_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
    "child_executable_sha256": hashlib.sha256(child_executable.read_bytes()).hexdigest(),
    "environment_sha256": hashlib.sha256(
        json.dumps(child_environment, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest(),
}}, sort_keys=True, separators=(",", ":")) + "\\n", encoding="utf-8")
if blocked_child_present:
    raise SystemExit(91)
os.execve({str(deeplaw_executable)!r}, [
    {str(deeplaw_executable)!r}, "knowledge", "mcp", "--closed-environment", "--stdio"
], child_environment)
"""
    path.write_text(script, encoding="utf-8")
    if os.name != "nt":
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    del node_binary


def process_creation_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))}
    return {"start_new_session": True}


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate the process group we created, failing closed if unavailable."""

    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = shutil.which("taskkill")
        if taskkill is None:
            process.kill()
            return
        result = subprocess.run(
            [taskkill, "/F", "/T", "/PID", str(process.pid)],
            env={"PATH": os.defpath},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        process.kill()


def _run_bounded_process(
    argv: Sequence[str | Path],
    *,
    environment: Mapping[str, str],
    cwd: Path,
    input_bytes: bytes = b"",
    timeout: float = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not argv:
        raise QualificationError("bounded process argv is empty")
    started = time.monotonic()
    process = subprocess.Popen(
        [str(item) for item in argv],
        cwd=str(cwd),
        env=dict(environment),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **process_creation_options(),
    )
    timed_out = False
    try:
        stdout, stderr = process.communicate(input=input_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    if len(stdout) > MAX_OUTPUT_BYTES or len(stderr) > MAX_OUTPUT_BYTES:
        _terminate_process_tree(process)
        return {
            "returncode": process.returncode,
            "stdout": b"",
            "stderr": b"",
            "elapsed_ms": elapsed_ms,
            "timed_out": timed_out,
            "output_overflow": True,
        }
    return {
        "returncode": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "elapsed_ms": elapsed_ms,
        "timed_out": timed_out,
        "output_overflow": False,
    }


def parse_model_inventory(stdout: bytes, *, returncode: int) -> dict[str, Any]:
    if not isinstance(stdout, bytes):
        raise QualificationError("model inventory bytes are invalid")
    receipt = {
        "checked": True,
        "selected_present": MODEL in stdout.decode("utf-8", errors="replace").splitlines(),
        "raw_sha256": _sha256(stdout),
        "raw_bytes": len(stdout),
    }
    if returncode != 0 or not receipt["selected_present"]:
        raise QualificationError("selected model is not present in the exact model inventory")
    return receipt


def validate_token_usage(usage: Mapping[str, Any]) -> dict[str, int | str]:
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    normalized: dict[str, int | str] = {}
    for field in fields:
        value = usage.get(field)
        if (isinstance(value, str) and value == "unreported") or (
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
        ):
            normalized[field] = value
        else:
            raise QualificationError(f"token usage field {field} is invalid")
    numeric = [normalized[field] for field in fields]
    if all(isinstance(value, int) for value in numeric) and normalized["total_tokens"] != (
            int(normalized["input_tokens"])
            + int(normalized["cached_input_tokens"])
            + int(normalized["cache_write_input_tokens"])
            + int(normalized["output_tokens"])
            + int(normalized["reasoning_output_tokens"])
    ):
        raise QualificationError("token usage arithmetic is inconsistent")
    return normalized


def parse_availability_result(
    *,
    stdout: bytes,
    returncode: int,
    elapsed_ms: int,
    timed_out: bool = False,
    output_overflow: bool = False,
    stderr: bytes = b"",
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    if returncode != 0 or timed_out or output_overflow or stderr:
        raise QualificationError("DeepSeek availability process did not complete cleanly")
    normalized_usage = _require_actual_usage(
        _analyze_availability_events(stdout, forbidden_values=forbidden_values)
    )
    return {
        "status": "available",
        "raw_sha256": _sha256(stdout),
        "raw_bytes": len(stdout),
        "elapsed_ms": max(0, int(elapsed_ms)),
        "input_tokens": normalized_usage["input_tokens"],
        "cached_input_tokens": normalized_usage["cached_input_tokens"],
        "cache_write_input_tokens": normalized_usage["cache_write_input_tokens"],
        "output_tokens": normalized_usage["output_tokens"],
        "reasoning_output_tokens": normalized_usage["reasoning_output_tokens"],
        "total_tokens": normalized_usage["total_tokens"],
    }


def _strict_json(value: str | bytes) -> Any:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise QualificationError("OpenCode event is not valid JSON") from exc
    return parsed


def _event_identity(event: Mapping[str, Any], *names: str) -> str | None:
    part = event.get("part")
    for source in (event, part):
        if isinstance(source, Mapping):
            for name in names:
                value = source.get(name)
                if isinstance(value, str) and value:
                    return value
    return None


def _sum_usages(usages: Sequence[Mapping[str, Any]]) -> dict[str, int | str]:
    if not usages:
        raise QualificationError("OpenCode token usage is missing")
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    if not all(all(isinstance(usage.get(field), int) for field in fields) for usage in usages):
        return {field: "unreported" for field in fields}
    return validate_token_usage(
        {field: sum(int(usage[field]) for usage in usages) for field in fields}
    )


def _require_actual_usage(usage: Mapping[str, Any]) -> dict[str, int]:
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    if not all(
        isinstance(usage.get(field), int) and not isinstance(usage.get(field), bool)
        for field in fields
    ):
        raise QualificationError("actual OpenCode provider token usage is missing")
    normalized = validate_token_usage({field: int(usage[field]) for field in fields})
    return {field: int(normalized[field]) for field in fields}


def _analyze_availability_events(
    data: bytes, *, forbidden_values: Sequence[str] = ()
) -> dict[str, int | str]:
    if not isinstance(data, bytes) or not data or len(data) > MAX_OUTPUT_BYTES:
        raise QualificationError("availability output exceeds its bound")
    _forbid_sensitive(data, forbidden_values)
    usages: list[dict[str, int | str]] = []
    text_count = 0
    for line in data.splitlines():
        if not line.strip():
            continue
        event = _strict_json(line)
        if not isinstance(event, Mapping):
            raise QualificationError("availability event is invalid")
        event_type = event.get("type")
        if event_type == "step_finish":
            part = event.get("part")
            if not isinstance(part, Mapping):
                raise QualificationError("availability usage event is invalid")
            usages.append(_normalize_usage(part))
        elif event_type == "text":
            part = event.get("part")
            text = part.get("text") if isinstance(part, Mapping) else None
            if not isinstance(text, str) or not text.strip():
                raise QualificationError("availability text event is invalid")
            text_count += 1
        elif event_type in {"step_start", "reasoning"}:
            continue
        else:
            raise QualificationError("availability probe emitted an unexpected event")
    if not usages:
        raise QualificationError("availability usage receipt is missing")
    if text_count != 1 or len(usages) != 1:
        raise QualificationError("availability probe did not produce one bounded model turn")
    return _sum_usages(usages)


def _native_provider_capsule(state: Mapping[str, Any]) -> tuple[Mapping[str, Any], str]:
    """Validate the exact Provider text projection exposed by OpenCode 1.18.16.

    OpenCode's MCP adapter returns the complete ``CallToolResult`` internally,
    then stores and emits only the joined MCP text content in
    ``tool_use.part.state.output``.  The native JSON event therefore cannot
    attest unobserved ``structuredContent`` bytes.
    """

    metadata = state.get("metadata")
    output = state.get("output")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("truncated") is not False
        or not isinstance(output, str)
        or not output
    ):
        raise QualificationError("OpenCode native Provider projection is invalid")
    raw = output.encode("utf-8")
    if len(raw) > PROVIDER_HARD_LIMIT_BYTES:
        raise QualificationError("OpenCode native Provider projection exceeds its bound")
    try:
        value = _strict_json(output)
    except QualificationError as exc:
        raise QualificationError("OpenCode native Provider projection is invalid") from exc
    if not isinstance(value, Mapping) or output != _canonical(value):
        raise QualificationError("OpenCode native Provider projection is not canonical")
    try:
        contract = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "contracts"
                / "provider-knowledge-capsule.v2.schema.json"
            ).read_text(encoding="utf-8")
        )
        definitions = contract["$defs"]
        capsule_contract = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **definitions["capsule"],
            "$defs": definitions,
        }
        Draft202012Validator(capsule_contract).validate(value)
    except (KeyError, OSError, TypeError, ValueError, ValidationError) as exc:
        raise QualificationError("OpenCode native Provider projection is invalid") from exc
    return value, output


def _native_tool_arguments(
    event: Mapping[str, Any],
    *,
    expected_task_binding: Mapping[str, Any],
    expected_task: str | None = None,
) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    part = event.get("part")
    if not isinstance(part, Mapping):
        raise QualificationError("tool event part is invalid")
    state = part.get("state")
    if not isinstance(state, Mapping):
        raise QualificationError("tool event state is invalid")
    call_id = part.get("callID") or part.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        raise QualificationError("tool event call id is missing")
    arguments = state.get("input", {})
    if (
        not isinstance(arguments, Mapping)
        or arguments.get("operation") != "context"
        or not isinstance(arguments.get("task"), str)
        or not arguments.get("task")
        or (expected_task is not None and arguments.get("task") != expected_task)
        or arguments.get("confirm_no_case_data") is not True
        or arguments.get("query_plan_version") != "6"
        or arguments.get("task_binding") != dict(expected_task_binding)
        or set(arguments)
        != {
            "operation",
            "task",
            "confirm_no_case_data",
            "query_plan_version",
            "task_binding",
        }
    ):
        raise QualificationError("MCP call lacks the exact public v6 context arguments")
    return part, arguments, call_id


def _native_tool_observation(
    event: Mapping[str, Any],
    capsule: Mapping[str, Any],
    provider_text: str,
    *,
    expected_task_binding: Mapping[str, Any],
    expected_task: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _part, arguments, call_id = _native_tool_arguments(
        event,
        expected_task_binding=expected_task_binding,
        expected_task=expected_task,
    )
    provider_bytes = provider_text.encode("utf-8")
    statements = capsule.get("statements")
    gaps = capsule.get("gaps")
    evidence = capsule.get("evidence", [])
    if not isinstance(statements, list) or not isinstance(gaps, list):
        raise QualificationError("Provider Capsule statements or gaps are invalid")
    if not isinstance(evidence, list):
        raise QualificationError("Provider Capsule evidence is invalid")
    evidence_keys = [
        (
            item.get("source_revision_id"),
            item.get("fragment_id"),
            item.get("content_sha256"),
        )
        for item in evidence
        if isinstance(item, Mapping)
    ]
    duplicate_evidence_count = len(evidence_keys) - len(set(evidence_keys))
    observation = {
        "call_id_sha256": _sha256(call_id.encode("utf-8")),
        "server": "deeplaw",
        "tool_name": "knowledge_support",
        "status": "completed",
        "arguments_sha256": _sha256(_encoded(arguments)),
        "arguments_bytes": len(_encoded(arguments)),
        "result_sha256": _sha256(provider_bytes),
        "result_bytes": len(provider_bytes),
    }
    payload = {
        "operation": "context",
        "provider_bytes": len(provider_bytes),
        "provider_sha256": _sha256(provider_bytes),
        # OpenCode's native JSON event exposes only the exact MCP text
        # projection.  Null is evidence that structuredContent was not
        # observed, not an estimate or reconstruction.
        "structured_output_bytes": None,
        "structured_output_sha256": None,
        "delivery_match": True,
        "write_performed": False,
        "statement_count": len(statements),
        "gap_count": len(gaps),
        "gap_codes": sorted(
            {
                gap["code"]
                for gap in gaps
                if isinstance(gap, Mapping) and isinstance(gap.get("code"), str)
            }
        ),
        "relevant_chars": 0,
        "context_chars": len(provider_text),
        "relevant_chars_context_chars": 0.0 if provider_text else None,
        "evidence_count": len(evidence_keys),
        "duplicate_evidence_count": duplicate_evidence_count,
        "duplicate_evidence_rate": (
            duplicate_evidence_count / len(evidence_keys) if evidence_keys else None
        ),
    }
    return observation, payload


def _native_hook_observation(
    capsule: Mapping[str, Any], provider_text: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Measure the provider-safe capsule injected by the native Host hook.

    The formal OpenCode path deliberately has no ``knowledge_support`` tool
    event.  The plugin resolves the same public DeepLaw route in its local
    hook, while the runner independently resolves it below and binds this
    observation to the exact canonical bytes returned by that public seam.
    """

    statements = capsule.get("statements")
    gaps = capsule.get("gaps")
    conflicts = capsule.get("conflicts")
    if not all(isinstance(value, list) for value in (statements, gaps, conflicts)):
        raise QualificationError("native Host continuity capsule is invalid")
    provider_bytes = provider_text.encode("utf-8")
    evidence_count = 0
    duplicate_evidence_count = 0
    observation = {
        "call_id_sha256": _sha256(b"opencode-native-continuity-hook"),
        "server": "deeplaw",
        "tool_name": "native_host_continuity_hook",
        "status": "completed",
        "arguments_sha256": None,
        "arguments_bytes": 0,
        "result_sha256": _sha256(provider_bytes),
        "result_bytes": len(provider_bytes),
    }
    payload = {
        "operation": "resolve-host-continuity",
        "provider_bytes": len(provider_bytes),
        "provider_sha256": _sha256(provider_bytes),
        "structured_output_bytes": None,
        "structured_output_sha256": None,
        "delivery_match": True,
        "write_performed": False,
        "statement_count": len(statements),
        "gap_count": len(gaps),
        "gap_codes": sorted(
            {
                item["code"]
                for item in gaps
                if isinstance(item, Mapping) and isinstance(item.get("code"), str)
            }
        ),
        "relevant_chars": 0,
        "context_chars": len(provider_text),
        "relevant_chars_context_chars": 0.0 if provider_text else None,
        "evidence_count": evidence_count,
        "duplicate_evidence_count": duplicate_evidence_count,
        "duplicate_evidence_rate": None,
        "conflict_count": len(conflicts),
    }
    return observation, payload


def _analyze_native_safe_reads(
    observations: Sequence[Mapping[str, Any]], payloads: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    if len(observations) not in {1, 2} or len(payloads) != len(observations):
        raise QualificationError("qualification requires one or two safe read calls")
    call_ids = [observation.get("call_id_sha256") for observation in observations]
    if len(set(call_ids)) != len(call_ids):
        raise QualificationError("safe read call identities must be unique")
    if len(payloads) == 2 and payloads[0].get("gap_count") == 0:
        raise QualificationError("bounded retry requires an insufficient first Provider Capsule")
    native_delivery = all(
        observation.get("tool_name") == "native_host_continuity_hook"
        for observation in observations
    )
    return {
        # A native Host delivery is not a Provider-side tool invocation.
        "call_count": 0 if native_delivery else len(observations),
        "first_call_valid": True,
        "bounded_retry_used": not native_delivery and len(observations) == 2,
        "safe_read_operations": [
            str(payload.get("operation", "context"))
            for payload in payloads
            if isinstance(payload, Mapping)
        ],
        "provider_payloads": [dict(payload) for payload in payloads],
    }


def _bind_native_relevant_chars(
    safe_read: Mapping[str, Any],
    provider_texts: Sequence[str],
    relevant_text: Sequence[str],
) -> dict[str, Any]:
    payloads = safe_read.get("provider_payloads")
    if not isinstance(payloads, list) or len(payloads) != len(provider_texts):
        raise QualificationError("Provider payload relevance inputs are inconsistent")
    markers = tuple(
        dict.fromkeys(item for item in relevant_text if isinstance(item, str) and item)
    )
    measured: list[dict[str, Any]] = []
    for payload, provider_text in zip(payloads, provider_texts, strict=True):
        if not isinstance(payload, Mapping) or not isinstance(provider_text, str):
            raise QualificationError("Provider relevance input is invalid")
        provider_bytes = provider_text.encode("utf-8")
        if (
            payload.get("provider_sha256") != _sha256(provider_bytes)
            or payload.get("provider_bytes") != len(provider_bytes)
            or payload.get("context_chars") != len(provider_text)
        ):
            raise QualificationError("Provider relevance text does not match its receipt")
        covered: set[int] = set()
        for marker in markers:
            start = 0
            while True:
                position = provider_text.find(marker, start)
                if position < 0:
                    break
                covered.update(range(position, position + len(marker)))
                start = position + max(1, len(marker))
        relevant_chars = len(covered)
        measured.append(
            {
                **dict(payload),
                "relevant_chars": relevant_chars,
                "relevant_chars_context_chars": (
                    relevant_chars / len(provider_text) if provider_text else None
                ),
            }
        )
    return {**dict(safe_read), "provider_payloads": measured}


def _contains_marker(value: Any, marker: str) -> bool:
    if isinstance(value, str):
        return marker in value
    if isinstance(value, Mapping):
        return any(_contains_marker(item, marker) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_marker(item, marker) for item in value)
    return False


def _normalize_usage(part: Mapping[str, Any]) -> dict[str, int | str]:
    raw = part.get("tokens")
    if not isinstance(raw, Mapping):
        raise QualificationError("OpenCode token usage is missing")
    values = {
        "input_tokens": raw.get("input", raw.get("input_tokens")),
        "cached_input_tokens": (
            raw.get("cache", {}).get("read")
            if isinstance(raw.get("cache"), Mapping)
            else raw.get("cache_read", raw.get("cached_input_tokens", 0))
        ),
        "cache_write_input_tokens": (
            raw.get("cache", {}).get("write")
            if isinstance(raw.get("cache"), Mapping)
            else raw.get("cache_write", raw.get("cache_write_input_tokens", 0))
        ),
        "output_tokens": raw.get("output", raw.get("output_tokens")),
        "reasoning_output_tokens": raw.get("reasoning", raw.get("reasoning_output_tokens", 0)),
        "total_tokens": raw.get("total", raw.get("total_tokens")),
    }
    return validate_token_usage(values)


def analyze_opencode_events(
    data: bytes,
    *,
    expected_task_binding: Mapping[str, Any] | None = None,
    expected_task: str | None = None,
    continuity_capsule: Mapping[str, Any] | None = None,
    continuity_text: str | None = None,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Parse only the bounded event fields needed for Pass 16 qualification."""

    if not isinstance(data, bytes) or len(data) > MAX_OUTPUT_BYTES:
        raise QualificationError("OpenCode output exceeds the bounded limit")
    # Formal Host turns use the native continuity hook and must not expose an
    # MCP task-binding argument.  ``allow_task_binding`` remains limited to
    # historical analyzer fixtures that explicitly provide the legacy binding.
    _forbid_sensitive(
        data,
        forbidden_values,
        allow_task_binding=expected_task_binding is not None,
    )
    if continuity_capsule is not None:
        if continuity_text is None or continuity_text != _canonical(continuity_capsule):
            raise QualificationError("native Host continuity bytes are not canonical")
        capsule_contract_path = (
            Path(__file__).resolve().parents[2]
            / "contracts"
            / "host-continuity-capsule.v1.schema.json"
        )
        try:
            capsule_contract = json.loads(capsule_contract_path.read_text(encoding="utf-8"))
            Draft202012Validator(capsule_contract).validate(dict(continuity_capsule))
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            raise QualificationError("native Host continuity capsule is invalid") from exc
        _forbid_sensitive(
            continuity_text.encode("utf-8"),
            forbidden_values,
        )
    observations: list[dict[str, Any]] = []
    payloads: list[dict[str, Any]] = []
    provider_values: list[Mapping[str, Any]] = []
    provider_texts: list[str] = []
    sanitized: list[dict[str, Any]] = []
    usages: list[dict[str, int | str]] = []
    final_response_sha256: str | None = None
    final_response_bytes = 0
    final_value: Mapping[str, Any] | None = None
    session_ids: set[str] = set()
    message_ids: set[str] = set()
    text_count = 0
    for line in data.splitlines():
        if not line.strip():
            continue
        event = _strict_json(line)
        if not isinstance(event, Mapping):
            raise QualificationError("OpenCode event is not an object")
        event_type = event.get("type")
        if session_id := _event_identity(event, "sessionID", "sessionId", "session_id"):
            session_ids.add(session_id)
        if message_id := _event_identity(event, "messageID", "messageId", "message_id"):
            message_ids.add(message_id)
        if event_type == "tool_use":
            if continuity_capsule is not None:
                raise QualificationError(
                    "knowledge_support tool call is not allowed for native continuity"
                )
            part = event.get("part")
            if not isinstance(part, Mapping):
                raise QualificationError("tool event part is invalid")
            tool = part.get("tool")
            if tool != TOOL_NAME:
                raise QualificationError("disallowed tool was invoked")
            state = part.get("state")
            if not isinstance(state, Mapping):
                raise QualificationError("tool event state is invalid")
            # Validate public tool arguments even when OpenCode reports an
            # error state.  This separates model call-shape failures from an
            # execution failure without reading or retaining the error text.
            if expected_task_binding is None:
                raise QualificationError("legacy MCP task binding is missing")
            _native_tool_arguments(
                event,
                expected_task_binding=expected_task_binding,
                expected_task=expected_task,
            )
            if state.get("status") != "completed":
                raise QualificationError("tool call did not complete")
            capsule, provider_text = _native_provider_capsule(state)
            observation, payload = _native_tool_observation(
                event,
                capsule,
                provider_text,
                expected_task_binding=expected_task_binding,
                expected_task=expected_task,
            )
            observations.append(observation)
            payloads.append(payload)
            provider_values.append(capsule)
            provider_texts.append(provider_text)
            sanitized.append(
                {
                    "type": "tool_use",
                    "tool": TOOL_NAME,
                    "status": "completed",
                    "call_id_sha256": observations[-1]["call_id_sha256"],
                    "result_sha256": observations[-1]["result_sha256"],
                    "result_bytes": observations[-1]["result_bytes"],
                }
            )
        elif event_type == "step_finish":
            part = event.get("part")
            if not isinstance(part, Mapping):
                raise QualificationError("step finish part is invalid")
            usage = _normalize_usage(part)
            usages.append(usage)
            sanitized.append({"type": "step_finish", "usage": usage})
        elif event_type == "text":
            part = event.get("part")
            text = part.get("text") if isinstance(part, Mapping) else None
            if not isinstance(text, str):
                raise QualificationError("final response text is invalid")
            final_raw = text.encode("utf-8")
            if len(final_raw) > MAX_OUTPUT_BYTES:
                raise QualificationError("final response exceeds the bounded limit")
            final_response_sha256 = _sha256(final_raw)
            final_response_bytes = len(final_raw)
            try:
                parsed = _strict_json(text)
            except QualificationError as exc:
                stripped = text.strip()
                if stripped.startswith("```"):
                    code = "final response used a code fence"
                elif not stripped.startswith("{"):
                    code = "final response is not a JSON object"
                else:
                    code = "final response JSON syntax is invalid"
                raise QualificationError(code) from exc
            try:
                Draft202012Validator(_FINAL_RESPONSE_SCHEMA).validate(parsed)
            except ValidationError as exc:
                code = {
                    "required": "final response omitted a required field",
                    "additionalProperties": "final response added an unsupported field",
                    "type": "final response field type is invalid",
                    "minLength": "final response field bound is invalid",
                    "maxLength": "final response field bound is invalid",
                    "maxItems": "final response field bound is invalid",
                }.get(str(exc.validator), "final response contract is invalid")
                raise QualificationError(code) from exc
            final_value = parsed
            text_count += 1
            sanitized.append(
                {
                    "type": "text",
                    "sha256": final_response_sha256,
                    "bytes": final_response_bytes,
                }
            )
        elif event_type in {"step_start", "reasoning"}:
            # Never retain reasoning or prompt content.
            sanitized.append({"type": str(event_type)})
        elif event_type == "error":
            raise QualificationError("OpenCode emitted an error event")
        else:
            raise QualificationError("unknown OpenCode event type")
    if text_count != 1 or final_value is None:
        raise QualificationError("OpenCode must emit exactly one bounded final response")
    if continuity_capsule is not None:
        if observations:
            raise QualificationError(
                "native continuity turn unexpectedly included a tool observation"
            )
        if continuity_text is None:
            raise QualificationError("native Host continuity text is missing")
        hook_observation, hook_payload = _native_hook_observation(
            continuity_capsule, continuity_text
        )
        observations.append(hook_observation)
        payloads.append(hook_payload)
        provider_values.append(continuity_capsule)
        provider_texts.append(continuity_text)
        sanitized.append(
            {
                "type": "native_host_continuity",
                "status": "completed",
                "result_sha256": hook_observation["result_sha256"],
                "result_bytes": hook_observation["result_bytes"],
            }
        )
    if len(session_ids) != 1 or not message_ids:
        raise QualificationError("OpenCode session or message identity is missing")
    usage = _require_actual_usage(_sum_usages(usages))
    safe_read = _analyze_native_safe_reads(observations, payloads)
    if len(safe_read["provider_payloads"]) > 1 and any(
        int(payload["provider_bytes"]) > PROVIDER_HARD_LIMIT_BYTES // 2
        for payload in safe_read["provider_payloads"]
    ):
        raise QualificationError("repeated large Provider payloads are not bounded")
    sanitized_bytes = b"".join((_canonical(row) + "\n").encode("utf-8") for row in sanitized)
    _forbid_sensitive(sanitized_bytes, forbidden_values)
    return {
        "safe_read": safe_read,
        "usage": usage,
        "final_response_sha256": final_response_sha256,
        "final_response_bytes": final_response_bytes,
        "final_value": final_value,
        # Provider values remain in memory for marker/outcome evaluation only;
        # no caller may place this field in retained report/artifact objects.
        "provider_values": provider_values,
        # Exact native text projections remain in memory only so task-relevant
        # character accounting can bind to the already measured hashes.
        "provider_texts": provider_texts,
        "thread_id_sha256": _sha256(next(iter(session_ids)).encode("utf-8")),
        "turn_id_sha256": _sha256(_encoded(sorted(message_ids))),
        "sanitized_events": sanitized_bytes,
    }


def validate_ledger_heads(before: str, after: str) -> bool:
    _require_sha(before, "ledger_audit_head_before")
    _require_sha(after, "ledger_audit_head_after")
    if before != after:
        raise QualificationError("ledger audit head changed during read-only Host run")
    return True


def validate_source_forget_receipt(receipt: Mapping[str, Any]) -> bool:
    required = {
        "target_type": "source_revision",
        "current_retrieval_eligible": False,
        "current_admission_eligible": False,
        "original_bytes_retained": True,
        "history_retained": True,
        "audit_history_retained": True,
        "bytes_deleted": False,
        "canonical_bytes_deleted": False,
    }
    if not isinstance(receipt, Mapping) or any(
        receipt.get(field) != expected for field, expected in required.items()
    ):
        raise QualificationError("source forget receipt does not prove retention")
    wording = receipt.get("message") or receipt.get("reason")
    if wording is not None:
        if not isinstance(wording, str):
            raise QualificationError("source forget receipt wording is invalid")
        lowered = wording.lower()
        if "withdraw" not in lowered or "retain" not in lowered:
            raise QualificationError("source forget receipt wording is incomplete")
    return True


def retain_artifact(
    path: Path,
    data: bytes,
    *,
    output_root: Path,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    try:
        return pass13_evidence.write_retained_artifact(
            path,
            data,
            output_root=output_root,
            forbidden_values=forbidden_values,
        )
    except pass13_evidence.EvidenceValidationError as exc:
        raise QualificationError(str(exc)) from exc


def _write_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    output_root: Path,
    forbidden_values: Sequence[str],
) -> dict[str, Any]:
    return retain_artifact(
        path,
        (_canonical(value) + "\n").encode("utf-8"),
        output_root=output_root,
        forbidden_values=forbidden_values,
    )


def _ledger_head(
    deeplaw_executable: Path,
    vault: Path,
    *,
    environment: Mapping[str, str],
    cwd: Path,
) -> str:
    """Commit both durable audit planes into one path-free observation."""

    heads: dict[str, str] = {}
    for label, arguments in (
        ("knowledge", ("knowledge", "inspect", "--vault", vault)),
        ("autonomous", ("knowledge", "autonomy", "status", "--vault", vault)),
    ):
        result = _run_bounded_process(
            [deeplaw_executable, *arguments],
            environment=environment,
            cwd=cwd,
            timeout=30,
        )
        if result["returncode"] != 0 or result["timed_out"] or result["output_overflow"]:
            raise QualificationError("installed Vault status command failed")
        value = _strict_json(result["stdout"])
        head = value.get("audit_head") if isinstance(value, Mapping) else None
        if not isinstance(head, str) or _SHA256.fullmatch(head) is None:
            raise QualificationError("installed Vault status omitted its audit head")
        heads[label] = head
    return _sha256(_encoded(heads))


def _extract_value(value: Any, *keys: str) -> Any:
    """Find one scalar identity in a public CLI response without retaining it."""

    if isinstance(value, Mapping):
        for key in keys:
            selected = value.get(key)
            if selected is not None:
                return selected
        for nested in value.values():
            selected = _extract_value(nested, *keys)
            if selected is not None:
                return selected
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            selected = _extract_value(nested, *keys)
            if selected is not None:
                return selected
    return None


def _run_public_cli(
    deeplaw_executable: Path,
    arguments: Sequence[str | Path],
    *,
    vault: Path,
    environment: Mapping[str, str],
    cwd: Path,
) -> Mapping[str, Any]:
    """Run one bounded owner/public CLI command and keep its JSON in memory only."""

    result = _run_bounded_process(
        [deeplaw_executable, *arguments],
        environment=environment,
        cwd=cwd,
        timeout=TIMEOUT_SECONDS,
    )
    if (
        result["returncode"] != 0
        or result["timed_out"]
        or result["output_overflow"]
        or result["stderr"]
    ):
        raise QualificationError("installed public DeepLaw CLI command failed")
    value = _strict_json(result["stdout"])
    if not isinstance(value, Mapping):
        raise QualificationError("installed public DeepLaw CLI did not return one object")
    # ``vault`` is intentionally an argument to make every invocation bind to
    # the fresh scenario state; it is never copied to an artifact.
    del vault
    return value


def _run_sink_request(
    deeplaw_executable: Path,
    *,
    vault: Path,
    grant_id: str,
    request: Mapping[str, Any],
    environment: Mapping[str, str],
    cwd: Path,
) -> Mapping[str, Any]:
    request_path = cwd / "pass16-sink-request.json"
    request_path.write_text(_canonical(dict(request)) + "\n", encoding="utf-8")
    try:
        return _run_public_cli(
            deeplaw_executable,
            (
                "knowledge",
                "sink",
                "apply",
                "--vault",
                vault,
                "--grant-id",
                grant_id,
                "--request",
                request_path,
            ),
            vault=vault,
            environment=environment,
            cwd=cwd,
        )
    finally:
        request_path.unlink(missing_ok=True)


def _checkpoint_body(
    case: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    *,
    route: str,
    binding: Mapping[str, Any],
) -> str:
    """Encode governed checkpoint evidence; prompts never carry this body."""

    lines = [
        f"GOAL: Complete the Pass 16 {case['scenario']} qualification.",
        f"CONFIRMED_DECISION: {checkpoint['decision']}",
        "CONSTRAINT: Use only governed read-only context and no case data.",
        f"VERIFIED_FACT: {checkpoint['verified_fact']}",
        f"OPEN_GAP: {checkpoint['open_gap']}",
        f"NEXT_ACTION: {checkpoint['next_action']}",
        f"ROUTE_MARKER: {checkpoint['marker']}",
        f"ROUTE_KIND: {route}",
        f"BINDING_DIGEST: {binding['binding_sha256']}",
    ]
    post_forget = case.get("post_forget_requirement")
    if route == "current" and isinstance(post_forget, Mapping):
        lines.append(f"FORGET_MARKER: {post_forget['forgotten_marker']}")
    return "\n".join(lines)


def _create_git_task_repository(
    root: Path,
    *,
    task_line: str,
    development: bool = False,
) -> tuple[Path, Path, dict[str, Any], dict[str, Any]]:
    """Create a real temporary Git task repo and an independent concurrent worktree."""

    # DeepLaw's public Vault resolver rejects symlinked ancestors.  Resolve the
    # runner-owned temporary root once so the public task seams receive the
    # canonical path while no path is retained in qualification evidence.
    root = root.resolve()
    repository = root / "task-repository"
    repository.mkdir(parents=True, exist_ok=True)

    def git(*arguments: str, cwd: Path = repository) -> str:
        result = _run_bounded_process(
            ["git", *arguments],
            environment={"PATH": os.defpath, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"},
            cwd=cwd,
            timeout=30,
        )
        if result["returncode"] != 0 or result["stderr"]:
            raise QualificationError("temporary Git task repository command failed")
        return result["stdout"].decode("utf-8", errors="strict").strip()

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
    (repository / ".gitignore").write_text(
        "deeplaw-closed-mcp\nvault/\n", encoding="utf-8"
    )
    git("add", "TASK.md", ".gitignore")
    git(
        "commit",
        "--quiet",
        "-m",
        "initial development diagnostic" if development else "initial qualification task",
    )
    concurrent = root / "concurrent-worktree"
    git("worktree", "add", "--quiet", "--detach", concurrent)
    primary_binding = pass16_continuity_cases.git_binding(repository, task_line=task_line)
    concurrent_binding = pass16_continuity_cases.git_binding(
        repository, task_line=task_line, worktree=concurrent
    )
    if (
        primary_binding["project_sha256"] != concurrent_binding["project_sha256"]
        or primary_binding["task_lineage_sha256"]
        != concurrent_binding["task_lineage_sha256"]
        or primary_binding["repository_sha256"]
        != concurrent_binding["repository_sha256"]
        or primary_binding["worktree_sha256"]
        == concurrent_binding["worktree_sha256"]
    ):
        raise QualificationError("concurrent worktree binding is not isolated correctly")
    if primary_binding["base_revision"] != concurrent_binding["base_revision"]:
        raise QualificationError("concurrent worktree does not bind the same base revision")
    if primary_binding["worktree_sha256"] == concurrent_binding["worktree_sha256"]:
        raise QualificationError("concurrent worktree binding is not independent")
    return repository, concurrent, primary_binding, concurrent_binding


def _seed_continuity_fixture(
    deeplaw_executable: Path,
    *,
    vault: Path,
    case: Mapping[str, Any],
    primary_binding: Mapping[str, Any],
    concurrent_binding: Mapping[str, Any],
    concurrent_workspace: Path,
    environment: Mapping[str, str],
    cwd: Path,
) -> dict[str, Any]:
    """Seed continuity through the public task driver and explicit grants.

    Qualification must exercise the same owner/public task seams used by a
    real Host.  In particular, no raw ``sink apply`` request is used here:
    ``task start`` creates opaque route handles and each checkpoint goes
    through the explicit ``knowledge_sink`` grant enforced by the public
    ``task checkpoint`` command.
    """

    _run_public_cli(
        deeplaw_executable,
        (
            "knowledge",
            "init",
            "--vault",
            vault,
            "--name",
            "pass16-opencode",
            "--scope",
            "project",
        ),
        vault=vault,
        environment=environment,
        cwd=cwd,
    )
    enabled = _run_public_cli(
        deeplaw_executable,
        (
            "knowledge",
            "sink",
            "enable",
            "--vault",
            vault,
            "--writer-id",
            "pass16-opencode-runner",
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
        ),
        vault=vault,
        environment=environment,
        cwd=cwd,
    )
    grant_id = _extract_value(enabled, "grant_id", "grantId")
    if not isinstance(grant_id, str) or not grant_id:
        raise QualificationError("owner sink enable did not return one grant")
    scenario = str(case["scenario"])
    current = case["current_checkpoint"]
    stale = case["stale_checkpoint"]
    challenges = case.get("wrong_state_challenges")
    if (
        not isinstance(current, Mapping)
        or not isinstance(stale, Mapping)
        or not isinstance(challenges, list)
    ):
        raise QualificationError("task case checkpoints are invalid")
    seed_before = _ledger_head(
        deeplaw_executable, vault, environment=environment, cwd=cwd
    )
    receipts: list[Mapping[str, Any]] = []

    def task_start(*, task: str, workspace: Path, suffix: str) -> str:
        result = _run_public_cli(
            deeplaw_executable,
            (
                "knowledge",
                "task",
                "start",
                "--vault",
                vault,
                "--project",
                "DeepLaw Pass 16 OpenCode",
                "--task",
                task,
                "--workspace",
                workspace,
            ),
            vault=vault,
            environment=environment,
            cwd=cwd,
        )
        handle = _extract_value(result, "task_handle", "taskHandle")
        if not isinstance(handle, str) or not handle:
            raise QualificationError(f"public task start omitted the {suffix} handle")
        return handle

    def task_checkpoint(
        *,
        handle: str,
        workspace: Path,
        checkpoint: Mapping[str, Any],
        key: str,
        task_text: str | None = None,
    ) -> Mapping[str, Any]:
        marker = checkpoint.get("marker")
        decision = checkpoint.get("decision")
        next_action = checkpoint.get("next_action")
        open_gap = checkpoint.get("open_gap")
        if not all(
            isinstance(item, str) and item
            for item in (marker, decision, next_action, open_gap)
        ):
            raise QualificationError("public task checkpoint input is invalid")
        checkpoint_args: list[str | Path] = [
            "knowledge",
            "task",
            "checkpoint",
            "--vault",
            vault,
            "--task-handle",
            handle,
            "--workspace",
            workspace,
            "--grant-id",
            grant_id,
            "--idempotency-key",
            key,
            "--summary",
            f"Pass 16 {scenario} governed checkpoint.",
            "--next-action",
            next_action,
            "--expires-at",
            "2099-01-01T00:00:00Z",
            "--decision",
            decision,
            "--decision",
            f"ROUTE_MARKER: {marker}",
            "--gap",
            open_gap,
            "--confirm-no-case-data",
        ]
        if task_text is not None:
            # Keep the optional task override adjacent to the handle.  The
            # public parser treats ``--workspace`` as a value-taking option;
            # inserting before that value would make the wrong-task fixture
            # fail before it reaches the public checkpoint seam.
            checkpoint_args[7:7] = ["--task", task_text]
        result = _run_public_cli(
            deeplaw_executable,
            checkpoint_args,
            vault=vault,
            environment=environment,
            cwd=cwd,
        )
        if result.get("status") != "checkpointed":
            raise QualificationError("public task checkpoint did not complete")
        receipts.append(result)
        return result

    primary_handle = task_start(
        task=str(case["task_case"]), workspace=cwd, suffix="primary"
    )
    stale_result = task_checkpoint(
        handle=primary_handle,
        workspace=cwd,
        checkpoint=stale,
        key=f"pass16-{scenario}-stale",
    )
    current_result = task_checkpoint(
        handle=primary_handle,
        workspace=cwd,
        checkpoint=current,
        key=f"pass16-{scenario}-current",
    )
    knowledge_id = _extract_value(current_result, "knowledge_id", "knowledgeId")
    revision_id = _extract_value(current_result, "revision_id", "revisionId")
    stale_knowledge_id = _extract_value(stale_result, "knowledge_id", "knowledgeId")
    stale_revision_id = _extract_value(stale_result, "revision_id", "revisionId")
    if not all(
        isinstance(value, str)
        for value in (knowledge_id, revision_id, stale_knowledge_id, stale_revision_id)
    ):
        raise QualificationError("public task checkpoint omitted its canonical identity")

    wrong_handles: dict[str, str] = {}
    for challenge in challenges:
        if not isinstance(challenge, Mapping):
            raise QualificationError("wrong-state challenge is invalid")
        name = str(challenge.get("challenge"))
        if name == "stale_checkpoint":
            continue
        route_checkpoint = dict(current)
        route_checkpoint.update(
            {
                "marker": challenge.get("marker"),
                "decision": f"Do not admit {challenge.get('marker')} into this route.",
                "next_action": f"Reject unrelated {name} state.",
                "open_gap": "The current route remains owner-authorized.",
            }
        )
        if name == "wrong_worktree":
            workspace = concurrent_workspace
            handle = task_start(
                task=str(case["task_case"]), workspace=workspace, suffix=name
            )
        elif name == "wrong_task_line":
            workspace = cwd
            handle = task_start(
                task=f"Pass 16 unrelated {scenario} task line.",
                workspace=workspace,
                suffix=name,
            )
        else:
            raise QualificationError("unsupported wrong-state challenge")
        wrong_handles[name] = handle
        task_checkpoint(
            handle=handle,
            workspace=workspace,
            checkpoint=route_checkpoint,
            key=f"pass16-{scenario}-{name}",
            task_text=(
                f"Pass 16 unrelated {scenario} task line."
                if name == "wrong_task_line"
                else None
            ),
        )
    seed_after = _ledger_head(
        deeplaw_executable, vault, environment=environment, cwd=cwd
    )
    return {
        "grant_id": grant_id,
        "task_handle": primary_handle,
        "knowledge_id": knowledge_id,
        "revision_id": revision_id,
        "stale_knowledge_id": stale_knowledge_id,
        "stale_revision_id": stale_revision_id,
        "wrong_task_handles": wrong_handles,
        "seed_boundary": {
            "kind": "public_task_checkpoint",
            "owner_enabled": True,
            "read_mcp_write_performed": False,
            "audit_changed": seed_before != seed_after,
            "audit_head_before": seed_before,
            "audit_head_after": seed_after,
            "receipt_sha256": _sha256(_encoded(receipts)),
            "target_sha256": _sha256(knowledge_id.encode("utf-8")),
        },
    }


def _seed_development_fixture(
    deeplaw_executable: Path,
    *,
    vault: Path,
    fixture: Mapping[str, Any],
    binding: Mapping[str, Any],
    environment: Mapping[str, str],
    cwd: Path,
) -> dict[str, Any]:
    """Seed one source-free development checkpoint through owner CLI paths."""

    checkpoint = fixture.get("checkpoint")
    if not isinstance(checkpoint, Mapping):
        raise QualificationError("development diagnostic checkpoint is invalid")
    _run_public_cli(
        deeplaw_executable,
        (
            "knowledge",
            "init",
            "--vault",
            vault,
            "--name",
            "pass17-opencode-development",
            "--scope",
            "project",
        ),
        vault=vault,
        environment=environment,
        cwd=cwd,
    )
    enabled = _run_public_cli(
        deeplaw_executable,
        (
            "knowledge",
            "sink",
            "enable",
            "--vault",
            vault,
            "--writer-id",
            "pass17-opencode-development-runner",
            "--scope",
            "project",
            "--max-sensitivity",
            "private",
            "--operation",
            "record_run",
            "--operation",
            "remember",
        ),
        vault=vault,
        environment=environment,
        cwd=cwd,
    )
    grant_id = _extract_value(enabled, "grant_id", "grantId")
    if not isinstance(grant_id, str) or not grant_id:
        raise QualificationError("development diagnostic grant is missing")
    before = _ledger_head(
        deeplaw_executable,
        vault,
        environment=environment,
        cwd=cwd,
    )
    run_id = "run-pass17-development-diagnostic"
    receipts = [
        _run_sink_request(
            deeplaw_executable,
            vault=vault,
            grant_id=grant_id,
            request={
                "operation": "record_run",
                "idempotency_key": "pass17-development-run",
                "confirm_no_case_data": True,
                "run_id": run_id,
                "task": "Source-free native Host development diagnostic.",
                "host_id": "opencode-pass17-development",
                "model_id": MODEL,
                "status": "succeeded",
                "scope": "project",
                "sensitivity": "private",
                "run_metadata": {"task_binding": dict(binding)},
            },
            environment=environment,
            cwd=cwd,
        )
    ]
    remembered = _run_sink_request(
        deeplaw_executable,
        vault=vault,
        grant_id=grant_id,
        request={
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
                    f"BINDING_DIGEST: {binding['binding_sha256']}",
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
            "tool_id": "opencode-pass17-development",
            "tags": ["pass17", "development", "diagnostic"],
        },
        environment=environment,
        cwd=cwd,
    )
    receipts.append(remembered)
    knowledge_id = _extract_value(remembered, "knowledge_id", "knowledgeId")
    revision_id = _extract_value(remembered, "revision_id", "revisionId")
    if not isinstance(knowledge_id, str) or not isinstance(revision_id, str):
        raise QualificationError("development checkpoint omitted CAS identity")
    after = _ledger_head(
        deeplaw_executable,
        vault,
        environment=environment,
        cwd=cwd,
    )
    if before == after:
        raise QualificationError("development checkpoint did not change the Ledger")
    return {
        "grant_id": grant_id,
        "knowledge_id": knowledge_id,
        "revision_id": revision_id,
        "seed_boundary": {
            "kind": "seed_checkpoint",
            "owner_enabled": True,
            "read_mcp_write_performed": False,
            "audit_changed": True,
            "audit_head_before": before,
            "audit_head_after": after,
            "receipt_sha256": _sha256(_encoded(receipts)),
            "target_sha256": _sha256(knowledge_id.encode("utf-8")),
        },
    }


def _forget_checkpoint(
    deeplaw_executable: Path,
    *,
    vault: Path,
    fixture: Mapping[str, Any],
    environment: Mapping[str, str],
    cwd: Path,
) -> Mapping[str, Any]:
    task_handle = fixture.get("task_handle")
    if not isinstance(task_handle, str) or not task_handle:
        raise QualificationError("public task forget handle is missing")
    return _run_public_cli(
        deeplaw_executable,
        (
            "knowledge",
            "task",
            "forget",
            "--vault",
            vault,
            "--task-handle",
            task_handle,
            "--workspace",
            cwd,
            "--grant-id",
            str(fixture["grant_id"]),
            "--idempotency-key",
            "pass16-compaction-forget",
            "--reason",
            "Owner-directed Pass 16 checkpoint forgetting.",
            "--confirm-no-case-data",
        ),
        vault=vault,
        environment=environment,
        cwd=cwd,
    )


def _inspect_opencode_binary_static(
    path: Path,
    *,
    identity: Mapping[str, Any],
    repository: Path,
) -> dict[str, Any]:
    """Bind the OpenCode selector and exact target bytes without executing it."""

    selected = Path(path).expanduser()
    if not selected.is_absolute():
        raise QualificationError("OpenCode executable path is outside the closed scope")

    def parent_has_symlink(value: Path) -> bool:
        current = Path(value.anchor)
        try:
            for part in value.parts[1:]:
                current /= part
                if stat.S_ISLNK(current.lstat().st_mode):
                    return True
        except OSError as exc:
            raise QualificationError("OpenCode executable parent path is unavailable") from exc
        return False

    def signature(details: os.stat_result) -> tuple[Any, ...]:
        return (
            details.st_dev,
            details.st_ino,
            details.st_size,
            details.st_mode,
            details.st_uid,
            details.st_nlink,
            getattr(details, "st_mtime_ns", details.st_mtime),
            getattr(details, "st_ctime_ns", details.st_ctime),
        )

    if parent_has_symlink(selected.parent):
        raise QualificationError("OpenCode executable parent path contains a symlink")
    try:
        source_before = selected.lstat()
        source_symlink = stat.S_ISLNK(source_before.st_mode)
        direct_target = selected
        selector_target: str | None = None
        if source_symlink:
            selector_target = os.readlink(selected)
            raw_target = Path(selector_target)
            direct_target = (
                raw_target if raw_target.is_absolute() else selected.parent / raw_target
            )
            direct_target = Path(os.path.abspath(direct_target))
        if parent_has_symlink(direct_target.parent):
            raise QualificationError(
                "OpenCode executable target parent path contains a symlink"
            )
        direct_before = direct_target.lstat()
        if source_symlink and stat.S_ISLNK(direct_before.st_mode):
            raise QualificationError(
                "OpenCode executable selector must not be a symlink chain"
            )
        resolved = direct_target.resolve(strict=True)
        target_before = resolved.lstat()
    except QualificationError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise QualificationError("OpenCode executable is unavailable") from exc
    if (
        stat.S_ISLNK(target_before.st_mode)
        or not stat.S_ISREG(target_before.st_mode)
        or target_before.st_nlink != 1
    ):
        raise QualificationError(
            "OpenCode executable target must be a regular single-link file"
        )
    if not os.access(resolved, os.X_OK):
        raise QualificationError("OpenCode executable target is not executable")
    try:
        resolved.relative_to(Path(repository).resolve(strict=True))
    except ValueError:
        pass
    except (OSError, RuntimeError) as exc:
        raise QualificationError("OpenCode repository binding is unavailable") from exc
    else:
        raise QualificationError("OpenCode executable target must be repository-external")

    expected = host_preflight_receipt.host_binary_identity(identity, "opencode")

    def topology_snapshot() -> tuple[Any, ...]:
        if parent_has_symlink(selected.parent) or parent_has_symlink(direct_target.parent):
            raise QualificationError(
                "OpenCode executable parent path contains a symlink"
            )
        try:
            selected_details = selected.lstat()
            direct_details = direct_target.lstat()
            resolved_details = resolved.lstat()
            observed_selector = os.readlink(selected) if source_symlink else None
        except OSError as exc:
            raise QualificationError(
                "OpenCode executable changed during static inspection"
            ) from exc
        return (
            signature(selected_details),
            signature(direct_details),
            signature(resolved_details),
            observed_selector,
        )

    before = topology_snapshot()
    try:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(resolved, flags)
        try:
            fd_before = os.fstat(descriptor)
            digest = hashlib.sha256()
            observed_bytes = 0
            while True:
                chunk = os.read(descriptor, _BROKER_CONTROL_READ_CHUNK_BYTES)
                if not chunk:
                    break
                observed_bytes += len(chunk)
                digest.update(chunk)
            fd_after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise QualificationError(
            "OpenCode executable changed during static inspection"
        ) from exc
    after = topology_snapshot()
    if (
        before != after
        or signature(fd_before) != signature(fd_after)
        or signature(fd_before) != signature(target_before)
        or observed_bytes != fd_before.st_size
    ):
        raise QualificationError("OpenCode executable changed during static inspection")
    observed_sha256 = digest.hexdigest()
    if observed_sha256 != expected["sha256"]:
        raise QualificationError("OpenCode executable target hash differs from frozen identity")
    return {
        "host": "opencode",
        "version": expected["version"],
        "sha256": observed_sha256,
        "source_symlink": source_symlink,
        "selector_source_symlink": source_symlink,
        "execution_target_regular": True,
        "execution_target_single_link": True,
        "repository_external": True,
        "host_identity_sha256": host_preflight_receipt.host_identity_sha256(
            identity["hosts"]["opencode"]
        ),
        "host_identity_source_sha256": str(identity["source_sha256"]),
    }


def _validate_binary(
    binary: Path,
    *,
    identity: Mapping[str, Any] | None = None,
    repository: Path | None = None,
) -> str:
    if identity is not None:
        try:
            observation = _inspect_opencode_binary_static(
                binary,
                identity=identity,
                repository=repository or Path(__file__).resolve().parents[2],
            )
        except (QualificationError, OSError, ValueError) as exc:
            raise QualificationError(
                "OpenCode executable did not match the frozen Host identity"
            ) from exc
        return str(observation["sha256"])
    try:
        resolved = binary.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise QualificationError("OpenCode binary is unavailable") from exc
    if not stat.S_ISREG(resolved.stat().st_mode) or resolved.stat().st_nlink != 1:
        raise QualificationError("OpenCode execution target must be a regular single-link file")
    try:
        return _sha256_file(resolved)
    except (OSError, ValueError) as exc:
        raise QualificationError("OpenCode binary is unavailable") from exc


def _validate_owner_broker_launcher(
    launcher: Path,
    *,
    host_binary: Path,
    host_binary_sha256: str | None = None,
    repository: Path | None = None,
    expected_broker_sha256: str | None = None,
) -> str:
    """Bind an external owner-only launcher without reading its credential source."""

    if not launcher.is_absolute():
        raise QualificationError("OpenCode credential broker launcher must be absolute")
    try:
        details = launcher.lstat()
    except OSError as exc:
        raise QualificationError("OpenCode credential broker launcher is unavailable") from exc
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or not os.access(launcher, os.X_OK)
        or (
            os.name != "nt"
            and (
                stat.S_IMODE(details.st_mode) & 0o077
                or (hasattr(os, "geteuid") and details.st_uid != os.geteuid())
            )
        )
    ):
        raise QualificationError("OpenCode credential broker launcher is not owner-only")
    repository_path = (repository or Path(__file__).resolve().parents[2]).resolve(strict=True)
    try:
        launcher.resolve(strict=True).relative_to(repository_path)
    except ValueError:
        pass
    else:
        raise QualificationError(
            "OpenCode credential broker launcher must be outside the repository"
        )
    launcher_sha256 = _sha256_file(launcher)
    bound_host_sha256 = (
        _control_sha256(host_binary_sha256, label="Host binary")
        if host_binary_sha256 is not None
        else _sha256_file(host_binary)
    )
    if launcher_sha256 == bound_host_sha256:
        raise QualificationError("OpenCode credential broker launcher is not process-separated")
    if expected_broker_sha256 is not None and launcher_sha256 != expected_broker_sha256:
        raise QualificationError("OpenCode credential broker launcher hash mismatch")
    return launcher_sha256


def _control_fail(message: str) -> None:
    raise QualificationError(message)


def _control_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or _SHA256.fullmatch(value) is None
        or value == "0" * 64
    ):
        _control_fail(f"{label} must be a nonzero lowercase SHA-256 digest")
    return value


def _control_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str):
        _control_fail(f"{label} must be a UTC timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise QualificationError(f"{label} must be a UTC timestamp") from exc


def _strict_control_json(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_BROKER_CONTROL_BYTES:
        _control_fail("OpenCode broker control response size is invalid")

    def closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _control_fail("OpenCode broker control response contains a duplicate field")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=closed_object,
            parse_constant=lambda _value: _control_fail(
                "OpenCode broker control response contains a non-finite number"
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationError(
            "OpenCode broker control response is not strict JSON"
        ) from exc
    if not isinstance(value, dict):
        _control_fail("OpenCode broker control response must be an object")
    return value


def _validate_opencode_zero_model_preflight_request(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _CONTROL_REQUEST_KEYS:
        _control_fail("OpenCode broker control request is not closed")
    if (
        value.get("schema_version") != OPENCODE_BROKER_CONTROL_SCHEMA_VERSION
        or value.get("operation") != "zero_model_preflight"
        or value.get("host") != "opencode"
        or value.get("task_case") not in host_process_receipt_v2.TASK_CASES
        or not isinstance(value.get("run_id"), str)
        or _CONTROL_IDENTIFIER.fullmatch(str(value.get("run_id"))) is None
        or value.get("required_sequence")
        != list(OPENCODE_ZERO_MODEL_REQUIRED_SEQUENCE)
        or value.get("optional_sequence")
        != list(OPENCODE_ZERO_MODEL_OPTIONAL_SEQUENCE)
        or value.get("allowed_routes") != list(OPENCODE_ZERO_MODEL_ALLOWED_ROUTES)
        or value.get("zero_model_constraints") != _ZERO_MODEL_CONSTRAINTS
    ):
        _control_fail("OpenCode broker control request contract differs")

    candidate = value.get("candidate_binding")
    if not isinstance(candidate, Mapping) or set(candidate) != set(
        host_process_receipt_v2.CANDIDATE_FIELDS
    ):
        _control_fail("OpenCode broker candidate binding is incomplete")
    for field in ("commit", "tree"):
        if not isinstance(candidate.get(field), str) or _GIT_OID.fullmatch(
            str(candidate.get(field))
        ) is None:
            _control_fail("OpenCode broker candidate Git binding is invalid")
    for field in ("lock_sha256", "wheel_sha256", "sdist_sha256"):
        _control_sha256(candidate.get(field), label=f"candidate {field}")

    run_binding = value.get("run_binding")
    if not isinstance(run_binding, Mapping) or set(run_binding) != set(
        host_process_receipt_v2.RUN_BINDING_FIELDS
    ):
        _control_fail("OpenCode broker run binding is incomplete")
    if any(
        type(run_binding.get(field)) is not int or run_binding[field] < 1
        for field in host_process_receipt_v2.RUN_BINDING_FIELDS
    ):
        _control_fail("OpenCode broker run binding is invalid")

    host_binary = value.get("host_binary")
    if (
        not isinstance(host_binary, Mapping)
        or set(host_binary) != {"version", "sha256"}
        or not isinstance(host_binary.get("version"), str)
        or _CONTROL_VERSION.fullmatch(host_binary["version"]) is None
    ):
        _control_fail("OpenCode broker Host binary binding is invalid")
    _control_sha256(host_binary.get("sha256"), label="Host binary")
    for field in (
        "broker_source_sha256",
        "host_identity_sha256",
        "host_identity_source_sha256",
    ):
        _control_sha256(value.get(field), label=field)

    challenge = value.get("challenge")
    if not isinstance(challenge, Mapping) or set(challenge) != {
        "nonce_sha256",
        "issued_at",
        "expires_at",
    }:
        _control_fail("OpenCode broker freshness challenge is incomplete")
    _control_sha256(challenge.get("nonce_sha256"), label="challenge nonce")
    issued = _control_timestamp(challenge.get("issued_at"), label="challenge issued_at")
    expires = _control_timestamp(
        challenge.get("expires_at"), label="challenge expires_at"
    )
    lifetime = (expires - issued).total_seconds()
    if lifetime <= 0 or lifetime > host_process_receipt_v2.MAX_RECEIPT_LIFETIME_SECONDS:
        _control_fail("OpenCode broker freshness challenge lifetime is invalid")
    return json.loads(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    )


def build_opencode_zero_model_preflight_request(
    *,
    task_case: str,
    run_id: str,
    candidate_binding: Mapping[str, Any],
    run_binding: Mapping[str, Any],
    host_binary: Mapping[str, Any],
    broker_source_sha256: str,
    host_identity_sha256: str,
    host_identity_source_sha256: str,
    nonce_sha256: str,
    issued_at: str,
    expires_at: str,
) -> dict[str, Any]:
    """Build one path-free control challenge; it is never Formal evidence."""

    return _validate_opencode_zero_model_preflight_request(
        {
            "schema_version": OPENCODE_BROKER_CONTROL_SCHEMA_VERSION,
            "operation": "zero_model_preflight",
            "host": "opencode",
            "task_case": task_case,
            "run_id": run_id,
            "candidate_binding": dict(candidate_binding),
            "run_binding": dict(run_binding),
            "host_binary": dict(host_binary),
            "broker_source_sha256": broker_source_sha256,
            "host_identity_sha256": host_identity_sha256,
            "host_identity_source_sha256": host_identity_source_sha256,
            "challenge": {
                "nonce_sha256": nonce_sha256,
                "issued_at": issued_at,
                "expires_at": expires_at,
            },
            "required_sequence": list(OPENCODE_ZERO_MODEL_REQUIRED_SEQUENCE),
            "optional_sequence": list(OPENCODE_ZERO_MODEL_OPTIONAL_SEQUENCE),
            "allowed_routes": list(OPENCODE_ZERO_MODEL_ALLOWED_ROUTES),
            "zero_model_constraints": dict(_ZERO_MODEL_CONSTRAINTS),
        }
    )


def validate_opencode_zero_model_preflight_response(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    observed_at: str,
    seen_nonce_sha256s: MutableSet[str],
) -> dict[str, Any]:
    """Validate closed broker output without self-attesting its provenance."""

    control = _validate_opencode_zero_model_preflight_request(request)
    if not isinstance(value, Mapping) or set(value) != _CONTROL_RESPONSE_KEYS:
        _control_fail("OpenCode broker control response is not closed")
    observed_sequence = value.get("observed_sequence")
    if (
        value.get("schema_version") != OPENCODE_BROKER_CONTROL_SCHEMA_VERSION
        or value.get("operation") != "zero_model_preflight"
        or value.get("status") != "observed"
        or observed_sequence
        not in (
            list(OPENCODE_ZERO_MODEL_REQUIRED_SEQUENCE),
            list(OPENCODE_ZERO_MODEL_OPTIONAL_SEQUENCE),
        )
    ):
        _control_fail("OpenCode broker observed an unexpected route sequence")
    for field in (
        "forbidden_route_count",
        "message_route_count",
        "provider_route_count",
        "model_route_count",
        "mcp_route_count",
        "model_invocation_count",
        "provider_request_count",
        "remote_workspace_forward_count",
        "share_request_count",
        "ambient_plugin_count",
    ):
        if type(value.get(field)) is not int or value[field] != 0:
            _control_fail("OpenCode broker observed forbidden Host, model, or route activity")
    barrier = value.get("event_barrier")
    if not isinstance(barrier, Mapping) or set(barrier) != {
        "status",
        "response_release",
        "timed_out",
        "child_plugin_event_count",
        "event_type",
        "timeout_seconds",
        "elapsed_ms",
        "parent_source",
    }:
        _control_fail("OpenCode broker child event barrier is not closed")
    if (
        barrier.get("status") != "satisfied"
        or barrier.get("response_release") != "after_child_plugin_event"
        or barrier.get("timed_out") is not False
        or barrier.get("child_plugin_event_count") != 1
        or barrier.get("event_type") != "session.created"
        or barrier.get("timeout_seconds") != 30
        or type(barrier.get("elapsed_ms")) is not int
        or not 0 <= barrier["elapsed_ms"] <= 30_000
        or barrier.get("parent_source") != "actual_ingress_route"
    ):
        _control_fail("OpenCode broker child event barrier was not satisfied")

    receipt = value.get("host_process_receipt")
    try:
        admitted = host_process_receipt_v2.validate_receipt(
            receipt,
            expected_host="opencode",
            expected_task_case=str(control["task_case"]),
            expected_run_id=str(control["run_id"]),
            expected_candidate=control["candidate_binding"],
            expected_run_binding=control["run_binding"],
            expected_broker_sha256=str(control["broker_source_sha256"]),
            expected_host_identity_sha256=str(control["host_identity_sha256"]),
            expected_host_identity_source_sha256=str(
                control["host_identity_source_sha256"]
            ),
            expected_host_binary=control["host_binary"],
            seen_nonce_sha256s=seen_nonce_sha256s,
        )
    except (TypeError, ValueError, host_process_receipt_v2.HostProcessReceiptV2Error) as exc:
        raise QualificationError("OpenCode broker v2 receipt was rejected") from exc
    if admitted["nonce_sha256"] != control["challenge"]["nonce_sha256"]:
        _control_fail("OpenCode broker freshness challenge differs")
    proof = admitted["proof"]
    if proof["request_body_sha256"] != hashlib.sha256(b"{}").hexdigest():
        _control_fail("OpenCode broker fork request body was not the exact empty object")

    challenge_issued = _control_timestamp(
        control["challenge"]["issued_at"], label="challenge issued_at"
    )
    challenge_expires = _control_timestamp(
        control["challenge"]["expires_at"], label="challenge expires_at"
    )
    receipt_issued = _control_timestamp(admitted["issued_at"], label="receipt issued_at")
    receipt_reference = _control_timestamp(
        admitted["validation_reference_time"], label="receipt validation_reference_time"
    )
    receipt_expires = _control_timestamp(
        admitted["expires_at"], label="receipt expires_at"
    )
    observed = _control_timestamp(observed_at, label="consumer observed_at")
    if not (
        challenge_issued
        <= receipt_issued
        <= receipt_reference
        <= observed
        <= receipt_expires
        <= challenge_expires
    ):
        _control_fail("OpenCode broker response is outside its challenge window")
    return admitted


def _terminate_broker_process_group(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    with suppress(OSError):
        process.kill()


def _bounded_broker_control_exchange(
    broker_executable: Path,
    *,
    payload: bytes,
    timeout_seconds: float,
) -> bytes:
    """Consume one owner-external response with an in-flight combined bound."""

    if os.name != "posix":
        _control_fail("OpenCode broker process-group isolation is unavailable")
    try:
        process = subprocess.Popen(
            [str(broker_executable), OPENCODE_BROKER_CONTROL_ARGUMENT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": os.defpath,
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "NO_COLOR": "1",
            },
            start_new_session=True,
        )
    except OSError as exc:
        raise QualificationError(
            "OpenCode owner-external broker control IPC failed to start"
        ) from exc
    if process.stdin is None or process.stdout is None or process.stderr is None:
        _terminate_broker_process_group(process)
        _control_fail("OpenCode owner-external broker control pipes are unavailable")

    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    buffer_lock = threading.Lock()
    overflow = threading.Event()
    read_failure = threading.Event()
    total_bytes = 0

    def drain(stream: Any, target: bytearray) -> None:
        nonlocal total_bytes
        try:
            while True:
                chunk = stream.read1(_BROKER_CONTROL_READ_CHUNK_BYTES)
                if not chunk:
                    return
                terminate = False
                with buffer_lock:
                    if overflow.is_set():
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > _MAX_BROKER_CONTROL_BYTES:
                        stdout_buffer.clear()
                        stderr_buffer.clear()
                        overflow.set()
                        terminate = True
                    else:
                        target.extend(chunk)
                if terminate:
                    _terminate_broker_process_group(process)
        except OSError:
            read_failure.set()
            _terminate_broker_process_group(process)

    readers = (
        threading.Thread(target=drain, args=(process.stdout, stdout_buffer), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr_buffer), daemon=True),
    )
    for reader in readers:
        reader.start()
    stdin_failed = False
    try:
        process.stdin.write(payload)
        process.stdin.flush()
    except (BrokenPipeError, OSError):
        stdin_failed = True
    finally:
        with suppress(OSError):
            process.stdin.close()

    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    try:
        process.wait(timeout=max(0.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_broker_process_group(process)
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=2)
    for reader in readers:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
    if any(reader.is_alive() for reader in readers):
        timed_out = True
        _terminate_broker_process_group(process)
        for stream in (process.stdout, process.stderr):
            with suppress(OSError):
                stream.close()
        for reader in readers:
            reader.join(timeout=1)

    def fail_closed(message: str) -> None:
        with buffer_lock:
            stdout_buffer.clear()
            stderr_buffer.clear()
        _terminate_broker_process_group(process)
        _control_fail(message)

    if overflow.is_set():
        fail_closed("OpenCode owner-external broker output limit exceeded")
    if timed_out:
        fail_closed("OpenCode owner-external broker control IPC timed out")
    if read_failure.is_set() or stdin_failed or process.returncode != 0:
        fail_closed("OpenCode owner-external broker control IPC failed")
    if stderr_buffer:
        fail_closed("OpenCode owner-external broker emitted unexpected stderr")
    return bytes(stdout_buffer)


def consume_opencode_zero_model_preflight(
    broker_launcher: Path,
    *,
    request: Mapping[str, Any],
    timeout_seconds: float = 60.0,
    seen_nonce_sha256s: MutableSet[str],
) -> dict[str, Any]:
    """Consume one transient broker response and discard its raw bytes."""

    control = _validate_opencode_zero_model_preflight_request(request)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    payload = json.dumps(
        control,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    raw = _bounded_broker_control_exchange(
        broker_launcher,
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    observed_at = datetime.now(UTC).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return validate_opencode_zero_model_preflight_response(
        _strict_control_json(raw),
        request=control,
        observed_at=observed_at,
        seen_nonce_sha256s=seen_nonce_sha256s,
    )


def _stable_stat_signature(details: os.stat_result) -> tuple[Any, ...]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mode,
        details.st_uid,
        details.st_nlink,
        getattr(details, "st_mtime_ns", details.st_mtime),
        getattr(details, "st_ctime_ns", details.st_ctime),
    )


def _validate_stable_path_fd_binding(
    *,
    path_before: os.stat_result,
    path_after: os.stat_result,
    fd_before: os.stat_result,
    fd_after: os.stat_result,
    observed_bytes: int,
    error_message: str,
) -> None:
    """Bind stable pathname and FD observations across platform stat interfaces."""

    if (
        _stable_stat_signature(path_before) != _stable_stat_signature(path_after)
        or _stable_stat_signature(fd_before) != _stable_stat_signature(fd_after)
    ):
        _control_fail(error_message)

    # Windows may normalize mode, uid, and timestamps differently for lstat()
    # and fstat(). Cross-bind only file type, size, and the stable volume/file
    # identity; an unavailable file identity fails closed instead of falling
    # back to digest-only correlation.
    path_identity = (path_before.st_dev, path_before.st_ino)
    fd_identity = (fd_before.st_dev, fd_before.st_ino)
    if (
        not stat.S_ISREG(path_before.st_mode)
        or not stat.S_ISREG(fd_before.st_mode)
        or path_before.st_ino == 0
        or fd_before.st_ino == 0
        or path_identity != fd_identity
        or path_before.st_size != fd_before.st_size
        or observed_bytes != fd_before.st_size
    ):
        _control_fail(error_message)


def _windows_acl_hardening_verified(report: object) -> bool:
    if not isinstance(report, Mapping):
        return False
    verification = report.get("verification")
    return bool(
        report.get("platform") == "nt"
        and report.get("applied") is True
        and isinstance(verification, Mapping)
        and verification.get("permissions_verified") is True
    )


def _harden_windows_broker_path(
    path: Path,
    *,
    directory: bool,
    error_message: str,
) -> None:
    try:
        from deeplaw.windows_acl import (
            harden_windows_private_file,
            harden_windows_vault,
        )

        report = (
            harden_windows_vault(path)
            if directory
            else harden_windows_private_file(path)
        )
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise QualificationError(error_message) from exc
    if not _windows_acl_hardening_verified(report):
        _control_fail(error_message)


@contextmanager
def _stage_exact_broker_executable(
    path: Path,
    *,
    repository: Path,
    host_binary: Path,
    host_binary_sha256: str | None = None,
    expected_sha256: str,
) -> Iterator[Path]:
    """Execute the exact inspected broker bytes from a private immutable copy."""

    source = Path(path)
    if not source.is_absolute():
        _control_fail("OpenCode owner-external broker source must be absolute")
    current = Path(source.anchor)
    try:
        for part in source.parent.parts[1:]:
            current /= part
            if stat.S_ISLNK(current.lstat().st_mode):
                _control_fail(
                    "OpenCode owner-external broker source parent contains a symlink"
                )
    except OSError as exc:
        raise QualificationError(
            "OpenCode owner-external broker source parent is unavailable"
        ) from exc
    observed = _validate_owner_broker_launcher(
        source,
        host_binary=host_binary,
        host_binary_sha256=host_binary_sha256,
        repository=repository,
        expected_broker_sha256=expected_sha256,
    )
    if observed != expected_sha256:
        _control_fail("OpenCode owner-external broker source hash differs")
    try:
        before = source.lstat()
        if before.st_size < 1 or before.st_size > _BROKER_SOURCE_MAX_BYTES:
            _control_fail("OpenCode owner-external broker source exceeds its byte bound")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(source, flags)
        try:
            fd_before = os.fstat(descriptor)
            raw = bytearray()
            while True:
                chunk = os.read(descriptor, _BROKER_CONTROL_READ_CHUNK_BYTES)
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > _BROKER_SOURCE_MAX_BYTES:
                    _control_fail(
                        "OpenCode owner-external broker source exceeds its byte bound"
                    )
            fd_after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = source.lstat()
    except OSError as exc:
        raise QualificationError(
            "OpenCode owner-external broker source changed while it was read"
        ) from exc
    _validate_stable_path_fd_binding(
        path_before=before,
        path_after=after,
        fd_before=fd_before,
        fd_after=fd_after,
        observed_bytes=len(raw),
        error_message="OpenCode owner-external broker source changed while it was read",
    )
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        _control_fail("OpenCode owner-external broker source changed while it was read")

    with tempfile.TemporaryDirectory(prefix="deeplaw-opencode-broker-") as raw_root:
        root = Path(raw_root).resolve(strict=True)
        details = root.lstat()
        if not stat.S_ISDIR(details.st_mode):
            _control_fail("OpenCode broker staging directory is unsafe")
        if os.name == "nt":
            _harden_windows_broker_path(
                root,
                directory=True,
                error_message="OpenCode broker staging directory is unsafe",
            )
        elif (
            stat.S_IMODE(details.st_mode) & 0o077
            or details.st_uid != os.geteuid()
        ):
            _control_fail("OpenCode broker staging directory is unsafe")
        staged = root / "broker-executable"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
        )
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(staged, flags, 0o700)
        try:
            offset = 0
            while offset < len(raw):
                offset += os.write(descriptor, raw[offset:])
            if os.name != "nt":
                os.fchmod(descriptor, 0o500)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if os.name == "nt":
            _harden_windows_broker_path(
                staged,
                directory=False,
                error_message="staged OpenCode broker ACL is unsafe",
            )
        else:
            os.chmod(root, 0o500)
        try:
            if hashlib.sha256(staged.read_bytes()).hexdigest() != expected_sha256:
                _control_fail("staged OpenCode broker bytes differ")
            yield staged
        finally:
            if os.name != "nt":
                os.chmod(root, 0o700)


def _validate_opencode_package(
    path: Path,
    *,
    identity: Mapping[str, Any],
    repository: Path,
) -> dict[str, str]:
    """Bind the preflight to stable bytes from the frozen OpenCode release."""

    host_item = identity.get("hosts", {}).get("opencode")
    if not isinstance(host_item, Mapping):
        _control_fail("OpenCode frozen Host identity is unavailable")
    version = host_item.get("version")
    source_commit = host_item.get("source_commit")
    expected_sha256 = host_item.get("package_sha256")
    if (
        version != HISTORICAL_OPENCODE_VERSION_FIXTURE
        or source_commit != OPENCODE_SOURCE_COMMIT
        or not isinstance(expected_sha256, str)
        or _SHA256.fullmatch(expected_sha256) is None
        or expected_sha256 == "0" * 64
    ):
        _control_fail("OpenCode frozen package identity is not the pinned release")

    source = Path(path)
    if not source.is_absolute():
        _control_fail("OpenCode package source must be absolute")
    current = Path(source.anchor)
    try:
        for part in source.parent.parts[1:]:
            current /= part
            if stat.S_ISLNK(current.lstat().st_mode):
                _control_fail("OpenCode package source parent contains a symlink")
        before = source.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > _OPENCODE_PACKAGE_MAX_BYTES
        ):
            _control_fail("OpenCode package source is not a bounded single-link file")
        resolved = source.resolve(strict=True)
        try:
            resolved.relative_to(repository.resolve(strict=True))
        except ValueError:
            pass
        else:
            _control_fail("OpenCode package source must be repository-external")

        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(source, flags)
        try:
            fd_before = os.fstat(descriptor)
            digest = hashlib.sha256()
            observed_bytes = 0
            while True:
                chunk = os.read(descriptor, _BROKER_CONTROL_READ_CHUNK_BYTES)
                if not chunk:
                    break
                observed_bytes += len(chunk)
                if observed_bytes > _OPENCODE_PACKAGE_MAX_BYTES:
                    _control_fail("OpenCode package source exceeds its byte bound")
                digest.update(chunk)
            fd_after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = source.lstat()
    except OSError as exc:
        raise QualificationError(
            "OpenCode package source changed while it was read"
        ) from exc

    _validate_stable_path_fd_binding(
        path_before=before,
        path_after=after,
        fd_before=fd_before,
        fd_after=fd_after,
        observed_bytes=observed_bytes,
        error_message="OpenCode package source changed while it was read",
    )
    observed_sha256 = digest.hexdigest()
    if observed_sha256 != expected_sha256:
        _control_fail("OpenCode package bytes differ from the frozen Host identity")
    return {
        "version": version,
        "source_commit": source_commit,
        "package_sha256": observed_sha256,
    }


def run_opencode_owner_external_zero_model_preflight(
    *,
    candidate_binding_input: Path,
    candidate_wheel: Path | None = None,
    host_identity_input: Path,
    opencode_package: Path,
    opencode_binary: Path,
    opencode_broker: Path,
    expected_broker_sha256: str,
    task_case: str,
    run_id: str,
    evidence_run_id: int,
    qualification_run_id: int,
    repository: Path | None = None,
    seen_nonce_sha256s: set[str] | None = None,
) -> dict[str, Any]:
    """Run a transient zero-model broker capability preflight, not a task run."""

    selected_repository = repository or Path(__file__).resolve().parents[2]
    if (
        task_case not in host_process_receipt_v2.TASK_CASES
        or not isinstance(run_id, str)
        or _CONTROL_IDENTIFIER.fullmatch(run_id) is None
        or type(evidence_run_id) is not int
        or evidence_run_id < 1
        or type(qualification_run_id) is not int
        or qualification_run_id < 1
        or not isinstance(expected_broker_sha256, str)
        or _SHA256.fullmatch(expected_broker_sha256) is None
        or expected_broker_sha256 == "0" * 64
    ):
        _control_fail("OpenCode zero-model run binding is invalid")
    try:
        candidate = load_exact_candidate_binding(
            candidate_binding_input,
            candidate_wheel=candidate_wheel,
            repository=selected_repository,
        )
        identity = host_preflight_receipt.load_host_identity_input(
            host_identity_input,
            repository=selected_repository,
        )
        _validate_opencode_package(
            opencode_package,
            identity=identity,
            repository=selected_repository,
        )
        expected_host_binary = host_preflight_receipt.host_binary_identity(
            identity, "opencode"
        )
        _inspect_opencode_binary_static(
            opencode_binary,
            identity=identity,
            repository=selected_repository,
        )
    except (
        HostTaskQualificationError,
        host_preflight_receipt.HostIdentityValidationError,
        OSError,
        ValueError,
    ) as exc:
        raise QualificationError(
            "OpenCode zero-model static candidate or Host binding was rejected"
        ) from exc
    issued = datetime.now(UTC).replace(microsecond=0)
    expires = issued + timedelta(seconds=60)
    nonce_sha256 = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
    request = build_opencode_zero_model_preflight_request(
        task_case=task_case,
        run_id=run_id,
        candidate_binding=candidate,
        run_binding={
            "evidence_run_id": evidence_run_id,
            "qualification_run_id": qualification_run_id,
        },
        host_binary=expected_host_binary,
        broker_source_sha256=expected_broker_sha256,
        host_identity_sha256=host_preflight_receipt.host_identity_sha256(
            identity["hosts"]["opencode"]
        ),
        host_identity_source_sha256=str(identity["source_sha256"]),
        nonce_sha256=nonce_sha256,
        issued_at=issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    try:
        with _stage_exact_broker_executable(
            opencode_broker,
            repository=selected_repository,
            host_binary=opencode_binary,
            host_binary_sha256=expected_host_binary["sha256"],
            expected_sha256=expected_broker_sha256,
        ) as broker_executable:
            admitted = consume_opencode_zero_model_preflight(
                broker_executable,
                request=request,
                seen_nonce_sha256s=(
                    seen_nonce_sha256s if seen_nonce_sha256s is not None else set()
                ),
            )
    except (OSError, ValueError, QualificationError) as exc:
        raise QualificationError(
            "OpenCode owner-external zero-model preflight failed closed"
        ) from exc
    return {
        "status": "passed",
        "evidence_class": "zero_model_preflight_only",
        "formal_admission": False,
        "host": "opencode",
        "observed_sequence": list(OPENCODE_ZERO_MODEL_REQUIRED_SEQUENCE),
        "model_invocation_count": 0,
        "provider_request_count": 0,
        "broker_source_sha256": expected_broker_sha256,
        "receipt_record_sha256": admitted["record_sha256"],
    }


def _run_opencode_command(
    binary: Path,
    *,
    args: Sequence[str],
    environment: Mapping[str, str],
    cwd: Path,
    input_bytes: bytes = b"",
) -> dict[str, Any]:
    return _run_bounded_process(
        [binary, *args],
        environment=environment,
        cwd=cwd,
        input_bytes=input_bytes,
    )


def _opencode_cli_turn_args(
    *,
    session_id: str | None = None,
    fork: bool = False,
    agent_name: str = "qualification",
) -> tuple[str, ...]:
    """Return the pinned project-plugin-enabled ``run --format json`` command."""

    if agent_name not in {"qualification", "development"}:
        raise QualificationError("OpenCode CLI agent mode is invalid")
    args: list[str] = [
        "run",
        "--format",
        "json",
        "--model",
        MODEL,
        "--variant",
        VARIANT,
        "--agent",
        agent_name,
    ]
    if session_id is not None:
        if _SESSION_ID.fullmatch(session_id) is None:
            raise QualificationError("OpenCode session identity is invalid")
        args.extend(("--session", session_id))
    if fork:
        args.append("--fork")
    return tuple(args)


def _session_id_from_events(data: bytes) -> str:
    """Extract one raw OpenCode session ID in memory for lifecycle continuation."""

    if not isinstance(data, bytes) or len(data) > MAX_OUTPUT_BYTES:
        raise QualificationError("OpenCode session output exceeds its bound")
    values: set[str] = set()
    for line in data.splitlines():
        if not line.strip():
            continue
        event = _strict_json(line)
        if not isinstance(event, Mapping):
            continue
        selected = _event_identity(event, "sessionID", "sessionId", "session_id")
        if selected:
            values.add(selected)
    if len(values) != 1:
        raise QualificationError("OpenCode task output omitted one stable session identity")
    selected = next(iter(values))
    if _SESSION_ID.fullmatch(selected) is None:
        raise QualificationError("OpenCode task output contained an unsafe session identity")
    return selected


class _OpenCodeLocalServer:
    """Bounded loopback client for the public OpenCode session lifecycle API."""

    def __init__(
        self,
        *,
        binary: Path,
        environment: Mapping[str, str],
        cwd: Path,
        root: Path,
        forbidden_output_values: Sequence[str] = (),
    ) -> None:
        self.binary = binary
        self.environment = dict(environment)
        self.cwd = cwd
        self.root = root
        self.forbidden_output_values = tuple(forbidden_output_values)
        self.process: subprocess.Popen[bytes] | None = None
        self.base_url = ""
        self.port = 0

    def start(self) -> None:
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = int(probe.getsockname()[1])
        # This process is intentionally loopback-only and uses the isolated
        # OpenCode environment; it is terminated before the scenario root is
        # cleaned up.
        self.process = subprocess.Popen(
            [
                str(self.binary),
                "serve",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=str(self.cwd),
            env=dict(self.environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **process_creation_options(),
        )
        self.base_url = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise QualificationError("OpenCode local server exited before readiness")
            try:
                self.request("GET", "/global/health")
            except (OSError, urllib.error.URLError, QualificationError):
                time.sleep(0.05)
            else:
                return
        self.stop()
        raise QualificationError("OpenCode local server readiness timed out")

    def request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Any:
        if not self.base_url or not path.startswith("/"):
            raise QualificationError("OpenCode local server request is invalid")
        body = None if payload is None else _encoded(dict(payload))
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=60) as response:
                raw = response.read(MAX_OUTPUT_BYTES + 1)
        except (OSError, urllib.error.URLError) as exc:
            raise QualificationError("OpenCode local server request failed") from exc
        if len(raw) > MAX_OUTPUT_BYTES:
            raise QualificationError("OpenCode local server response exceeds its bound")
        _forbid_values(raw, self.forbidden_output_values)
        value = _strict_json(raw)
        if not isinstance(value, (Mapping, list, bool)):
            raise QualificationError("OpenCode local server response is not JSON")
        return value

    def create(self) -> str:
        """Create one native OpenCode session through POST /session."""

        value = self.request("POST", "/session", {})
        selected = _extract_value(value, "id", "sessionID", "sessionId")
        if not isinstance(selected, str) or _SESSION_ID.fullmatch(selected) is None:
            raise QualificationError("OpenCode session create omitted a safe session identity")
        return selected

    def summarize(self, session_id: str) -> bool:
        if _SESSION_ID.fullmatch(session_id) is None:
            raise QualificationError("OpenCode compaction session identity is missing")
        value = self.request(
            "POST",
            f"/session/{session_id}/summarize",
            {"providerID": "deepseek", "modelID": "deepseek-v4-flash", "auto": False},
        )
        if value is not True:
            raise QualificationError("OpenCode summarize response is invalid")
        return True

    def resume(self, session_id: str) -> Mapping[str, Any]:
        if _SESSION_ID.fullmatch(session_id) is None:
            raise QualificationError("OpenCode session identity is invalid")
        value = self.request("GET", f"/session/{session_id}")
        if not isinstance(value, Mapping):
            raise QualificationError("OpenCode resume response is invalid")
        return value

    def fork(self, session_id: str) -> str:
        if _SESSION_ID.fullmatch(session_id) is None:
            raise QualificationError("OpenCode session identity is invalid")
        value = self.request("POST", f"/session/{session_id}/fork", {})
        selected = _extract_value(value, "id", "sessionID", "sessionId")
        if not isinstance(selected, str) or _SESSION_ID.fullmatch(selected) is None:
            raise QualificationError("OpenCode fork response omitted a session identity")
        return selected

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        _terminate_process_tree(process)
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        self.process = None


def _bind_public_host_session(
    deeplaw_executable: Path,
    *,
    vault: Path,
    session_id: str,
    task_handle: str,
    grant_id: str,
    workspace: Path,
    environment: Mapping[str, str],
    cwd: Path,
    idempotency_key: str,
) -> Mapping[str, Any]:
    if _SESSION_ID.fullmatch(session_id) is None:
        raise QualificationError("OpenCode bind session identity is invalid")
    session_sha256 = _sha256(session_id.encode("utf-8"))
    result = _run_public_cli(
        deeplaw_executable,
        (
            "knowledge",
            "task",
            "bind-host-session",
            "--vault",
            vault,
            "--host",
            "opencode",
            "--session-sha256",
            session_sha256,
            "--task-handle",
            task_handle,
            "--workspace",
            workspace,
            "--grant-id",
            grant_id,
            "--idempotency-key",
            idempotency_key,
            "--confirm-no-case-data",
        ),
        vault=vault,
        environment=environment,
        cwd=cwd,
    )
    if result.get("status") != "bound" or result.get("write_performed") is not True:
        raise QualificationError("public Host session bind did not complete")
    return result


def _resolve_public_host_continuity(
    deeplaw_executable: Path,
    *,
    vault: Path,
    session_id: str,
    workspace: Path,
    environment: Mapping[str, str],
    cwd: Path,
    forbidden_values: Sequence[str],
) -> tuple[Mapping[str, Any], str]:
    if _SESSION_ID.fullmatch(session_id) is None:
        raise QualificationError("OpenCode resolve session identity is invalid")
    result = _run_public_cli(
        deeplaw_executable,
        (
            "knowledge",
            "task",
            "resolve-host-continuity",
            "--vault",
            vault,
            "--host",
            "opencode",
            "--session-sha256",
            _sha256(session_id.encode("utf-8")),
            "--workspace",
            workspace,
        ),
        vault=vault,
        environment=environment,
        cwd=cwd,
    )
    contract_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "host-continuity-capsule.v1.schema.json"
    )
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        Draft202012Validator(contract).validate(dict(result))
    except (OSError, TypeError, ValueError, ValidationError) as exc:
        raise QualificationError("public Host continuity response is invalid") from exc
    text = _canonical(result)
    _forbid_sensitive(text.encode("utf-8"), forbidden_values)
    return result, text


def _continuity_with_checkpoint_gap(
    capsule: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    """Mirror the project plugin's compaction-only checkpoint gap exactly."""

    gaps = capsule.get("gaps")
    if not isinstance(gaps, list):
        raise QualificationError("OpenCode continuity capsule gaps are invalid")
    if any(
        isinstance(gap, Mapping) and gap.get("code") == "checkpoint_grant_missing"
        for gap in gaps
    ):
        selected = dict(capsule)
    elif len(gaps) >= 8:
        selected = {
            "schema_version": "deeplaw.host-continuity-capsule/v1",
            "status": "gap",
            "statements": [],
            "gaps": [{"code": "checkpoint_grant_missing"}],
            "conflicts": [],
            "write_performed": False,
        }
    else:
        selected = {**capsule, "gaps": [*gaps, {"code": "checkpoint_grant_missing"}]}
    text = _canonical(selected)
    if len(text.encode("utf-8")) > 1400:
        selected = {
            "schema_version": "deeplaw.host-continuity-capsule/v1",
            "status": "gap",
            "statements": [],
            "gaps": [{"code": "continuity_capsule_bound"}],
            "conflicts": [],
            "write_performed": False,
        }
        text = _canonical(selected)
    return selected, text


def _probe_model_availability(
    host_launcher: Path,
    *,
    environment: Mapping[str, str],
    cwd: Path,
) -> dict[str, Any]:
    result = _run_opencode_command(
        host_launcher,
        args=(
            "run",
            "--format",
            "json",
            "--model",
            MODEL,
            "--variant",
            VARIANT,
            "--title",
            "native-host-model-availability",
        ),
        environment=environment,
        cwd=cwd,
        input_bytes=b"Reply with one short availability confirmation; do not call tools.\n",
    )
    return parse_availability_result(
        stdout=result["stdout"],
        returncode=int(result["returncode"]),
        elapsed_ms=int(result["elapsed_ms"]),
        timed_out=bool(result["timed_out"]),
        output_overflow=bool(result["output_overflow"]),
        stderr=result["stderr"],
        forbidden_values=tuple(
            value
            for name, value in environment.items()
            if name in {*_CANARY_NAMES, _OWNER_DOTENV_ENV_NAME}
        ),
    )


def preflight_opencode(
    *,
    binary: Path,
    host_launcher: Path,
    deeplaw_executable: Path,
    environment: Mapping[str, str],
    cwd: Path,
    project_root: Path | None = None,
    plugin_receipt: Mapping[str, Any] | None = None,
    expected_broker_sha256: str | None = None,
    expected_host_binary_sha256: str | None = None,
    expected_version: str = HISTORICAL_OPENCODE_VERSION_FIXTURE,
    agent_name: str = "qualification",
) -> dict[str, Any]:
    if project_root is None or plugin_receipt is None:
        raise QualificationError("OpenCode plugin preflight binding is required")
    if expected_version is None:
        raise QualificationError("OpenCode expected version from external identity is required")
    if expected_broker_sha256 is not None:
        _validate_owner_broker_launcher(
            host_launcher,
            host_binary=binary,
            host_binary_sha256=expected_host_binary_sha256,
            repository=project_root,
            expected_broker_sha256=expected_broker_sha256,
        )
    inspection_cwd = project_root
    plugin_target, plugin_sha256 = _validate_plugin_receipt(
        plugin_receipt,
        repository=project_root,
        deeplaw_executable=deeplaw_executable,
    )
    forbidden_values = tuple(
        value
        for name, value in environment.items()
        if name in {*_CANARY_NAMES, _OWNER_DOTENV_ENV_NAME}
    )
    inspection_environment = {
        name: value
        for name, value in environment.items()
        if name not in _CANARY_NAMES and name != _OWNER_DOTENV_ENV_NAME
    }
    models = _run_opencode_command(
        host_launcher,
        args=("models", "deepseek"),
        environment=inspection_environment,
        cwd=inspection_cwd,
    )
    _forbid_sensitive(
        bytes(models["stdout"]) + bytes(models["stderr"]),
        forbidden_values,
    )
    model_inventory = parse_model_inventory(models["stdout"], returncode=int(models["returncode"]))
    config = _run_opencode_command(
        host_launcher,
        args=("debug", "config"),
        environment=inspection_environment,
        cwd=inspection_cwd,
    )
    if config["returncode"] != 0:
        raise QualificationError("OpenCode resolved config command failed")
    config_bytes = bytes(config["stdout"])
    if len(config_bytes) > MAX_OUTPUT_BYTES:
        raise QualificationError("resolved OpenCode config exceeds the bound")
    _forbid_sensitive(bytes(config["stderr"]), forbidden_values)
    try:
        resolved = _strict_json(config_bytes)
    except QualificationError as exc:
        raise QualificationError("resolved OpenCode config is not JSON") from exc
    if not isinstance(resolved, Mapping):
        raise QualificationError("resolved OpenCode config is not an object")
    if not _resolved_plugin_matches(resolved, target=plugin_target):
        raise QualificationError("resolved config did not load the exact project plugin")
    sanitized_resolved = dict(resolved)
    sanitized_resolved["plugin"] = ["file://verified-project-plugin"]
    _forbid_sensitive(_encoded(sanitized_resolved), forbidden_values)
    resolved_mcp = resolved.get("mcp")
    if not isinstance(resolved_mcp, Mapping) or set(resolved_mcp) != {"deeplaw_knowledge"}:
        raise QualificationError("resolved config enabled an unexpected MCP")
    mcp_entry = resolved_mcp.get("deeplaw_knowledge")
    if (
        not isinstance(mcp_entry, Mapping)
        or mcp_entry.get("type") != "local"
        or mcp_entry.get("enabled") is not True
        or mcp_entry.get("command") != ["./deeplaw-closed-mcp"]
        or mcp_entry.get("timeout") != 60_000
    ):
        raise QualificationError("resolved config did not enable the exact DeepLaw MCP")
    resolved_model = resolved.get("model")
    resolved_agent = resolved.get("agent")
    resolved_selected_agent = (
        resolved_agent.get(agent_name) if isinstance(resolved_agent, Mapping) else None
    )
    if (
        resolved_model != MODEL
        or resolved.get("small_model") != MODEL
        or resolved.get("enabled_providers") != ["deepseek"]
        or resolved.get("share") != "disabled"
        or resolved.get("snapshot") is not False
        or resolved.get("permission") != build_permission()
        or not isinstance(resolved_selected_agent, Mapping)
        or resolved_selected_agent.get("variant") != VARIANT
        or resolved_selected_agent.get("permission") != build_permission()
    ):
        raise QualificationError("resolved config selected an unexpected model")
    config_receipt = {
        "raw_sha256": _sha256(config_bytes),
        "raw_bytes": len(config_bytes),
    }
    availability_config = cwd / "availability-opencode.json"
    no_tools_config = build_opencode_config(agent_name=agent_name)
    no_tools_config["mcp"] = {}
    availability_config.write_text(_canonical(no_tools_config) + "\n", encoding="utf-8")
    availability = _probe_model_availability(
        host_launcher,
        environment={
            **environment,
            "OPENCODE_CONFIG": str(availability_config),
        },
        cwd=inspection_cwd,
    )
    if availability["status"] != "available":
        raise QualificationError("DeepSeek model availability probe failed")
    return {
        "version": expected_version,
        "version_sha256": _sha256(expected_version.encode("utf-8")),
        "version_bytes": len(expected_version.encode("utf-8")),
        "model_inventory": model_inventory,
        "resolved_config": config_receipt,
        "external_plugin": {
            **dict(plugin_receipt),
            "resolved_installed_sha256": plugin_sha256,
        },
        "availability": availability,
    }


def _final_metrics(
    scenario: str,
    safe_read: Mapping[str, Any],
    *,
    marker_checks: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Compute the shared Codex/OpenCode continuity outcome metrics."""

    payloads = safe_read.get("provider_payloads")
    payloads = payloads if isinstance(payloads, list) else []
    provider_boundary_correct = bool(payloads) and all(
        payload.get("delivery_match") is True
        and payload.get("write_performed") is False
        and 0 < int(payload.get("provider_bytes", 0)) <= PROVIDER_HARD_LIMIT_BYTES
        for payload in payloads
        if isinstance(payload, Mapping)
    )
    checks = list(marker_checks)
    post_forget = checks[-1] if scenario == "compaction_forget" and checks else None
    preservation_checks = (
        checks[:-1] if scenario == "compaction_forget" and len(checks) > 1 else checks
    )
    return {
        "first_correct_action": (
            safe_read.get("first_call_valid") is True if checks else None
        ),
        "decision_preservation": (
            all(
                item.get("expected_decision") is True
                and item.get("expected_next_action") is True
                for item in preservation_checks
            )
            if preservation_checks
            else None
        ),
        "wrong_state_admission": (
            sum(int(item.get("forbidden_admission_count", 0)) for item in checks)
            if checks
            else None
        ),
        "stale_state_rejected": (
            all(item.get("stale_absent") is True for item in checks) if checks else None
        ),
        "forgotten_state_admission": (
            int(post_forget.get("forgotten_admission_count", 1))
            if scenario == "compaction_forget"
            and post_forget is not None
            else None
        ),
        "gap_observed": (
            post_forget.get("gap_observed") is True
            if scenario == "compaction_forget" and post_forget is not None
            else None
        ),
        "projection_state_correct": None,
        "retention_wording_correct": None,
        "provider_boundary_correct": provider_boundary_correct,
        "evidence_sha256": "0" * 64,
    }


def _require_regular_path(path: Path, *, label: str) -> None:
    """Reject symlinks and non-regular paths at the plugin boundary."""

    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise QualificationError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(mode):
        raise QualificationError(f"{label} is not a regular file")


def _require_directory(path: Path, *, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        path.mkdir()
        return
    except OSError as exc:
        raise QualificationError(f"{label} is unavailable") from exc
    if not stat.S_ISDIR(mode):
        raise QualificationError(f"{label} is not a directory")


def _freeze_local_plugin_dependency_state(
    directory: Path,
    *,
    expected_version: str = HISTORICAL_OPENCODE_VERSION_FIXTURE,
) -> None:
    """Keep exact local-plugin loading offline and prevent Host workspace mutation."""

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "node_modules").mkdir(exist_ok=True)
    dependency = {_OPENCODE_PLUGIN_API_PACKAGE: expected_version}
    (directory / "package.json").write_text(
        _canonical({"dependencies": dependency}) + "\n",
        encoding="utf-8",
    )
    (directory / "package-lock.json").write_text(
        _canonical({"packages": {"": {"dependencies": dependency}}}) + "\n",
        encoding="utf-8",
    )
    (directory / ".gitignore").write_text(
        "node_modules\npackage.json\npackage-lock.json\nbun.lock\n.gitignore",
        encoding="utf-8",
    )


def _installed_opencode_plugin_bytes(deeplaw_executable: Path) -> bytes:
    """Read the plugin only from the isolated candidate-wheel installation."""

    runtime_python = deeplaw_executable.parent / "python"
    _require_regular_path(runtime_python, label="candidate runtime Python")
    resource = _PLUGIN_RESOURCE_RELATIVE.as_posix()
    script = (
        "import importlib.resources, sys\n"
        "root = importlib.resources.files('deeplaw')\n"
        f"sys.stdout.buffer.write(root.joinpath({resource!r}).read_bytes())\n"
    )
    result = _run_bounded_process(
        [runtime_python, "-I", "-c", script],
        environment={
            "PATH": str(runtime_python.parent),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONNOUSERSITE": "1",
        },
        cwd=deeplaw_executable.parent,
        timeout=30,
    )
    source_bytes = bytes(result["stdout"])
    if (
        result["returncode"] != 0
        or result["timed_out"]
        or result["output_overflow"]
        or result["stderr"]
        or not source_bytes
    ):
        raise QualificationError(
            "candidate wheel did not provide the exact OpenCode plugin"
        )
    return source_bytes


def _install_exact_opencode_plugin(
    *,
    repository: Path,
    run_root: Path,
    deeplaw_executable: Path,
    expected_version: str = HISTORICAL_OPENCODE_VERSION_FIXTURE,
) -> tuple[dict[str, Any], Path]:
    """Install candidate-wheel plugin bytes and retain a path-free binding receipt."""

    try:
        repository_mode = repository.lstat().st_mode
    except OSError as exc:
        raise QualificationError("OpenCode scenario repository is unavailable") from exc
    if not stat.S_ISDIR(repository_mode):
        raise QualificationError("OpenCode scenario repository is not a directory")

    source_bytes = _installed_opencode_plugin_bytes(deeplaw_executable)

    opencode_dir = repository / ".opencode"
    plugins_dir = opencode_dir / "plugins"
    _require_directory(opencode_dir, label="OpenCode project plugin directory")
    _freeze_local_plugin_dependency_state(
        opencode_dir, expected_version=expected_version
    )
    _require_directory(plugins_dir, label="OpenCode project plugin directory")
    target = repository / _PLUGIN_INSTALLED_RELATIVE
    if target.exists() or target.is_symlink():
        _require_regular_path(target, label="OpenCode installed plugin")
    try:
        target.write_bytes(source_bytes)
    except OSError as exc:
        raise QualificationError("OpenCode plugin cannot be installed") from exc
    _require_regular_path(target, label="OpenCode installed plugin")
    try:
        installed_bytes = target.read_bytes()
    except OSError as exc:
        raise QualificationError("OpenCode installed plugin cannot be read") from exc

    source_sha256 = _sha256(source_bytes)
    installed_sha256 = _sha256(installed_bytes)
    receipt: dict[str, Any] = {
        "source_relative": _PLUGIN_SOURCE_RELATIVE.as_posix(),
        "source_sha256": source_sha256,
        "source_bytes": len(source_bytes),
        "installed_relative": _PLUGIN_INSTALLED_RELATIVE.as_posix(),
        "installed_sha256": installed_sha256,
        "installed_bytes": len(installed_bytes),
        "exact_match": source_bytes == installed_bytes,
    }
    if (
        receipt["exact_match"] is not True
        or source_sha256 != installed_sha256
        or receipt["source_bytes"] != receipt["installed_bytes"]
    ):
        raise QualificationError("OpenCode installed plugin bytes do not match the source")
    receipt_path = run_root / "opencode-plugin-receipt.json"
    try:
        receipt_path.write_text(_canonical(receipt) + "\n", encoding="utf-8")
    except OSError as exc:
        raise QualificationError("OpenCode plugin receipt cannot be written") from exc
    return receipt, receipt_path


def _validate_plugin_receipt(
    receipt: Mapping[str, Any], *, repository: Path, deeplaw_executable: Path
) -> tuple[Path, str]:
    """Revalidate candidate-wheel/install binding before trusting resolved config."""

    expected_keys = {
        "source_relative",
        "source_sha256",
        "source_bytes",
        "installed_relative",
        "installed_sha256",
        "installed_bytes",
        "exact_match",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != expected_keys:
        raise QualificationError("OpenCode plugin receipt is incomplete")
    if receipt.get("source_relative") != _PLUGIN_SOURCE_RELATIVE.as_posix():
        raise QualificationError("OpenCode plugin source binding is unexpected")
    if receipt.get("installed_relative") != _PLUGIN_INSTALLED_RELATIVE.as_posix():
        raise QualificationError("OpenCode plugin install binding is unexpected")
    source_sha256 = _require_sha(receipt.get("source_sha256"), "plugin_source_sha256")
    installed_sha256 = _require_sha(
        receipt.get("installed_sha256"), "plugin_installed_sha256"
    )
    source_bytes = receipt.get("source_bytes")
    installed_bytes = receipt.get("installed_bytes")
    if (
        not isinstance(source_bytes, int)
        or isinstance(source_bytes, bool)
        or source_bytes <= 0
        or not isinstance(installed_bytes, int)
        or isinstance(installed_bytes, bool)
        or installed_bytes <= 0
        or receipt.get("exact_match") is not True
        or source_sha256 != installed_sha256
        or source_bytes != installed_bytes
    ):
        raise QualificationError("OpenCode plugin receipt is inconsistent")
    target = repository / _PLUGIN_INSTALLED_RELATIVE
    _require_regular_path(target, label="OpenCode installed plugin")
    try:
        installed_data = target.read_bytes()
    except OSError as exc:
        raise QualificationError("OpenCode plugin bytes cannot be read") from exc
    source_data = _installed_opencode_plugin_bytes(deeplaw_executable)
    if (
        len(source_data) != source_bytes
        or len(installed_data) != installed_bytes
        or _sha256(source_data) != source_sha256
        or _sha256(installed_data) != installed_sha256
        or source_data != installed_data
    ):
        raise QualificationError("OpenCode plugin bytes changed after installation")
    return target, installed_sha256


def _resolved_plugin_matches(
    resolved: Mapping[str, Any], *, target: Path
) -> bool:
    """Require the exact one project plugin emitted by OpenCode 1.18.16."""

    plugins = resolved.get("plugin")
    if not isinstance(plugins, list) or len(plugins) != 1 or not isinstance(plugins[0], str):
        return False
    parsed = urlparse(plugins[0])
    if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
        return False
    try:
        raw_path = unquote(parsed.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:[\\/]", raw_path):
            raw_path = raw_path[1:]
        resolved_path = Path(raw_path).resolve(strict=True)
        target_path = target.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved_path == target_path


def _prepare_scenario_state(
    *,
    base_environment: Mapping[str, str],
    run_root: Path,
    repository: Path,
    deeplaw_executable: Path,
    node_binary: Path,
    expected_version: str = HISTORICAL_OPENCODE_VERSION_FIXTURE,
    agent_name: str = "qualification",
) -> tuple[dict[str, str], Path, dict[str, Any]]:
    """Give each scenario a distinct OpenCode state tree and MCP wrapper."""

    for name in (
        "home",
        "config",
        "data",
        "cache",
        "state",
        "tmp",
        "appdata",
        "localappdata",
        "opencode-config",
    ):
        (run_root / name).mkdir(parents=True, exist_ok=True)
    wrapper = run_root / "deeplaw-closed-mcp"
    receipt = run_root / "mcp-wrapper-receipt.json"
    _write_mcp_wrapper(
        wrapper,
        deeplaw_executable=deeplaw_executable,
        receipt_path=receipt,
        node_binary=node_binary,
    )
    config = build_opencode_config(agent_name=agent_name)
    if os.name == "nt":
        config["mcp"]["deeplaw_knowledge"]["command"] = [  # type: ignore[index]
            sys.executable,
            str(wrapper),
        ]
    config_path = run_root / "opencode.json"
    config_path.write_text(_canonical(config) + "\n", encoding="utf-8")
    model_receipt_path = run_root / "tmp" / "opencode-model-observations.jsonl"
    model_receipt_path.touch(mode=0o600, exist_ok=False)
    plugin_receipt, _plugin_receipt_path = _install_exact_opencode_plugin(
        repository=repository,
        run_root=run_root,
        deeplaw_executable=deeplaw_executable,
        expected_version=expected_version,
    )
    environment = dict(base_environment)
    environment["PATH"] = os.pathsep.join(
        dict.fromkeys(
            (
                str(deeplaw_executable.parent),
                environment.get("PATH", os.defpath),
            )
        )
    )
    environment.update(
        {
            "HOME": str(run_root / "home"),
            "USERPROFILE": str(run_root / "home"),
            "XDG_CONFIG_HOME": str(run_root / "config"),
            "XDG_DATA_HOME": str(run_root / "data"),
            "XDG_CACHE_HOME": str(run_root / "cache"),
            "XDG_STATE_HOME": str(run_root / "state"),
            "TMPDIR": str(run_root / "tmp"),
            "TMP": str(run_root / "tmp"),
            "TEMP": str(run_root / "tmp"),
            "APPDATA": str(run_root / "appdata"),
            "LOCALAPPDATA": str(run_root / "localappdata"),
            "OPENCODE_CONFIG": str(config_path),
            "OPENCODE_CONFIG_DIR": str(run_root / "opencode-config"),
            _MODEL_RECEIPT_ENV_NAME: str(model_receipt_path),
            # The native plugin accepts only an explicit absolute Vault path;
            # keeping this local to the scenario prevents ambient resolution
            # from selecting a checkout or another user's Vault.
            "DEEPLAW_KNOWLEDGE_VAULT": str((repository / "vault").resolve()),
        }
    )
    _freeze_local_plugin_dependency_state(
        Path(environment["XDG_CONFIG_HOME"]) / "opencode",
        expected_version=expected_version,
    )
    _freeze_local_plugin_dependency_state(
        Path(environment["OPENCODE_CONFIG_DIR"]), expected_version=expected_version
    )
    return environment, receipt, plugin_receipt


def _empty_analysis(code: str) -> dict[str, Any]:
    return {
        "safe_read": {
            "call_count": 0,
            "first_call_valid": False,
            "bounded_retry_used": False,
            "safe_read_operations": [],
            "provider_payloads": [],
        },
        "usage": {
            "input_tokens": "unreported",
            "cached_input_tokens": "unreported",
            "cache_write_input_tokens": "unreported",
            "output_tokens": "unreported",
            "reasoning_output_tokens": "unreported",
            "total_tokens": "unreported",
        },
        "final_response_sha256": None,
        "final_response_bytes": 0,
        "final_value": None,
        "provider_values": [],
        "sanitized_events": (_canonical({"type": "failure", "code": code}) + "\n").encode(
            "utf-8"
        ),
    }


def _safe_failure_code(exc: QualificationError) -> str:
    """Map known failures to stable labels without retaining exception text."""

    known = {
        "evidence contains an absolute path": "absolute_path_leak",
        "evidence contains a forbidden field": "unsafe_evidence_field",
        "evidence contains a forbidden value": "secret_or_canary_leak",
        "OpenCode task process failed": "host_task_process_failed",
        "OpenCode output exceeds the bounded limit": "host_output_overflow",
        "OpenCode event is not valid JSON": "host_event_json_invalid",
        "OpenCode event is not an object": "host_event_object_invalid",
        "OpenCode emitted an error event": "host_error_event",
        "unknown OpenCode event type": "host_event_type_invalid",
        "tool event part is invalid": "host_tool_event_invalid",
        "tool event state is invalid": "host_tool_event_invalid",
        "tool event call id is missing": "host_tool_event_invalid",
        "disallowed tool was invoked": "disallowed_tool_invoked",
        "tool call did not complete": "safe_read_tool_failed",
        "OpenCode new-session tool call did not complete": "cli_run_tool_failed",
        "OpenCode resume tool call did not complete": "cli_resume_tool_failed",
        "OpenCode fork tool call did not complete": "cli_fork_tool_failed",
        "OpenCode new-session final response schema is invalid": (
            "cli_run_final_response_schema_invalid"
        ),
        "OpenCode resume final response schema is invalid": (
            "cli_resume_final_response_schema_invalid"
        ),
        "OpenCode fork final response schema is invalid": (
            "cli_fork_final_response_schema_invalid"
        ),
        "completed MCP output is not an object": "safe_read_output_invalid",
        "completed MCP output has no exact Provider transport": (
            "safe_read_output_invalid"
        ),
        "OpenCode native Provider projection is invalid": "provider_capsule_invalid",
        "OpenCode native Provider projection exceeds its bound": (
            "provider_capsule_overflow"
        ),
        "OpenCode native Provider projection is not canonical": (
            "provider_capsule_transport_mismatch"
        ),
        "step finish part is invalid": "provider_usage_invalid",
        "OpenCode token usage is missing": "provider_usage_missing",
        "OpenCode must emit exactly one bounded final response": (
            "final_response_count_invalid"
        ),
        "final response text is invalid": "final_response_schema_invalid",
        "final response exceeds the bounded limit": "final_response_overflow",
        "final response schema is invalid": "final_response_schema_invalid",
        "MCP call lacks the exact safe context and task-binding attestation": (
            "safe_read_task_binding_invalid"
        ),
        "MCP call lacks the exact public v6 context arguments": (
            "safe_read_call_shape_invalid"
        ),
        "qualification requires one or two safe read calls": (
            "safe_read_call_count_invalid"
        ),
        "safe read used an unexpected MCP server": "safe_read_tool_failed",
        "safe read used an unexpected tool": "safe_read_tool_failed",
        "safe read did not complete": "safe_read_tool_failed",
        "safe read call identities must be unique": "safe_read_identity_invalid",
        "safe read observation is invalid": "safe_read_observation_invalid",
        "bounded retry requires an insufficient first Provider Capsule": (
            "safe_read_retry_invalid"
        ),
        "MCP result observation does not match in-memory output": (
            "safe_read_receipt_mismatch"
        ),
        "structured MCP output exceeds its local bound": "safe_read_output_overflow",
        "structured output observation does not match MCP result": (
            "safe_read_receipt_mismatch"
        ),
        "safe read must use the current MCP output schema": (
            "safe_read_contract_invalid"
        ),
        "knowledge_support operation is not a safe read": "safe_read_operation_invalid",
        "current Provider Capsule is missing": "provider_capsule_invalid",
        "Provider Capsule delivery is invalid": "provider_capsule_invalid",
        "Provider text is not the exact canonical inner Capsule": (
            "provider_capsule_transport_mismatch"
        ),
        "Provider byte accounting does not match delivery": (
            "provider_capsule_accounting_invalid"
        ),
        "read-only Provider delivery reported a write": "hidden_write_detected",
        "Provider Capsule statements or gaps are invalid": "provider_capsule_invalid",
        "Provider Capsule evidence is invalid": "provider_capsule_invalid",
        "Provider projection does not match delivery": "provider_capsule_invalid",
        "Provider payload relevance inputs are inconsistent": (
            "provider_relevance_invalid"
        ),
        "Provider relevance input is invalid": "provider_relevance_invalid",
        "Provider relevance text does not match its receipt": (
            "provider_relevance_receipt_mismatch"
        ),
        "actual OpenCode provider token usage is missing": "provider_usage_missing",
        "token usage arithmetic is inconsistent": "provider_usage_inconsistent",
        "OpenCode session or message identity is missing": "native_identity_missing",
        "OpenCode task output omitted one stable session identity": (
            "native_identity_missing"
        ),
        "OpenCode task output contained an unsafe session identity": (
            "native_identity_invalid"
        ),
        "OpenCode resume changed the session identity": "native_identity_mismatch",
        "OpenCode session.get lineage is invalid": "native_lineage_invalid",
        "OpenCode local session server is unavailable": "native_session_server_missing",
        "OpenCode local server exited before readiness": "native_session_server_failed",
        "OpenCode local server readiness timed out": "native_session_server_failed",
        "OpenCode local server request is invalid": "native_request_invalid",
        "OpenCode local server request failed": "native_request_failed",
        "OpenCode local server response exceeds its bound": "native_response_overflow",
        "OpenCode local server response is not JSON": "native_response_invalid",
        "OpenCode resume response is invalid": "native_response_invalid",
        "OpenCode session messages response is invalid": "native_response_invalid",
        "OpenCode summarize response is invalid": "native_response_invalid",
        "OpenCode compaction session identity is missing": "native_identity_missing",
        "OpenCode compaction token usage is unreported": "provider_usage_missing",
        "MCP wrapper receipt is missing": "mcp_child_receipt_missing",
        "MCP wrapper receipt is invalid": "mcp_child_receipt_invalid",
        "read-only OpenCode turn changed the ledger": "ledger_changed",
        "repeated large Provider payloads are not bounded": "provider_payload_repeated",
    }
    for stage, stage_code in (
        ("new-session", "cli_run"),
        ("resume", "cli_resume"),
        ("fork", "cli_fork"),
    ):
        stage_failures = {
            f"OpenCode {stage} final response used a code fence": (
                f"{stage_code}_final_response_fenced"
            ),
            f"OpenCode {stage} final response is not a JSON object": (
                f"{stage_code}_final_response_not_json"
            ),
            f"OpenCode {stage} final response JSON syntax is invalid": (
                f"{stage_code}_final_response_json_invalid"
            ),
            f"OpenCode {stage} final response omitted a required field": (
                f"{stage_code}_final_response_required_field_missing"
            ),
            f"OpenCode {stage} final response added an unsupported field": (
                f"{stage_code}_final_response_extra_field"
            ),
            f"OpenCode {stage} final response field type is invalid": (
                f"{stage_code}_final_response_type_invalid"
            ),
            f"OpenCode {stage} final response field bound is invalid": (
                f"{stage_code}_final_response_bound_invalid"
            ),
            f"OpenCode {stage} final response contract is invalid": (
                f"{stage_code}_final_response_contract_invalid"
            ),
        }
        known.update(stage_failures)
    return known.get(str(exc), "host_qualification_failure")


_MODEL_OBSERVATION_KEYS = frozenset(
    {
        "schema_version",
        "event_type",
        "session_sha256",
        "message_sha256",
        "role",
        "provider_id",
        "model_id",
        "summary",
        "mode",
        "finish",
        "tokens",
    }
)
_CONTINUITY_DELIVERY_KEYS = frozenset(
    {
        "schema_version",
        "event_type",
        "session_sha256",
        "context_sha256",
        "context_bytes",
        "status",
        "statement_count",
        "gap_codes",
        "conflict_count",
    }
)


def _model_receipt_offset(path: Path) -> int:
    if path.is_symlink() or not path.is_file():
        raise QualificationError("OpenCode model observation receipt is unavailable")
    details = path.stat()
    if os.name != "nt" and (
        stat.S_IMODE(details.st_mode) & 0o077
        or (hasattr(os, "geteuid") and details.st_uid != os.geteuid())
    ):
        raise QualificationError("OpenCode model observation receipt is not owner-only")
    return details.st_size


def _read_host_observations(
    path: Path,
    *,
    offset: int,
    forbidden_values: Sequence[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current = _model_receipt_offset(path)
    if offset < 0 or offset > current:
        raise QualificationError("OpenCode model observation offset is invalid")
    with path.open("rb") as stream:
        stream.seek(offset)
        raw = stream.read(MAX_OUTPUT_BYTES + 1)
    if not raw or len(raw) > MAX_OUTPUT_BYTES or not raw.endswith(b"\n"):
        raise QualificationError("OpenCode model observation receipt is incomplete")
    _forbid_sensitive(raw, forbidden_values)
    model_observations: list[dict[str, Any]] = []
    delivery_observations: list[dict[str, Any]] = []
    for line in raw.splitlines():
        value = _strict_json(line)
        if not isinstance(value, Mapping):
            raise QualificationError("OpenCode Host observation shape is invalid")
        if set(value) == _CONTINUITY_DELIVERY_KEYS:
            gap_codes = value.get("gap_codes")
            if (
                value.get("schema_version")
                != "deeplaw.opencode-continuity-delivery-observation/v1"
                or value.get("event_type")
                not in {
                    "experimental.chat.system.transform",
                    "experimental.session.compacting",
                }
                or value.get("status") not in {"admitted", "gap"}
                or not isinstance(value.get("session_sha256"), str)
                or _SHA256.fullmatch(str(value.get("session_sha256"))) is None
                or not isinstance(value.get("context_sha256"), str)
                or _SHA256.fullmatch(str(value.get("context_sha256"))) is None
                or isinstance(value.get("context_bytes"), bool)
                or not isinstance(value.get("context_bytes"), int)
                or not 1 <= int(value["context_bytes"]) <= 2048
                or any(
                    isinstance(value.get(field), bool)
                    or not isinstance(value.get(field), int)
                    or int(value[field]) < 0
                    for field in ("statement_count", "conflict_count")
                )
                or not isinstance(gap_codes, list)
                or gap_codes != sorted(set(gap_codes))
                or any(
                    not isinstance(code, str) or _GAP_CODE.fullmatch(code) is None
                    for code in gap_codes
                )
            ):
                raise QualificationError("OpenCode continuity delivery observation is invalid")
            delivery_observations.append(dict(value))
            continue
        if set(value) != _MODEL_OBSERVATION_KEYS:
            raise QualificationError("OpenCode model observation shape is invalid")
        tokens = value.get("tokens")
        cache = tokens.get("cache") if isinstance(tokens, Mapping) else None
        if (
            value.get("schema_version")
            != "deeplaw.opencode-model-observation/v1"
            or value.get("event_type") != "message.updated"
            or value.get("role") != "assistant"
            or not isinstance(value.get("provider_id"), str)
            or not isinstance(value.get("model_id"), str)
            or not isinstance(value.get("summary"), bool)
            or not (
                value.get("mode") is None or isinstance(value.get("mode"), str)
            )
            or not (
                value.get("finish") is None or isinstance(value.get("finish"), str)
            )
            or any(
                not isinstance(value.get(field), str)
                or _SHA256.fullmatch(str(value.get(field))) is None
                for field in ("session_sha256", "message_sha256")
            )
            or not isinstance(tokens, Mapping)
            or set(tokens) != {"input", "output", "reasoning", "total", "cache"}
            or not isinstance(cache, Mapping)
            or set(cache) != {"read", "write"}
            or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 0
                for item in (
                    tokens.get("input"),
                    tokens.get("output"),
                    tokens.get("reasoning"),
                    tokens.get("total"),
                    cache.get("read"),
                    cache.get("write"),
                )
            )
        ):
            raise QualificationError("OpenCode model observation value is invalid")
        model_observations.append(dict(value))
    return model_observations, delivery_observations


def _read_model_observations(
    path: Path,
    *,
    offset: int,
    forbidden_values: Sequence[str],
) -> list[dict[str, Any]]:
    """Compatibility helper returning only sanitized response-model rows."""

    model_observations, _delivery_observations = _read_host_observations(
        path,
        offset=offset,
        forbidden_values=forbidden_values,
    )
    return model_observations


def _continuity_delivery_from_observations(
    observations: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
    continuity_capsule: Mapping[str, Any],
    continuity_text: str,
    event_type: str = "experimental.chat.system.transform",
) -> dict[str, Any]:
    """Require one plugin-observed delivery matching the independently resolved bytes."""

    session_sha256 = _sha256(session_id.encode("utf-8"))
    selected = [
        item
        for item in observations
        if item.get("session_sha256") == session_sha256
        and item.get("event_type") == event_type
    ]
    if len(selected) != 1:
        raise QualificationError(
            "OpenCode continuity delivery was not observed exactly once"
        )
    delivery = selected[0]
    encoded = continuity_text.encode("utf-8")
    statements = continuity_capsule.get("statements")
    gaps = continuity_capsule.get("gaps")
    conflicts = continuity_capsule.get("conflicts")
    if not all(isinstance(value, list) for value in (statements, gaps, conflicts)):
        raise QualificationError("OpenCode continuity delivery capsule is invalid")
    expected = {
        "context_sha256": _sha256(encoded),
        "context_bytes": len(encoded),
        "status": continuity_capsule.get("status"),
        "statement_count": len(statements),
        "gap_codes": sorted(
            {
                str(item["code"])
                for item in gaps
                if isinstance(item, Mapping) and isinstance(item.get("code"), str)
            }
        ),
        "conflict_count": len(conflicts),
    }
    if any(delivery.get(key) != value for key, value in expected.items()):
        raise QualificationError(
            "OpenCode continuity delivery did not match the resolver"
        )
    return {
        "event_type": event_type,
        "session_sha256": session_sha256,
        **expected,
    }


def _response_model_from_observations(
    observations: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
) -> tuple[str, str, int]:
    """Validate completed assistant model metadata without reading message parts."""

    session_sha256 = _sha256(session_id.encode("utf-8"))
    completed = [
        item
        for item in observations
        if item.get("session_sha256") == session_sha256
        and item.get("summary") is False
        and item.get("mode") != "compaction"
        and isinstance(item.get("finish"), str)
    ]
    models = {
        (str(item.get("provider_id")), str(item.get("model_id")))
        for item in completed
    }
    if models != {("deepseek", "deepseek-v4-flash")}:
        raise QualificationError("assistant response model identity is missing or unexpected")
    messages = {str(item.get("message_sha256")) for item in completed}
    if len(messages) != 1:
        raise QualificationError("assistant response model observation is ambiguous")
    provider_id, model_id = next(iter(models))
    return provider_id, model_id, len(completed)


def _compaction_usage_from_observations(
    observations: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
) -> dict[str, int]:
    """Read compaction usage only from sanitized ``message.updated`` metadata."""

    session_sha256 = _sha256(session_id.encode("utf-8"))
    completed = [
        item
        for item in observations
        if item.get("session_sha256") == session_sha256
        and (item.get("summary") is True or item.get("mode") == "compaction")
        and isinstance(item.get("finish"), str)
    ]
    messages = {str(item.get("message_sha256")) for item in completed}
    if len(messages) != 1 or not completed:
        raise QualificationError(
            "OpenCode model observations omitted one actual compaction usage"
        )
    return _require_actual_usage(_normalize_usage({"tokens": completed[-1]["tokens"]}))


def _merge_usage(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> dict[str, int | str]:
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    if not all(
        isinstance(first.get(field), int) and isinstance(second.get(field), int)
        for field in fields
    ):
        raise QualificationError("OpenCode compaction token usage is unreported")
    return validate_token_usage(
        {field: int(first[field]) + int(second[field]) for field in fields}
    )


def _account_turn_usage(
    native_turn_usage: Mapping[str, Any],
    pending_compaction_usage: Mapping[str, Any] | None,
) -> tuple[dict[str, int | str], dict[str, int]]:
    """Keep aggregate turn cost complete without double-counting native receipts."""

    native = _require_actual_usage(native_turn_usage)
    aggregate = (
        _merge_usage(pending_compaction_usage, native)
        if pending_compaction_usage is not None
        else native
    )
    return aggregate, native


def _marker_check(
    analysis: Mapping[str, Any],
    *,
    case: Mapping[str, Any],
    post_forget: bool = False,
) -> dict[str, Any]:
    provider_values = analysis.get("provider_values", [])
    final_value = analysis.get("final_value")
    values = [provider_values, final_value]
    current = case.get("current_checkpoint")
    stale = case.get("stale_checkpoint")
    challenges = case.get("wrong_state_challenges")
    if not isinstance(current, Mapping) or not isinstance(stale, Mapping):
        raise QualificationError("task case checkpoints are invalid")
    forbidden = [
        item.get("marker")
        for item in challenges or []
        if isinstance(item, Mapping) and isinstance(item.get("marker"), str)
    ]
    if not isinstance(final_value, Mapping):
        raise QualificationError("OpenCode final response is unavailable for marker scoring")
    preserved = final_value.get("preserved_decisions")
    open_gaps = final_value.get("open_gaps")
    current_decision = str(current.get("decision", ""))
    current_next_action = str(current.get("next_action", ""))
    expected_decision = (
        not post_forget
        and _contains_marker(provider_values, current_decision)
        and isinstance(preserved, list)
        and current_decision in preserved
    )
    expected_next_action = (
        not post_forget
        and _contains_marker(provider_values, current_next_action)
        and final_value.get("next_step") == current_next_action
    )
    post_requirement = case.get("post_forget_requirement")
    forgotten_marker = (
        post_requirement.get("forgotten_marker")
        if isinstance(post_requirement, Mapping)
        else None
    )
    forbidden_admission_count = sum(
        1
        for marker in forbidden
        if isinstance(marker, str) and _contains_marker(values, marker)
    )
    forgotten_admission_count = sum(
        (
            int(_contains_marker(values, str(current.get("marker", "")))),
            int(_contains_marker(values, current_decision)),
            int(
                isinstance(forgotten_marker, str)
                and _contains_marker(values, forgotten_marker)
            ),
        )
    )
    provider_gap = any(
        isinstance(payload, Mapping) and int(payload.get("gap_count", 0)) > 0
        for payload in analysis.get("safe_read", {}).get("provider_payloads", [])
        if isinstance(analysis.get("safe_read"), Mapping)
    )
    final_gap = isinstance(open_gaps, list) and any(
        isinstance(gap, str) and bool(gap.strip()) for gap in open_gaps
    )
    return {
        "expected_decision": expected_decision,
        "expected_next_action": expected_next_action,
        "forbidden_admission_count": forbidden_admission_count,
        "forbidden_absent": forbidden_admission_count == 0,
        "stale_absent": not _contains_marker(values, str(stale.get("marker", ""))),
        "forgotten_absent": not (
            isinstance(forgotten_marker, str) and _contains_marker(values, forgotten_marker)
        ),
        "expected_state_absent": not post_forget
        or (
            not _contains_marker(values, str(current.get("marker", "")))
            and not _contains_marker(values, current_decision)
        ),
        "forgotten_admission_count": forgotten_admission_count if post_forget else 0,
        "gap_observed": provider_gap and final_gap,
    }


def _run_one_scenario(
    *,
    run_index: int,
    scenario: str,
    opencode_binary: Path,
    host_launcher: Path,
    deeplaw_executable: Path,
    expected_version: str = HISTORICAL_OPENCODE_VERSION_FIXTURE,
    environment: Mapping[str, str],
    run_root: Path,
    forbidden_values: Sequence[str],
    case: Mapping[str, Any] | None = None,
    reported_scenario: str | None = None,
    agent_name: str = "qualification",
) -> tuple[
    dict[str, Any],
    list[bytes],
    list[Mapping[str, Any]],
    dict[str, Any],
]:
    if scenario not in SCENARIOS:
        raise QualificationError("unsupported Pass 16 OpenCode scenario")
    selected_case = pass16_continuity_cases.task_case(scenario) if case is None else dict(case)
    semantic_task_family = reported_scenario or scenario
    development = semantic_task_family == "development_diagnostic"
    run_root.mkdir(parents=True, exist_ok=True)
    task_line = (
        str(selected_case["task_case"])
        if development
        else f"pass16-{selected_case['task_case']}"
    )
    repository, concurrent, _primary_binding, _concurrent_binding = (
        _create_git_task_repository(
            run_root,
            task_line=task_line,
            development=development,
        )
    )
    scenario_environment, wrapper_receipt_path, plugin_receipt = _prepare_scenario_state(
        base_environment=environment,
        run_root=run_root,
        repository=repository,
        deeplaw_executable=deeplaw_executable,
        node_binary=opencode_binary,
        expected_version=expected_version,
        agent_name=agent_name,
    )
    _validate_plugin_receipt(
        plugin_receipt,
        repository=repository,
        deeplaw_executable=deeplaw_executable,
    )
    model_receipt_value = scenario_environment.get(_MODEL_RECEIPT_ENV_NAME)
    model_receipt_path = Path(model_receipt_value or "")
    expected_model_receipt = run_root / "tmp" / "opencode-model-observations.jsonl"
    try:
        receipt_matches = (
            model_receipt_path.resolve(strict=True)
            == expected_model_receipt.resolve(strict=True)
        )
    except OSError:
        receipt_matches = False
    if not receipt_matches:
        raise QualificationError("OpenCode model observation receipt path is invalid")
    _model_receipt_offset(model_receipt_path)
    deeplaw_environment = {
        name: value
        for name, value in scenario_environment.items()
        if name
        not in {
            _PROVIDER_ENV_NAME,
            _OWNER_DOTENV_ENV_NAME,
            _MODEL_RECEIPT_ENV_NAME,
        }
        and name not in _CANARY_NAMES
    }
    # OpenCode resolves the relative MCP command from its task repository.  A
    # byte-identical transient wrapper is placed there, while its receipt still
    # lands only in the isolated run root.
    repository_wrapper = repository / "deeplaw-closed-mcp"
    repository_wrapper.write_bytes((run_root / "deeplaw-closed-mcp").read_bytes())
    if os.name != "nt":
        repository_wrapper.chmod(repository_wrapper.stat().st_mode | stat.S_IXUSR)
    # Recompute both bindings after the runner-owned wrapper is materialized so
    # dirty-state digests describe the actual task worktrees used by this turn.
    primary_binding = pass16_continuity_cases.git_binding(
        repository, task_line=task_line
    )
    concurrent_binding = pass16_continuity_cases.git_binding(
        repository, task_line=task_line, worktree=concurrent
    )
    vault = repository / "vault"
    fixture = (
        _seed_development_fixture(
            deeplaw_executable,
            vault=vault,
            fixture=selected_case,
            binding=primary_binding,
            environment=deeplaw_environment,
            cwd=repository,
        )
        if development
        else _seed_continuity_fixture(
            deeplaw_executable,
            vault=vault,
            case=selected_case,
            primary_binding=primary_binding,
            concurrent_binding=concurrent_binding,
            concurrent_workspace=concurrent,
            environment=deeplaw_environment,
            cwd=repository,
        )
    )
    try:
        tool_schema = observe_knowledge_support_tools_list(
            command=repository_wrapper,
            args=(),
            cwd=repository,
            environment=deeplaw_environment,
        )
    except Exception as exc:
        raise QualificationError("knowledge_support tools/list observation failed") from exc
    mutation_boundaries: list[dict[str, Any]] = [dict(fixture["seed_boundary"])]
    turns: list[dict[str, Any]] = []
    marker_checks: list[dict[str, Any]] = []
    methods: list[str] = []
    native_receipts: list[dict[str, Any]] = []
    route_receipts: list[dict[str, Any]] = []
    sanitized: list[bytes] = []
    wrapper_receipts: list[Mapping[str, Any]] = []
    session_id: str | None = None
    root_session_id: str | None = None
    server: _OpenCodeLocalServer | None = None
    compaction_usage: dict[str, int | str] | None = None
    prompt = (
        pass17_development_diagnostic.candidate_prompt(selected_case)
        + " Use this complete JSON object as the exact knowledge_support arguments: "
        + _canonical(_context_call_arguments(selected_case, primary_binding))
        + ". Copy every key and value unchanged; do not add, remove, rename, infer, or "
        "rewrite any field"
        + ". End with the required bare four-key JSON object only; do not use a code fence, "
        "prefix, or suffix."
        if development
        else _candidate_prompt(selected_case)
    )

    def capture_wrapper_receipt() -> None:
        if not wrapper_receipt_path.is_file():
            raise QualificationError("MCP wrapper receipt is missing")
        wrapper_value = _strict_json(wrapper_receipt_path.read_bytes())
        if not isinstance(wrapper_value, Mapping):
            raise QualificationError("MCP wrapper receipt is invalid")
        validate_mcp_receipt(
            wrapper_value,
            wrapper_path=run_root / "deeplaw-closed-mcp",
            deeplaw_executable=deeplaw_executable,
            host_environment=scenario_environment,
        )
        wrapper_receipts.append(wrapper_value)

    def turn(
        requested_operation: str,
        turn_prompt: str,
        *,
        fork: bool = False,
        post_forget: bool = False,
        precreated: bool = False,
        parent_session_id: str | None = None,
    ) -> None:
        nonlocal session_id, root_session_id, compaction_usage
        previous_session_id = session_id
        expected_session_id = session_id
        before = _ledger_head(
            deeplaw_executable, vault, environment=deeplaw_environment, cwd=repository
        )
        started = time.monotonic()
        args = _opencode_cli_turn_args(
            # Every formal session is created through POST /session before
            # this call.  The compatibility ``fork`` argument is retained for
            # development fixtures, but formal forks pass a pre-created child
            # and never ask the CLI to synthesize one.
            session_id=expected_session_id,
            fork=fork and not precreated,
            agent_name=agent_name,
        )
        continuity_capsule: Mapping[str, Any] | None = None
        continuity_text: str | None = None
        if not development:
            if expected_session_id is None:
                raise QualificationError("OpenCode Host session was not pre-created")
            continuity_capsule, continuity_text = _resolve_public_host_continuity(
                deeplaw_executable,
                vault=vault,
                session_id=expected_session_id,
                workspace=repository,
                environment=deeplaw_environment,
                cwd=repository,
                forbidden_values=forbidden_values,
            )
        model_observation_offset = _model_receipt_offset(model_receipt_path)
        result = _run_opencode_command(
            host_launcher,
            args=args,
            environment=scenario_environment,
            cwd=repository,
            input_bytes=(turn_prompt + "\n").encode("utf-8"),
        )
        _forbid_sensitive(result["stderr"], forbidden_values)
        after = _ledger_head(
            deeplaw_executable, vault, environment=deeplaw_environment, cwd=repository
        )
        if result["returncode"] != 0 or result["timed_out"] or result["output_overflow"]:
            raise QualificationError("OpenCode task process failed")
        try:
            analysis = analyze_opencode_events(
                result["stdout"],
                expected_task_binding=(primary_binding if development else None),
                expected_task=(str(selected_case["task_case"]) if development else None),
                continuity_capsule=continuity_capsule,
                continuity_text=continuity_text,
                forbidden_values=forbidden_values,
            )
        except QualificationError as exc:
            stage = {
                "cli.run": "new-session",
                "cli.run.session": "resume",
                "cli.run.fork": "fork",
            }.get(requested_operation)
            if stage is not None:
                if str(exc) == "tool call did not complete":
                    raise QualificationError(
                        f"OpenCode {stage} tool call did not complete"
                    ) from exc
                if str(exc).startswith("final response "):
                    raise QualificationError(
                        f"OpenCode {stage} {exc}"
                    ) from exc
            raise
        relevant_checkpoint = (
            selected_case.get("checkpoint")
            if development
            else selected_case.get("current_checkpoint")
        )
        if not isinstance(relevant_checkpoint, Mapping):
            raise QualificationError("Host fixture checkpoint is invalid")
        try:
            analysis["safe_read"] = _bind_native_relevant_chars(
                analysis["safe_read"],
                analysis["provider_texts"],
                tuple(
                    str(relevant_checkpoint[field])
                    for field in ("decision", "next_action", "marker")
                ),
            )
        except pass13_evidence.EvidenceValidationError as exc:
            raise QualificationError(str(exc)) from exc
        observed_session = _session_id_from_events(result["stdout"])
        if expected_session_id is not None and observed_session != expected_session_id:
            raise QualificationError("OpenCode Host changed the pre-created session identity")
        session_id = observed_session
        if root_session_id is None:
            root_session_id = observed_session
        if server is None:
            raise QualificationError("OpenCode local session server is unavailable")
        model_observations, delivery_observations = _read_host_observations(
            model_receipt_path,
            offset=model_observation_offset,
            forbidden_values=forbidden_values,
        )
        actual_provider_id, actual_model_id, completed_observation_count = (
            _response_model_from_observations(
                model_observations,
                session_id=observed_session,
            )
        )
        response_model_observation = {
            "observation_count": len(model_observations),
            "completed_observation_count": completed_observation_count,
            "actual_response_provider_id": actual_provider_id,
            "actual_response_model_id": actual_model_id,
            "source": "candidate_plugin_message.updated_metadata",
        }
        continuity_delivery_receipt: dict[str, Any] | None = None
        if continuity_capsule is not None and continuity_text is not None:
            continuity_delivery_receipt = _continuity_delivery_from_observations(
                delivery_observations,
                session_id=observed_session,
                continuity_capsule=continuity_capsule,
                continuity_text=continuity_text,
            )
            response_model_observation["continuity_delivery"] = (
                continuity_delivery_receipt
            )
        usage, native_turn_usage = _account_turn_usage(
            analysis["usage"], compaction_usage
        )
        if compaction_usage is not None:
            compaction_usage = None
            analysis["usage"] = usage
        if not development:
            check = _marker_check(
                analysis,
                case=selected_case,
                post_forget=post_forget,
            )
            marker_checks.append(check)
        continuity_observation: dict[str, Any] | None = None
        if continuity_text is not None and continuity_capsule is not None:
            known_markers = pass16_continuity_cases.marker_values(selected_case)
            continuity_observation = {
                "sha256": _sha256(continuity_text.encode("utf-8")),
                "bytes": len(continuity_text.encode("utf-8")),
                "status": continuity_capsule.get("status"),
                "delivery_source": "opencode_project_plugin",
                "delivery_sha256": _sha256(
                    _encoded(continuity_delivery_receipt)
                ),
                "provider_sha256": _sha256(continuity_text.encode("utf-8")),
                "provider_bytes": len(continuity_text.encode("utf-8")),
                "marker_presence": {
                    name: _contains_marker(continuity_capsule, marker)
                    for name, marker in known_markers.items()
                },
            }
        events = analysis["sanitized_events"]
        sanitized.append(events)
        turns.append(
            {
                "status": "passed",
                "lifecycle_method": requested_operation,
                "thread_id_sha256": analysis["thread_id_sha256"],
                "turn_id_sha256": analysis["turn_id_sha256"],
                "prompt_sha256": _sha256(turn_prompt.encode("utf-8")),
                "final_response_sha256": analysis["final_response_sha256"],
                "final_response_bytes": analysis["final_response_bytes"],
                "host_elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
                "usage": usage,
                "actual_response_provider_id": actual_provider_id,
                "actual_response_model_id": actual_model_id,
                "actual_response_model_receipt": {
                    "bytes": len(_encoded(response_model_observation)),
                    "sha256": _sha256(_encoded(response_model_observation)),
                },
                "ledger_audit_head_before": before,
                "ledger_audit_head_after": after,
                "ledger_unchanged": before == after,
                "safe_read": analysis["safe_read"],
                "host_continuity_capsule": continuity_observation,
                "sanitized_events": {
                    "name": f"opencode-run-{run_index}-turn-{len(turns)+1}.sanitized.jsonl",
                    "bytes": len(events),
                    "sha256": _sha256(events),
                },
            }
        )
        if before != after:
            raise QualificationError("read-only OpenCode turn changed the ledger")
        if root_session_id is None:
            raise QualificationError("OpenCode root session identity is missing")
        relation = (
            "fork"
            if parent_session_id is not None
            else "new"
            if precreated and root_session_id == observed_session
            else "resume"
        )
        receipt_parent = parent_session_id if relation == "fork" else (
            previous_session_id if relation == "resume" else None
        )
        sanitized_args = [
            "<session-sha256>"
            if isinstance(item, str) and item in {previous_session_id, expected_session_id}
            else item
            for item in args
        ]
        native_receipts.append(
            pass13_evidence.native_lifecycle_receipt(
                semantic_task_family=semantic_task_family,
                transport="opencode_cli",
                request_seam="opencode run --format json",
                requested_operation=requested_operation,
                sanitized_request={
                    "argv": sanitized_args,
                    "session_id_sha256": (
                        _sha256(previous_session_id.encode("utf-8"))
                        if previous_session_id
                        else None
                    ),
                    "stdin_sha256": _sha256((turn_prompt + "\n").encode("utf-8")),
                },
                observation_kind="cli_json_record",
                methods_observed=["cli.run.json"],
                sanitized_observation=analysis["sanitized_events"],
                current_identity=observed_session,
                parent_identity=receipt_parent,
                root_identity=root_session_id,
                relation=relation,
                actual_provider_usage=native_turn_usage,
            )
        )
        session_info = server.resume(observed_session)
        response_session = _extract_value(session_info, "id", "sessionID", "sessionId")
        parent_session = _extract_value(session_info, "parentID", "parentId", "parent_id")
        # OpenCode 1.18.16 Session.fork clones the session without persisting a
        # parentID.  The fork predecessor is therefore attested by the explicit
        # loopback POST receipt below; session.get cannot claim a parent field
        # that the pinned Host does not return.
        if response_session != observed_session or (
            parent_session_id and parent_session is not None
        ):
            raise QualificationError("OpenCode session.get lineage is invalid")
        native_receipts.append(
            pass13_evidence.native_lifecycle_receipt(
                semantic_task_family=semantic_task_family,
                transport="opencode_loopback_http",
                request_seam="GET session/:sessionID",
                requested_operation="session.get",
                sanitized_request={
                    "session_id_sha256": _sha256(observed_session.encode("utf-8"))
                },
                observation_kind="native_response",
                methods_observed=["session.get"],
                sanitized_observation={
                    "response": "Session.Info",
                    "session_id_sha256": _sha256(observed_session.encode("utf-8")),
                    "parent_id_sha256": (
                        _sha256(parent_session.encode("utf-8"))
                        if isinstance(parent_session, str) and parent_session
                        else None
                    ),
                    "parent_id_present": bool(parent_session),
                },
                current_identity=observed_session,
                parent_identity=(
                    parent_session_id
                    if parent_session_id is not None
                    else parent_session
                    if isinstance(parent_session, str) and parent_session
                    else None
                ),
                root_identity=root_session_id,
                relation="same_session",
                actual_provider_usage=None,
            )
        )

    def summarize_current_session() -> None:
        nonlocal compaction_usage
        if server is None or session_id is None or root_session_id is None:
            raise QualificationError("OpenCode compaction session identity is missing")
        continuity_capsule, _continuity_text = _resolve_public_host_continuity(
            deeplaw_executable,
            vault=vault,
            session_id=session_id,
            workspace=repository,
            environment=deeplaw_environment,
            cwd=repository,
            forbidden_values=forbidden_values,
        )
        compact_capsule, compact_text = _continuity_with_checkpoint_gap(
            continuity_capsule
        )
        model_observation_offset = _model_receipt_offset(model_receipt_path)
        server.summarize(session_id)
        model_observations, delivery_observations = _read_host_observations(
            model_receipt_path,
            offset=model_observation_offset,
            forbidden_values=forbidden_values,
        )
        compaction_delivery = _continuity_delivery_from_observations(
            delivery_observations,
            session_id=session_id,
            continuity_capsule=compact_capsule,
            continuity_text=compact_text,
            event_type="experimental.session.compacting",
        )
        compaction_usage = _compaction_usage_from_observations(
            model_observations,
            session_id=session_id,
        )
        native_receipts.extend(
            [
                pass13_evidence.native_lifecycle_receipt(
                    semantic_task_family=semantic_task_family,
                    transport="opencode_loopback_http",
                    request_seam="POST session/:sessionID/summarize",
                    requested_operation="session.summarize",
                    sanitized_request={
                        "session_id_sha256": _sha256(session_id.encode("utf-8")),
                        "provider_id": "deepseek",
                        "model_id": "deepseek-v4-flash",
                        "auto": False,
                    },
                    observation_kind="native_response",
                    methods_observed=["session.summarize"],
                    sanitized_observation={"response": True},
                    current_identity=session_id,
                    parent_identity=session_id,
                    root_identity=root_session_id,
                    relation="same_session",
                    actual_provider_usage=None,
                ),
                pass13_evidence.native_lifecycle_receipt(
                    semantic_task_family=semantic_task_family,
                    transport="opencode_project_plugin",
                    request_seam="message.updated metadata receipt",
                    requested_operation="message.updated.metadata",
                    sanitized_request={
                        "session_id_sha256": _sha256(session_id.encode("utf-8"))
                    },
                    observation_kind="native_event",
                    methods_observed=["message.updated.metadata"],
                    sanitized_observation={
                        "response": "sanitized_message.updated_metadata",
                        "observation_count": len(model_observations),
                        "compaction_usage_sha256": _sha256(
                            _encoded(compaction_usage)
                        ),
                    },
                    current_identity=session_id,
                    parent_identity=session_id,
                    root_identity=root_session_id,
                    relation="same_session",
                    actual_provider_usage=compaction_usage,
                ),
                pass13_evidence.native_lifecycle_receipt(
                    semantic_task_family=semantic_task_family,
                    transport="opencode_project_plugin",
                    request_seam="experimental.session.compacting",
                    requested_operation="experimental.session.compacting",
                    sanitized_request={
                        "session_id_sha256": _sha256(session_id.encode("utf-8"))
                    },
                    observation_kind="native_event",
                    methods_observed=["experimental.session.compacting"],
                    sanitized_observation={"delivery": compaction_delivery},
                    current_identity=session_id,
                    parent_identity=session_id,
                    root_identity=root_session_id,
                    relation="same_session",
                    actual_provider_usage=None,
                ),
            ]
        )

    try:
        server = _OpenCodeLocalServer(
            binary=host_launcher,
            environment=scenario_environment,
            cwd=repository,
            root=run_root,
            forbidden_output_values=tuple(
                value
                for name, value in scenario_environment.items()
                if name in {_PROVIDER_ENV_NAME, _OWNER_DOTENV_ENV_NAME}
                or name in _CANARY_NAMES
            ),
        )
        server.start()
        if development:
            turn("cli.run", prompt)
        else:
            # The native session is created first.  Its opaque ID is hashed
            # locally and bound through the public owner-granted route before
            # the first model turn, so the plugin can inject the same capsule.
            session_id = server.create()
            root_session_id = session_id
            _bind_public_host_session(
                deeplaw_executable,
                vault=vault,
                session_id=session_id,
                task_handle=str(fixture["task_handle"]),
                grant_id=str(fixture["grant_id"]),
                workspace=repository,
                environment=deeplaw_environment,
                cwd=repository,
                idempotency_key=f"pass16-{scenario}-bind-root",
            )
            wrong_handles = fixture.get("wrong_task_handles", {})
            if not isinstance(wrong_handles, Mapping):
                raise QualificationError("public wrong-state task handles are invalid")
            wrong_worktree_handle = wrong_handles.get("wrong_worktree")
            if isinstance(wrong_worktree_handle, str):
                wrong_session = server.create()
                _bind_public_host_session(
                    deeplaw_executable,
                    vault=vault,
                    session_id=wrong_session,
                    task_handle=wrong_worktree_handle,
                    grant_id=str(fixture["grant_id"]),
                    workspace=concurrent,
                    environment=deeplaw_environment,
                    cwd=repository,
                    idempotency_key=f"pass16-{scenario}-bind-wrong-worktree",
                )
                wrong_capsule, _wrong_text = _resolve_public_host_continuity(
                    deeplaw_executable,
                    vault=vault,
                    session_id=wrong_session,
                    workspace=repository,
                    environment=deeplaw_environment,
                    cwd=repository,
                    forbidden_values=forbidden_values,
                )
                wrong_gaps = wrong_capsule.get("gaps", [])
                wrong_gap_codes = {
                    item.get("code")
                    for item in wrong_gaps
                    if isinstance(item, Mapping)
                }
                if wrong_capsule.get("status") != "gap" or (
                    "route_wrong_worktree" not in wrong_gap_codes
                ):
                    raise QualificationError("wrong worktree route did not fail closed")
                wrong_response = {
                    "status": "gap",
                    "gap_codes": sorted(str(code) for code in wrong_gap_codes),
                }
                route_receipts.append(
                    {
                        "operation": "wrong_worktree",
                        "status": "gap",
                        "session_sha256": _sha256(wrong_session.encode("utf-8")),
                        "parent_session_sha256": None,
                        "request_sha256": _sha256(b"resolve-host-continuity"),
                        "response_sha256": _sha256(_encoded(wrong_response)),
                        "gap_codes": wrong_response["gap_codes"],
                    }
                )
            turn("cli.run", prompt, precreated=True)
        # The wrapper creates this receipt immediately before exec'ing the MCP
        # child.  It cannot exist before the first real Host turn starts the
        # configured server, so validate it at the first observable boundary.
        capture_wrapper_receipt()
        if development:
            turn("cli.run.session", prompt)
            turn("cli.run.fork", prompt, fork=True)
            summarize_current_session()
            turn("cli.run.session", prompt)
        elif scenario == "resume_fork":
            turn("cli.run.session", prompt)
            if server is None or session_id is None or root_session_id is None:
                raise QualificationError("OpenCode fork parent session is missing")
            parent_session_id = session_id
            child_session_id = server.fork(parent_session_id)
            fork_response = {
                "child_session_sha256": _sha256(child_session_id.encode("utf-8")),
                "forked_from_id_sha256": _sha256(parent_session_id.encode("utf-8")),
            }
            route_receipts.append(
                {
                    "operation": "fork",
                    "status": "forked",
                    "session_sha256": fork_response["child_session_sha256"],
                    "parent_session_sha256": fork_response["forked_from_id_sha256"],
                    "request_sha256": _sha256(_encoded({"operation": "session.fork"})),
                    "response_sha256": _sha256(_encoded(fork_response)),
                    "gap_codes": [],
                }
            )
            _bind_public_host_session(
                deeplaw_executable,
                vault=vault,
                session_id=child_session_id,
                task_handle=str(fixture["task_handle"]),
                grant_id=str(fixture["grant_id"]),
                workspace=repository,
                environment=deeplaw_environment,
                cwd=repository,
                idempotency_key=f"pass16-{scenario}-bind-child",
            )
            session_id = child_session_id
            turn(
                "cli.run.fork",
                prompt,
                precreated=True,
                parent_session_id=parent_session_id,
            )
        elif scenario == "compaction_forget":
            # No synthetic/estimated compaction usage is admissible.  The
            # public message result must expose actual provider accounting.
            summarize_current_session()
            turn("cli.run.session", prompt)
            forget_before = _ledger_head(
                deeplaw_executable, vault, environment=deeplaw_environment, cwd=repository
            )
            forget_receipt = _forget_checkpoint(
                deeplaw_executable,
                vault=vault,
                fixture=fixture,
                environment=deeplaw_environment,
                cwd=repository,
            )
            forget_after = _ledger_head(
                deeplaw_executable, vault, environment=deeplaw_environment, cwd=repository
            )
            if (
                _extract_value(forget_receipt, "knowledge_id", "knowledgeId")
                != fixture["knowledge_id"]
                or forget_before == forget_after
            ):
                raise QualificationError("owner forget receipt did not bind the current checkpoint")
            mutation_boundaries.append(
                {
                    "kind": "forget",
                    "owner_enabled": True,
                    "read_mcp_write_performed": False,
                    "audit_changed": forget_before != forget_after,
                    "audit_head_before": forget_before,
                    "audit_head_after": forget_after,
                    "receipt_sha256": _sha256(_encoded(forget_receipt)),
                    "target_sha256": _sha256(str(fixture["knowledge_id"]).encode("utf-8")),
                }
            )
            turn(
                "cli.run.session",
                _candidate_prompt(selected_case, phase="post_forget"),
                post_forget=True,
            )
        else:
            # cold_start intentionally stops after one fresh Host session.
            pass
    except QualificationError as exc:
        safe_failure_code = _safe_failure_code(exc)
        failure = _empty_analysis(safe_failure_code)
        sanitized.append(failure["sanitized_events"])
        # A partially observed lifecycle is not presented as a valid prefix;
        # retain one explicit failed receipt so shared evidence validation cannot
        # mistake an incomplete Host sequence for a successful turn set.
        turns = []
        before = _ledger_head(
            deeplaw_executable, vault, environment=deeplaw_environment, cwd=repository
        )
        turns.append(
            {
                "status": "failed",
                "lifecycle_method": "not_applicable",
                "thread_id_sha256": None,
                "turn_id_sha256": None,
                "prompt_sha256": _sha256(prompt.encode("utf-8")),
                "final_response_sha256": None,
                "final_response_bytes": 0,
                "host_elapsed_ms": 0,
                "usage": failure["usage"],
                "actual_response_provider_id": None,
                "actual_response_model_id": None,
                "ledger_audit_head_before": before,
                "ledger_audit_head_after": before,
                "ledger_unchanged": True,
                "safe_read": failure["safe_read"],
                "sanitized_events": {
                    "name": f"opencode-run-{run_index}-turn-{len(turns)+1}.sanitized.jsonl",
                    "bytes": len(failure["sanitized_events"]),
                    "sha256": _sha256(failure["sanitized_events"]),
                },
            }
        )
        methods = sorted(
            {
                method
                for receipt in native_receipts
                for method in receipt.get("methods_observed", [])
                if isinstance(method, str)
            }
        )
        failure_codes = [safe_failure_code]
    else:
        methods = sorted(
            {
                method
                for receipt in native_receipts
                for method in receipt.get("methods_observed", [])
                if isinstance(method, str)
            }
        )
        failure_codes = []
        metrics = (
            {
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
            if development
            else _final_metrics(
                scenario,
                {
                    "first_call_valid": bool(turns)
                    and all(
                        turn_row["safe_read"].get("first_call_valid") is True
                        for turn_row in turns
                    ),
                    "provider_payloads": [
                        payload
                        for turn_row in turns
                        for payload in turn_row["safe_read"].get("provider_payloads", [])
                    ],
                },
                marker_checks=marker_checks,
            )
        )
        if not development and metrics["first_correct_action"] is not True:
            failure_codes.append("first_correct_action_invalid")
        if not development and metrics["wrong_state_admission"] != 0:
            failure_codes.append("wrong_state_admitted")
        if not development and metrics["stale_state_rejected"] is not True:
            failure_codes.append("stale_state_admitted")
        if not development and metrics["provider_boundary_correct"] is not True:
            failure_codes.append("provider_boundary_invalid")
        if scenario == "resume_fork" and metrics["decision_preservation"] is not True:
            failure_codes.append("decision_not_preserved")
        if scenario == "compaction_forget" and (
            metrics["forgotten_state_admission"] != 0 or metrics["gap_observed"] is not True
        ):
            failure_codes.append("forgotten_state_admitted")
    finally:
        if server is not None:
            server.stop()

    if "metrics" not in locals():
        metrics = (
            {
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
            if development
            else _final_metrics(
                scenario,
                {"provider_payloads": []},
                marker_checks=marker_checks,
            )
        )
    run = {
        "run_index": run_index,
        "scenario": semantic_task_family,
        "task_family": semantic_task_family,
        "status": "failed" if failure_codes else "passed",
        "failure_codes": failure_codes,
        "task_sha256": _sha256(prompt.encode("utf-8")),
        "new_thread": True,
        "methods_observed": methods,
        "native_receipts": native_receipts,
        "route_receipts": route_receipts,
        "plugin_receipt": plugin_receipt,
        "turns": turns,
        "metrics": metrics,
        "mutation_boundaries": mutation_boundaries,
    }
    run["metrics"]["evidence_sha256"] = pass13_evidence.metric_evidence_sha256(run)
    return run, sanitized, wrapper_receipts, tool_schema


def _cleanup_isolated_root(root: Path) -> None:
    """Remove one runner-owned temporary root, and fail closed on ambiguity."""

    if not isinstance(root, Path) or not root.name.startswith(_ISOLATED_ROOT_PREFIX):
        raise QualificationError("isolated runtime cleanup target is not runner-owned")
    temporary_parent = Path(tempfile.gettempdir()).resolve(strict=True)
    if root.parent.resolve(strict=True) != temporary_parent:
        raise QualificationError("isolated runtime cleanup target escaped the temporary root")
    if not root.exists():
        return
    if root.is_symlink() or not root.is_dir():
        raise QualificationError("isolated runtime cleanup target is unsafe")
    try:
        shutil.rmtree(root)
    except OSError as exc:
        raise QualificationError("isolated runtime cleanup failed") from exc
    if root.exists():
        raise QualificationError("isolated runtime cleanup did not remove its root")


def _cleanup_after_qualification(root: Path, original: BaseException | None = None) -> None:
    """Clean up while preserving any original qualification exception."""

    try:
        _cleanup_isolated_root(root)
    except BaseException as cleanup_error:
        if original is None:
            raise
        original.add_note(
            "SECURITY: isolated OpenCode runtime cleanup failed; qualification was not retained "
            f"as successful ({type(cleanup_error).__name__})"
        )


def _execute_qualification_body(
    *,
    candidate_wheel: Path,
    deeplaw_executable: Path,
    output_dir: Path,
    opencode_binary: Path,
    opencode_package: Path | None = None,
    host_launcher: Path,
    human_gold_path: Path | None,
    owner_dotenv: Path | None = None,
    host_identity_input: Path | None = None,
    root: Path,
    source_revision_id: str | None = None,
    expected_broker_sha256: str | None = None,
    candidate_binding_input: Path | None = None,
    run_id: str | None = None,
    evidence_run_id: int | None = None,
    qualification_run_id: int | None = None,
    mode: str = "qualification",
) -> dict[str, Any]:
    if source_revision_id is not None:
        raise QualificationError(
            "source_revision_id is a retired historical input; use the frozen Pass 16 task cases"
        )
    repository = Path(__file__).resolve().parents[2]
    if mode not in {"qualification", "diagnostic"}:
        raise QualificationError("OpenCode execution mode is invalid")
    if human_gold_path is not None:
        raise QualificationError(
            "OpenCode candidate runner must not receive Human Gold or reference labels"
        )
    if mode == "qualification":
        if (
            host_identity_input is None
            or opencode_package is None
            or expected_broker_sha256 is None
            or candidate_binding_input is None
            or run_id is None
            or evidence_run_id is None
            or qualification_run_id is None
        ):
            raise QualificationError(
                "OpenCode owner-external public fork-route and child plugin-event "
                "correlation control input is unavailable; qualification Host/model "
                "execution remains not_executed"
            )
        try:
            preflight = run_opencode_owner_external_zero_model_preflight(
                candidate_binding_input=candidate_binding_input,
                candidate_wheel=candidate_wheel,
                host_identity_input=host_identity_input,
                opencode_package=opencode_package,
                opencode_binary=opencode_binary,
                opencode_broker=host_launcher,
                expected_broker_sha256=expected_broker_sha256,
                task_case="continuity",
                run_id=run_id,
                evidence_run_id=evidence_run_id,
                qualification_run_id=qualification_run_id,
                repository=repository,
            )
        except (OSError, ValueError, QualificationError) as exc:
            raise QualificationError(
                "OpenCode owner-external zero-model preflight failed closed"
            ) from exc
        if (
            preflight.get("status") != "passed"
            or preflight.get("evidence_class") != "zero_model_preflight_only"
            or preflight.get("formal_admission") is not False
        ):
            raise QualificationError(
                "OpenCode owner-external zero-model preflight was not admitted"
            )
    else:
        raise QualificationError(
            "OpenCode owner-external public fork-route and child plugin-event "
            "correlation is unavailable; diagnostic Host/model execution remains "
            "not_executed"
        )
    if mode == "qualification" or host_identity_input is not None:
        owner_dotenv = _validate_owner_dotenv(
            owner_dotenv,
            repository=Path(__file__).resolve().parents[2],
        )
    agent_name = "qualification" if mode == "qualification" else "development"
    orchestrator = QualificationOrchestrator(
        host="opencode",
        repository=repository,
        candidate_wheel=candidate_wheel,
        deeplaw_executable=deeplaw_executable,
        output_dir=output_dir.resolve(strict=False),
        error_type=QualificationError,
        execution_mode=mode,
    )
    output_dir, binding, installed = orchestrator.prepare_candidate()
    output_dir.mkdir(parents=True)
    host_identity: Mapping[str, Any] | None = None
    expected_version = HISTORICAL_OPENCODE_VERSION_FIXTURE
    if host_identity_input is None:
        if mode == "qualification":
            raise QualificationError(
                "OpenCode formal qualification requires the repository-external Host identity input"
            )
    else:
        try:
            host_identity = host_preflight_receipt.load_host_identity_input(
                host_identity_input, repository=repository
            )
            expected_version = host_preflight_receipt.host_binary_identity(
                host_identity, "opencode"
            )["version"]
            _inspect_opencode_binary_static(
                opencode_binary,
                identity=host_identity,
                repository=repository,
            )
        except (
            host_preflight_receipt.HostIdentityValidationError,
            OSError,
            ValueError,
        ) as exc:
            raise QualificationError(
                "OpenCode Host identity input or executable was rejected"
            ) from exc
    canaries = {name: _sha256(name.encode("utf-8")) for name in _CANARY_NAMES}
    if root.is_symlink() or not root.is_dir():
        raise QualificationError("isolated runtime root is unavailable")
    environment = build_host_environment(
        root=root,
        opencode_binary=opencode_binary,
        node_binary=opencode_binary,
        canaries=canaries,
        owner_dotenv=owner_dotenv,
        repository=repository,
    )
    for name in (
        "host-home",
        "xdg-config",
        "xdg-data",
        "xdg-cache",
        "xdg-state",
        "tmp",
        "appdata",
        "localappdata",
        "opencode-config",
        "mcp-home",
        "mcp-config",
        "mcp-tmp",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    _freeze_local_plugin_dependency_state(
        root / "xdg-config" / "opencode", expected_version=expected_version
    )
    _freeze_local_plugin_dependency_state(
        root / "opencode-config", expected_version=expected_version
    )
    _write_mcp_wrapper(
        root / "deeplaw-closed-mcp",
        deeplaw_executable=deeplaw_executable,
        receipt_path=root / "mcp-wrapper-receipt.json",
        node_binary=opencode_binary,
    )
    config_path = root / "opencode.json"
    config_path.write_text(
        _canonical(build_opencode_config(agent_name=agent_name)) + "\n",
        encoding="utf-8",
    )
    # This config is never retained as an artifact; it is regenerated in the
    # isolated directory for this invocation only.
    if host_identity is None:
        # Diagnostic/unit seams may supply a minimal stub; formal
        # qualification uses the identity-bound branch below.
        binary_sha = _validate_binary(opencode_binary)
    else:
        binary_sha = _validate_binary(
            opencode_binary, identity=host_identity, repository=repository
        )
    broker_launcher_sha = _validate_owner_broker_launcher(
        host_launcher,
        host_binary=opencode_binary,
        host_binary_sha256=binary_sha,
        repository=repository,
        expected_broker_sha256=expected_broker_sha256,
    )
    runtime_check = _run_bounded_process(
        [deeplaw_executable, "--version"],
        environment={
            key: value
            for key, value in environment.items()
            if key not in {
                _PROVIDER_ENV_NAME,
                _OWNER_DOTENV_ENV_NAME,
                *_CANARY_NAMES,
            }
        },
        cwd=root,
    )
    runtime_text = runtime_check["stdout"].decode("utf-8", errors="replace").strip()
    if runtime_check["returncode"] != 0 or PACKAGE_VERSION not in runtime_text:
        raise QualificationError("installed DeepLaw runtime is not version 0.12.0")
    preflight_project = root / "preflight-project"
    preflight_project.mkdir(parents=True, exist_ok=True)
    preflight_plugin_receipt, _preflight_plugin_receipt_path = (
        _install_exact_opencode_plugin(
            repository=preflight_project,
            run_root=root,
            deeplaw_executable=deeplaw_executable,
            expected_version=expected_version,
        )
    )
    preflight = preflight_opencode(
        binary=opencode_binary,
        host_launcher=host_launcher,
        deeplaw_executable=deeplaw_executable,
        environment=environment,
        cwd=root,
        project_root=preflight_project,
        plugin_receipt=preflight_plugin_receipt,
        expected_broker_sha256=expected_broker_sha256,
        expected_host_binary_sha256=binary_sha,
        expected_version=expected_version,
        agent_name=agent_name,
    )
    broker_source = host_preflight_receipt.inspect_broker_source(
        host_launcher,
        repository=repository,
        host_binary=opencode_binary,
        expected_sha256=expected_broker_sha256,
    )
    if broker_source.get("failure_reason_code") is not None:
        raise QualificationError("OpenCode broker source preflight failed")
    preflight_receipt = host_preflight_receipt.build_receipt(
        host={
            "name": "opencode",
            "version": expected_version,
            "sha256": binary_sha,
        },
        broker_source=broker_source,
        status="passed",
        stage="complete",
        reason_code="preflight_passed",
        check_count=5,
    )
    host_preflight_receipt.write_receipt(output_dir, preflight_receipt)
    runs: list[dict[str, Any]] = []
    artifacts: dict[str, Path] = {}
    wrapper_receipts: list[Mapping[str, Any]] = []
    tool_schema_rows: list[dict[str, Any]] = []
    forbidden_values = tuple(
        value
        for value in (
            *canaries.values(),
            str(root),
            str(owner_dotenv) if owner_dotenv is not None else None,
        )
        if value is not None
    )
    diagnostic_fixture = (
        pass17_development_diagnostic.load_fixture() if mode == "diagnostic" else None
    )
    run_specs = (
        [(scenario, scenario) for scenario in SCENARIOS]
        if mode == "qualification"
        else [("development_diagnostic", "cold_start")]
    )
    for index, (reported_scenario, engine_scenario) in enumerate(run_specs, start=1):
        run_root = root / f"run-{index}"
        run, sanitized, wrapper_receipt, observed_tool_schema = _run_one_scenario(
            run_index=index,
            scenario=engine_scenario,
            opencode_binary=opencode_binary,
            host_launcher=host_launcher,
            deeplaw_executable=deeplaw_executable,
            environment=environment,
            run_root=run_root,
            expected_version=expected_version,
            forbidden_values=forbidden_values,
            case=(
                diagnostic_fixture
                if isinstance(diagnostic_fixture, Mapping)
                else None
            ),
            reported_scenario=reported_scenario,
            agent_name=agent_name,
        )
        runs.append(run)
        wrapper_receipts.extend(wrapper_receipt)
        tool_schema_rows.append(observed_tool_schema)
        path = output_dir / run["turns"][0]["sanitized_events"]["name"]
        retain_artifact(
            path,
            b"".join(sanitized),
            output_root=output_dir,
            forbidden_values=forbidden_values,
        )
        artifacts[f"sanitized_events_run_{index}"] = path
    if not tool_schema_rows or any(
        row != tool_schema_rows[0] for row in tool_schema_rows[1:]
    ):
        raise QualificationError("tools/list schema changed across Host runs")
    _cleanup_isolated_root(root)
    report = orchestrator.build_report(
        binding={
            "commit": binding["commit"],
            "tree": binding["tree"],
            "worktree_clean": True,
            **{
                key: installed[key]
                for key in (
                    "wheel_name",
                    "wheel_sha256",
                    "wheel_bytes",
                    "runtime_executable_sha256",
                    "import_path_class",
                    "contract_digests",
                )
            },
        },
        environment={
            "operating_system": platform.system(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
            "isolation": pass13_evidence.isolation_receipt(host="opencode"),
        },
        host_attestation={
            "binary_name": "opencode",
            "binary_sha256": binary_sha,
            "version": expected_version,
            "model": MODEL,
            "reasoning_effort": VARIANT,
            "actual_response_provider_id": "deepseek",
            "actual_response_model_id": "deepseek-v4-flash",
            "model_identity_semantics": (
                "request_pin_and_returned_runtime_id_not_weight_identity"
            ),
            "credential_broker_launcher_sha256": broker_launcher_sha,
            "credential_boundary": {
                "runner_secret_received": False,
                "runner_dotenv_path_received": False,
                "host_secret_injected_by": "owner_credential_broker",
                "external_process_receipt_required": True,
            },
            "authentication": {
                "status": "provider_available",
                "source": "owner_credential_broker",
                "auth_file_read": False,
                "checked": True,
                "raw_sha256": preflight["availability"]["raw_sha256"],
                "raw_bytes": preflight["availability"]["raw_bytes"],
            },
            "model_inventory": preflight["model_inventory"],
            "mcp_inventory": {
                "checked": True,
                "selected_present": True,
                "raw_sha256": preflight["resolved_config"]["raw_sha256"],
                "raw_bytes": preflight["resolved_config"]["raw_bytes"],
            },
            "availability": dict(preflight["availability"]),
        },
        tool_schema=tool_schema_rows[0],
        runs=runs,
        lifecycle={
            "host_owns_threads": True,
            "common_task_families": [item[0] for item in run_specs],
            "transport_seams": sorted(
                {
                    str(receipt["transport"])
                    for run in runs
                    for receipt in run.get("native_receipts", [])
                    if isinstance(receipt, Mapping)
                }
            ),
            "requested_operations": sorted(
                {
                    str(receipt["requested_operation"])
                    for run in runs
                    for receipt in run.get("native_receipts", [])
                    if isinstance(receipt, Mapping)
                }
            ),
            "methods_observed": sorted(
                {
                    str(method)
                    for run in runs
                    for method in run.get("methods_observed", [])
                }
            ),
            "deeplaw_session_store_created": False,
        },
        security={
            "mcp_child_closed_environment": len(wrapper_receipts) == len(run_specs),
            "only_knowledge_support_enabled": True,
            "absolute_path_leak": False,
            "secret_leak": False,
            "raw_transcript_retained": False,
            "hidden_reasoning_retained": False,
            "authentication_material_retained": False,
            "cleanup_complete": True,
        },
        not_executed=(
            ["Human Gold", "release_claim", "final_blind_holdout"]
            if mode == "qualification"
            else ["qualification", "Human Gold", "blind scoring", "release decision"]
        ),
    )
    report_path = output_dir / (
        "opencode-continuity-qualification.json"
        if mode == "qualification"
        else "opencode-development-diagnostic.json"
    )
    retain_artifact(
        report_path,
        (_canonical(report) + "\n").encode("utf-8"),
        output_root=output_dir,
        forbidden_values=forbidden_values,
    )
    if mode == "qualification":
        artifacts["qualification_report"] = report_path
    preflight_path = output_dir / host_preflight_receipt.RECEIPT_FILENAME
    if mode == "qualification":
        artifacts["preflight_receipt"] = preflight_path
        orchestrator.finalize_bundle(
            commit=binding["commit"],
            tree=binding["tree"],
            artifacts=artifacts,
            forbidden_values=forbidden_values,
        )
    return report


def execute_qualification(
    *,
    candidate_wheel: Path,
    deeplaw_executable: Path,
    output_dir: Path,
    opencode_binary: Path,
    opencode_package: Path | None = None,
    host_launcher: Path,
    human_gold_path: Path | None,
    owner_dotenv: Path | None = None,
    host_identity_input: Path | None = None,
    source_revision_id: str | None = None,
    expected_broker_sha256: str | None = None,
    candidate_binding_input: Path | None = None,
    run_id: str | None = None,
    evidence_run_id: int | None = None,
    qualification_run_id: int | None = None,
    mode: str = "qualification",
) -> dict[str, Any]:
    """Run one Host mode with an external root and deterministic cleanup."""

    owner_dotenv = _validate_owner_dotenv(
        owner_dotenv,
        repository=Path(__file__).resolve().parents[2],
    )
    root = Path(tempfile.mkdtemp(prefix=_ISOLATED_ROOT_PREFIX))
    try:
        result = _execute_qualification_body(
            candidate_wheel=candidate_wheel,
            deeplaw_executable=deeplaw_executable,
            output_dir=output_dir,
            opencode_binary=opencode_binary,
            opencode_package=opencode_package,
            host_launcher=host_launcher,
            human_gold_path=human_gold_path,
            owner_dotenv=owner_dotenv,
            host_identity_input=host_identity_input,
            root=root,
            source_revision_id=source_revision_id,
            expected_broker_sha256=expected_broker_sha256,
            candidate_binding_input=candidate_binding_input,
            run_id=run_id,
            evidence_run_id=evidence_run_id,
            qualification_run_id=qualification_run_id,
            mode=mode,
        )
    except BaseException as original:
        target = Path(output_dir).resolve(strict=False)
        receipt_path = target / host_preflight_receipt.RECEIPT_FILENAME
        if not receipt_path.exists() and target.is_dir() and not target.is_symlink():
            try:
                expected_version = "unknown"
                if host_identity_input is not None:
                    with suppress(
                        host_preflight_receipt.HostIdentityValidationError,
                        OSError,
                        ValueError,
                    ):
                        expected_version = host_preflight_receipt.host_binary_identity(
                            host_preflight_receipt.load_host_identity_input(
                                host_identity_input,
                                repository=Path(__file__).resolve().parents[2],
                            ),
                            "opencode",
                        )["version"]
                failed = host_preflight_receipt.failed_receipt(
                    host_name="opencode",
                    host_version=expected_version,
                    host_binary=Path(opencode_binary),
                    broker_path=Path(host_launcher),
                    repository=Path(__file__).resolve().parents[2],
                    expected_broker_sha256=expected_broker_sha256,
                    error=original,
                )
                host_preflight_receipt.write_receipt(target, failed)
            except BaseException as receipt_error:
                original.add_note(
                    "Host preflight receipt was not retained: "
                    f"{type(receipt_error).__name__}"
                )
        _cleanup_after_qualification(root, original)
        raise
    else:
        _cleanup_after_qualification(root)
        return result


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("qualification", "diagnostic"), default="qualification")
    parser.add_argument("--zero-model-preflight", action="store_true")
    parser.add_argument("--candidate-wheel", type=Path)
    parser.add_argument("--candidate-binding-input", type=Path)
    parser.add_argument("--deeplaw-executable", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--opencode-binary", type=Path, required=True)
    parser.add_argument("--opencode-package", type=Path)
    parser.add_argument("--opencode-launcher", type=Path, required=True)
    parser.add_argument("--opencode-dotenv", type=Path)
    parser.add_argument("--host-identity-input", type=Path)
    parser.add_argument("--expected-broker-sha256")
    parser.add_argument("--task-case", choices=host_process_receipt_v2.TASK_CASES)
    parser.add_argument("--run-id")
    parser.add_argument("--evidence-run-id", type=int)
    parser.add_argument("--qualification-run-id", type=int)
    parser.add_argument("--human-gold", type=Path)
    parser.add_argument("--source-revision-id")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.zero_model_preflight:
            required = {
                "--candidate-binding-input": args.candidate_binding_input,
                "--host-identity-input": args.host_identity_input,
                "--opencode-package": args.opencode_package,
                "--expected-broker-sha256": args.expected_broker_sha256,
                "--task-case": args.task_case,
                "--run-id": args.run_id,
                "--evidence-run-id": args.evidence_run_id,
                "--qualification-run-id": args.qualification_run_id,
            }
            if any(value is None for value in required.values()):
                raise QualificationError(
                    "OpenCode zero-model preflight is missing required control input"
                )
            result = run_opencode_owner_external_zero_model_preflight(
                candidate_binding_input=args.candidate_binding_input,
                candidate_wheel=args.candidate_wheel,
                host_identity_input=args.host_identity_input,
                opencode_package=args.opencode_package,
                opencode_binary=args.opencode_binary,
                opencode_broker=args.opencode_launcher,
                expected_broker_sha256=args.expected_broker_sha256,
                task_case=args.task_case,
                run_id=args.run_id,
                evidence_run_id=args.evidence_run_id,
                qualification_run_id=args.qualification_run_id,
            )
            return 0 if result.get("status") == "passed" else 1
        if args.opencode_dotenv is None:
            raise QualificationError("OpenCode owner dotenv path is required")
        if (
            args.candidate_wheel is None
            or args.deeplaw_executable is None
            or args.output_dir is None
            or args.opencode_package is None
        ):
            raise QualificationError(
                "OpenCode qualification is missing required execution input"
            )
        report = execute_qualification(
            candidate_wheel=args.candidate_wheel,
            deeplaw_executable=args.deeplaw_executable,
            output_dir=args.output_dir,
            opencode_binary=args.opencode_binary,
            opencode_package=args.opencode_package,
            host_launcher=args.opencode_launcher,
            human_gold_path=args.human_gold,
            owner_dotenv=args.opencode_dotenv,
            host_identity_input=args.host_identity_input,
            source_revision_id=args.source_revision_id,
            expected_broker_sha256=args.expected_broker_sha256,
            candidate_binding_input=args.candidate_binding_input,
            run_id=args.run_id,
            evidence_run_id=args.evidence_run_id,
            qualification_run_id=args.qualification_run_id,
            mode=args.mode,
        )
    except (OSError, QualificationError) as exc:
        print(f"qualification failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    return 0 if report.get("status") == "executed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
