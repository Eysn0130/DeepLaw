"""Run one evaluator-isolated Pass 11 continuity task through OpenCode.

The Host receives one natural-language task and one non-secret task binding.  Gold,
scorers, expected labels, repository files, ambient configuration, and credentials
never enter the candidate working directory.  The DeepSeek credential is selected by
a minimal parser immediately before execution and is not forwarded to the MCP child.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import mean
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts.run_codex_continuity_qualification import (
    _FORBIDDEN_PROVIDER_FIELDS,
    _candidate_fixture,
    _candidate_output_directory,
    _environment_receipt,
    _ledger_head,
    _load_object,
    _parse_final,
    _preflight,
    _prepare_runtime,
    _prompt,
    _provider_capsule_from_value,
    _repository,
    _seed_vault,
    _sha256_file,
    _task_binding,  # noqa: F401 - shared candidate seam intentionally re-exported
)
from benchmarks.hosts.run_living_wiki_host_harness import _run_bounded_process
from benchmarks.release.evidence import repository_binding
from deeplaw.util import canonical_json, sha256_bytes, strict_json_loads

REPORT_SCHEMA_VERSION = "deeplaw.opencode-continuity-observation/v1"
MODEL = "deepseek/deepseek-v4-flash"
VARIANT = "max"
RUN_COUNT = 1
TOOL_NAME = "deeplaw_knowledge_knowledge_support"
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
TIMEOUT_SECONDS = 300
MAX_DOTENV_BYTES = 64 * 1024

OPENCODE_PACKAGE_NAME = "opencode-ai"
OPENCODE_VERSION = "1.18.16"
OPENCODE_TARBALL_SHA256 = (
    "1e0ac00a7dafd5e7c22d468ce7e088ae329dc02abb48b52581cf1c63fb2c3ffd"
)
OPENCODE_TARBALL_SHA1 = "303d2f3f307d55716a2a203633fab24635468b63"
OPENCODE_SOURCE_COMMIT = "a3647eb025c7615159d417dcc49fc39fdaeba65b"
OPENCODE_OFFICIAL_SOURCE = "https://github.com/anomalyco/opencode"
OPENCODE_REGISTRY_TARBALL = (
    "https://registry.npmjs.org/opencode-ai/-/opencode-ai-1.18.16.tgz"
)

_PROVIDER_ENV_NAME = "DEEPSEEK_API_KEY"
_CANARY_NAMES = (
    "DEEPLAW_QUALIFICATION_SECRET_CANARY",
    "DEEPLAW_QUALIFICATION_PATH_CANARY",
)
_EXPECTED_HOST_ENVIRONMENT_NAMES = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "TMPDIR",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
        "DEEPSEEK_API_KEY",
        "NO_COLOR",
        "GIT_TERMINAL_PROMPT",
        "CI",
    }
)
_ALLOWED_EVENT_TYPES = frozenset(
    {"step_start", "step_finish", "tool_use", "text", "error", "reasoning"}
)
_DOTENV_TARGET = re.compile(r"^DEEPSEEK_API_KEY=(.*)$")
_ABSOLUTE_PATH = re.compile(
    rb'(?:^|[\s=:"\'])(?:/(?:Users|home|tmp|private|var)(?:[\s/"\']|$)|[A-Za-z]:[\\/])'
)


def _sha1_file(path: Path) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_deepseek_key(path: Path) -> str:
    """Select exactly one simple DEEPSEEK_API_KEY assignment without logging it."""

    try:
        if path.is_symlink():
            raise ValueError
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        selected: str | None = None
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_DOTENV_BYTES:
            os.close(descriptor)
            raise ValueError
        with os.fdopen(
            descriptor, "r", encoding="utf-8", errors="strict", newline=""
        ) as stream:
            for raw_line in stream:
                line = raw_line.rstrip("\r\n")
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "DEEPSEEK_API_KEY" not in stripped:
                    continue
                match = _DOTENV_TARGET.fullmatch(stripped)
                if match is None or selected is not None:
                    raise ValueError
                value = match.group(1).strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if (
                    not value
                    or any(character in value for character in ("$", "`", "\\", "\x00"))
                    or any(character.isspace() for character in value)
                    or value.startswith(("\"", "'"))
                    or value.endswith(("\"", "'"))
                ):
                    raise ValueError
                selected = value
        if selected is None:
            raise ValueError
        return selected
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError("DeepSeek credential file is invalid") from error


def _permission() -> dict[str, str]:
    return {"*": "deny", TOOL_NAME: "allow"}


def _opencode_config() -> dict[str, Any]:
    permission = _permission()
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": MODEL,
        "small_model": MODEL,
        "default_agent": "qualification",
        "subagent_depth": 0,
        "enabled_providers": ["deepseek"],
        "provider": {
            "deepseek": {
                "options": {"apiKey": "{env:DEEPSEEK_API_KEY}"},
            }
        },
        "share": "disabled",
        "autoupdate": False,
        "snapshot": False,
        "plugin": [],
        "instructions": [],
        "permission": permission,
        "agent": {
            "qualification": {
                "description": "Pass 11 read-only continuity qualification",
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


def _host_environment(
    *,
    root: Path,
    opencode_binary: Path,
    node_binary: Path,
    provider_key: str,
    canaries: Mapping[str, str],
) -> dict[str, str]:
    path = os.pathsep.join(
        dict.fromkeys((str(opencode_binary.parent), str(node_binary.parent), os.defpath))
    )
    environment = {
        "PATH": path,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(root / "host-home"),
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "TMPDIR": str(root / "tmp"),
        "OPENCODE_CONFIG": str(root / "opencode.json"),
        "OPENCODE_CONFIG_DIR": str(root / "opencode-config"),
        _PROVIDER_ENV_NAME: provider_key,
        "NO_COLOR": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "CI": "1",
    }
    environment.update(canaries)
    return environment


def _prepare_host_directories(root: Path) -> None:
    for relative in (
        "host-home",
        "xdg-config",
        "xdg-data",
        "xdg-cache",
        "tmp",
        "opencode-config",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)


def _remove_host_state(root: Path) -> None:
    for relative in (
        "host-home",
        "xdg-config",
        "xdg-data",
        "xdg-cache",
        "tmp",
        "opencode-config",
    ):
        target = root / relative
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)


def _managed_config_present() -> bool:
    if platform.system() == "Darwin":
        return Path("/Library/Application Support/opencode").exists()
    if platform.system() == "Linux":
        return Path("/etc/opencode").exists()
    program_data = os.environ.get("PROGRAMDATA")
    return bool(program_data and (Path(program_data) / "opencode").exists())


def _safe_argv() -> list[str]:
    return [
        "opencode",
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
        "--title",
        "pass11-continuity-qualification",
    ]


def _actual_argv(opencode_binary: Path) -> list[str]:
    return [str(opencode_binary), *_safe_argv()[1:]]


def _preflight_opencode(
    *, root: Path, opencode_binary: Path, node_binary: Path
) -> dict[str, Any]:
    if _managed_config_present():
        raise RuntimeError("OpenCode managed configuration is present")
    environment = _host_environment(
        root=root,
        opencode_binary=opencode_binary,
        node_binary=node_binary,
        provider_key="qualification-placeholder-not-a-secret",
        canaries={},
    )
    version = subprocess.run(
        [str(opencode_binary), "--version"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if version.returncode != 0 or version.stdout.strip() != OPENCODE_VERSION:
        raise RuntimeError("OpenCode version preflight failed")
    resolved = subprocess.run(
        [str(opencode_binary), "--pure", "debug", "config"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        config = strict_json_loads(resolved.stdout)
    except (TypeError, ValueError) as error:
        raise RuntimeError("OpenCode resolved configuration was not JSON") from error
    expected = _opencode_config()
    if (
        resolved.returncode != 0
        or bool(resolved.stderr)
        or not isinstance(config, Mapping)
        or config.get("share") != "disabled"
        or config.get("autoupdate") is not False
        or config.get("snapshot") is not False
        or config.get("plugin") != []
        or config.get("instructions") != []
        or config.get("enabled_providers") != ["deepseek"]
        or set(config.get("provider", {})) != {"deepseek"}
        or config.get("provider", {}).get("deepseek", {}).get("options", {}).get(
            "apiKey"
        )
        != "qualification-placeholder-not-a-secret"
        or config.get("permission") != expected["permission"]
        or set(config.get("agent", {})) != {"qualification"}
        or config.get("agent", {}).get("qualification", {}).get("permission")
        != expected["agent"]["qualification"]["permission"]
        or set(config.get("mcp", {})) != {"deeplaw_knowledge"}
        or config.get("mcp", {}).get("deeplaw_knowledge") != expected["mcp"][
            "deeplaw_knowledge"
        ]
        or config.get("subagent_depth") != 0
    ):
        raise RuntimeError("OpenCode resolved configuration is not closed")
    models = subprocess.run(
        [str(opencode_binary), "--pure", "models", "deepseek"],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if models.returncode != 0 or models.stderr or MODEL not in models.stdout.splitlines():
        raise RuntimeError("OpenCode model identity preflight failed")
    return {
        "status": "passed",
        "version": OPENCODE_VERSION,
        "model": MODEL,
        "variant": VARIANT,
        "managed_config_loaded": False,
        "user_global_config_loaded": False,
        "organization_config_loaded": False,
        "external_plugins_loaded": False,
        "resolved_configuration_sha256": sha256_bytes(
            canonical_json(expected).encode("utf-8")
        ),
    }


def _parse_events(stdout: bytes) -> tuple[list[dict[str, Any]], int]:
    try:
        lines = stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return [], 1
    events: list[dict[str, Any]] = []
    invalid = 0
    for line in lines:
        try:
            value = strict_json_loads(line)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            invalid += 1
            continue
        events.append(value)
    return events, invalid


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _analyze_events(stdout: bytes) -> dict[str, Any]:
    events, invalid_lines = _parse_events(stdout)
    sanitized: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    disallowed_tools: set[str] = set()
    provider_output = b""
    provider_capsule: dict[str, Any] | None = None
    host_output: dict[str, Any] | None = None
    usage_rows: list[dict[str, Any]] = []
    unknown_event_types: set[str] = set()
    error_event_observed = False
    reasoning_event_observed = False
    for event in events:
        event_type = event["type"]
        projected: dict[str, Any] = {"type": event_type}
        session_id = event.get("sessionID")
        if isinstance(session_id, str):
            projected["session_sha256"] = sha256_bytes(session_id.encode("utf-8"))
        if event_type not in _ALLOWED_EVENT_TYPES:
            unknown_event_types.add(event_type)
        part = event.get("part")
        part = part if isinstance(part, Mapping) else {}
        if event_type == "reasoning":
            reasoning_event_observed = True
        elif event_type == "error":
            error_event_observed = True
            error = event.get("error")
            if isinstance(error, Mapping) and isinstance(error.get("name"), str):
                projected["error_name"] = error["name"][:100]
        elif event_type == "step_finish":
            tokens = part.get("tokens")
            cache = tokens.get("cache") if isinstance(tokens, Mapping) else None
            values = {
                "input_tokens": _nonnegative_int(tokens.get("input"))
                if isinstance(tokens, Mapping)
                else None,
                "output_tokens": _nonnegative_int(tokens.get("output"))
                if isinstance(tokens, Mapping)
                else None,
                "reasoning_tokens": _nonnegative_int(tokens.get("reasoning"))
                if isinstance(tokens, Mapping)
                else None,
                "cached_input_tokens": _nonnegative_int(cache.get("read"))
                if isinstance(cache, Mapping)
                else None,
                "cache_write_tokens": _nonnegative_int(cache.get("write"))
                if isinstance(cache, Mapping)
                else None,
                "reported_total": _nonnegative_int(tokens.get("total"))
                if isinstance(tokens, Mapping)
                else None,
            }
            cost = part.get("cost")
            if (
                all(values[field] is not None for field in values if field != "reported_total")
                and isinstance(cost, (int, float))
                and not isinstance(cost, bool)
                and cost >= 0
            ):
                total = values["reported_total"]
                if total is None:
                    total = (
                        values["input_tokens"]
                        + values["output_tokens"]
                        + values["reasoning_tokens"]
                    )
                row = {
                    **{key: value for key, value in values.items() if key != "reported_total"},
                    "total_tokens": total,
                    "cost_usd": float(cost),
                }
                usage_rows.append(row)
                projected["usage"] = row
        elif event_type == "tool_use":
            tool = part.get("tool") if isinstance(part.get("tool"), str) else "unknown"
            state = part.get("state")
            state = state if isinstance(state, Mapping) else {}
            status = state.get("status") if isinstance(state.get("status"), str) else None
            input_value = state.get("input")
            input_bytes = canonical_json(input_value).encode("utf-8")
            output_value = state.get("output")
            output_bytes = output_value.encode("utf-8") if isinstance(output_value, str) else b""
            call = {
                "tool": tool,
                "status": status,
                "input_sha256": sha256_bytes(input_bytes),
                "output_sha256": sha256_bytes(output_bytes) if output_bytes else None,
                "output_bytes": len(output_bytes),
            }
            tool_calls.append(call)
            projected["tool_call"] = call
            if tool != TOOL_NAME:
                disallowed_tools.add(tool)
            if tool == TOOL_NAME and status == "completed" and output_bytes:
                provider_output += output_bytes
                parsed = _provider_capsule_from_value(output_value)
                if parsed is not None and provider_capsule is None:
                    provider_capsule = parsed
        elif event_type == "text":
            text = part.get("text")
            if isinstance(text, str):
                encoded = text.encode("utf-8")
                projected["text_sha256"] = sha256_bytes(encoded)
                projected["text_bytes"] = len(encoded)
                parsed = _parse_final(text)
                if parsed is not None:
                    host_output = parsed
                    projected["host_output"] = parsed
        sanitized.append(projected)
    if usage_rows:
        usage = {
            "status": "provider_reported",
            "input_tokens": sum(row["input_tokens"] for row in usage_rows),
            "cached_input_tokens": sum(row["cached_input_tokens"] for row in usage_rows),
            "cache_write_tokens": sum(row["cache_write_tokens"] for row in usage_rows),
            "output_tokens": sum(row["output_tokens"] for row in usage_rows),
            "reasoning_tokens": sum(row["reasoning_tokens"] for row in usage_rows),
            "total_tokens": sum(row["total_tokens"] for row in usage_rows),
            "cost_usd": round(sum(row["cost_usd"] for row in usage_rows), 12),
        }
    else:
        usage = {
            "status": "unreported",
            "input_tokens": None,
            "cached_input_tokens": None,
            "cache_write_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
            "cost_usd": None,
        }
    return {
        "sanitized_events": sanitized,
        "event_types": sorted({event["type"] for event in events}),
        "invalid_event_lines": invalid_lines,
        "unknown_event_types": sorted(unknown_event_types),
        "error_event_observed": error_event_observed,
        "reasoning_event_observed": reasoning_event_observed,
        "tool_calls": tool_calls,
        "disallowed_tools": sorted(disallowed_tools),
        "provider_output": provider_output,
        "provider_capsule": provider_capsule,
        "host_output": host_output,
        "usage": usage,
    }


def _run_once(
    *,
    argv: list[str],
    prompt: str,
    environment: dict[str, str],
    output_dir: Path,
    vault: Path,
    provider_key: str,
    canary_values: Sequence[str],
) -> dict[str, Any]:
    receipt_path = output_dir / "mcp-environment-receipt.json"
    if receipt_path.exists():
        receipt_path.unlink()
    audit_before = _ledger_head(vault)
    started = time.monotonic()
    try:
        exit_status, stdout, stderr, process_failure = _run_bounded_process(
            argv,
            prompt=prompt.encode("utf-8"),
            environment=environment,
            working_directory=output_dir,
            timeout_seconds=TIMEOUT_SECONDS,
            max_output_bytes=MAX_OUTPUT_BYTES,
        )
    except OSError:
        exit_status, stdout, stderr, process_failure = 127, b"", b"", "process_start_failed"
    latency_ms = round((time.monotonic() - started) * 1000)
    audit_after = _ledger_head(vault)
    analysis = _analyze_events(stdout)
    provider_output = analysis.pop("provider_output")
    sensitive_values = (provider_key, *canary_values)
    secret_leak = any(
        value.encode("utf-8") in stdout + stderr + provider_output
        for value in sensitive_values
    )
    absolute_path_leak = bool(_ABSOLUTE_PATH.search(stdout + stderr + provider_output))
    internal_surface_leak = any(field in provider_output for field in _FORBIDDEN_PROVIDER_FIELDS)
    if secret_leak or absolute_path_leak:
        analysis["sanitized_events"] = [{"type": "security_failure"}]
        analysis["host_output"] = None
        analysis["provider_capsule"] = None
    sanitized_bytes = b"".join(
        canonical_json(event).encode("utf-8") + b"\n"
        for event in analysis["sanitized_events"]
    )
    sanitized_name = "opencode-run-1-events.sanitized.jsonl"
    (output_dir / sanitized_name).write_bytes(sanitized_bytes)
    environment_receipt = _environment_receipt(receipt_path)
    tool_calls = analysis["tool_calls"]
    exact_tool = (
        len(tool_calls) == 1
        and tool_calls[0]["tool"] == TOOL_NAME
        and tool_calls[0]["status"] == "completed"
        and not analysis["disallowed_tools"]
    )
    provider_bytes = len(provider_output)
    provider_proven = (
        analysis["provider_capsule"] is not None
        and 1 <= provider_bytes <= 65_536
        and not internal_surface_leak
    )
    passed = bool(
        exit_status == 0
        and process_failure is None
        and not stderr
        and analysis["usage"]["status"] == "provider_reported"
        and exact_tool
        and analysis["invalid_event_lines"] == 0
        and not analysis["unknown_event_types"]
        and not analysis["error_event_observed"]
        and not analysis["reasoning_event_observed"]
        and environment_receipt is not None
        and not secret_leak
        and not absolute_path_leak
        and provider_proven
        and analysis["host_output"] is not None
        and audit_before == audit_after
    )
    failures: list[str] = []
    if process_failure is not None:
        failures.append(process_failure)
    if exit_status != 0:
        failures.append("nonzero_exit")
    if stderr:
        failures.append("stderr_observed")
    if analysis["usage"]["status"] != "provider_reported":
        failures.append("provider_usage_unreported")
    if not exact_tool:
        failures.append("single_knowledge_support_call_not_proven")
    if analysis["invalid_event_lines"] or analysis["unknown_event_types"]:
        failures.append("event_stream_not_proven")
    if analysis["error_event_observed"]:
        failures.append("host_error_event")
    if analysis["reasoning_event_observed"]:
        failures.append("hidden_reasoning_event")
    if environment_receipt is None:
        failures.append("closed_mcp_environment_not_proven")
    if secret_leak:
        failures.append("secret_leak")
    if absolute_path_leak:
        failures.append("absolute_path_leak")
    if not provider_proven:
        failures.append("provider_capsule_not_proven_clean")
    if analysis["host_output"] is None:
        failures.append("neutral_host_output_missing")
    if audit_before != audit_after:
        failures.append("read_mutated_ledger")
    raw_hashes = not secret_leak
    return {
        "run_index": 1,
        "status": "passed" if passed else "failed",
        "exit_status": exit_status,
        "latency_ms": latency_ms,
        "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
        "actual_event_receipt": {
            "stdout_sha256": sha256_bytes(stdout) if raw_hashes else None,
            "stdout_bytes": len(stdout),
            "stderr_sha256": sha256_bytes(stderr) if raw_hashes else None,
            "stderr_bytes": len(stderr),
            "sanitized_events_name": sanitized_name,
            "sanitized_events_sha256": sha256_bytes(sanitized_bytes),
            "sanitized_event_types": analysis["event_types"],
            "invalid_event_lines": analysis["invalid_event_lines"],
            "unknown_event_types": analysis["unknown_event_types"],
            "tool_calls": tool_calls,
            "final_response_sha256": sha256_bytes(
                canonical_json(analysis["host_output"]).encode("utf-8")
            )
            if analysis["host_output"] is not None
            else None,
        },
        "usage": analysis["usage"],
        "environment_receipt": environment_receipt,
        "host_output": analysis["host_output"],
        "provider_capsule": analysis["provider_capsule"],
        "provider_internal_surface_leak": internal_surface_leak,
        "provider_bytes": provider_bytes,
        "ledger_audit_head_before": audit_before,
        "ledger_audit_head_after": audit_after,
        "ledger_unchanged": audit_before == audit_after,
        "secret_leak": secret_leak,
        "absolute_path_leak": absolute_path_leak,
        "failure_class": None if passed else "qualification_failure",
        "failure_summary": None if passed else ",".join(dict.fromkeys(failures))[:500],
    }


def _validate_report(report: dict[str, Any]) -> None:
    schema = _load_object(
        _repository() / "contracts/opencode-continuity-observation.v1.schema.json"
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)


def execute(
    *,
    fixture_path: Path,
    candidate_wheel: Path,
    deeplaw_executable: Path,
    output_dir: Path,
    opencode_command: Path,
    node_command: Path,
    package_tarball: Path,
    dotenv_path: Path,
) -> dict[str, Any]:
    repository = _repository()
    fixture_path = fixture_path.resolve(strict=True)
    wheel = candidate_wheel.resolve(strict=True)
    package_tarball = package_tarball.resolve(strict=True)
    opencode_binary = opencode_command.resolve(strict=True)
    node_binary = node_command.resolve(strict=True)
    output_dir = _candidate_output_directory(output_dir, repository=repository)
    output_dir.mkdir(parents=True)
    _prepare_host_directories(output_dir)
    fixture = _candidate_fixture(fixture_path)
    binding = repository_binding(repository)
    if not binding["worktree_clean"]:
        raise RuntimeError("real Host qualification requires a clean candidate worktree")
    if binding["package_version"] != "0.12.0":
        raise RuntimeError("Pass 11 qualification must keep package version 0.12.0")
    if (
        _sha256_file(package_tarball) != OPENCODE_TARBALL_SHA256
        or _sha1_file(package_tarball) != OPENCODE_TARBALL_SHA1
    ):
        raise RuntimeError("OpenCode official package bytes do not match the frozen receipt")
    _wrapper, wrapper_sha256 = _prepare_runtime(
        output_dir=output_dir,
        deeplaw_executable=deeplaw_executable,
    )
    config = _opencode_config()
    (output_dir / "opencode.json").write_text(
        canonical_json(config) + "\n", encoding="utf-8"
    )
    host_preflight = _preflight_opencode(
        root=output_dir,
        opencode_binary=opencode_binary,
        node_binary=node_binary,
    )
    vault = output_dir / "vault"
    seeded = _seed_vault(vault, fixture)
    capsule_preflight = _preflight(vault, fixture, seeded)
    prompt = _prompt(fixture, seeded["task_binding"])
    provider_key = _load_deepseek_key(dotenv_path)
    canaries = {name: secrets.token_hex(32) for name in _CANARY_NAMES}
    environment = _host_environment(
        root=output_dir,
        opencode_binary=opencode_binary,
        node_binary=node_binary,
        provider_key=provider_key,
        canaries=canaries,
    )
    try:
        run = _run_once(
            argv=_actual_argv(opencode_binary),
            prompt=prompt,
            environment=environment,
            output_dir=output_dir,
            vault=vault,
            provider_key=provider_key,
            canary_values=tuple(canaries.values()),
        )
    finally:
        _remove_host_state(output_dir)
    usage = run["usage"]
    configuration_sha256 = sha256_bytes(canonical_json(config).encode("utf-8"))
    fixture_bytes = fixture_path.read_bytes()
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "executed" if run["status"] == "passed" else "failed",
        "release_ready": False,
        "claim_eligible": False,
        "binding": {
            "commit": binding["commit"],
            "tree": binding["tree"],
            "package_version": binding["package_version"],
            "worktree_clean": binding["worktree_clean"],
            "candidate_wheel_name": wheel.name,
            "candidate_wheel_sha256": _sha256_file(wheel),
        },
        "environment": {
            "operating_system": platform.system(),
            "architecture": platform.machine(),
            "python_version": platform.python_version(),
        },
        "candidate": {
            "name": fixture_path.name,
            "sha256": sha256_bytes(fixture_bytes),
            "configuration_sha256": configuration_sha256,
            "case_id": fixture["case_id"],
            "task_sha256": sha256_bytes(fixture["task"].encode("utf-8")),
            "task_binding_sha256": seeded["task_binding"]["binding_sha256"],
        },
        "host": {
            "binary_name": "opencode",
            "binary_sha256": _sha256_file(opencode_binary),
            "version": OPENCODE_VERSION,
            "package_name": OPENCODE_PACKAGE_NAME,
            "package_tarball_sha256": OPENCODE_TARBALL_SHA256,
            "package_tarball_sha1": OPENCODE_TARBALL_SHA1,
            "registry_tarball": OPENCODE_REGISTRY_TARBALL,
            "official_source": OPENCODE_OFFICIAL_SOURCE,
            "source_commit": OPENCODE_SOURCE_COMMIT,
            "model": MODEL,
            "variant": VARIANT,
            "authentication": {
                "status": "environment_only",
                "source": "isolated_dotenv_parser",
                "auth_store_read": False,
                "credential_value_recorded": False,
            },
            "argv": _safe_argv(),
            "argv_sha256": sha256_bytes(canonical_json(_safe_argv()).encode("utf-8")),
            "mcp_argv": config["mcp"]["deeplaw_knowledge"]["command"],
            "enabled_tools": ["knowledge_support"],
            "host_environment_names": sorted(environment),
        },
        "security": {
            "share_disabled": True,
            "snapshot_disabled": True,
            "autoupdate_disabled": True,
            "plugins_disabled": True,
            "subagents_disabled": True,
            "host_state_removed": True,
            "host_closed_environment": set(environment)
            == _EXPECTED_HOST_ENVIRONMENT_NAMES | set(_CANARY_NAMES),
            "mcp_child_closed_environment": run["environment_receipt"] is not None,
            "provider_capsule_clean": run["provider_capsule"] is not None
            and not run["provider_internal_surface_leak"]
            and not run["secret_leak"]
            and not run["absolute_path_leak"],
            "provider_internal_surface_leak": run["provider_internal_surface_leak"],
            "event_receipts_clean": not run["secret_leak"]
            and not run["absolute_path_leak"],
            "report_clean": True,
            "absolute_path_leak": run["absolute_path_leak"],
            "secret_leak": run["secret_leak"],
            "credential_path_forwarded": run["environment_receipt"] is None,
            "mcp_wrapper_sha256": wrapper_sha256,
        },
        "host_preflight": host_preflight,
        "capsule_preflight": capsule_preflight,
        "runs": [run],
        "aggregate": {
            "passed_runs": int(run["status"] == "passed"),
            "failed_runs": int(run["status"] != "passed"),
            "actual_input_tokens": usage["input_tokens"] or 0,
            "actual_cached_input_tokens": usage["cached_input_tokens"] or 0,
            "actual_cache_write_tokens": usage["cache_write_tokens"] or 0,
            "actual_output_tokens": usage["output_tokens"] or 0,
            "actual_reasoning_tokens": usage["reasoning_tokens"] or 0,
            "actual_total_tokens": usage["total_tokens"] or 0,
            "actual_cost_usd": usage["cost_usd"] or 0,
            "latency_ms": {
                "min": run["latency_ms"],
                "max": run["latency_ms"],
                "mean": round(mean([run["latency_ms"]]), 3),
            },
            "candidate_execution_complete": run["status"] == "passed",
        },
        "not_executed": [
            *fixture["not_executed_scenarios"],
            "second_distinct_opencode_workflow",
            "third_distinct_opencode_workflow",
            "independent_scoring",
            "qualification_holdout",
            "final_blind",
        ],
    }
    report_bytes = canonical_json(report).encode("utf-8")
    if provider_key.encode("utf-8") in report_bytes or any(
        value.encode("utf-8") in report_bytes for value in canaries.values()
    ):
        raise RuntimeError("OpenCode observation contains a credential value")
    if _ABSOLUTE_PATH.search(report_bytes):
        raise RuntimeError("OpenCode observation contains an absolute path")
    _validate_report(report)
    report_path = output_dir / "opencode-continuity-observation.json"
    report_path.write_bytes(report_bytes + b"\n")
    manifest = {
        "schema_version": "deeplaw.opencode-continuity-observation-artifacts/v1",
        "artifacts": [
            {
                "name": path.name,
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in sorted(output_dir.glob("*.json*"), key=lambda item: item.name)
            if path.is_file() and not path.is_symlink()
        ],
    }
    (output_dir / "SHA256SUMS.json").write_text(
        canonical_json(manifest) + "\n", encoding="utf-8"
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one isolated Pass 11 continuity task through OpenCode."
    )
    parser.add_argument(
        "--fixture",
        default="benchmarks/v013/qualification/candidate/continuity-task-suite-v1.json",
    )
    parser.add_argument("--candidate-wheel", required=True)
    parser.add_argument("--deeplaw-executable", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--opencode-command", required=True)
    parser.add_argument("--node-command", default=shutil.which("node"))
    parser.add_argument("--package-tarball", required=True)
    parser.add_argument("--dotenv", default=".env")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.node_command:
        raise RuntimeError("Node runtime was not found")
    report = execute(
        fixture_path=Path(args.fixture),
        candidate_wheel=Path(args.candidate_wheel),
        deeplaw_executable=Path(args.deeplaw_executable),
        output_dir=Path(args.output_dir),
        opencode_command=Path(args.opencode_command),
        node_command=Path(args.node_command),
        package_tarball=Path(args.package_tarball),
        dotenv_path=Path(args.dotenv),
    )
    print(canonical_json(report))
    return 0 if report["status"] == "executed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
