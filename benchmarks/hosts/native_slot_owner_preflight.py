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
import socket
import stat
import subprocess
import sys
import time
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
    reply_timeout: float | None = None, **fields: str
) -> dict[str, Any]:
    payload = json.dumps(
        {"op": operation, **fields}, sort_keys=True, separators=(",", ":")
    ).encode()
    write_frame(connection, FrameKind.CONTROL_REQUEST, sequence, payload, timeout=timeout)
    reply = read_frame(connection, timeout=timeout if reply_timeout is None else reply_timeout)
    if reply.kind != FrameKind.CONTROL_REPLY or reply.sequence != sequence:
        raise NativePreflightError("control_reply_identity_invalid")
    value = json.loads(reply.payload)
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


def run_preflight(
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
    fork_routes_only: bool = False,
) -> dict[str, Any]:
    if sys.platform != "darwin":
        raise NativePreflightError("macos_required")
    destination = destination.absolute()
    destination.mkdir(mode=0o700)
    result: dict[str, Any] = {
        "formal_admission": False,
        "model_invocations": 0,
        "credentials_supplied": False,
        "failure": None,
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
                    "90",
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
            connection = _guest_fd(handoff)
            health = _exchange(connection, 1, "health", timeout=16)
            if set(health) != {"healthy"} or health["healthy"] is not True:
                raise NativePreflightError("host_not_healthy")
            parent = _exchange(connection, 2, "new_session")
            if set(parent) != {"session_id"} or not isinstance(parent["session_id"], str):
                raise NativePreflightError("session_reply_invalid")
            if not fork_routes_only:
                mcp = _exchange(connection, 3, "mcp_status")
                if set(mcp) != {"connected"} or mcp["connected"] is not True:
                    raise NativePreflightError("mcp_not_connected")
            fork_sequence = 3 if fork_routes_only else 4
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
            stop = _exchange(connection, fork_sequence + 1, "stop")
            if set(stop) != {"stopping"} or stop["stopping"] is not True:
                raise NativePreflightError("stop_reply_invalid")
            final = read_frame(connection)
            if final.kind != FrameKind.FINAL or final.sequence != fork_sequence + 2:
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
            if (
                not isinstance(value, dict) or set(value) != expected_keys
                or value["formal_admission"] is not False
            ):
                raise NativePreflightError("final_reply_invalid")
            if require_route_observation:
                from .linux_http_route_observer import validate_observation

                observation = validate_observation(value["route_observation"])
                result["route_observation"] = observation
                requests = observation["requests"]
                expected_routes = ["health", "new_session"]
                if not fork_routes_only:
                    expected_routes.append("mcp_status")
                expected_routes.append("fork")
                if [row["route"] for row in requests] != expected_routes:
                    raise NativePreflightError("observed_route_sequence_gap")
                expected_target = "/session/" + parent["session_id"] + "/fork"
                if requests[-1]["target_sha256"] != hashlib.sha256(
                    expected_target.encode()
                ).hexdigest():
                    raise NativePreflightError("observed_fork_route_gap")
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
            if require_route_observation:
                host_role = next(role for role in roles if role["role"] == "host")
                if result["route_observation"]["namespace_sha256"] != host_role.get(
                    "observation", {}
                ).get("net_namespace_sha256"):
                    raise NativePreflightError("route_namespace_binding_gap")
            result.update(
                healthy=True,
                mcp_connected=None if fork_routes_only else True,
                mcp_exercised=not fork_routes_only,
                native_child_created=True,
                launcher_receipt=receipt,
            )
            connection.close()
            connection = None
            result["vm_exit_code"] = process.wait(timeout=10)
            if result["vm_exit_code"] != 0:
                raise NativePreflightError("vm_exit_nonzero")
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
    parser.add_argument("--fork-routes-only", action="store_true")
    result = run_preflight(**vars(parser.parse_args()))
    print(
        json.dumps(
            {
                key: result.get(key)
                for key in (
                    "formal_admission",
                    "failure",
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
