"""Current-installed-wheel OpenCode 1.18.16 Pass 16 qualification runner.

The runner is deliberately independent from the historical continuity runners.  It
attests the wheel and runtime selected for this invocation, starts every OpenCode
scenario in a fresh isolated state, and keeps only bounded hashes/counts and the
contracted Provider Capsule transport receipt.  Raw prompts, model output, MCP
payloads, credentials, and host paths remain in memory only.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from benchmarks.hosts import (
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

MODEL = "deepseek/deepseek-v4-flash"
VARIANT = "max"
OPENCODE_VERSION = "1.18.16"
TOOL_NAME = "deeplaw_knowledge_knowledge_support"
RUN_COUNT = 3
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
PROVIDER_HARD_LIMIT_BYTES = 65_536
TIMEOUT_SECONDS = 300
MAX_DOTENV_BYTES = 64 * 1024
_ISOLATED_ROOT_PREFIX = "deeplaw-pass17-opencode-"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID = re.compile(r"^[0-9a-f]{40}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$")
_ABSOLUTE_PATH = re.compile(
    rb'(?:^|[\s=:"\'])/(?!/)[A-Za-z0-9._~-]+(?:/[^\s"\'\\]*)?|'
    rb'(?:^|[\s="\'(])[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._$-]+[\\/]'
)
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
        "OPENCODE_DISABLE_PROJECT_CONFIG",
        "OPENCODE_PURE",
        "DEEPSEEK_API_KEY",
        "NO_COLOR",
        "GIT_TERMINAL_PROMPT",
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
SCENARIO_TASKS = {
    scenario: pass16_continuity_cases.candidate_prompt(
        pass16_continuity_cases.task_case(scenario)
    )
    for scenario in SCENARIOS
}


def _candidate_prompt(
    case: Mapping[str, Any],
    task_binding: Mapping[str, Any],
    *,
    phase: str = "current",
) -> str:
    """Bind a neutral frozen task prompt to the exact current worktree."""

    normalized = pass16_continuity_cases.binding_sha256(task_binding)
    if normalized != task_binding.get("binding_sha256"):
        raise QualificationError("candidate task binding is inconsistent")
    return (
        pass16_continuity_cases.candidate_prompt(case, phase=phase)
        + " The canonical task_binding argument is "
        + _canonical(dict(task_binding))
        + ". End with the required bare four-key JSON object only; do not use a code fence, "
        "prefix, or suffix."
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


def load_deepseek_key(path: Path) -> str:
    """Read one exact ``DEEPSEEK_API_KEY`` assignment without interpolation."""

    selected: str | None = None
    try:
        if path.is_symlink():
            raise ValueError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_size > MAX_DOTENV_BYTES
            or details.st_nlink != 1
            or (os.name != "nt" and stat.S_IMODE(details.st_mode) != 0o600)
            or (
                hasattr(os, "geteuid")
                and os.geteuid() != details.st_uid
            )
        ):
            os.close(descriptor)
            raise ValueError
        with os.fdopen(descriptor, "r", encoding="utf-8", errors="strict", newline="") as stream:
            for raw_line in stream:
                line = raw_line.rstrip("\r\n").strip()
                if not line or line.startswith("#"):
                    continue
                match = re.fullmatch(r"DEEPSEEK_API_KEY=(.*)", line)
                if match is None or selected is not None:
                    raise ValueError
                value = match.group(1).strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if (
                    not value
                    or any(c in value for c in ("$", "`", "\\", "\x00"))
                    or any(c.isspace() for c in value)
                    or value.startswith(('"', "'"))
                    or value.endswith(('"', "'"))
                ):
                    raise ValueError
                selected = value
        if selected is None:
            raise ValueError
        return selected
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("DeepSeek credential file is invalid") from exc


def build_permission() -> dict[str, str]:
    return {"*": "deny", TOOL_NAME: "allow"}


def build_opencode_config(*, agent_name: str = "qualification") -> dict[str, Any]:
    if agent_name not in {"qualification", "development"}:
        raise QualificationError("OpenCode agent mode is invalid")
    permission = build_permission()
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
        "plugin": [],
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
            }
        },
        "mcp": {
            "deeplaw_knowledge": {
                "type": "local",
                "command": [
                    "./deeplaw-closed-mcp",
                    "knowledge",
                    "mcp",
                    "--stdio",
                    "--vault",
                    "vault",
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
    provider_key: str,
    canaries: Mapping[str, str] | None = None,
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
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "OPENCODE_PURE": "1",
        "DEEPSEEK_API_KEY": provider_key,
        "NO_COLOR": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "CI": "1",
    }
    if canaries:
        if set(canaries) != set(_CANARY_NAMES) or not all(
            isinstance(value, str) and value for value in canaries.values()
        ):
            raise QualificationError("qualification canaries are incomplete")
        values.update(canaries)
    if set(values) - EXPECTED_HOST_ENVIRONMENT_NAMES:
        raise QualificationError("host environment contains an unallowlisted name")
    return values


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
        or not {_PROVIDER_ENV_NAME, *_CANARY_NAMES}.issubset(set(blocked_host))
        or argv != ["deeplaw", "knowledge", "mcp", "--stdio", "--vault", "vault"]
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
    "child_argv": ["deeplaw", "knowledge", "mcp", "--stdio", "--vault", "vault"],
    "wrapper_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
    "child_executable_sha256": hashlib.sha256(child_executable.read_bytes()).hexdigest(),
    "environment_sha256": hashlib.sha256(
        json.dumps(child_environment, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest(),
}}, sort_keys=True, separators=(",", ":")) + "\\n", encoding="utf-8")
if blocked_child_present:
    raise SystemExit(91)
os.execve({str(deeplaw_executable)!r}, [
    {str(deeplaw_executable)!r}, "knowledge", "mcp", "--stdio", "--vault", "vault"
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
        or arguments.get("confirm_no_case_data") is not True
        or arguments.get("task_binding") != dict(expected_task_binding)
    ):
        raise QualificationError(
            "MCP call lacks the exact safe context and task-binding attestation"
        )
    return part, arguments, call_id


def _native_tool_observation(
    event: Mapping[str, Any],
    capsule: Mapping[str, Any],
    provider_text: str,
    *,
    expected_task_binding: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _part, arguments, call_id = _native_tool_arguments(
        event, expected_task_binding=expected_task_binding
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
    return {
        "call_count": len(observations),
        "first_call_valid": True,
        "bounded_retry_used": len(observations) == 2,
        "safe_read_operations": ["context"] * len(observations),
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
    expected_task_binding: Mapping[str, Any],
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Parse only the bounded event fields needed for Pass 16 qualification."""

    if not isinstance(data, bytes) or len(data) > MAX_OUTPUT_BYTES:
        raise QualificationError("OpenCode output exceeds the bounded limit")
    # The raw in-memory Host event must carry the exact task_binding argument
    # so admission can be verified. Retained sanitized evidence still forbids
    # every task-binding field and never preserves the raw tool arguments.
    _forbid_sensitive(data, forbidden_values, allow_task_binding=True)
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
            _native_tool_arguments(
                event, expected_task_binding=expected_task_binding
            )
            if state.get("status") != "completed":
                raise QualificationError("tool call did not complete")
            capsule, provider_text = _native_provider_capsule(state)
            observation, payload = _native_tool_observation(
                event,
                capsule,
                provider_text,
                expected_task_binding=expected_task_binding,
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
                raise QualificationError("final response schema is invalid") from exc
            try:
                Draft202012Validator(_FINAL_RESPONSE_SCHEMA).validate(parsed)
            except ValidationError as exc:
                raise QualificationError("final response schema is invalid") from exc
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
    environment: Mapping[str, str],
    cwd: Path,
) -> dict[str, Any]:
    """Seed current, superseded, wrong-task, and wrong-worktree routes via CLI."""

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
    if not isinstance(current, Mapping) or not isinstance(stale, Mapping):
        raise QualificationError("task case checkpoints are invalid")
    seed_before = _ledger_head(
        deeplaw_executable, vault, environment=environment, cwd=cwd
    )
    receipts: list[Mapping[str, Any]] = []
    run_id = f"run-pass16-{scenario}"
    current_run = _run_sink_request(
        deeplaw_executable,
        vault=vault,
        grant_id=grant_id,
        request={
            "operation": "record_run",
            "idempotency_key": f"pass16-{scenario}-current-run",
            "confirm_no_case_data": True,
            "run_id": run_id,
            "task": f"Pass 16 {scenario} qualification.",
            "host_id": "opencode-local-server-pass16",
            "model_id": MODEL,
            "status": "succeeded",
            "scope": "project",
            "sensitivity": "private",
            "run_metadata": {"task_binding": dict(primary_binding)},
        },
        environment=environment,
        cwd=cwd,
    )
    receipts.append(current_run)
    stale_run = _run_sink_request(
        deeplaw_executable,
        vault=vault,
        grant_id=grant_id,
        request={
            "operation": "remember",
            "idempotency_key": f"pass16-{scenario}-stale",
            "confirm_no_case_data": True,
            "title": f"Pass 16 {scenario} superseded checkpoint",
            "body": _checkpoint_body(case, stale, route="stale", binding=primary_binding),
            "kind": "memory",
            "memory_type": "working",
            "semantic_key": f"checkpoint:pass16:{scenario}",
            "expires_at": "2099-01-01T00:00:00Z",
            "scope": "project",
            "sensitivity": "private",
            "run_id": run_id,
            "model_id": MODEL,
            "tool_id": "opencode-local-server-pass16",
            "tags": ["pass16", "qualification", scenario, "stale"],
        },
        environment=environment,
        cwd=cwd,
    )
    receipts.append(stale_run)
    stale_knowledge_id = _extract_value(stale_run, "knowledge_id", "knowledgeId")
    stale_revision_id = _extract_value(stale_run, "revision_id", "revisionId")
    if not isinstance(stale_knowledge_id, str) or not isinstance(stale_revision_id, str):
        raise QualificationError("stale checkpoint omitted its CAS identity")
    current_checkpoint = _run_sink_request(
        deeplaw_executable,
        vault=vault,
        grant_id=grant_id,
        request={
            "operation": "remember",
            "idempotency_key": f"pass16-{scenario}-current",
            "confirm_no_case_data": True,
            "title": f"Pass 16 {scenario} current checkpoint",
            "body": _checkpoint_body(case, current, route="current", binding=primary_binding),
            "kind": "memory",
            "memory_type": "working",
            "semantic_key": f"checkpoint:pass16:{scenario}",
            "expires_at": "2099-01-01T00:00:00Z",
            "knowledge_id": stale_knowledge_id,
            "expected_revision_id": stale_revision_id,
            "scope": "project",
            "sensitivity": "private",
            "run_id": run_id,
            "model_id": MODEL,
            "tool_id": "opencode-local-server-pass16",
            "tags": ["pass16", "qualification", scenario],
        },
        environment=environment,
        cwd=cwd,
    )
    receipts.append(current_checkpoint)
    knowledge_id = _extract_value(current_checkpoint, "knowledge_id", "knowledgeId")
    revision_id = _extract_value(current_checkpoint, "revision_id", "revisionId")
    if not isinstance(knowledge_id, str) or not isinstance(revision_id, str):
        raise QualificationError("current checkpoint omitted its CAS identity")
    for challenge in case["wrong_state_challenges"]:
        if not isinstance(challenge, Mapping):
            raise QualificationError("wrong-state challenge is invalid")
        name = str(challenge["challenge"])
        # The stale checkpoint is already a superseded revision of the exact
        # current semantic identity above. Seeding another active stale route
        # with the current binding would make correct admission impossible.
        if name == "stale_checkpoint":
            continue
        binding = (
            concurrent_binding if name == "wrong_worktree" else primary_binding
        )
        if name == "wrong_task_line":
            binding = pass16_continuity_cases.git_binding(
                cwd, task_line=f"pass16-{scenario}-wrong-task-line"
            )
        route_run = _run_sink_request(
            deeplaw_executable,
            vault=vault,
            grant_id=grant_id,
            request={
                "operation": "record_run",
                "idempotency_key": f"pass16-{scenario}-{name}-run",
                "confirm_no_case_data": True,
                "run_id": f"{run_id}-{name}",
                "task": f"Pass 16 unrelated {name} route.",
                "host_id": "opencode-local-server-pass16",
                "model_id": MODEL,
                "status": "succeeded",
                "scope": "project",
                "sensitivity": "private",
                "run_metadata": {"task_binding": dict(binding)},
            },
            environment=environment,
            cwd=cwd,
        )
        receipts.append(route_run)
        route_checkpoint = dict(current)
        route_checkpoint["marker"] = challenge["marker"]
        route_checkpoint["decision"] = f"Do not admit {challenge['marker']} into this route."
        route_checkpoint["next_action"] = f"Reject unrelated {name} state."
        route_checkpoint["verified_fact"] = f"The {name} route is unrelated to this task."
        route_checkpoint["open_gap"] = "The current route remains owner-authorized."
        route = _run_sink_request(
            deeplaw_executable,
            vault=vault,
            grant_id=grant_id,
            request={
                "operation": "remember",
                "idempotency_key": f"pass16-{scenario}-{name}",
                "confirm_no_case_data": True,
                "title": f"Pass 16 {scenario} {name} route",
                "body": _checkpoint_body(case, route_checkpoint, route=name, binding=binding),
                "kind": "memory",
                "memory_type": "working",
                "semantic_key": f"checkpoint:pass16:{scenario}:{name}",
                "expires_at": "2099-01-01T00:00:00Z",
                "scope": "project",
                "sensitivity": "private",
                "run_id": f"{run_id}-{name}",
                "model_id": MODEL,
                "tool_id": "opencode-local-server-pass16",
                "tags": ["pass16", "qualification", scenario, name],
            },
            environment=environment,
            cwd=cwd,
        )
        receipts.append(route)
    seed_after = _ledger_head(
        deeplaw_executable, vault, environment=environment, cwd=cwd
    )
    return {
        "grant_id": grant_id,
        "knowledge_id": knowledge_id,
        "revision_id": revision_id,
        "seed_boundary": {
            "kind": "seed_checkpoint",
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
    return _run_sink_request(
        deeplaw_executable,
        vault=vault,
        grant_id=str(fixture["grant_id"]),
        request={
            "operation": "forget",
            "idempotency_key": "pass16-compaction-forget",
            "confirm_no_case_data": True,
            "knowledge_id": fixture["knowledge_id"],
            "expected_revision_id": fixture["revision_id"],
            "reason": "Owner-directed Pass 16 checkpoint forgetting.",
        },
        environment=environment,
        cwd=cwd,
    )


def _validate_binary(binary: Path) -> str:
    return _sha256_file(binary)


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
    """Return the pinned ``--pure run --format json`` task command."""

    if agent_name not in {"qualification", "development"}:
        raise QualificationError("OpenCode CLI agent mode is invalid")
    args: list[str] = [
        "--pure",
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
                "--pure",
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

    def messages(self, session_id: str) -> list[Any]:
        if _SESSION_ID.fullmatch(session_id) is None:
            raise QualificationError("OpenCode session identity is invalid")
        value = self.request("GET", f"/session/{session_id}/message")
        if not isinstance(value, list):
            raise QualificationError("OpenCode session messages response is invalid")
        return value

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


def _probe_model_availability(
    binary: Path,
    *,
    environment: Mapping[str, str],
    cwd: Path,
) -> dict[str, Any]:
    result = _run_opencode_command(
        binary,
        args=(
            "--pure",
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
            if name == _PROVIDER_ENV_NAME or name in _CANARY_NAMES
        ),
    )


def preflight_opencode(
    *,
    binary: Path,
    environment: Mapping[str, str],
    cwd: Path,
    provider_key: str | None = None,
    agent_name: str = "qualification",
) -> dict[str, Any]:
    forbidden_values = tuple(
        value
        for name, value in environment.items()
        if name == _PROVIDER_ENV_NAME or name in _CANARY_NAMES
    )
    inspection_environment = {
        name: value
        for name, value in environment.items()
        if name != _PROVIDER_ENV_NAME and name not in _CANARY_NAMES
    }
    version = _run_opencode_command(
        binary,
        args=("--version",),
        environment=inspection_environment,
        cwd=cwd,
    )
    _forbid_sensitive(version["stdout"] + version["stderr"], forbidden_values)
    version_text = version["stdout"].decode("utf-8", errors="replace").strip()
    if (
        version["returncode"] != 0
        or re.fullmatch(r"(?:opencode\s+)?1\.18\.16", version_text, re.IGNORECASE) is None
    ):
        raise QualificationError("OpenCode version is not exactly 1.18.16")
    models = _run_opencode_command(
        binary,
        args=("--pure", "models", "deepseek"),
        environment=inspection_environment,
        cwd=cwd,
    )
    _forbid_sensitive(
        bytes(models["stdout"]) + bytes(models["stderr"]),
        forbidden_values,
    )
    model_inventory = parse_model_inventory(models["stdout"], returncode=int(models["returncode"]))
    config = _run_opencode_command(
        binary,
        args=("--pure", "debug", "config"),
        environment=inspection_environment,
        cwd=cwd,
    )
    if config["returncode"] != 0:
        raise QualificationError("OpenCode resolved config command failed")
    config_bytes = bytes(config["stdout"])
    if len(config_bytes) > MAX_OUTPUT_BYTES:
        raise QualificationError("resolved OpenCode config exceeds the bound")
    _forbid_sensitive(config_bytes + bytes(config["stderr"]), forbidden_values)
    try:
        resolved = _strict_json(config_bytes)
    except QualificationError as exc:
        raise QualificationError("resolved OpenCode config is not JSON") from exc
    if not isinstance(resolved, Mapping):
        raise QualificationError("resolved OpenCode config is not an object")
    resolved_mcp = resolved.get("mcp")
    if not isinstance(resolved_mcp, Mapping) or set(resolved_mcp) != {"deeplaw_knowledge"}:
        raise QualificationError("resolved config enabled an unexpected MCP")
    mcp_entry = resolved_mcp.get("deeplaw_knowledge")
    if (
        not isinstance(mcp_entry, Mapping)
        or mcp_entry.get("type") != "local"
        or mcp_entry.get("enabled") is not True
        or mcp_entry.get("command")
        != [
            "./deeplaw-closed-mcp",
            "knowledge",
            "mcp",
            "--stdio",
            "--vault",
            "vault",
        ]
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
        or resolved.get("plugin") != []
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
    availability = None
    if provider_key is not None:
        availability_config = cwd / "availability-opencode.json"
        no_tools_config = build_opencode_config(agent_name=agent_name)
        no_tools_config["mcp"] = {}
        availability_config.write_text(_canonical(no_tools_config) + "\n", encoding="utf-8")
        availability = _probe_model_availability(
            binary,
            environment={
                **environment,
                _PROVIDER_ENV_NAME: provider_key,
                "OPENCODE_CONFIG": str(availability_config),
            },
            cwd=cwd,
        )
        if availability["status"] != "available":
            raise QualificationError("DeepSeek model availability probe failed")
    return {
        "version": OPENCODE_VERSION,
        "version_sha256": _sha256(version["stdout"]),
        "version_bytes": len(version["stdout"]),
        "model_inventory": model_inventory,
        "resolved_config": config_receipt,
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


def _prepare_scenario_state(
    *,
    base_environment: Mapping[str, str],
    run_root: Path,
    deeplaw_executable: Path,
    node_binary: Path,
    agent_name: str = "qualification",
) -> tuple[dict[str, str], Path]:
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
            "knowledge",
            "mcp",
            "--stdio",
            "--vault",
            "vault",
        ]
    config_path = run_root / "opencode.json"
    config_path.write_text(_canonical(config) + "\n", encoding="utf-8")
    environment = dict(base_environment)
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
        }
    )
    return environment, receipt


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
    return known.get(str(exc), "host_qualification_failure")


def _compaction_usage_from_messages(messages: Sequence[Any]) -> dict[str, int]:
    """Read actual AI-compaction usage from the public session message API."""

    matches: list[Mapping[str, Any]] = []
    for row in messages:
        if not isinstance(row, Mapping):
            continue
        info = row.get("info")
        if not isinstance(info, Mapping):
            continue
        if (
            info.get("role") == "assistant"
            and (info.get("summary") is True or info.get("mode") == "compaction")
            and isinstance(info.get("tokens"), Mapping)
        ):
            matches.append(info["tokens"])
    if len(matches) != 1:
        raise QualificationError(
            "OpenCode public session messages omitted one actual compaction usage"
        )
    return _require_actual_usage(_normalize_usage({"tokens": matches[0]}))


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
    deeplaw_executable: Path,
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
    scenario_environment, wrapper_receipt_path = _prepare_scenario_state(
        base_environment=environment,
        run_root=run_root,
        deeplaw_executable=deeplaw_executable,
        node_binary=opencode_binary,
        agent_name=agent_name,
    )
    deeplaw_environment = {
        name: value
        for name, value in scenario_environment.items()
        if name != _PROVIDER_ENV_NAME and name not in _CANARY_NAMES
    }
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
    sanitized: list[bytes] = []
    wrapper_receipts: list[Mapping[str, Any]] = []
    session_id: str | None = None
    root_session_id: str | None = None
    server: _OpenCodeLocalServer | None = None
    compaction_usage: dict[str, int | str] | None = None
    prompt = (
        pass17_development_diagnostic.candidate_prompt(selected_case)
        + " The canonical task_binding argument is "
        + _canonical(dict(primary_binding))
        + ". End with the required bare four-key JSON object only; do not use a code fence, "
        "prefix, or suffix."
        if development
        else _candidate_prompt(selected_case, primary_binding)
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
    ) -> None:
        nonlocal session_id, root_session_id, compaction_usage
        previous_session_id = session_id
        before = _ledger_head(
            deeplaw_executable, vault, environment=deeplaw_environment, cwd=repository
        )
        started = time.monotonic()
        args = _opencode_cli_turn_args(
            session_id=session_id,
            fork=fork,
            agent_name=agent_name,
        )
        result = _run_opencode_command(
            opencode_binary,
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
                expected_task_binding=primary_binding,
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
                if str(exc) == "final response schema is invalid":
                    raise QualificationError(
                        f"OpenCode {stage} final response schema is invalid"
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
        if session_id is not None and not fork and observed_session != session_id:
            raise QualificationError("OpenCode resume changed the session identity")
        session_id = observed_session
        if root_session_id is None:
            root_session_id = observed_session
        usage = analysis["usage"]
        if compaction_usage is not None:
            usage = _merge_usage(compaction_usage, usage)
            compaction_usage = None
            analysis["usage"] = usage
        if not development:
            check = _marker_check(
                analysis,
                case=selected_case,
                post_forget=post_forget,
            )
            marker_checks.append(check)
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
                "ledger_audit_head_before": before,
                "ledger_audit_head_after": after,
                "ledger_unchanged": before == after,
                "safe_read": analysis["safe_read"],
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
        relation = "new" if previous_session_id is None else "fork" if fork else "resume"
        sanitized_args = [
            "<session-sha256>" if item == previous_session_id else item for item in args
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
                parent_identity=previous_session_id,
                root_identity=root_session_id,
                relation=relation,
                actual_provider_usage=usage,
            )
        )
        if server is None:
            raise QualificationError("OpenCode local session server is unavailable")
        session_info = server.resume(observed_session)
        response_session = _extract_value(session_info, "id", "sessionID", "sessionId")
        parent_session = _extract_value(session_info, "parentID", "parentId", "parent_id")
        if response_session != observed_session or (
            fork and parent_session != previous_session_id
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
                },
                current_identity=observed_session,
                parent_identity=(
                    parent_session
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
        server.summarize(session_id)
        messages = server.messages(session_id)
        compaction_usage = _compaction_usage_from_messages(messages)
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
                    transport="opencode_loopback_http",
                    request_seam="GET session/:sessionID/message",
                    requested_operation="session.messages",
                    sanitized_request={
                        "session_id_sha256": _sha256(session_id.encode("utf-8"))
                    },
                    observation_kind="native_response",
                    methods_observed=["session.messages"],
                    sanitized_observation={
                        "response": "Message.Info[]",
                        "message_count": len(messages),
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
            ]
        )

    try:
        server = _OpenCodeLocalServer(
            binary=opencode_binary,
            environment=scenario_environment,
            cwd=repository,
            root=run_root,
            forbidden_output_values=tuple(
                value
                for name, value in scenario_environment.items()
                if name == _PROVIDER_ENV_NAME or name in _CANARY_NAMES
            ),
        )
        server.start()
        turn("cli.run", prompt)
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
            turn("cli.run.fork", prompt, fork=True)
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
                _candidate_prompt(selected_case, primary_binding, phase="post_forget"),
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
    dotenv: Path,
    human_gold_path: Path | None,
    root: Path,
    source_revision_id: str | None = None,
    mode: str = "qualification",
) -> dict[str, Any]:
    if source_revision_id is not None:
        raise QualificationError(
            "source_revision_id is a retired historical input; use the frozen Pass 16 task cases"
        )
    repository = Path(__file__).resolve().parents[2]
    if mode not in {"qualification", "diagnostic"}:
        raise QualificationError("OpenCode execution mode is invalid")
    if mode == "diagnostic" and human_gold_path is not None:
        raise QualificationError("OpenCode diagnostic must not receive Human Gold")
    if mode == "qualification":
        # No candidate preparation or Provider/model process may start before
        # the external frozen Gold satisfies its closed structural contract.
        from benchmarks.evaluator.score_pass16_host_continuity import (
            HumanGoldValidationError,
            load_human_gold,
        )

        if human_gold_path is None:
            raise QualificationError(
                "OpenCode qualification requires frozen external Human Gold"
            )
        try:
            load_human_gold(
                Path(human_gold_path),
                repository=repository,
                candidate_wheel_path=candidate_wheel,
            )
        except HumanGoldValidationError as exc:
            raise QualificationError(
                "OpenCode qualification requires frozen external Human Gold"
            ) from exc
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
    provider_key = load_deepseek_key(dotenv)
    canaries = {name: _sha256(name.encode("utf-8")) for name in _CANARY_NAMES}
    if root.is_symlink() or not root.is_dir():
        raise QualificationError("isolated runtime root is unavailable")
    environment = build_host_environment(
        root=root,
        opencode_binary=opencode_binary,
        node_binary=opencode_binary,
        provider_key=provider_key,
        canaries=canaries,
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
    binary_sha = _validate_binary(opencode_binary)
    runtime_check = _run_bounded_process(
        [deeplaw_executable, "--version"],
        environment={
            key: value
            for key, value in environment.items()
            if key != _PROVIDER_ENV_NAME and key not in _CANARY_NAMES
        },
        cwd=root,
    )
    runtime_text = runtime_check["stdout"].decode("utf-8", errors="replace").strip()
    if runtime_check["returncode"] != 0 or PACKAGE_VERSION not in runtime_text:
        raise QualificationError("installed DeepLaw runtime is not version 0.12.0")
    preflight = preflight_opencode(
        binary=opencode_binary,
        environment=environment,
        cwd=root,
        provider_key=provider_key,
        agent_name=agent_name,
    )
    runs: list[dict[str, Any]] = []
    artifacts: dict[str, Path] = {}
    wrapper_receipts: list[Mapping[str, Any]] = []
    tool_schema_rows: list[dict[str, Any]] = []
    forbidden_values = (provider_key, *canaries.values(), str(root))
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
            deeplaw_executable=deeplaw_executable,
            environment=environment,
            run_root=run_root,
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
            "version": OPENCODE_VERSION,
            "model": MODEL,
            "reasoning_effort": VARIANT,
            "authentication": {
                "status": "provider_available",
                "source": "process_environment",
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
    preflight_receipt = {
        "isolation": pass13_evidence.isolation_receipt(host="opencode"),
        "opencode_version_sha256": preflight["version_sha256"],
        "opencode_version_bytes": preflight["version_bytes"],
        "model_inventory": preflight["model_inventory"],
        "resolved_config": preflight["resolved_config"],
        "availability": preflight["availability"],
        "mcp_wrapper_receipts": wrapper_receipts,
    }
    preflight_path = output_dir / "opencode-preflight-receipt.json"
    _write_json(
        preflight_path,
        preflight_receipt,
        output_root=output_dir,
        forbidden_values=forbidden_values,
    )
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
    dotenv: Path,
    human_gold_path: Path | None,
    source_revision_id: str | None = None,
    mode: str = "qualification",
) -> dict[str, Any]:
    """Run one Host mode with an external root and deterministic cleanup."""

    root = Path(tempfile.mkdtemp(prefix=_ISOLATED_ROOT_PREFIX))
    try:
        result = _execute_qualification_body(
            candidate_wheel=candidate_wheel,
            deeplaw_executable=deeplaw_executable,
            output_dir=output_dir,
            opencode_binary=opencode_binary,
            dotenv=dotenv,
            human_gold_path=human_gold_path,
            root=root,
            source_revision_id=source_revision_id,
            mode=mode,
        )
    except BaseException as original:
        _cleanup_after_qualification(root, original)
        raise
    else:
        _cleanup_after_qualification(root)
        return result


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("qualification", "diagnostic"), default="qualification")
    parser.add_argument("--candidate-wheel", type=Path, required=True)
    parser.add_argument("--deeplaw-executable", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--opencode-binary", type=Path, required=True)
    parser.add_argument("--dotenv", type=Path, required=True)
    parser.add_argument("--human-gold", type=Path)
    parser.add_argument("--source-revision-id")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = execute_qualification(
            candidate_wheel=args.candidate_wheel,
            deeplaw_executable=args.deeplaw_executable,
            output_dir=args.output_dir,
            opencode_binary=args.opencode_binary,
            dotenv=args.dotenv,
            human_gold_path=args.human_gold,
            source_revision_id=args.source_revision_id,
            mode=args.mode,
        )
    except (OSError, QualificationError) as exc:
        print(f"qualification failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    return 0 if report.get("status") == "executed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
