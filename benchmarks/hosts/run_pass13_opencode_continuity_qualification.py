"""Current-installed-wheel OpenCode 1.18.16 Pass 13 qualification runner.

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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from benchmarks.hosts import pass13_evidence
from benchmarks.hosts.pass13_orchestrator import (
    PACKAGE_VERSION,
    QualificationOrchestrator,
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
_ISOLATED_ROOT_PREFIX = "deeplaw-pass13-opencode-"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID = re.compile(r"^[0-9a-f]{40}$")
_ABSOLUTE_PATH = re.compile(
    rb'(?:^|[\s=:"\'])/(?!/)[A-Za-z0-9._~-]+(?:/[^\s"\'\\]*)?|'
    rb"[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._$-]+[\\/]"
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
SCENARIOS = ("projection_status", "source_forget", "provider_boundary")
SCENARIO_TASKS = {
    "projection_status": (
        "Read the newly added uncompiled Source through bounded knowledge context and verify "
        "that the Gap does not claim Wiki projection readiness. Report only bounded, verifiable "
        "knowledge context. Do not mutate, publish, or invoke any tool other than "
        "the read-only DeepLaw knowledge support operation. After the tool call, return exactly "
        "one JSON object with a short summary field."
    ),
    "source_forget": (
        "Verify the owner-approved temporary Source withdrawal state and its retained-evidence "
        "boundary. Use only read-only DeepLaw knowledge support; do not perform a write. After "
        "the tool call, return exactly one JSON object with a short summary field."
    ),
    "provider_boundary": (
        "Verify that provider-visible context stays within the bounded capsule and "
        "reports any gap honestly. Use only read-only DeepLaw knowledge support. After the "
        "tool call, return exactly one JSON object with a short summary field."
    ),
}
_FINAL_RESPONSE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["summary"],
    "properties": {"summary": {"type": "string", "minLength": 1, "maxLength": 1000}},
}


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


def _forbid_sensitive(data: bytes, forbidden_values: Sequence[str] = ()) -> None:
    if _ABSOLUTE_PATH.search(data):
        raise QualificationError("evidence contains an absolute path")
    lowered = data.lower()
    if any(field in lowered for field in _FORBIDDEN_FIELDS):
        raise QualificationError("evidence contains a forbidden field")
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
        if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_DOTENV_BYTES:
            os.close(descriptor)
            raise ValueError
        with os.fdopen(descriptor, "r", encoding="utf-8", errors="strict", newline="") as stream:
            for raw_line in stream:
                line = raw_line.rstrip("\r\n").strip()
                if not line or line.startswith("#"):
                    continue
                if "DEEPSEEK_API_KEY" not in line:
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


def build_opencode_config() -> dict[str, Any]:
    permission = build_permission()
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": MODEL,
        "small_model": MODEL,
        "default_agent": "qualification",
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
            "qualification": {
                "description": "Pass 13 read-only qualification",
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
    normalized_usage = _analyze_availability_events(stdout, forbidden_values=forbidden_values)
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


def _event_tool_output(state: Mapping[str, Any]) -> Mapping[str, Any]:
    output = state.get("output")
    if isinstance(output, (str, bytes)):
        output = _strict_json(output)
    if not isinstance(output, Mapping):
        raise QualificationError("completed MCP output is not an object")
    content = output.get("content")
    structured = output.get("structuredContent")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(structured, Mapping):
        raise QualificationError("completed MCP output has no exact Provider transport")
    return output


def _tool_observation(event: Mapping[str, Any], output: Mapping[str, Any]) -> dict[str, Any]:
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
    structured = output.get("structuredContent")
    if (
        not isinstance(arguments, Mapping)
        or not isinstance(structured, Mapping)
        or arguments.get("operation") != structured.get("operation")
        or arguments.get("operation") != "context"
        or arguments.get("confirm_no_case_data") is not True
    ):
        raise QualificationError("MCP call lacks the exact safe context attestation")
    result_bytes = _encoded(output)
    structured_bytes = _encoded(output["structuredContent"])
    return {
        "call_id_sha256": _sha256(call_id.encode("utf-8")),
        "server": "deeplaw",
        "tool_name": "knowledge_support",
        "status": "completed",
        "arguments_sha256": _sha256(_encoded(arguments)),
        "arguments_bytes": len(_encoded(arguments)),
        "result_sha256": _sha256(result_bytes),
        "result_bytes": len(result_bytes),
        "structured_content_sha256": _sha256(structured_bytes),
        "structured_content_bytes": len(structured_bytes),
    }


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
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Parse only the bounded event fields needed for Pass 13 qualification."""

    if not isinstance(data, bytes) or len(data) > MAX_OUTPUT_BYTES:
        raise QualificationError("OpenCode output exceeds the bounded limit")
    _forbid_sensitive(data, forbidden_values)
    observations: list[dict[str, Any]] = []
    outputs: list[Mapping[str, Any]] = []
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
            if not isinstance(state, Mapping) or state.get("status") != "completed":
                raise QualificationError("tool call did not complete")
            output = _event_tool_output(state)
            observations.append(_tool_observation(event, output))
            outputs.append(output)
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
            parsed = _strict_json(text)
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
    usage = _sum_usages(usages)
    try:
        safe_read = pass13_evidence.analyze_safe_read_calls(observations, outputs)
    except pass13_evidence.EvidenceValidationError as exc:
        raise QualificationError(str(exc)) from exc
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
            "pass13-model-availability",
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
) -> dict[str, Any]:
    version = _run_opencode_command(binary, args=("--version",), environment=environment, cwd=cwd)
    version_text = version["stdout"].decode("utf-8", errors="replace").strip()
    if (
        version["returncode"] != 0
        or re.fullmatch(r"(?:opencode\s+)?1\.18\.16", version_text, re.IGNORECASE) is None
    ):
        raise QualificationError("OpenCode version is not exactly 1.18.16")
    models = _run_opencode_command(
        binary,
        args=("--pure", "models", "deepseek"),
        environment={**environment, _PROVIDER_ENV_NAME: ""},
        cwd=cwd,
    )
    model_inventory = parse_model_inventory(models["stdout"], returncode=int(models["returncode"]))
    config = _run_opencode_command(
        binary,
        args=("--pure", "debug", "config"),
        environment=environment,
        cwd=cwd,
    )
    if config["returncode"] != 0:
        raise QualificationError("OpenCode resolved config command failed")
    config_bytes = bytes(config["stdout"])
    if len(config_bytes) > MAX_OUTPUT_BYTES:
        raise QualificationError("resolved OpenCode config exceeds the bound")
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
    resolved_qualification = (
        resolved_agent.get("qualification") if isinstance(resolved_agent, Mapping) else None
    )
    if (
        resolved_model != MODEL
        or resolved.get("small_model") != MODEL
        or resolved.get("enabled_providers") != ["deepseek"]
        or resolved.get("share") != "disabled"
        or resolved.get("snapshot") is not False
        or resolved.get("plugin") != []
        or resolved.get("permission") != build_permission()
        or not isinstance(resolved_qualification, Mapping)
        or resolved_qualification.get("variant") != VARIANT
        or resolved_qualification.get("permission") != build_permission()
    ):
        raise QualificationError("resolved config selected an unexpected model")
    config_receipt = {
        "raw_sha256": _sha256(config_bytes),
        "raw_bytes": len(config_bytes),
    }
    availability = None
    if provider_key is not None:
        availability_config = cwd / "availability-opencode.json"
        no_tools_config = build_opencode_config()
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
    retention_wording_correct: bool | None = None,
) -> dict[str, Any]:
    payloads = safe_read.get("provider_payloads")
    payloads = payloads if isinstance(payloads, list) else []
    gap_codes = {
        code
        for payload in payloads
        if isinstance(payload, Mapping)
        for code in payload.get("gap_codes", [])
        if isinstance(code, str)
    }
    gap_observed = any(
        payload.get("gap_count", 0) > 0 for payload in payloads if isinstance(payload, Mapping)
    )
    statement_count = sum(
        int(payload.get("statement_count", 0))
        for payload in payloads
        if isinstance(payload, Mapping)
    )
    provider_boundary_correct = bool(payloads) and all(
        payload.get("delivery_match") is True
        and payload.get("write_performed") is False
        and 0 < int(payload.get("provider_bytes", 0)) <= PROVIDER_HARD_LIMIT_BYTES
        for payload in payloads
        if isinstance(payload, Mapping)
    )
    return {
        "first_correct_action": None,
        "decision_preservation": None,
        "wrong_state_admission": None,
        "stale_state_rejected": None,
        "forgotten_state_admission": (
            0 if scenario == "source_forget" and statement_count == 0 else None
        ),
        "gap_observed": gap_observed if scenario == "source_forget" else None,
        "projection_state_correct": (
            "uncompiled_source" in gap_codes if scenario == "projection_status" else None
        ),
        "retention_wording_correct": retention_wording_correct
        if scenario == "source_forget"
        else None,
        "provider_boundary_correct": provider_boundary_correct,
        "evidence_sha256": "0" * 64,
    }


