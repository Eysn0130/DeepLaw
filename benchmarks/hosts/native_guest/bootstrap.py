"""Install the frozen guest runtime and execute one zero-model preflight."""

import hashlib
import json
import os
import pathlib
import re
import select
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time


def approved_wheels(directory: pathlib.Path, inventory_path: pathlib.Path) -> list[pathlib.Path]:
    with inventory_path.open("rb") as stream:
        raw_inventory = stream.read(65537)
    if len(raw_inventory) > 65536:
        raise RuntimeError("wheel_inventory_bound")
    inventory = json.loads(raw_inventory)
    if not isinstance(inventory, list) or not 1 <= len(inventory) <= 64:
        raise RuntimeError("wheel_inventory_invalid")
    wheels = []
    seen = set()
    for item in inventory:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "sha256"}
            or not isinstance(item["name"], str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,254}\.whl", item["name"]) is None
            or item["name"] in seen
        ):
            raise RuntimeError("wheel_inventory_invalid")
        seen.add(item["name"])
        path = directory / item["name"]
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 512 * 1024 * 1024:
            raise RuntimeError("wheel_file_invalid")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise RuntimeError("wheel_digest_mismatch")
        wheels.append(path)
    return wheels


def finish_route_observer(observers, read_observer):
    if len(observers) != 1:
        return {"status": "gap", "failure": "route_observer_missing", "formal_admission": False}
    observer = observers[0]
    try:
        observer.stdin.write(b'{"op":"finish"}\n')
        observer.stdin.flush()
        result = read_observer(observer.stdout)
        if observer.wait(timeout=2) != 0 and result.get("status") != "gap":
            raise RuntimeError("route_observer_exit_failed")
        return result
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return {"status": "gap", "failure": "route_observer_failed", "formal_admission": False}
    finally:
        if observer.poll() is None:
            observer.terminate()
            try:
                observer.wait(timeout=2)
            except subprocess.TimeoutExpired:
                observer.kill()
                observer.wait(timeout=2)


def close_unused_mcp(workdir):
    """Supply owner EOF to an unused stdio server, without any MCP request."""
    deadline = time.monotonic() + 10
    endpoint = workdir / "mcp.sock"
    while not endpoint.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("mcp_endpoint_timeout")
        time.sleep(0.01)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        connection.connect(str(endpoint))
        peer = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer[1] != 1001:
            raise RuntimeError("mcp_peer_uid_invalid")
        connection.shutdown(socket.SHUT_WR)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("mcp_endpoint_timeout")
        connection.settimeout(remaining)
        if connection.recv(1) != b"":
            raise RuntimeError("unused_mcp_output_invalid")


def observe_boundary_probes(handles):
    from benchmarks.hosts import linux_audit_syscall_metadata as metadata
    from benchmarks.hosts import linux_guest_observer as audit

    deadline = time.monotonic() + 10
    while not all((h.workdir / "probe-ready").is_file() for h in handles):
        if time.monotonic() >= deadline:
            raise RuntimeError("probe_ready_timeout")
        time.sleep(0.01)
    records = []

    def release(h, name):
        temporary = h.workdir / f".{name}.tmp"
        temporary.write_bytes(b"G")
        temporary.replace(h.workdir / name)

    def consume(kind, payload):
        if len(records) >= metadata.MAX_RECORDS:
            raise RuntimeError("probe_event_bound")
        records.append((kind, payload))

    rules = tuple(
        audit.AuditRuleSpec(syscall_numbers=(56, 203), uid=uid, success=False)
        for uid in (1000, 1001)
    )
    config = audit.AuditCollectorConfig(rules=rules, required_event_types=("SYSCALL",))
    with audit.AuditCollector(config, event_consumer=consume) as collector:
        collector.start()
        for h in handles:
            release(h, "probe-go")
        while not all((h.workdir / "probe-result.json").is_file() for h in handles):
            if time.monotonic() >= deadline:
                raise RuntimeError("probe_result_timeout")
            collector.collect_once()
        base = collector.finish(role_observation=None)
    unexpected = set(base["failure_codes"]) - {"role_metadata_missing"}
    if unexpected:
        raise RuntimeError("probe_audit_gap:" + ":".join(sorted(unexpected)))
    expected = []
    for h in handles:
        with (h.workdir / "probe-result.json").open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise RuntimeError("probe_result_bound")
        report = json.loads(raw)
        if report.get("uid") != h.uid or report.get("formal_admission") is not False:
            raise RuntimeError("probe_identity_invalid")
        checks = report.get("checks")
        if not isinstance(checks, list) or len(checks) != 2:
            raise RuntimeError("probe_checks_invalid")
        for check, action, syscall, errors in zip(
            checks, ("readonly_runtime_write", "nonloopback_connect"),
            (56, 203), ({1, 13, 30}, {101}), strict=True,
        ):
            if (
                check.get("action_id") != action or check.get("syscall") != syscall
                or check.get("success") is not False
                or type(check.get("errno")) is not int or check["errno"] not in errors
            ):
                raise RuntimeError("probe_outcome_invalid")
            expected.append(metadata.ExpectedCanary(
                role=h.role, action_id=f"syscall_{syscall}", syscall=syscall,
                count=1, expected_success=False, expected_errno=check["errno"],
            ))
    state = base["audit"]
    result = metadata.aggregate_audit_metadata(
        records, role_bindings={h.role: {"uid": h.uid, "pid": h.pid} for h in handles},
        expected_actions=expected, configured_syscalls=(56, 203),
        rule_binding_sha256=state["rule_binding_sha256"],
        audit_pid_registered=state["pid_registered"], audit_enabled=state["enabled"],
        lost_before=state["lost_before"], lost_after=state["lost_after"],
        backlog_before=state["backlog_before"], backlog_after=state["backlog_after"],
        source_bound=True, window_closed=True,
    )
    records.clear()
    if result["status"] != "observed":
        raise RuntimeError("probe_metadata_gap")
    for h in handles:
        release(h, "probe-resume")
    return result


