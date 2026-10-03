"""Owner-side, zero-model VZ preflight with immutable local input copies.

This entry point exercises the guest's fixed control routes. It does not load
credentials, send prompts, or produce formal qualification. The supplied native
launcher, kernel and initrd must already have owner-approved SHA-256 identities.
"""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from .native_slot_frames import FrameKind, read_frame, write_frame


class NativePreflightError(ValueError):
    pass


def _freeze(
    source: Path, target: Path, expected: str, *, executable: bool = False
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise NativePreflightError("input_digest_invalid")
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as incoming:
        info = os.fstat(incoming.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 512 * 1024 * 1024:
            raise NativePreflightError("input_file_invalid")
        copied = 0
        with target.open("xb") as outgoing:
            while chunk := incoming.read(min(1024 * 1024, info.st_size - copied + 1)):
                copied += len(chunk)
                if copied > info.st_size:
                    raise NativePreflightError("input_size_changed")
                digest.update(chunk)
                outgoing.write(chunk)
        if copied != info.st_size:
            raise NativePreflightError("input_size_changed")
    if digest.hexdigest() != expected:
        raise NativePreflightError("input_digest_mismatch")
    target.chmod(0o500 if executable else 0o400)
    return {"sha256": expected, "bytes": target.stat().st_size}


def _guest_fd(path: Path) -> socket.socket:
    descriptors: list[int] = []
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(35)
            connection.connect(str(path))
            data, ancillary, flags, _ = connection.recvmsg(
                17,
                socket.CMSG_SPACE(8),
                socket.MSG_WAITALL,
            )
            for level, kind, payload in ancillary:
                if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
                    raise NativePreflightError("handoff_ancillary_invalid")
                values = array.array("i")
                values.frombytes(payload)
                descriptors.extend(values)
            expected = b"DLVZ\x01\x01\x00\x10" + (4050).to_bytes(4, "big") + b"\0" * 4
            if data != expected or flags & ~socket.MSG_WAITALL != 0 or len(descriptors) != 1:
                raise NativePreflightError("handoff_frame_invalid")
        result = socket.socket(fileno=descriptors[0])
        descriptors.pop()
        result.settimeout(8)
        return result
    finally:
        for descriptor in descriptors:
            with suppress(OSError):
                os.close(descriptor)


def _exchange(
    connection: socket.socket, sequence: int, operation: str, *, timeout: float = 8,
    reply_timeout: float | None = None,
    provider_forward: Callable | None = None, **fields: str,
) -> dict[str, Any]:
    payload = json.dumps(
        {"op": operation, **fields}, sort_keys=True, separators=(",", ":")
    ).encode()
    if provider_forward is None:
        write_frame(connection, FrameKind.CONTROL_REQUEST, sequence, payload, timeout=timeout)
        reply = read_frame(connection, timeout=timeout if reply_timeout is None else reply_timeout)
        if reply.kind != FrameKind.CONTROL_REPLY or reply.sequence != sequence:
            raise NativePreflightError("control_reply_identity_invalid")
        raw = reply.payload
    else:
        from .native_provider_bridge import owner_exchange_with_provider

        raw = owner_exchange_with_provider(
            connection, sequence=sequence, payload=payload, forward=provider_forward,
            timeout_seconds=timeout if reply_timeout is None else reply_timeout,
            max_requests=1,
        )
    value = json.loads(raw)
    if (
        isinstance(value, dict)
        and set(value) == {"ok", "error", "formal_admission"}
        and value["ok"] is False
        and value["formal_admission"] is False
        and isinstance(value["error"], str)
        and re.fullmatch(r"[a-z][a-z0-9_]{1,63}", value["error"])
    ):
        raise NativePreflightError("guest_" + value["error"])
    if (
        not isinstance(value, dict)
        or set(value) != {"ok", "result", "formal_admission"}
        or value["ok"] is not True
        or value["formal_admission"] is not False
        or not isinstance(value["result"], dict)
    ):
        raise NativePreflightError("control_reply_rejected")
    return value["result"]


def _validate_execution_observation(
    value: Any, roles: list[dict[str, Any]], expected_host_sha256: str,
    expected_binding_sha256: str,
) -> dict[str, Any]:
    from .linux_host_execution_observer import validate_execution_snapshot

    if (
        not isinstance(value, dict)
        or set(value) != {"formal_admission", "claim_eligible", "binding_sha256", "roles"}
        or value["formal_admission"] is not False or value["claim_eligible"] is not False
        or value.get("binding_sha256") != expected_binding_sha256
        or not isinstance(value["roles"], dict) or set(value["roles"]) != {"host", "mcp"}
    ):
        raise NativePreflightError("execution_observation_invalid")
    try:
        snapshots = {
            role: validate_execution_snapshot(snapshot) for role, snapshot in value["roles"].items()
        }
    except ValueError as error:
        raise NativePreflightError("execution_observation_invalid") from error
    for role, snapshot in snapshots.items():
        launcher_role = next(row for row in roles if row["role"] == role)
        namespace_keys = {"mnt": "mount", "pid": "pid", "net": "net", "ipc": "ipc"}
        if (
            snapshot["role"] != role or snapshot["uid"] != launcher_role["uid"]
            or snapshot["process_start_identity_sha256"] != launcher_role.get(
                "process_start_identity_sha256"
            )
            or snapshot["binding_sha256"] != value["binding_sha256"]
            or any(
                snapshot["namespaces"][name] != launcher_role.get("observation", {}).get(
                    prefix + "_namespace_sha256"
                )
                for name, prefix in namespace_keys.items()
            )
        ):
            raise NativePreflightError("execution_role_binding_gap")
    if snapshots["host"]["executable_sha256"] != expected_host_sha256:
        raise NativePreflightError("execution_host_bytes_gap")
    return {**value, "roles": snapshots}


def _validate_execution_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "run_id", "candidate_id", "candidate_binding",
    }:
        raise NativePreflightError("execution_context_invalid")
    for key in ("run_id", "candidate_id"):
        if (
            not isinstance(value[key], str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", value[key]) is None
        ):
            raise NativePreflightError("execution_context_invalid")
    candidate = value["candidate_binding"]
    if not isinstance(candidate, dict) or set(candidate) != {
        "commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256",
    }:
        raise NativePreflightError("execution_context_invalid")
    for key, digest in candidate.items():
        length = 40 if key in {"commit", "tree"} else 64
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{" + str(length) + "}", digest) is None
            or digest == "0" * length
        ):
            raise NativePreflightError("execution_context_invalid")
    return json.loads(json.dumps(value))


def _execution_context_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise NativePreflightError("execution_context_duplicate_key")
        value[key] = item
    return value


def run_preflight(**arguments: Any) -> dict[str, Any]:
    """Run zero-model control; this entry point cannot install a Provider forwarder."""
    if any(key in arguments for key in ("_model_probe_forward", "_model_probe_bind_authority")):
        raise NativePreflightError("zero_model_provider_forbidden")
    return _run_slot(**arguments)


def run_model_probe(
    *, forward: Callable, bind_authority: Callable[[str], None] | None = None,
    **arguments: Any,
) -> dict[str, Any]:
    """Run one explicit engineering model probe using an existing owner authority."""
    if not callable(forward) or not all(arguments.get(key) is True for key in (
        "require_process_observation", "require_boundary_observation",
        "require_fork_observation", "require_route_observation", "fork_routes_only",
    )) or any(key in arguments for key in (
        "_model_probe_forward", "_model_probe_bind_authority",
    )) or (bind_authority is not None and not callable(bind_authority)):
        raise NativePreflightError("model_probe_observations_required")
    return _run_slot(
        _model_probe_forward=forward, _model_probe_bind_authority=bind_authority, **arguments,
    )


def _model_probe_observed(value: Any, session_id: str) -> bool:
    """Validate engineering metadata without creating native qualification authority."""
    from .native_provider_bridge import _child_observation

    if not isinstance(value, dict) or set(value) != {
        "schema_version", "formal_admission", "mcp_functional_claim",
        "model_invocation_count", "model_task_executed", "provider_requests_admitted",
        "provider_requests_forwarded", "client", "proxy", "children",
        "cleanup_confirmed", "gaps",
    } or (
        value["schema_version"] != "deeplaw.fixed-native-model-probe/v1"
        or value["formal_admission"] is not False or value["mcp_functional_claim"] is not False
        or value["model_invocation_count"] is not None
        or type(value["model_task_executed"]) is not bool
        or type(value["cleanup_confirmed"]) is not bool
        or not isinstance(value["gaps"], list)
        or len(value["gaps"]) > 8
        or any(not isinstance(gap, str) or re.fullmatch(r"[a-z][a-z0-9_]{1,63}", gap) is None
               for gap in value["gaps"])
    ):
        raise NativePreflightError("model_probe_observation_invalid")
    if value["client"] is not None:
        _child_observation(json.dumps(value["client"], allow_nan=False).encode(), proxy=False)
    if value["proxy"] is not None:
        _child_observation(json.dumps(value["proxy"], allow_nan=False).encode(), proxy=True)
    children = value["children"]
    if not isinstance(children, dict) or set(children) - {"client", "proxy"}:
        raise NativePreflightError("model_probe_observation_invalid")
    for child in children.values():
        if not isinstance(child, dict) or set(child) != {"reaped", "exit_code"} or (
            type(child["reaped"]) is not bool
            or (child["exit_code"] is not None and type(child["exit_code"]) is not int)
        ):
            raise NativePreflightError("model_probe_observation_invalid")
    client, proxy = value["client"], value["proxy"]
    return (
        value["model_task_executed"] is True and value["cleanup_confirmed"] is True
        and not value["gaps"] and set(children) == {"client", "proxy"}
        and all(child["reaped"] is True and child["exit_code"] == 0
                for child in children.values())
        and type(value["provider_requests_admitted"]) is int
        and value["provider_requests_admitted"] == 1
        and type(value["provider_requests_forwarded"]) is int
        and value["provider_requests_forwarded"] == 1
        and isinstance(proxy, dict) and proxy["admitted"] == 1 and proxy["rejected"] == 0
        and isinstance(client, dict) and client.get("model_task_executed") is True
        and isinstance(client.get("assistant"), dict)
        and client["assistant"]["sessionID_sha256"] == hashlib.sha256(session_id.encode())
        .hexdigest()
    )


def _route_observation_for_profile(value: Any, *, model_probe: bool) -> dict[str, Any]:
    from .linux_http_route_observer import validate_observation

    if isinstance(value, dict) and set(value) == {"status", "failure", "formal_admission"} and (
        value["status"] == "gap" and value["formal_admission"] is False
    ):
        code = value["failure"] if isinstance(value["failure"], str) and value["failure"] in {
            "tcp_port_gap", "ipv4_route_gap", "packet_protocol_gap", "tcp_window_open",
            "capture_timeout", "packet_capture_loss", "capture_os_error",
        } else "other_gap"
        raise NativePreflightError("route_capture_" + code)
    observation = validate_observation(value)
    expected = "deeplaw.linux-http-route-observation/v2" if model_probe else (
        "deeplaw.linux-http-route-observation/v1"
    )
    if observation["schema_version"] != expected:
        raise NativePreflightError("route_observation_profile_gap")
    return observation


def _run_slot(
    *,
    native: Path,
    kernel: Path,
    initrd: Path,
    destination: Path,
    native_sha256: str,
    kernel_sha256: str,
    initrd_sha256: str,
    require_process_observation: bool = False,
    require_boundary_observation: bool = False,
    require_fork_observation: bool = False,
    require_route_observation: bool = False,
    require_execution_observation: bool = True,
    expected_host_sha256: str | None = None,
    execution_context: dict[str, Any] | None = None,
    fork_routes_only: bool = False,
    _model_probe_forward: Callable | None = None,
    _model_probe_bind_authority: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise NativePreflightError("macos_required")
    if require_execution_observation is not True:
        raise NativePreflightError("execution_observation_required")
    if require_execution_observation and (
        not isinstance(expected_host_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_host_sha256) is None
        or expected_host_sha256 == "0" * 64
    ):
        raise NativePreflightError("expected_host_digest_required")
    if require_execution_observation:
        execution_context = _validate_execution_context(execution_context)
    elif execution_context is not None:
        raise NativePreflightError("execution_context_without_observation")
    destination = destination.absolute()
    destination.mkdir(mode=0o700)
    result: dict[str, Any] = {
        "formal_admission": False,
        "model_invocations": None if _model_probe_forward is not None else 0,
        # A supplied callback does not establish external credential loading.
        "credentials_supplied": None if _model_probe_forward is not None else False,
        "failure": None,
        "failure_stage": "input_freeze",
    }
    process: subprocess.Popen[bytes] | None = None
    connection: socket.socket | None = None
    try:
        frozen = destination / "inputs"
        frozen.mkdir(mode=0o700)
        result["inputs"] = {
            name: _freeze(source, frozen / name, digest, executable=name == "launcher")
            for name, source, digest in (
                ("launcher", native, native_sha256),
                ("kernel", kernel, kernel_sha256),
                ("initrd", initrd, initrd_sha256),
            )
        }
        handoff = destination / "control.sock"
        with (
            (destination / "native.jsonl").open("xb") as output,
            (destination / "native.stderr").open("xb") as error,
        ):
            result["failure_stage"] = "native_start"
            process = subprocess.Popen(
                [
                    str(frozen / "launcher"),
                    "--kernel",
                    str(frozen / "kernel"),
                    "--initrd",
                    str(frozen / "initrd"),
                    "--cpu",
                    "2",
                    "--memory-mib",
                    "2048",
                    "--timeout-seconds",
                    "210" if _model_probe_forward is not None else "90",
                    "--guest-control-port",
                    "4050",
                    "--fd-handoff-socket",
                    str(handoff),
                ],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=error,
                env={"PATH": os.defpath},
                cwd=destination,
            )
            deadline = time.monotonic() + 10
            while not handoff.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if not handoff.exists():
                raise NativePreflightError("handoff_unavailable")
            result["failure_stage"] = "guest_handoff"
            connection = _guest_fd(handoff)
            sequence_offset = 0
            if require_execution_observation:
                binding = {
                    **execution_context, "inputs": result["inputs"],
                    "host_executable_sha256": expected_host_sha256,
                    "nonce_sha256": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                    **({"execution_purpose": "native_model_probe"}
                       if _model_probe_forward is not None else {}),
                }
                binding_sha256 = hashlib.sha256(json.dumps(
                    binding, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode()).hexdigest()
                result["execution_binding"] = {**binding, "binding_sha256": binding_sha256}
                result["failure_stage"] = "execution_binding"
                bound = _exchange(
                    connection, 1, "bind_execution", binding_sha256=binding_sha256,
                    run_id=execution_context["run_id"],
                    candidate_id=execution_context["candidate_id"],
                    reply_timeout=15,
                )
                if bound != {"binding_sha256": binding_sha256}:
                    raise NativePreflightError("execution_challenge_reply_gap")
                if _model_probe_bind_authority is not None:
                    result["failure_stage"] = "authority_binding"
                    try:
                        _model_probe_bind_authority(binding_sha256)
                    except Exception:
                        raise NativePreflightError("authority_binding_failed") from None
                sequence_offset = 1
            result["failure_stage"] = "health"
            health = _exchange(connection, 1 + sequence_offset, "health", timeout=16)
            if set(health) != {"healthy"} or health["healthy"] is not True:
                raise NativePreflightError("host_not_healthy")
            result["failure_stage"] = "new_session"
            parent = _exchange(connection, 2 + sequence_offset, "new_session")
            if set(parent) != {"session_id"} or not isinstance(parent["session_id"], str):
                raise NativePreflightError("session_reply_invalid")
            if not fork_routes_only:
                result["failure_stage"] = "mcp_status"
                mcp = _exchange(connection, 3 + sequence_offset, "mcp_status")
                if set(mcp) != {"connected"} or mcp["connected"] is not True:
                    raise NativePreflightError("mcp_not_connected")
            fork_sequence = (3 if fork_routes_only else 4) + sequence_offset
            result["failure_stage"] = "fork"
            child = _exchange(
                connection, fork_sequence, "fork", session_id=parent["session_id"],
                reply_timeout=31 if require_fork_observation else 8,
            )
            if (
                set(child) != {"session_id"}
                or not isinstance(child["session_id"], str)
                or child == parent
            ):
                raise NativePreflightError("fork_reply_invalid")
            stop_sequence = fork_sequence + 1
            if _model_probe_forward is not None:
                result["failure_stage"] = "model_probe"
                result["model_probe"] = _exchange(
                    connection, stop_sequence, "model_probe", session_id=child["session_id"],
                    timeout=125, provider_forward=_model_probe_forward,
                )
                model_observed = _model_probe_observed(result["model_probe"], child["session_id"])
                if model_observed:
                    result["model_invocations"] = 1
                stop_sequence += 1
            result["failure_stage"] = "stop"
            stop = _exchange(connection, stop_sequence, "stop")
            if set(stop) != {"stopping"} or stop["stopping"] is not True:
                raise NativePreflightError("stop_reply_invalid")
            result["failure_stage"] = "final_observations"
            final = read_frame(connection)
            if final.kind != FrameKind.FINAL or final.sequence != stop_sequence + 1:
                raise NativePreflightError("final_reply_identity_invalid")
            value = json.loads(final.payload)
            expected_keys = {"formal_admission", "launcher_receipt"}
            if require_process_observation:
                expected_keys.add("process_observation")
            if require_boundary_observation:
                expected_keys.add("boundary_observation")
            if require_fork_observation:
                expected_keys.add("fork_observation")
            if require_route_observation:
                expected_keys.add("route_observation")
            if require_execution_observation:
                expected_keys.add("execution_observation")
            if _model_probe_forward is not None:
                expected_keys.add("host_failure_codes")
            if (
                not isinstance(value, dict) or set(value) != expected_keys
                or value["formal_admission"] is not False
            ):
                raise NativePreflightError("final_reply_invalid")
            if _model_probe_forward is not None:
                from .native_provider_bridge import validate_host_failure_codes

                result["host_failure_codes"] = validate_host_failure_codes(
                    value["host_failure_codes"],
                )
            if require_route_observation:
                observation = _route_observation_for_profile(
                    value["route_observation"], model_probe=_model_probe_forward is not None,
                )
                result["route_observation"] = observation
                requests = observation["requests"]
                expected_routes = ["health", "new_session"]
                if not fork_routes_only:
                    expected_routes.append("mcp_status")
                expected_routes.append("fork")
                if _model_probe_forward is not None:
                    expected_routes.append("model_message")
                if [row["route"] for row in requests] != expected_routes:
                    raise NativePreflightError("observed_route_sequence_gap")
                expected_target = "/session/" + parent["session_id"] + "/fork"
                fork_row = requests[-2] if _model_probe_forward is not None else requests[-1]
                if fork_row["target_sha256"] != hashlib.sha256(
                    expected_target.encode()
                ).hexdigest():
                    raise NativePreflightError("observed_fork_route_gap")
                if _model_probe_forward is not None and requests[-1]["target_sha256"] != (
                    hashlib.sha256(("/session/" + child["session_id"] + "/message").encode())
                    .hexdigest()
                ):
                    raise NativePreflightError("observed_model_route_gap")
            if require_fork_observation:
                observation = value["fork_observation"]
                if (
                    not isinstance(observation, dict)
                    or observation.get("formal_admission") is not False
                    or observation.get("claim_eligible") is not False
                    or observation.get("release_boundary") != "owner_control_reply"
                    or not isinstance(observation.get("forks"), list)
                    or len(observation["forks"]) != 1
                ):
                    raise NativePreflightError("fork_observation_invalid")
                fork = observation["forks"][0]
                response = fork.get("response", {})
                event = fork.get("child_event", {})
                for record in (observation, response, event):
                    body = {k: v for k, v in record.items() if k != "record_sha256"}
                    digest = hashlib.sha256(json.dumps(
                        body, sort_keys=True, separators=(",", ":"), allow_nan=False,
                    ).encode()).hexdigest()
                    if record.get("record_sha256") != digest:
                        raise NativePreflightError("fork_observation_digest_invalid")
                result["fork_observation"] = observation
                parent_digest = hashlib.sha256(parent["session_id"].encode()).hexdigest()
                child_digest = hashlib.sha256(child["session_id"].encode()).hexdigest()
                if (
                    response.get("parent_session_sha256") != parent_digest
                    or response.get("child_session_sha256") != child_digest
                    or response.get("request_body_sha256") != hashlib.sha256(b"{}").hexdigest()
                    or event.get("child_plugin_session_sha256") != child_digest
                    or type(event.get("observed_at_ns")) is not int
                    or type(fork.get("control_reply_sent_at_ns")) is not int
                    or not 0 < event["observed_at_ns"] <= fork["control_reply_sent_at_ns"]
                    or type(event.get("elapsed_ms")) is not int
                    or not 0 <= event["elapsed_ms"] <= 30_000
                ):
                    raise NativePreflightError("fork_event_binding_gap")
            if require_boundary_observation:
                from .linux_audit_syscall_metadata import validate_metadata_receipt

                observation = validate_metadata_receipt(value["boundary_observation"])
                result["boundary_observation"] = observation
                if (
                    observation["status"] != "observed" or observation["failure_codes"]
                    or observation["source_bound"] is not True
                    or observation["window_closed"] is not True
                    or observation["audit_pid_registered"] is not True
                    or observation["audit_enabled"] != 1
                    or any(observation[k] != 0 for k in (
                        "lost_before", "lost_after", "backlog_before", "backlog_after",
                    ))
                ):
                    raise NativePreflightError("boundary_observation_gap")
                counts = observation["action_counts"]
                if (
                    len(counts) != 4
                    or {(x["role"], x["syscall"]) for x in counts}
                    != {(role, syscall) for role in ("host", "mcp") for syscall in (56, 203)}
                    or any(
                        x["expected_count"] != 1 or x["observed_count"] != 1
                        or x["denied_count"] != 1 for x in counts
                    )
                ):
                    raise NativePreflightError("boundary_action_gap")
            if require_process_observation:
                observation = value["process_observation"]
                if (
                    not isinstance(observation, dict)
                    or observation.get("formal_admission") is not False
                ):
                    raise NativePreflightError("process_observation_gap")
                body = {key: val for key, val in observation.items() if key != "record_sha256"}
                digest = hashlib.sha256(json.dumps(
                    body, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode()).hexdigest()
                tree = observation.get("tree_receipt")
                if (
                    observation.get("record_sha256") != digest
                    or not isinstance(tree, dict)
                    or tree.get("formal_admission") is not False
                ):
                    raise NativePreflightError("process_observation_invalid")
                tree_body = {key: val for key, val in tree.items() if key != "record_sha256"}
                tree_digest = hashlib.sha256(json.dumps(
                    tree_body, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode()).hexdigest()
                if tree.get("record_sha256") != tree_digest:
                    raise NativePreflightError("process_tree_digest_invalid")
                result["process_observation"] = observation
                if (
                    observation.get("source_bound") is not True
                    or observation.get("cpu_windows_closed") is not True
                ):
                    raise NativePreflightError("process_observation_gap")
                if tree.get("status") != "observed":
                    raise NativePreflightError("process_tree_gap")
                windows = observation.get("native_cpu_windows")
                if not isinstance(windows, list) or len(windows) != 2:
                    raise NativePreflightError("process_cpu_windows_invalid")
                seen_cpus: set[int] = set()
                for window in windows:
                    if (
                        not isinstance(window, dict)
                        or set(window) != {"cpu", "start_sequence", "end_sequence", "event_count"}
                        or any(type(item) is not int for item in window.values())
                        or not 0 <= window["cpu"] <= 1 or window["cpu"] in seen_cpus
                        or not 0 <= window["start_sequence"] < 2**32
                        or not 0 <= window["end_sequence"] < 2**32
                        or not 1 <= window["event_count"] <= 8192
                        or (window["end_sequence"] - window["start_sequence"]) % 2**32 + 1
                        != window["event_count"]
                    ):
                        raise NativePreflightError("process_cpu_windows_invalid")
                    seen_cpus.add(window["cpu"])
            receipt = value["launcher_receipt"]
            if (
                not isinstance(receipt, dict)
                or receipt.get("status") != "observed"
                or receipt.get("formal_admission") is not False
                or receipt.get("native_mutation") is not True
            ):
                raise NativePreflightError("role_lifecycle_gap")
            body = {key: val for key, val in receipt.items() if key != "record_sha256"}
            digest = hashlib.sha256(
                json.dumps(
                    body,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            if receipt.get("record_sha256") != digest or receipt.get("failure_codes") != []:
                raise NativePreflightError("role_receipt_invalid")
            roles = receipt.get("roles")
            if (
                not isinstance(roles, list)
                or len(roles) != 2
                or any(not isinstance(role, dict) for role in roles)
                or {role.get("role") for role in roles} != {"host", "mcp"}
            ):
                raise NativePreflightError("role_set_invalid")
            for role in roles:
                if (
                    type(role.get("exit_code")) is not int
                    or role["exit_code"] != 0
                    or role.get("cgroup_populated_checked") is not True
                    or type(role.get("cgroup_populated")) is not int
                    or role["cgroup_populated"] != 0
                    or type(role.get("uid")) is not int
                    or role["uid"] != {"host": 1000, "mcp": 1001}[role["role"]]
                ):
                    raise NativePreflightError("role_cleanup_incomplete")
            if require_execution_observation:
                result["execution_observation"] = _validate_execution_observation(
                    value["execution_observation"], roles, expected_host_sha256,
                    binding_sha256,
                )
            if require_route_observation:
                host_role = next(role for role in roles if role["role"] == "host")
                if result["route_observation"]["namespace_sha256"] != host_role.get(
                    "observation", {}
                ).get("net_namespace_sha256"):
                    raise NativePreflightError("route_namespace_binding_gap")
            result.update(
                healthy=True,
                mcp_connected=None if fork_routes_only else True,
                # /mcp proves initialize/list connectivity, not a tools/call.
                mcp_exercised=False,
                native_child_created=True,
                launcher_receipt=receipt,
            )
            connection.close()
            connection = None
            result["failure_stage"] = "vm_cleanup"
            result["vm_exit_code"] = process.wait(timeout=10)
            if result["vm_exit_code"] != 0:
                raise NativePreflightError("vm_exit_nonzero")
            if _model_probe_forward is not None and not model_observed:
                result["failure_stage"] = "model_probe_observation"
                raise NativePreflightError("model_probe_not_observed")
            result["failure_stage"] = None
    except Exception as error:
        result["failure"] = (
            str(error) if isinstance(error, NativePreflightError) else type(error).__name__
        )
    finally:
        if connection is not None:
            connection.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                result["forced_vm_kill"] = True
        if process is not None:
            result["vm_exit_code"] = process.returncode
        (destination / "observation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("native", "kernel", "initrd", "destination"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("native", "kernel", "initrd"):
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--require-process-observation", action="store_true")
    parser.add_argument("--require-boundary-observation", action="store_true")
    parser.add_argument("--require-fork-observation", action="store_true")
    parser.add_argument("--require-route-observation", action="store_true")
    parser.add_argument("--require-execution-observation", action="store_true", default=True)
    parser.add_argument("--expected-host-sha256", required=True)
    parser.add_argument("--execution-context", type=Path, required=True)
    parser.add_argument("--execution-context-sha256", required=True)
    parser.add_argument("--fork-routes-only", action="store_true")
    arguments = vars(parser.parse_args())
    context_path = arguments["execution_context"]
    expected_context_sha = arguments.pop("execution_context_sha256")
    if context_path is not None:
        if (
            not isinstance(expected_context_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_context_sha) is None
        ):
            parser.error("--execution-context requires --execution-context-sha256")
        descriptor = os.open(context_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as incoming:
            info = os.fstat(incoming.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 65536:
                parser.error("execution context must be a bounded regular file")
            raw = incoming.read(65537)
        if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != expected_context_sha:
            parser.error("execution context digest differs")
        arguments["execution_context"] = _validate_execution_context(json.loads(
            raw, object_pairs_hook=_execution_context_pairs,
        ))
    elif expected_context_sha is not None:
        parser.error("--execution-context-sha256 requires --execution-context")
    result = run_preflight(**arguments)
    print(
        json.dumps(
            {
                key: result.get(key)
                for key in (
                    "formal_admission",
                    "failure",
                    "failure_stage",
                    "healthy",
                    "mcp_connected",
                    "native_child_created",
                    "vm_exit_code",
                )
            }
        )
    )
    return 0 if result["failure"] is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
