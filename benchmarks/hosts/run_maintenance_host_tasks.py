"""Finite real OpenCode tasks with owner-side state scoring and no transcript reads.

This producer is not a formal isolation attestation. Formal collection must
supply its separate native process and security-domain observations.
``model_task_executed`` means a bound completed response with generated-token
usage was observed; false does not prove that no Provider request occurred.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from benchmarks.hosts import opencode_single_task_producer as shared
from benchmarks.hosts import run_pass13_opencode_continuity_qualification as host
from benchmarks.hosts.maintenance_fixture_snapshot import freeze_public_fixture_vault
from benchmarks.hosts.maintenance_host_context import (
    OutcomeGrantClosureError,
    capture_context,
    digest,
    prepare_context_vault,
    record_host_outcome,
)
from benchmarks.hosts.maintenance_task_cases import (
    CONFIGURATION_ORDER,
    FROZEN_INPUT_SHA256,
    SCENARIO_ORDER,
    public_task_projection,
    score_host_trace,
)
from benchmarks.hosts.maintenance_task_mcp import (
    MAX_PROVIDER_CAPSULE_BYTES,
    MAX_TRACE_BYTES,
    MaintenanceMCPError,
    load_persisted_trace,
    make_owner_binding,
)
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes

ROOT = Path(__file__).resolve().parents[2]
TOOL = shared.GUARD_TOOL_PROFILES["maintenance"]
MAX_SECONDS = 180


def outcome_commit_eligible(result: dict[str, Any]) -> bool:
    guard = result.get("guard")
    return (
        result.get("failure") is None and result.get("forced_kill") is False
        and result.get("model_task_executed") is True
        and type(result.get("host_exit_code")) is int and result["host_exit_code"] == 0
        and isinstance(guard, dict) and guard.get("cleanup_confirmed") is True
        and guard.get("first_failure") is None and guard.get("cleanup_failure") is None
        and guard.get("rejected") == 0 and guard.get("in_flight") == 0
    )


def _json_file(path: Path, value: Any) -> None:
    raw = canonical_json(value).encode()
    with path.open("xb") as stream:
        stream.write(raw)
    path.chmod(0o600)


def _read_evidence_bytes(root: Path, case_name: str, name: str, maximum: int) -> bytes | None:
    """Read one fixed owner-only file without following links or retrying changes."""
    if os.name != "posix" or not root.is_absolute() or root.resolve(strict=True) != root:
        raise ValueError("evidence root is unsafe")
    descriptors = []
    file_seen = False
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        root_fd = os.open(root.anchor, flags | os.O_DIRECTORY)
        descriptors.append(root_fd)
        for component in root.parts[1:]:
            parent_fd = root_fd
            root_fd = os.open(component, flags | os.O_DIRECTORY, dir_fd=parent_fd)
            descriptors.append(root_fd)
            os.close(parent_fd)
            descriptors.remove(parent_fd)
        root_before = os.fstat(root_fd)
        case_fd = os.open(case_name, flags | os.O_DIRECTORY, dir_fd=root_fd)
        descriptors.append(case_fd)
        case_before = os.fstat(case_fd)
        for directory in (root_before, case_before):
            if not stat.S_ISDIR(directory.st_mode) or directory.st_mode & 0o077 \
                    or directory.st_uid != os.geteuid():
                raise ValueError("evidence directory is unsafe")
        before = os.stat(name, dir_fd=case_fd, follow_symlinks=False)
        file_seen = True
        fd = os.open(name, flags, dir_fd=case_fd)
        descriptors.append(fd)
        opened = os.fstat(fd)

        def identity(value):
            return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                    value.st_ctime_ns, value.st_mode, value.st_uid, value.st_nlink)

        if (not stat.S_ISREG(opened.st_mode) or opened.st_mode & 0o077
                or opened.st_uid != os.geteuid() or opened.st_nlink != 1
                or not 0 <= opened.st_size <= maximum or identity(before) != identity(opened)):
            raise ValueError("evidence file is unsafe or exceeds its bound")
        payload = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        current = os.stat(name, dir_fd=case_fd, follow_symlinks=False)
        case_after = os.stat(case_name, dir_fd=root_fd, follow_symlinks=False)
        root_after = root.lstat()
        if (len(payload) != opened.st_size or identity(after) != identity(opened)
                or identity(current) != identity(opened)
                or identity(case_after) != identity(case_before)
                or identity(root_after) != identity(root_before)):
            raise ValueError("evidence file or directory changed during reading")
        return payload
    except FileNotFoundError:
        if file_seen:
            raise ValueError("evidence file disappeared during reading") from None
        return None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _evidence_json(raw: bytes) -> Any:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("evidence JSON has duplicate keys")
            value[key] = item
        return value

    def constant(value):
        raise ValueError("evidence JSON has a non-finite number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _capture_evidence_files(
    root: Path, result: dict[str, Any], *, configuration: str, scenario: str,
    context: dict[str, Any] | None, prepared: bool = True,
) -> None:
    """Bind retained engineering bytes after outcome; never supply native authority."""
    receipt: dict[str, Any] = {
        "action_trace": None, "provider_capsule": None, "native_events": None, "gaps": [],
        "caption": "Engineering evidence hashes; native authority is not established.",
    }
    result["evidence_files"] = receipt
    files = (
        ("action_trace", "action-trace.json", MAX_TRACE_BYTES),
        ("provider_capsule", "provider-capsule.json", MAX_PROVIDER_CAPSULE_BYTES),
        ("native_events", "native-events.jsonl", 65_536),
    )
    if not prepared:
        receipt["gaps"] = [{"evidence": field, "reason": "not_created_pre_dispatch"}
                           for field, _, _ in files]
        return
    expected = {
        "action_trace": result.get("trace"),
        "provider_capsule": context.get("provider_capsule") if context is not None else None,
        "native_events": result.get("native_observations"),
    }
    observed = result.get("model_task_executed") is True
    required = {
        "action_trace": expected["action_trace"] is not None
                        or result.get("state_score_status") == "observed",
        "provider_capsule": observed or result.get("binding") is not None
                            or result.get("prompt_dispatched") is True,
        "native_events": observed or bool(expected["native_events"])
                         or result.get("model_execution_status") == "observed",
    }
    failures = []
    for field, name, maximum in files:
        try:
            if configuration not in CONFIGURATION_ORDER or scenario not in SCENARIO_ORDER:
                raise ValueError("evidence slot identity differs")
            relative = configuration + "-" + scenario + "/" + name
            raw = _read_evidence_bytes(root, configuration + "-" + scenario, name, maximum)
            if raw is None:
                receipt["gaps"].append({"evidence": field, "reason": "missing"})
                if required[field]:
                    failures.append({"evidence": field, "reason": "missing_observed_file"})
                continue
            if expected[field] is not None:
                parsed = ([_evidence_json(line) for line in raw.splitlines() if line.strip()]
                          if field == "native_events" else _evidence_json(raw))
                if canonical_json(parsed) != canonical_json(expected[field]):
                    raise ValueError("evidence file differs from its in-memory projection")
            receipt[field] = {"path": relative, "sha256": sha256_bytes(raw), "size": len(raw)}
        except Exception:
            receipt["gaps"].append({"evidence": field, "reason": "rejected"})
            failures.append({"evidence": field, "reason": "rejected"})
    if failures:
        result["evidence_binding_failure"] = {
            "failure_stage": "evidence_binding", "reasons": failures,
        }
        if result.get("failure") is None:
            result["failure"] = "EvidenceBindingRejected"
            result["failure_stage"] = "evidence_binding"
        if result.get("status") != "not_executed":
            result["status"] = "failed"


def _persist_context_snapshot(
    root: Path, vault: Path, *, configuration: str, scenario: str,
    context: dict[str, Any], run_id: str, candidate_id: str, expected_vault_id: str,
) -> dict[str, Any]:
    """Persist pre-dispatch inputs; this binding conveys no native Host authority."""
    task = public_task_projection(configuration, scenario)
    if (
        set(context) != {"capsule", "provider_capsule", "verification", "context_id",
                         "task_input_sha256", "context_provenance"}
        or context["context_provenance"] != "source_free_synthetic_memory_compile"
        or context["verification"].get("valid") is not True
        or context["task_input_sha256"] != task["input_sha256"]
        or context["context_id"] != context["capsule"]["capsule_id"]
        or canonical_json(context["provider_capsule"])
        != canonical_json(context["capsule"]["provider_capsule"])
    ):
        raise ValueError("maintenance snapshot context binding differs")
    owner_binding = make_owner_binding(
        configuration, scenario, run_id=run_id, candidate_id=candidate_id,
        context_id=context["context_id"], capsule_digest=digest(context["provider_capsule"]),
    )
    directory = root / "context-snapshots"
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise ValueError("maintenance snapshot directory is unsafe")
    slot = directory / (configuration + "-" + scenario)
    slot.mkdir(mode=0o700)
    context_path, manifest_path = slot / "context.json", slot / "snapshot-manifest.json"
    _json_file(context_path, context)
    snapshot = freeze_public_fixture_vault(
        vault, slot / "vault", expected_vault_id=expected_vault_id,
    )
    if (
        snapshot["schema_version"] != "deeplaw.maintenance-fixture-snapshot/v1"
        or snapshot["vault_id"] != expected_vault_id
        or snapshot["public_input_sha256"] != FROZEN_INPUT_SHA256
        or snapshot["inventory_sha256"] != digest(snapshot["inventory"])
    ):
        raise ValueError("maintenance snapshot manifest binding differs")
    _json_file(manifest_path, snapshot)

    def reference(path: Path) -> dict[str, Any]:
        raw = path.read_bytes()
        return {"path": path.relative_to(root).as_posix(),
                "sha256": sha256_bytes(raw), "size": len(raw)}

    receipt = {
        "schema_version": "deeplaw.maintenance-context-snapshot/v1",
        "capture_phase": "pre_dispatch_pre_outcome",
        "configuration_id": configuration, "scenario_id": scenario,
        "run_id": run_id, "candidate_id": candidate_id,
        "public_input_sha256": FROZEN_INPUT_SHA256,
        "task_input_sha256": task["input_sha256"],
        "task_binding": build_task_context_binding(digest(configuration), digest(task["task_id"])),
        "context_id": context["context_id"],
        "provider_capsule_sha256": digest(context["provider_capsule"]),
        "owner_binding": owner_binding,
        "context": reference(context_path), "snapshot_manifest": reference(manifest_path),
        "vault_path": (slot / "vault").relative_to(root).as_posix(),
        **{field: snapshot[field] for field in (
            "vault_id", "legacy_revision", "legacy_audit_head", "autonomous_sequence",
            "autonomous_audit_head", "inventory_sha256",
        )},
    }
    return {**receipt, "binding_sha256": digest(receipt)}


def _control(base_url: str, method: str, path: str, value: Any = None) -> Any:
    if not (path in {"/global/health", "/session", "/session/status", "/mcp"}
            or (path.startswith("/session/") and path.endswith("/prompt_async"))):
        raise ValueError("maintenance Host control route differs")
    request = urllib.request.Request(
        base_url + path, method=method,
        data=canonical_json(value).encode() if value is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        request, timeout=20,
    ) as response:
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("maintenance Host response exceeds bound")
        return json.loads(raw) if raw else None


def _install_observer(workspace: Path, event_path: Path) -> None:
    directory = workspace / ".opencode/supervised-source"
    directory.mkdir(parents=True)
    source = '''import { appendFileSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
const path = EVENT_PATH;
const hash = value => createHash("sha256").update(String(value)).digest("hex");
const number = value => typeof value === "number" && Number.isFinite(value) ? value : null;
function record(value) {
 if (statSync(path).size > 65536) throw new Error("maintenance native event bound");
 appendFileSync(path, JSON.stringify(value) + "\\n");
}
export default { id: "maintenance-native-observer", server: async () => ({
 "experimental.chat.system.transform": async () => {},
 event: async input => {
  const event = input.event ?? input;
  const p = event.properties ?? {};
  if (["session.idle", "session.error"].includes(event.type) && typeof p.sessionID === "string")
   record({event: event.type, session_sha256: hash(p.sessionID)});
  if (event.type !== "message.updated" || p.info?.role !== "assistant") return;
  const info = p.info;
  if (typeof info.sessionID !== "string") return;
  record({event: "message.updated", session_sha256: hash(info.sessionID),
    provider: info.providerID ?? null, model: info.modelID ?? null,
    finished: info.time?.completed != null, cost: number(info.cost),
    tokens: {input:number(info.tokens?.input), output:number(info.tokens?.output),
      reasoning:number(info.tokens?.reasoning), cache_read:number(info.tokens?.cache?.read)}});
 }
})};
'''.replace("EVENT_PATH", json.dumps(str(event_path)))
    (directory / "deeplaw-native.ts").write_text(source)
    plugin_dir = workspace / ".opencode/plugins"
    plugin_dir.mkdir()
    (plugin_dir / "deeplaw-native.ts").write_text(shared.privacy_wrapper(
        "../supervised-source/deeplaw-native.ts", directory=str(workspace), worktree=str(workspace),
    ))


def _model_execution_observed(observations: list[dict[str, Any]], session_sha: str | None) -> bool:
    """Require completed, bound native response usage; dispatch alone is not execution."""
    for item in observations:
        if not isinstance(item, dict):
            continue
        tokens = item.get("tokens")
        if (session_sha is not None and item.get("session_sha256") == session_sha
                and item.get("event") == "message.updated" and item.get("finished") is True
                and item.get("provider") == "deepseek"
                and item.get("model") == "deepseek-v4-flash" and isinstance(tokens, dict)
                and any(type(tokens.get(key)) in (int, float)
                        and math.isfinite(tokens[key]) and tokens[key] > 0
                        for key in ("output", "reasoning"))):
            return True
    return False


def _failed_slot(configuration: str, scenario: str, *, run_id: str, candidate_id: str,
                 stage: str, failure: str) -> dict[str, Any]:
    not_dispatched = stage in {"setup", "context", "context_snapshot"}
    return {
        "configuration_id": configuration, "scenario_id": scenario,
        "run_id": run_id, "candidate_id": candidate_id,
        "binding": None, "trace": None, "score": None, "native_observations": [],
        "host_exit_code": None, "forced_kill": None, "guard": None, "elapsed_ms": None,
        "failure": failure, "failure_stage": stage,
        "status": "not_executed" if not_dispatched else "failed",
        "formal_admission": False, "model_task_executed": False,
        "prompt_dispatch_attempted": False if not_dispatched else None,
        "prompt_dispatched": False if not_dispatched else None,
        "model_execution_status": ("not_dispatched" if not_dispatched
                                   else "unverified_execution_exception"),
        "zero_model_mcp_status": None,
        "state_score_status": ("not_executed" if not_dispatched
                               else "unavailable_execution_exception"),
        "isolation_evidence": "not_supplied_by_this_producer",
        "knowledge_outcome": {
            "status": "not_committed", "reason": "case_preparation_or_execution_failed",
        },
    }


def execute_case(
    case_root: Path, *, configuration: str, scenario: str, context: dict[str, Any],
    binary: Path, node: Path, key_file: Path, run_id: str, candidate_id: str,
    zero_model: bool = False,
) -> dict[str, Any]:
    process = None
    guard = None
    binding = None
    trace_path = None
    events_path = None
    session_sha = None
    failure: str | None = None
    failure_stage: str | None = None
    observations: list[dict[str, Any]] = []
    started = time.monotonic()
    forced = False
    dispatch_attempted = False
    dispatched = False
    preflight_status: dict[str, Any] | None = None
    stage = "setup"
    try:
        case_root.mkdir(mode=0o700)
        workspace = case_root / "workspace"
        workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(workspace)], check=True, capture_output=True)
        events_path = case_root / "native-events.jsonl"
        events_path.touch(mode=0o600)
        _install_observer(workspace, events_path)
        capsule_path = case_root / "provider-capsule.json"
        _json_file(capsule_path, context["provider_capsule"])
        binding = make_owner_binding(
            configuration, scenario, run_id=run_id, candidate_id=candidate_id,
            context_id=context["capsule"]["capsule_id"],
            capsule_digest=digest(context["provider_capsule"]),
        )
        trace_path = case_root / "action-trace.json"
        command = ["/usr/bin/env", "-i", "PATH=" + os.defpath, "PYTHONPATH=" + str(ROOT),
                   sys.executable, "-m", "benchmarks.hosts.maintenance_task_mcp", "--stdio"]
        for key in ("configuration_id", "scenario_id", "run_id", "candidate_id",
                    "context_id", "capsule_digest", "binding_sha256"):
            command.extend(["--" + key.replace("_", "-"), binding[key]])
        command.extend(["--provider-capsule-file", str(capsule_path),
                        "--trace-path", str(trace_path)])
        environment = host.build_host_environment(
            root=case_root, opencode_binary=binary, node_binary=node,
        )
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
                    "TMPDIR", "APPDATA", "LOCALAPPDATA", "OPENCODE_CONFIG_DIR"):
            Path(environment[key]).mkdir(parents=True, exist_ok=True)
        for plugin_directory in (
            Path(environment["XDG_CONFIG_HOME"]) / "opencode",
            Path(environment["OPENCODE_CONFIG_DIR"]), workspace / ".opencode",
        ):
            host._freeze_local_plugin_dependency_state(
                plugin_directory, expected_version="1.18.16-deeplaw.1",
            )
        nonce = secrets.token_hex(32)
        guard_path = case_root / "guard.json"
        _json_file(guard_path, {"key_file": str(key_file), "nonce": nonce,
                                "tool_profile": "maintenance"})
        guard = shared.ExternalGuard(guard_path)
        stage = "host"
        guard.start()
        environment["DEEPSEEK_API_KEY"] = nonce
        config = host.build_opencode_config()
        permission = {"*": "deny", TOOL: "allow"}
        config["permission"] = permission
        config["agent"]["qualification"].update({
            "permission": permission, "steps": 6,
            "description": "Finite public maintenance environment",
            "prompt": (
                f"Use only {TOOL}. Inspect the task with operation describe, then submit "
                "actions to satisfy its goal within the stated budget. Use current state hashes. "
                "Treat memory as fallible context. Stop after completion or an unknown outcome; "
                "do not repeat an action with an unknown result. Do not invoke other tools."
            ),
        })
        config["mcp"] = {"maintenance_environment": {
            "type": "local", "command": command, "enabled": True, "timeout": 60000,
        }}
        config["provider"]["deepseek"]["options"]["baseURL"] = guard.url
        config["compaction"] = {"auto": False, "prune": False}
        _json_file(Path(environment["OPENCODE_CONFIG"]), config)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        process = subprocess.Popen(
            [str(binary), "serve", "--hostname", "127.0.0.1", "--port", str(port)],
            cwd=workspace, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        stage = "readiness"
        deadline = time.monotonic() + 20
        while True:
            if process.poll() is not None or time.monotonic() >= deadline:
                raise ValueError("Host readiness failed")
            try:
                _control(base_url, "GET", "/global/health")
                break
            except OSError:
                time.sleep(.1)
        session = _control(base_url, "POST", "/session", {})["id"]
        session_sha = shared.digest(session.encode())
        if zero_model:
            mcp_status = _control(base_url, "GET", "/mcp")
            preflight_status = json.loads(canonical_json(mcp_status).replace(
                str(case_root), "[TASK_ROOT]",
            ).replace(str(ROOT), "[SOURCE_ROOT]").replace(nonce, "[NONCE]"))
            if mcp_status.get("maintenance_environment", {}).get("status") != "connected":
                raise ValueError("maintenance MCP preflight failed")
        else:
            guard.active(True)
            stage = "dispatch"
            dispatch_attempted = True
            _control(base_url, "POST", f"/session/{session}/prompt_async", {
                "agent": "qualification",
                "model": {"providerID": "deepseek", "modelID": "deepseek-v4-flash"},
                "parts": [{"type": "text", "text":
                           "Inspect the finite maintenance task and complete it."}],
            })
            dispatched = True
            stage = "observation"
            deadline = time.monotonic() + MAX_SECONDS
            while time.monotonic() < deadline:
                observations = shared.json_lines(events_path)
                matching = [item for item in observations if item["session_sha256"] == session_sha]
                if any(item["event"] == "session.error" for item in matching):
                    raise ValueError("Host task error")
                if (any(item.get("finished") is True for item in matching)
                        and any(item["event"] == "session.idle" for item in matching)):
                    break
                if process.poll() is not None:
                    raise ValueError("Host exited during task")
                time.sleep(.2)
            else:
                raise TimeoutError("Host task deadline")
            completed = [item for item in matching if item.get("finished") is True]
            if not completed or any(
                item.get("provider") != "deepseek" or item.get("model") != "deepseek-v4-flash"
                for item in completed
            ):
                raise ValueError("actual response model identity differs")
    except Exception as error:
        failure = type(error).__name__  # Do not retain provider/control exception text.
        failure_stage = stage
    finally:
        try:
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    forced = True
                    process.kill()
                    process.wait(timeout=5)
        except Exception as error:
            failure = failure or type(error).__name__
            failure_stage = failure_stage or "host_cleanup"
        try:
            if guard is not None:
                guard.stop()
        except Exception as error:
            failure = failure or type(error).__name__
            failure_stage = failure_stage or "guard_cleanup"
    if process is not None and process.returncode != 0:
        failure = failure or "HostExitNonZero"
        failure_stage = failure_stage or "host_cleanup"
    try:
        if events_path is not None and events_path.exists():
            observations = shared.json_lines(events_path)
    except Exception as error:
        failure = failure or type(error).__name__
        failure_stage = failure_stage or "observation"
    model_executed = (not zero_model and dispatch_attempted
                      and _model_execution_observed(observations, session_sha))
    try:
        trace = (load_persisted_trace(trace_path, binding=binding)
                 if trace_path is not None and trace_path.exists() else None)
    except (MaintenanceMCPError, OSError, ValueError):
        # Preserve the original bounded trace file, but do not score rejected
        # evidence as a successful action sequence or abort the fixed sample.
        trace = None
        failure = failure or "ActionTraceRejected"
        failure_stage = failure_stage or "trace"
    scored = None
    try:
        if trace and not zero_model:
            scored = score_host_trace(configuration, scenario, trace["events"])
    except Exception as error:
        failure = failure or type(error).__name__
        failure_stage = failure_stage or "scoring"
    result = {
        "configuration_id": configuration, "scenario_id": scenario,
        "run_id": run_id, "candidate_id": candidate_id,
        "binding": binding, "trace": trace, "score": scored, "native_observations": observations,
        "host_exit_code": process.returncode if process else None, "forced_kill": forced,
        "guard": guard.receipt if guard is not None else None,
        "elapsed_ms": (time.monotonic() - started) * 1000,
        "failure": failure, "failure_stage": failure_stage,
        "formal_admission": False, "model_task_executed": model_executed,
        "prompt_dispatch_attempted": dispatch_attempted, "prompt_dispatched": dispatched,
        "model_execution_status": ("observed" if model_executed else
                                   "unverified_after_dispatch" if dispatch_attempted else
                                   "not_dispatched"),
        "zero_model_mcp_status": preflight_status,
        "state_score_status": ("not_executed_zero_model_preflight" if zero_model else
                               "observed" if scored is not None else
                               "unavailable_invalid_or_missing_trace"),
        "isolation_evidence": "not_supplied_by_this_producer",
    }
    result["status"] = ("not_executed" if not dispatch_attempted else
                        "succeeded" if outcome_commit_eligible(result)
                        and scored is not None and scored["passed"] is True else "failed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    value = shared.read_json(Path(args.input))
    root = Path(value["root"]).resolve()
    if root.exists() or root.is_relative_to(ROOT):
        raise ValueError("maintenance run must use a fresh external directory")
    binary = shared.exact_file(Path(value["binary"]), value["binary_sha256"])
    root.mkdir(mode=0o700)
    _json_file(root / "frozen-input.json", {
        "input_sha256": FROZEN_INPUT_SHA256, "configurations": CONFIGURATION_ORDER,
        "scenarios": SCENARIO_ORDER, "max_seconds_per_case": MAX_SECONDS,
        "max_provider_requests_per_case": 6, "binary_sha256": value["binary_sha256"],
        "candidate_id": value["candidate_id"],
    })
    results = []
    for configuration in CONFIGURATION_ORDER:
        vault = root / (configuration + "-vault")
        setup_failure = None
        try:
            setup = prepare_context_vault(vault, configuration)
        except Exception as error:
            setup_failure = type(error).__name__
        for scenario in SCENARIO_ORDER:
            run_id = value["run_id"] + "-" + configuration + "-" + scenario
            context_snapshot = None
            context = None
            if setup_failure is not None:
                result = _failed_slot(
                    configuration, scenario, run_id=run_id, candidate_id=value["candidate_id"],
                    stage="setup", failure=setup_failure,
                )
            else:
                result = None
                stage = "context"
                try:
                    context = capture_context(vault, configuration, scenario)
                    stage = "context_snapshot"
                    context_snapshot = _persist_context_snapshot(
                        root, vault, configuration=configuration, scenario=scenario,
                        context=context, run_id=run_id, candidate_id=value["candidate_id"],
                        expected_vault_id=setup["vault_id"],
                    )
                    stage = "execution"
                    result = execute_case(
                        root / (configuration + "-" + scenario), configuration=configuration,
                        scenario=scenario, context=context, binary=binary, node=Path(value["node"]),
                        key_file=Path(value["key_file"]), run_id=run_id,
                        candidate_id=value["candidate_id"],
                    )
                    result.update({"configuration_id": configuration, "scenario_id": scenario,
                                   "run_id": run_id, "candidate_id": value["candidate_id"]})
                    result["status"] = (
                        "succeeded" if outcome_commit_eligible(result)
                        and isinstance(result.get("score"), dict)
                        and result["score"].get("passed") is True else
                        "not_executed" if result.get("prompt_dispatch_attempted") is False else
                        "failed"
                    )
                    stage = "outcome"
                    if result["trace"] and outcome_commit_eligible(result):
                        result["knowledge_outcome"] = record_host_outcome(
                            vault, setup["grant_id"], configuration=configuration,
                            scenario=scenario,
                            host_run_id=run_id, host_id="opencode", model_id="deepseek-v4-flash",
                            context=context, trace_payload=result["trace"], score=result["score"],
                            candidate_id=value["candidate_id"],
                        )
                    else:
                        result["knowledge_outcome"] = {
                            "status": "not_committed",
                            "reason": "trace_missing_or_host_lifecycle_unverified",
                        }
                except Exception as error:
                    if result is not None and stage == "outcome":
                        result["failure"] = result.get("failure") or type(error).__name__
                        result["failure_stage"] = result.get("failure_stage") or stage
                        result["status"] = "failed"
                        result["knowledge_outcome"] = {
                            "status": "failed", "reason": "outcome_recording_failed",
                            "commit_status": "unknown",
                        }
                        if isinstance(error, OutcomeGrantClosureError):
                            result["knowledge_outcome"].update({
                                "grant_closure_status": "failed",
                                "mutation_failure": error.mutation_failure,
                                "cleanup_failure": error.cleanup_failure,
                            })
                    else:
                        result = _failed_slot(
                            configuration, scenario, run_id=run_id,
                            candidate_id=value["candidate_id"], stage=stage,
                            failure=type(error).__name__,
                        )
            result["context_snapshot"] = context_snapshot
            _capture_evidence_files(
                root, result, configuration=configuration, scenario=scenario,
                context=context, prepared=context_snapshot is not None,
            )
            results.append(result)
            _json_file(root / f"case-{len(results):02}.json", result)
    _json_file(root / "report.json", {"results": results, "formal_admission": False,
                                    "fixed_input_sha256": FROZEN_INPUT_SHA256})


if __name__ == "__main__":
    main()