def _run_source_forget(
    deeplaw_executable: Path,
    *,
    vault: Path,
    source_revision_id: str,
    environment: Mapping[str, str],
    cwd: Path,
) -> Mapping[str, Any]:
    result = _run_bounded_process(
        [
            deeplaw_executable,
            "knowledge",
            "forget",
            "--vault",
            vault,
            "--source-revision-id",
            source_revision_id,
            "--reason",
            "Owner withdrew this Source Revision from current admission.",
            "--confirm",
        ],
        environment=environment,
        cwd=cwd,
    )
    if result["returncode"] != 0:
        raise QualificationError("installed public source-forget CLI failed")
    value = _strict_json(result["stdout"])
    if not isinstance(value, Mapping):
        raise QualificationError("source-forget CLI receipt is not an object")
    validate_source_forget_receipt(value)
    return value


def _extract_source_revision_id(value: Any) -> str | None:
    if isinstance(value, Mapping):
        selected = value.get("source_revision_id")
        if isinstance(selected, str) and selected:
            return selected
        for nested in value.values():
            found = _extract_source_revision_id(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _extract_source_revision_id(nested)
            if found is not None:
                return found
    return None


def _seed_source_forget_fixture(
    deeplaw_executable: Path,
    *,
    vault: Path,
    source_path: Path,
    environment: Mapping[str, str],
    cwd: Path,
) -> tuple[str, Mapping[str, Any]]:
    added = _run_bounded_process(
        [
            deeplaw_executable,
            "knowledge",
            "source",
            "add",
            "--vault",
            vault,
            "--source",
            source_path,
            "--source-kind",
            "document",
            "--trust",
            "user_provided",
            "--sensitivity",
            "private",
            "--confirm-no-case-data",
        ],
        environment=environment,
        cwd=cwd,
    )
    if added["returncode"] != 0:
        raise QualificationError("temporary qualification source add failed")
    value = _strict_json(added["stdout"])
    source_revision_id = _extract_source_revision_id(value)
    if source_revision_id is None:
        raise QualificationError("source add receipt omitted a Source Revision identity")
    if not isinstance(value, Mapping):
        raise QualificationError("source add receipt is not an object")
    return source_revision_id, value


def _initialize_temp_vault(
    deeplaw_executable: Path,
    *,
    vault: Path,
    environment: Mapping[str, str],
    cwd: Path,
) -> None:
    result = _run_bounded_process(
        [
            deeplaw_executable,
            "knowledge",
            "init",
            "--vault",
            vault,
            "--name",
            "pass13-opencode",
            "--scope",
            "personal",
        ],
        environment=environment,
        cwd=cwd,
    )
    if result["returncode"] != 0:
        raise QualificationError("temporary qualification Vault initialization failed")


def _prepare_scenario_state(
    *,
    base_environment: Mapping[str, str],
    run_root: Path,
    deeplaw_executable: Path,
    node_binary: Path,
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
    config = build_opencode_config()
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


def _run_one_scenario(
    *,
    run_index: int,
    scenario: str,
    opencode_binary: Path,
    deeplaw_executable: Path,
    environment: Mapping[str, str],
    run_root: Path,
    forbidden_values: Sequence[str],
    source_revision_id: str | None,
) -> tuple[dict[str, Any], bytes, Mapping[str, Any] | None]:
    run_root.mkdir(parents=True, exist_ok=True)
    scenario_environment, wrapper_receipt_path = _prepare_scenario_state(
        base_environment=environment,
        run_root=run_root,
        deeplaw_executable=deeplaw_executable,
        node_binary=opencode_binary,
    )
    deeplaw_environment = {
        name: value
        for name, value in scenario_environment.items()
        if name != _PROVIDER_ENV_NAME and name not in _CANARY_NAMES
    }
    vault = run_root / "vault"
    _initialize_temp_vault(
        deeplaw_executable,
        vault=vault,
        environment=deeplaw_environment,
        cwd=run_root,
    )
    mutation_boundaries: list[dict[str, Any]] = []
    retention_wording_correct: bool | None = None
    selected_source_revision_id: str | None = None
    if scenario in {"projection_status", "source_forget"}:
        if source_revision_id is not None:
            raise QualificationError(
                "external Source Revision cannot be bound to a fresh isolated Vault"
            )
        source_path = run_root / "qualification-source.md"
        source_path.write_text(
            "Pass 13 temporary owner source for source withdrawal qualification.\n",
            encoding="utf-8",
        )
        seed_before = _ledger_head(
            deeplaw_executable, vault, environment=deeplaw_environment, cwd=run_root
        )
        selected_source_revision_id, seed_receipt = _seed_source_forget_fixture(
            deeplaw_executable,
            vault=vault,
            source_path=source_path,
            environment=deeplaw_environment,
            cwd=run_root,
        )
        seed_after = _ledger_head(
            deeplaw_executable, vault, environment=deeplaw_environment, cwd=run_root
        )
        mutation_boundaries.append(
            {
                "kind": "seed_checkpoint",
                "owner_enabled": True,
                "read_mcp_write_performed": False,
                "audit_changed": seed_before != seed_after,
                "audit_head_before": seed_before,
                "audit_head_after": seed_after,
                "receipt_sha256": _sha256(_encoded(seed_receipt)),
                "target_sha256": _sha256(selected_source_revision_id.encode("utf-8")),
            }
        )
    if scenario == "source_forget":
        if selected_source_revision_id is None:
            raise QualificationError("source-forget fixture identity is missing")
        forget_before = _ledger_head(
            deeplaw_executable, vault, environment=deeplaw_environment, cwd=run_root
        )
        receipt = _run_source_forget(
            deeplaw_executable,
            vault=vault,
            source_revision_id=selected_source_revision_id,
            environment=deeplaw_environment,
            cwd=run_root,
        )
        forget_after = _ledger_head(
            deeplaw_executable, vault, environment=deeplaw_environment, cwd=run_root
        )
        retention_wording_correct = validate_source_forget_receipt(receipt)
        mutation_boundaries.append(
            {
                "kind": "forget",
                "owner_enabled": True,
                "read_mcp_write_performed": False,
                "audit_changed": forget_before != forget_after,
                "audit_head_before": forget_before,
                "audit_head_after": forget_after,
                "receipt_sha256": _sha256(_encoded(receipt)),
                "target_sha256": _sha256(selected_source_revision_id.encode("utf-8")),
            }
        )
    before = _ledger_head(
        deeplaw_executable, vault, environment=deeplaw_environment, cwd=run_root
    )
    if scenario == "provider_boundary":
        mutation_boundaries.append(
            {
                "kind": "none",
                "owner_enabled": False,
                "read_mcp_write_performed": False,
                "audit_changed": False,
                "audit_head_before": before,
                "audit_head_after": before,
                "receipt_sha256": None,
                "target_sha256": None,
            }
        )
    started = time.monotonic()
    result = _run_opencode_command(
        opencode_binary,
        args=(
            "--pure",
            "run",
            "--format",
            "json",
            "--model",
            MODEL,
            "--variant",
            VARIANT,
            "--agent",
            "qualification",
        ),
        environment=scenario_environment,
        cwd=run_root,
        input_bytes=(SCENARIO_TASKS[scenario] + "\n").encode("utf-8"),
    )
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    after = _ledger_head(
        deeplaw_executable, vault, environment=deeplaw_environment, cwd=run_root
    )
    try:
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
        analysis = analyze_opencode_events(result["stdout"], forbidden_values=forbidden_values)
        validate_ledger_heads(before, after)
        if result["returncode"] != 0 or result.get("timed_out") or result.get("output_overflow"):
            raise QualificationError("OpenCode task process failed")
        turn_status = "passed"
        failure_codes: list[str] = []
    except QualificationError as exc:
        # A failed turn still retains only a sanitized error receipt.  No raw
        # provider/process bytes are copied into the report.
        analysis = {
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
            "sanitized_events": (
                _canonical({"type": "failure", "code": type(exc).__name__}) + "\n"
            ).encode("utf-8"),
        }
        turn_status = "failed"
        failure_codes = [type(exc).__name__]
        wrapper_value = None
    turn = {
        "status": turn_status,
        "lifecycle_method": "opencode/run",
        "thread_id_sha256": analysis.get("thread_id_sha256"),
        "turn_id_sha256": analysis.get("turn_id_sha256"),
        "prompt_sha256": _sha256(SCENARIO_TASKS[scenario].encode("utf-8")),
        "final_response_sha256": analysis["final_response_sha256"],
        "final_response_bytes": analysis["final_response_bytes"],
        "host_elapsed_ms": elapsed_ms,
        "usage": analysis["usage"],
        "ledger_audit_head_before": before,
        "ledger_audit_head_after": after,
        "ledger_unchanged": before == after,
        "safe_read": analysis["safe_read"],
        "sanitized_events": {
            "name": f"opencode-run-{run_index}-events.sanitized.jsonl",
            "bytes": len(analysis["sanitized_events"]),
            "sha256": _sha256(analysis["sanitized_events"]),
        },
    }
    metrics = _final_metrics(
        scenario,
        analysis["safe_read"],
        retention_wording_correct=retention_wording_correct,
    )
    if turn_status == "passed":
        if metrics["provider_boundary_correct"] is not True:
            failure_codes.append("provider_boundary_invalid")
        if scenario == "projection_status" and metrics["projection_state_correct"] is not True:
            failure_codes.append("projection_status_invalid")
        if scenario == "source_forget" and metrics["retention_wording_correct"] is not True:
            failure_codes.append("source_forget_retention_invalid")
        if scenario == "source_forget" and (
            metrics["forgotten_state_admission"] != 0 or metrics["gap_observed"] is not True
        ):
            failure_codes.append("source_forget_admission_invalid")
    run = {
        "run_index": run_index,
        "scenario": scenario,
        "status": "failed" if failure_codes else "passed",
        "failure_codes": failure_codes,
        "task_sha256": _sha256(SCENARIO_TASKS[scenario].encode("utf-8")),
        "new_thread": True,
        "methods_observed": ["opencode/run"],
        "turns": [turn],
        "metrics": metrics,
        "mutation_boundaries": mutation_boundaries,
    }
    run["metrics"]["evidence_sha256"] = pass13_evidence.metric_evidence_sha256(run)
    return run, analysis["sanitized_events"], wrapper_value


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
    root: Path,
    source_revision_id: str | None = None,
) -> dict[str, Any]:
    repository = Path(__file__).resolve().parents[2]
    orchestrator = QualificationOrchestrator(
        host="opencode",
        repository=repository,
        candidate_wheel=candidate_wheel,
        deeplaw_executable=deeplaw_executable,
        output_dir=output_dir.resolve(strict=False),
        error_type=QualificationError,
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
    config_path.write_text(_canonical(build_opencode_config()) + "\n", encoding="utf-8")
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
    )
    runs: list[dict[str, Any]] = []
    artifacts: dict[str, Path] = {}
    wrapper_receipts: list[Mapping[str, Any]] = []
    forbidden_values = (provider_key, *canaries.values(), str(root))
    for index, scenario in enumerate(SCENARIOS, start=1):
        run_root = root / f"run-{index}"
        run, sanitized, wrapper_receipt = _run_one_scenario(
            run_index=index,
            scenario=scenario,
            opencode_binary=opencode_binary,
            deeplaw_executable=deeplaw_executable,
            environment=environment,
            run_root=run_root,
            forbidden_values=forbidden_values,
            source_revision_id=source_revision_id,
        )
        runs.append(run)
        if wrapper_receipt is not None:
            wrapper_receipts.append(wrapper_receipt)
        path = output_dir / run["turns"][0]["sanitized_events"]["name"]
        retain_artifact(
            path,
            sanitized,
            output_root=output_dir,
            forbidden_values=forbidden_values,
        )
        artifacts[f"sanitized_events_run_{index}"] = path
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
        runs=runs,
        lifecycle={
            "host_owns_threads": True,
            "methods_observed": ["not_applicable"],
            "deeplaw_session_store_created": False,
        },
        security={
            "mcp_child_closed_environment": len(wrapper_receipts) == RUN_COUNT,
            "only_knowledge_support_enabled": True,
            "absolute_path_leak": False,
            "secret_leak": False,
            "raw_transcript_retained": False,
            "hidden_reasoning_retained": False,
            "authentication_material_retained": False,
        },
        not_executed=["resume", "fork", "compaction", "release_claim", "final_blind_holdout"],
    )
    report_path = output_dir / "opencode-continuity-qualification.json"
    retain_artifact(
        report_path,
        (_canonical(report) + "\n").encode("utf-8"),
        output_root=output_dir,
        forbidden_values=forbidden_values,
    )
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
    source_revision_id: str | None = None,
) -> dict[str, Any]:
    """Run qualification with an external temporary root and deterministic cleanup."""

    root = Path(tempfile.mkdtemp(prefix=_ISOLATED_ROOT_PREFIX))
    try:
        result = _execute_qualification_body(
            candidate_wheel=candidate_wheel,
            deeplaw_executable=deeplaw_executable,
            output_dir=output_dir,
            opencode_binary=opencode_binary,
            dotenv=dotenv,
            root=root,
            source_revision_id=source_revision_id,
        )
    except BaseException as original:
        _cleanup_after_qualification(root, original)
        raise
    else:
        _cleanup_after_qualification(root)
        return result


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-wheel", type=Path, required=True)
    parser.add_argument("--deeplaw-executable", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--opencode-binary", type=Path, required=True)
    parser.add_argument("--dotenv", type=Path, required=True)
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
            source_revision_id=args.source_revision_id,
        )
    except (OSError, QualificationError) as exc:
        print(f"qualification failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    return 0 if report.get("status") == "executed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