def main() -> int:
    sys.path.insert(0, "/opt")
    from benchmarks.hosts import linux_guest_observer as audit
    from benchmarks.hosts import linux_role_launcher as l
    owner = json.loads(pathlib.Path("/opt/owner-input.json").read_bytes())
    if owner.get("purpose") not in {"zero_model_preflight", "zero_model_fork_preflight"}:
        raise RuntimeError("owner_purpose_invalid")
    fork_only = owner["purpose"] == "zero_model_fork_preflight"
    from benchmarks.hosts.linux_guest_slot_control import GuestSlotControl

    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("native_guest_required")
    # audit_alloc() runs at fork and skips tasks born before audit_ever_enabled.
    # Enable it before creating either role; install the bounded rules only
    # after both isolated entry processes reach their probe gates.
    audit_transport = audit.NetlinkAuditTransport()
    try:
        audit_transport.set_status(mask=audit.AUDIT_STATUS_ENABLED, enabled=1)
        if audit_transport.get_status().enabled != 1:
            raise RuntimeError("audit_early_enable_failed")
    finally:
        audit_transport.close()
    pathlib.Path("/sys/fs/cgroup/cgroup.subtree_control").write_text("+cpu +memory +pids")
    site = pathlib.Path("/opt/mcp-site")
    wheels = approved_wheels(
        pathlib.Path("/opt/python-artifacts"), pathlib.Path("/opt/wheel-inputs.json")
    )
    result = subprocess.run(
        [
            "/usr/bin/python3",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--no-compile",
            "--target",
            str(site),
            *[str(p) for p in wheels],
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )
    if result.returncode:
        raise RuntimeError("offline_install_failed")
    sys.path.insert(0, str(site))
    stage = pathlib.Path("/opt/staged-role-runtime")
    stage.mkdir()
    libs = stage / "lib"
    libs.mkdir()
    for directory in ("/lib", "/usr/lib"):
        for source in pathlib.Path(directory).glob("*.so*"):
            if source.is_file():
                shutil.copy2(source, libs / source.name)
    shutil.copytree("/usr/lib/python3.12", stage / "python3.12", symlinks=False)
    shutil.copy2(pathlib.Path("/usr/bin/python3").resolve(), stage / "python")

    def binding(source, target):
        sha, _ = l._file_or_tree_sha256(source)
        return {"source": str(source), "target": target, "sha256": sha}

    common = [
        binding(stage / "lib", "/lib"),
        binding(stage / "python3.12", "/usr/lib/python3.12"),
        binding(stage / "python", "/usr/bin/python3.12"),
        binding(pathlib.Path("/opt/linux_mcp_socket_transport.py"), "/runtime/transport.py"),
        binding(pathlib.Path("/opt/linux_role_boundary_probe.py"), "/runtime/boundary_probe.py"),
        binding(pathlib.Path("/opt/boundary_gate.py"), "/runtime/boundary_gate.py"),
        binding(pathlib.Path("/opt/owner-input.json"), "/runtime/owner-input.json"),
    ]
    host = [
        *common,
        binding(pathlib.Path("/opt/opencode"), "/runtime/opencode"),
        binding(pathlib.Path("/opt/opencode_entry.py"), "/runtime/entry.py"),
        binding(pathlib.Path("/opt/mcp_client.py"), "/runtime/mcp_client.py"),
        binding(pathlib.Path("/opt/plugin-source"), "/runtime/plugins"),
    ]
    mcp = [
        *common,
        binding(site, "/runtime/site-packages"),
        binding(pathlib.Path("/opt/mcp-modules"), "/runtime/modules"),
        binding(pathlib.Path("/opt/mcp_entry.py"), "/runtime/entry.py"),
    ]
    fresh = pathlib.Path("/opt/roles")
    fresh.mkdir(mode=0o700)
    value = {
        "schema": l.CONFIG_SCHEMA,
        "freshroot": str(fresh),
        "cgroup_root": "/sys/fs/cgroup",
        "timeout_seconds": 50,
        "roles": [
            {
                "role": role,
                "command": ["/usr/bin/python3.12", "/runtime/entry.py"],
                "runtime_bindings": host if role == "host" else mcp,
                "budgets": {
                    "pids_max": 128,
                    "memory_max_bytes": 1073741824,
                    "cpu_max_us": 100000,
                    "cpu_period_us": 100000,
                },
            }
            for role in ("host", "mcp")
        ],
    }
    controllers = []
    seeds = {}
    boundary_observations = []
    route_observers = []

    def read_observer(stream, timeout=5):
        result = bytearray()
        deadline = time.monotonic() + timeout
        while b"\n" not in result:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
                raise RuntimeError("observer_timeout")
            part = os.read(stream.fileno(), min(4096, 262145 - len(result)))
            if not part:
                raise RuntimeError("observer_eof")
            result.extend(part)
            if len(result) > 262144:
                raise RuntimeError("observer_output_bound")
        if result.count(b"\n") != 1 or not result.endswith(b"\n"):
            raise RuntimeError("observer_output_shape")
        return json.loads(result)

    observer = subprocess.Popen(
        ["/usr/bin/python3", "-m", "benchmarks.hosts.linux_process_observer"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd="/opt",
    )
    if read_observer(observer.stdout) != {"ready": True, "formal_admission": False}:
        raise RuntimeError("observer_not_ready")
    control = GuestSlotControl(
        port=4050, timeout_seconds=40, observe_fork=True, require_host_ready=True,
    )

    def on_started(handles):
        for handle in handles:
            seeds[handle.role] = {
                "pid": handle.pid,
                "uid": handle.uid,
                "captured_at_ns": time.monotonic_ns(),
            }
        h = next(x for x in handles if x.role == "host")
        route_observer = subprocess.Popen(
            ["/usr/bin/python3", "-m", "benchmarks.hosts.linux_http_route_observer",
             "--host-pid", str(h.pid)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd="/opt",
        )
        route_observers.append(route_observer)
        if read_observer(route_observer.stdout) != {"ready": True, "formal_admission": False}:
            raise RuntimeError("route_observer_not_ready")
        try:
            boundary_observations.append(observe_boundary_probes(handles))
        except Exception as error:
            code = str(error)
            if re.fullmatch(r"[A-Za-z0-9 _:-]{1,100}", code) is None:
                code = "boundary_probe_failed"
            print(json.dumps({
                "phase": "boundary_probe", "error_type": type(error).__name__,
                "error_code": code, "formal_admission": False,
            }), flush=True)
            raise
        h = next(x for x in handles if x.role == "host")
        m = next(x for x in handles if x.role == "mcp")
        if fork_only:
            close_unused_mcp(m.workdir)
        else:
            controllers.append(
                subprocess.Popen(
                    ["/usr/bin/python3", "/opt/mcp_relay.py", str(h.workdir), str(m.workdir)]
                )
            )
        control(handles)

    config = l.parse_config(value)
    receipt, code = l.run_native(
        config, config_sha256=l.sha256_bytes(l.canonical_json(value)), on_roles_started=on_started
    )
    if code != 0:
        print(json.dumps({
            "phase": "role_launch", "failure_codes": receipt["failure_codes"],
            "role_exit_codes": {row["role"]: row.get("exit_code") for row in receipt["roles"]},
            "formal_admission": False,
        }), flush=True)
    for controller in controllers:
        try:
            controller.wait(timeout=2)
        except subprocess.TimeoutExpired:
            controller.kill()
            controller.wait(timeout=2)
    if any(controller.returncode != 0 for controller in controllers):
        receipt = l._receipt(
            status="gap",
            config_sha256=receipt["config_sha256"],
            roles=receipt["roles"],
            failure_codes=[*receipt["failure_codes"], "mcp_relay_exit_nonzero"],
            events=receipt["events"],
            native_mutation=True,
        )
    observer.stdin.write(
        (
            json.dumps(
                {
                    "op": "finish",
                    "roots": seeds,
                    "cgroups_empty": all(
                        row.get("cgroup_populated") == 0
                        and row.get("cgroup_populated_checked") is True
                        for row in receipt["roles"]
                    ),
                }
            )
            + "\n"
        ).encode()
    )
    observer.stdin.flush()
    process_observation = read_observer(observer.stdout)
    if (
        observer.wait(timeout=2) != 0
        and process_observation.get("tree_receipt", {}).get("status") != "gap"
    ):
        raise RuntimeError("observer_exit_nonzero")
    control.finish(
        receipt, process_observation=process_observation,
        boundary_observation=boundary_observations[0] if boundary_observations else None,
        route_observation=finish_route_observer(route_observers, read_observer),
    )
    control.close()
    return (
        0
        if (
            code == 0
            and receipt["status"] == "observed"
            and process_observation.get("tree_receipt", {}).get("status") == "observed"
        )
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
