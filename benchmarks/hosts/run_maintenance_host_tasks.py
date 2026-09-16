"""Finite real OpenCode tasks with owner-side state scoring and no transcript reads.

This producer is not a formal isolation attestation. Formal collection must
supply its separate native process and security-domain observations.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from benchmarks.hosts import opencode_single_task_producer as shared
from benchmarks.hosts import run_pass13_opencode_continuity_qualification as host
from benchmarks.hosts.maintenance_host_context import (
    capture_context,
    digest,
    prepare_context_vault,
    record_host_outcome,
)
from benchmarks.hosts.maintenance_task_cases import (
    CONFIGURATION_ORDER,
    FROZEN_INPUT_SHA256,
    SCENARIO_ORDER,
    score_host_trace,
)
from benchmarks.hosts.maintenance_task_mcp import (
    MaintenanceMCPError,
    load_persisted_trace,
    make_owner_binding,
)
from deeplaw.util import canonical_json

ROOT = Path(__file__).resolve().parents[2]
TOOL = shared.GUARD_TOOL_PROFILES["maintenance"]
MAX_SECONDS = 180


def outcome_commit_eligible(result: dict[str, Any]) -> bool:
    guard = result.get("guard")
    return (
        result.get("failure") is None and result.get("forced_kill") is False
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


def execute_case(
    case_root: Path, *, configuration: str, scenario: str, context: dict[str, Any],
    binary: Path, node: Path, key_file: Path, run_id: str, candidate_id: str,
    zero_model: bool = False,
) -> dict[str, Any]:
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
    command.extend(["--provider-capsule-file", str(capsule_path), "--trace-path", str(trace_path)])
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
    process = None
    failure: str | None = None
    observations: list[dict[str, Any]] = []
    started = time.monotonic()
    forced = False
    preflight_status: dict[str, Any] | None = None
    try:
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
            _control(base_url, "POST", f"/session/{session}/prompt_async", {
                "agent": "qualification",
                "model": {"providerID": "deepseek", "modelID": "deepseek-v4-flash"},
                "parts": [{"type": "text", "text":
                           "Inspect the finite maintenance task and complete it."}],
            })
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
    finally:
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=7)
            except subprocess.TimeoutExpired:
                forced = True
                process.kill()
                process.wait(timeout=5)
        try:
            guard.stop()
        except Exception as error:
            failure = failure or type(error).__name__
    if process is not None and process.returncode != 0:
        failure = failure or "HostExitNonZero"
    try:
        trace = load_persisted_trace(trace_path, binding=binding) if trace_path.exists() else None
    except (MaintenanceMCPError, OSError, ValueError):
        # Preserve the original bounded trace file, but do not score rejected
        # evidence as a successful action sequence or abort the fixed sample.
        trace = None
        failure = failure or "ActionTraceRejected"
    scored = (score_host_trace(configuration, scenario, trace["events"])
              if trace and not zero_model else None)
    return {
        "binding": binding, "trace": trace, "score": scored, "native_observations": observations,
        "host_exit_code": process.returncode if process else None, "forced_kill": forced,
        "guard": guard.receipt, "elapsed_ms": (time.monotonic() - started) * 1000,
        "failure": failure, "formal_admission": False, "model_task_executed": not zero_model,
        "zero_model_mcp_status": preflight_status,
        "state_score_status": ("not_executed_zero_model_preflight" if zero_model else
                               "observed" if trace else "unavailable_invalid_or_missing_trace"),
        "isolation_evidence": "not_supplied_by_this_producer",
    }


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
        setup = prepare_context_vault(vault, configuration)
        for scenario in SCENARIO_ORDER:
            context = capture_context(vault, configuration, scenario)
            run_id = value["run_id"] + "-" + configuration + "-" + scenario
            result = execute_case(
                root / (configuration + "-" + scenario), configuration=configuration,
                scenario=scenario, context=context, binary=binary, node=Path(value["node"]),
                key_file=Path(value["key_file"]), run_id=run_id, candidate_id=value["candidate_id"],
            )
            if result["trace"] and outcome_commit_eligible(result):
                result["knowledge_outcome"] = record_host_outcome(
                    vault, setup["grant_id"], configuration=configuration, scenario=scenario,
                    host_run_id=run_id, host_id="opencode", model_id="deepseek-v4-flash",
                    context=context, trace_payload=result["trace"], score=result["score"],
                    candidate_id=value["candidate_id"],
                )
            else:
                result["knowledge_outcome"] = {
                    "status": "not_committed",
                    "reason": "trace_missing_or_host_lifecycle_unverified",
                }
            results.append(result)
            _json_file(root / f"case-{len(results):02}.json", result)
    _json_file(root / "report.json", {"results": results, "formal_admission": False,
                                    "fixed_input_sha256": FROZEN_INPUT_SHA256})


if __name__ == "__main__":
    main()
