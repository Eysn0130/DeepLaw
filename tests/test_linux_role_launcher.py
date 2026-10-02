from __future__ import annotations

import errno
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.hosts import linux_role_launcher as launcher


@pytest.mark.parametrize("result", [0, -1])
def test_seccomp_requires_kernel_logging_installation(result: int) -> None:
    calls = []

    def syscall(number, operation, flags, pointer):
        calls.append((number, operation, flags))
        assert pointer is not None
        return result

    native = launcher._LibC.__new__(launcher._LibC)
    native.libc = SimpleNamespace(syscall=syscall, prctl=lambda *args: 0)
    if result == 0:
        launcher._install_seccomp(native)
    else:
        with pytest.raises(launcher.LauncherError, match="seccomp_install_failed"):
            launcher._install_seccomp(native)
    assert calls == [(277, 1, 2)]


def _interpret_seccomp(*, syscall: int, arch: int, arg0: int = 0) -> int:
    """Execute the launcher's finite classic-BPF subset on seccomp_data."""

    program = launcher._seccomp_program()
    seccomp_data = bytearray(24)
    struct.pack_into("<I", seccomp_data, 0, syscall)
    struct.pack_into("<I", seccomp_data, 4, arch)
    struct.pack_into("<Q", seccomp_data, 16, arg0)
    accumulator = 0
    pc = 0
    for _step in range(128):
        assert 0 <= pc < len(program)
        instruction = program[pc]
        opcode = int(instruction.code)
        if opcode == 0x20:  # BPF_LD | BPF_W | BPF_ABS
            assert int(instruction.k) + 4 <= len(seccomp_data)
            accumulator = struct.unpack_from("<I", seccomp_data, int(instruction.k))[0]
            pc += 1
            continue
        if opcode == 0x54:  # BPF_ALU | BPF_AND | BPF_K
            accumulator &= int(instruction.k)
            pc += 1
            continue
        if opcode == 0x15:  # BPF_JMP | BPF_JEQ | BPF_K
            offset = (
                int(instruction.jt)
                if accumulator == int(instruction.k)
                else int(instruction.jf)
            )
            pc += 1 + offset
            continue
        if opcode == 0x06:  # BPF_RET | BPF_K
            return int(instruction.k)
        raise AssertionError(f"unsupported classic-BPF opcode: {opcode:#x}")
    raise AssertionError("classic-BPF program did not terminate")


def test_seccomp_filter_allows_aarch64_read_write_exec_and_kills_other_arch() -> None:
    allow = launcher.SECCOMP_RET_ALLOW
    kill = launcher.SECCOMP_RET_KILL_PROCESS
    # ARM64 uses asm-generic numbering: read=63, write=64, execve=221.
    for syscall in (63, 64, 221):
        assert (
            _interpret_seccomp(syscall=syscall, arch=launcher.AUDIT_ARCH_AARCH64)
            == allow
        )
    assert _interpret_seccomp(syscall=64, arch=0) == kill


def test_seccomp_filter_returns_eperm_for_each_blocked_syscall() -> None:
    deny = launcher.SECCOMP_RET_ERRNO | errno.EPERM
    blocked = (
        launcher.SYS_UNSHARE,
        launcher.SYS_SETNS,
        launcher.SYS_CLONE3,
        launcher.SYS_MOUNT,
        launcher.SYS_UMOUNT2,
        launcher.SYS_PIVOT_ROOT,
        launcher.SYS_CHROOT,
        launcher.SYS_OPEN_TREE,
        launcher.SYS_MOVE_MOUNT,
        launcher.SYS_FSOPEN,
        launcher.SYS_FSCONFIG,
        launcher.SYS_FSMOUNT,
        launcher.SYS_FSPICK,
        launcher.SYS_MOUNT_SETATTR,
        launcher.SYS_PTRACE,
        launcher.SYS_BPF,
        launcher.SYS_IO_URING_SETUP,
        launcher.SYS_IO_URING_ENTER,
        launcher.SYS_IO_URING_REGISTER,
    )
    assert len(blocked) == len(set(blocked))
    for syscall in blocked:
        assert (
            _interpret_seccomp(syscall=syscall, arch=launcher.AUDIT_ARCH_AARCH64)
            == deny
        )


def test_seccomp_filter_checks_clone_flags_and_socket_families() -> None:
    allow = launcher.SECCOMP_RET_ALLOW
    deny = launcher.SECCOMP_RET_ERRNO | errno.EPERM
    for flags in (0, 17):  # fork-like clone and SIGCHLD are ordinary cases.
        assert (
            _interpret_seccomp(
                syscall=launcher.SYS_CLONE,
                arch=launcher.AUDIT_ARCH_AARCH64,
                arg0=flags,
            )
            == allow
        )
    namespace_flags = (
        launcher.CLONE_NEWNS,
        launcher.CLONE_NEWCGROUP,
        launcher.CLONE_NEWUTS,
        launcher.CLONE_NEWIPC,
        launcher.CLONE_NEWUSER,
        launcher.CLONE_NEWPID,
        launcher.CLONE_NEWNET,
        launcher.CLONE_NEWTIME,
    )
    for flags in (*namespace_flags, launcher.CLONE_NAMESPACE_FLAGS | 17):
        assert (
            _interpret_seccomp(
                syscall=launcher.SYS_CLONE,
                arch=launcher.AUDIT_ARCH_AARCH64,
                arg0=flags,
            )
            == deny
        )
    for family in (socket.AF_INET, socket.AF_UNIX):
        assert (
            _interpret_seccomp(
                syscall=launcher.SYS_SOCKET,
                arch=launcher.AUDIT_ARCH_AARCH64,
                arg0=family,
            )
            == allow
        )
    for family in (launcher.AF_VSOCK, launcher.AF_NETLINK, launcher.AF_PACKET):
        assert (
            _interpret_seccomp(
                syscall=launcher.SYS_SOCKET,
                arch=launcher.AUDIT_ARCH_AARCH64,
                arg0=family,
            )
            == deny
        )


def _write_config(tmp_path: Path, *, mutate: dict | None = None) -> tuple[Path, dict, str]:
    runtime = tmp_path / "staged-runtime"
    runtime.mkdir()
    executable = runtime / "entry"
    executable.write_bytes(b"staged executable\n")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    roles = []
    for role in ("host", "mcp"):
        roles.append(
            {
                "role": role,
                "command": [f"/runtime/{role}/entry", "--bounded"],
                "runtime_bindings": [
                    {
                        "source": str(executable),
                        "target": f"/runtime/{role}/entry",
                        "sha256": digest,
                    }
                ],
                "budgets": {
                    "pids_max": 16,
                    "memory_max_bytes": 64 * 1024 * 1024,
                    "cpu_max_us": 100_000,
                    "cpu_period_us": 100_000,
                },
            }
        )
    value = {
        "schema": launcher.CONFIG_SCHEMA,
        "freshroot": str(tmp_path / "freshroot"),
        "cgroup_root": str(tmp_path / "cgroup"),
        "timeout_seconds": 30,
        "roles": roles,
    }
    (tmp_path / "freshroot").mkdir(mode=0o700)
    (tmp_path / "cgroup").mkdir(mode=0o700)
    if mutate:
        value.update(mutate)
    path = tmp_path / "launcher.json"
    raw = launcher.canonical_json(value)
    path.write_bytes(raw)
    return path, value, hashlib.sha256(raw).hexdigest()


def _run_cli(config_path: Path, digest: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "benchmarks/hosts/linux_role_launcher.py",
            "--config",
            str(config_path),
            "--config-sha256",
            digest,
            *extra,
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )


def _receipt(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.stdout
    return json.loads(result.stdout)


def test_validation_only_is_closed_and_does_not_mutate_guest_roots(tmp_path: Path) -> None:
    path, _value, digest = _write_config(tmp_path)
    before = sorted(item.name for item in (tmp_path / "freshroot").iterdir())
    result = _run_cli(path, digest, "--validation-only")
    assert result.returncode == 0
    receipt = _receipt(result)
    assert receipt["status"] == "validated"
    assert receipt["formal_admission"] is False
    assert receipt["claim_eligible"] is False
    assert receipt["native_mutation"] is False
    assert {row["role"] for row in receipt["roles"]} == {"host", "mcp"}
    assert {row["role"]: (row["uid"], row["gid"]) for row in receipt["roles"]} == {
        "host": (1000, 1000),
        "mcp": (1001, 1001),
    }
    assert sorted(item.name for item in (tmp_path / "freshroot").iterdir()) == before
    serialized = result.stdout
    assert "runtime/" not in serialized
    assert "staged-runtime" not in serialized
    assert "stdout" not in serialized and "stderr" not in serialized


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        (("--validation-only", "--run"), "cli_mode_required"),
        (("--validation-only", "--unknown"), "cli_unknown_option"),
    ],
)
def test_cli_rejects_invalid_mode_or_option_before_native_start(
    tmp_path: Path, extra: tuple[str, ...], code: str
) -> None:
    path, _value, digest = _write_config(tmp_path)
    result = _run_cli(path, digest, *extra)
    assert result.returncode == 64
    assert code in _receipt(result)["failure_codes"]
    assert not (tmp_path / "freshroot" / "host").exists()


def test_cli_rejects_missing_config_and_digest_before_native_start(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    digest = "0" * 64
    result = subprocess.run(
        [
            sys.executable,
            "benchmarks/hosts/linux_role_launcher.py",
            "--config",
            str(missing),
            "--config-sha256",
            digest,
            "--validation-only",
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "config_missing" in _receipt(result)["failure_codes"]


def test_cli_rejects_config_digest_mismatch(tmp_path: Path) -> None:
    path, _value, digest = _write_config(tmp_path)
    result = _run_cli(path, "f" * 64, "--validation-only")
    assert result.returncode != 0
    assert "config_digest_mismatch" in _receipt(result)["failure_codes"]
    assert digest != "f" * 64


def test_duplicate_role_is_rejected_without_creating_role_directories(tmp_path: Path) -> None:
    path, value, _digest = _write_config(tmp_path)
    value["roles"][1]["role"] = "host"
    path.write_bytes(launcher.canonical_json(value))
    result = _run_cli(path, hashlib.sha256(path.read_bytes()).hexdigest(), "--validation-only")
    assert result.returncode != 0
    assert "role_set_invalid" in _receipt(result)["failure_codes"]
    assert list((tmp_path / "freshroot").iterdir()) == []


def test_budget_out_of_range_is_rejected_before_native_start(tmp_path: Path) -> None:
    path, value, _digest = _write_config(tmp_path)
    value["roles"][0]["budgets"]["memory_max_bytes"] = 1
    path.write_bytes(launcher.canonical_json(value))
    result = _run_cli(path, hashlib.sha256(path.read_bytes()).hexdigest(), "--validation-only")
    assert result.returncode != 0
    assert "memory_budget_out_of_range" in _receipt(result)["failure_codes"]
    assert list((tmp_path / "freshroot").iterdir()) == []


def test_missing_binding_source_is_rejected_before_native_start(tmp_path: Path) -> None:
    path, value, _digest = _write_config(tmp_path)
    value["roles"][1]["runtime_bindings"][0]["source"] = str(tmp_path / "gone")
    path.write_bytes(launcher.canonical_json(value))
    result = _run_cli(path, hashlib.sha256(path.read_bytes()).hexdigest(), "--validation-only")
    assert result.returncode != 0
    assert "runtime_binding_missing" in _receipt(result)["failure_codes"]
    assert list((tmp_path / "freshroot").iterdir()) == []


def test_path_traversal_is_rejected_before_native_start(tmp_path: Path) -> None:
    path, value, _digest = _write_config(tmp_path)
    value["roles"][0]["runtime_bindings"][0]["target"] = "/runtime/../escape"
    path.write_bytes(launcher.canonical_json(value))
    result = _run_cli(path, hashlib.sha256(path.read_bytes()).hexdigest(), "--validation-only")
    assert result.returncode != 0
    assert "runtime_target_invalid" in _receipt(result)["failure_codes"]
    assert list((tmp_path / "freshroot").iterdir()) == []


def test_extra_config_fields_are_rejected_before_native_start(tmp_path: Path) -> None:
    path, value, _digest = _write_config(tmp_path)
    value["unexpected"] = True
    path.write_bytes(launcher.canonical_json(value))
    result = _run_cli(path, hashlib.sha256(path.read_bytes()).hexdigest(), "--validation-only")
    assert result.returncode != 0
    assert "config_shape_invalid" in _receipt(result)["failure_codes"]
    assert list((tmp_path / "freshroot").iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="native symlink boundary")
def test_symlinked_runtime_source_is_rejected(tmp_path: Path) -> None:
    path, value, _digest = _write_config(tmp_path)
    target = tmp_path / "link"
    target.symlink_to(tmp_path / "staged-runtime" / "entry")
    value["roles"][0]["runtime_bindings"][0]["source"] = str(target)
    value["roles"][0]["runtime_bindings"][0]["sha256"] = hashlib.sha256(
        b"staged executable\n"
    ).hexdigest()
    path.write_bytes(launcher.canonical_json(value))
    result = _run_cli(path, hashlib.sha256(path.read_bytes()).hexdigest(), "--validation-only")
    assert result.returncode != 0
    codes = _receipt(result)["failure_codes"]
    assert codes == ["runtime_binding_missing"]


def test_import_and_validation_do_not_call_native_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = False

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True
        raise AssertionError("native mutation was called")

    monkeypatch.setattr(launcher, "run_native", fail_if_called)
    path, _value, digest = _write_config(tmp_path)
    assert launcher.main(
        ["--config", str(path), "--config-sha256", digest, "--validation-only"]
    ) == 0
    assert called is False


def test_native_mode_is_explicit_without_executing_native_mutation() -> None:
    values, error = launcher._parse_cli(
        ["--config", "/owner/staged.json", "--config-sha256", "0" * 64, "--run"]
    )
    assert error is None
    assert values["run"] is True
    assert values["validation_only"] is False


class _PrivilegeProbe:
    def __init__(self, *, fail_capset: bool = False) -> None:
        self.events: list[str] = []
        self.caps_cleared = False
        self.fail_capset = fail_capset
        self.uid = 0
        self.gid = 0

    def setgroups(self, groups: list[int]) -> None:
        assert groups == []
        self.events.append("setgroups")

    def getgroups(self) -> list[int]:
        return []

    def setresgid(self, _real: int, effective: int, _saved: int) -> None:
        if self.caps_cleared:
            raise OSError(errno.EPERM, "CAP_SETGID was already cleared")
        self.events.append("setresgid")
        self.gid = effective

    def setresuid(self, _real: int, effective: int, _saved: int) -> None:
        if self.caps_cleared:
            raise OSError(errno.EPERM, "CAP_SETUID was already cleared")
        self.events.append("setresuid")
        self.uid = effective

    def geteuid(self) -> int:
        return self.uid

    def getegid(self) -> int:
        return self.gid


class _PrivilegeLibc:
    def __init__(self, probe: _PrivilegeProbe) -> None:
        self.libc = self
        self.probe = probe

    def prctl(self, option: int, *_args: int, code: str | None = None) -> int:
        del code
        if option == launcher.PR_CAPBSET_DROP:
            self.probe.events.append("drop-bound")
        elif option == launcher.PR_SET_NO_NEW_PRIVS:
            self.probe.events.append("no-new-privs")
        return 0

    def capset(self, *_args: object) -> int:
        self.probe.events.append("capset")
        if self.probe.fail_capset:
            return -1
        self.probe.caps_cleared = True
        return 0


def test_privilege_drop_keeps_setuid_setgid_until_identity_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _PrivilegeProbe()
    monkeypatch.setattr(launcher.os, "setgroups", probe.setgroups, raising=False)
    monkeypatch.setattr(launcher.os, "getgroups", probe.getgroups, raising=False)
    monkeypatch.setattr(launcher.os, "setresgid", probe.setresgid, raising=False)
    monkeypatch.setattr(launcher.os, "setresuid", probe.setresuid, raising=False)
    monkeypatch.setattr(launcher.os, "geteuid", probe.geteuid, raising=False)
    monkeypatch.setattr(launcher.os, "getegid", probe.getegid, raising=False)

    launcher._drop_privileges(_PrivilegeLibc(probe), 1000, 1000)

    assert probe.events.index("setresgid") < probe.events.index("capset")
    assert probe.events.index("setresuid") < probe.events.index("capset")
    assert probe.events[-1] == "no-new-privs"


def test_capability_clear_failure_stops_privilege_setup_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = _PrivilegeProbe(fail_capset=True)
    monkeypatch.setattr(launcher.os, "setgroups", probe.setgroups, raising=False)
    monkeypatch.setattr(launcher.os, "getgroups", probe.getgroups, raising=False)
    monkeypatch.setattr(launcher.os, "setresgid", probe.setresgid, raising=False)
    monkeypatch.setattr(launcher.os, "setresuid", probe.setresuid, raising=False)
    monkeypatch.setattr(launcher.os, "geteuid", probe.geteuid, raising=False)
    monkeypatch.setattr(launcher.os, "getegid", probe.getegid, raising=False)

    with pytest.raises(launcher.LauncherError, match="capabilities_clear_failed"):
        launcher._drop_privileges(_PrivilegeLibc(probe), 1000, 1000)
    assert "capset" in probe.events
    assert "no-new-privs" not in probe.events


def _native_test_config(tmp_path: Path) -> launcher.LauncherConfig:
    budget = launcher.Budget(
        pids_max=2,
        memory_max_bytes=launcher.MIN_MEMORY_BYTES,
        cpu_max_us=1_000,
        cpu_period_us=1_000,
    )
    return launcher.LauncherConfig(
        freshroot=tmp_path / "freshroot",
        cgroup_root=tmp_path / "cgroup",
        timeout_seconds=30,
        roles=tuple(
            launcher.RoleSpec(
                role=role,
                command=(f"/runtime/{role}/entry",),
                bindings=(),
                budget=budget,
            )
            for role in ("host", "mcp")
        ),
    )


def _patch_native_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    startup_lines: tuple[str, ...] = ("READY:7001", "READY:7002"),
    identities: tuple[tuple[object | None, str | None], ...] = (
        (object(), None),
        (object(), None),
    ),
) -> tuple[launcher.LauncherConfig, list[tuple[int, int]]]:
    config = _native_test_config(tmp_path)
    config.freshroot.mkdir()
    config.cgroup_root.mkdir()
    (config.cgroup_root / "cgroup.controllers").write_text("cpu memory pids", encoding="ascii")
    (config.cgroup_root / "cgroup.procs").touch()
    monkeypatch.setattr(launcher, "_native_requirements", lambda: None)
    monkeypatch.setattr(launcher, "_require_native_directory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_require_cgroup_budget_controllers", lambda *_args: None)
    monkeypatch.setattr(launcher, "_ensure_fixed_users", lambda: None)
    monkeypatch.setattr(launcher, "_prepare_cgroup", lambda root, spec: root / spec.role)
    monkeypatch.setattr(launcher, "_enter_cgroup", lambda *_args: None)
    monkeypatch.setattr(launcher, "_verify_binding", lambda *_args: None)
    monkeypatch.setattr(launcher, "_require_native_binding", lambda *_args: None)
    monkeypatch.setattr(launcher.os, "chown", lambda *_args: None, raising=False)
    monkeypatch.setattr(launcher.os, "setpgid", lambda *_args: None, raising=False)
    monkeypatch.setattr(launcher.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(launcher.os, "write", lambda *_args: 1)

    child_ids = iter((7001, 7002))
    monkeypatch.setattr(launcher.os, "fork", lambda: next(child_ids), raising=False)
    monkeypatch.setattr(launcher.os, "waitpid", lambda pid, _options: (pid, 0), raising=False)

    responses = list(startup_lines)

    def fake_read_status_line(_fd: int, _buffer: bytearray, _deadline: float) -> str:
        return responses.pop(0) if responses else "EXIT:0"

    monkeypatch.setattr(launcher, "_read_status_line", fake_read_status_line)
    identity_results = list(identities)

    def fake_capture_identity(
        _role: str, _pid: int, _cgroup_dir: Path
    ) -> tuple[object | None, str | None]:
        return identity_results.pop(0)

    monkeypatch.setattr(launcher, "_capture_role_identity", fake_capture_identity)
    monkeypatch.setattr(launcher, "_capture_process_start_identity", lambda role, pid: "a" * 64)
    monkeypatch.setattr(launcher, "_finish_role_observation", lambda *_args: None)
    kill_calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, _cgroup_dir: Path | None, sig: int) -> None:
        kill_calls.append((pid, sig))

    monkeypatch.setattr(launcher, "_kill_role", fake_kill)
    return config, kill_calls


def test_roles_started_callback_receives_read_only_handles_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, _kill_calls = _patch_native_lifecycle(monkeypatch, tmp_path)
    calls: list[tuple[launcher.RoleHandle, ...]] = []

    def callback(handles: tuple[launcher.RoleHandle, ...]) -> None:
        calls.append(handles)
        assert isinstance(handles, tuple)
        with pytest.raises(FrozenInstanceError):
            handles[0].pid = 9999  # type: ignore[misc]

    receipt, exit_code = launcher.run_native(
        config,
        config_sha256="a" * 64,
        on_roles_started=callback,
    )

    assert exit_code == 0
    assert receipt["status"] == "observed"
    assert len(calls) == 1
    assert [(handle.role, handle.pid, handle.uid) for handle in calls[0]] == [
        ("host", 7001, 1000),
        ("mcp", 7002, 1001),
    ]
    assert calls[0][0].cgroup_dir == tmp_path / "cgroup" / "host"
    assert calls[0][1].workdir == tmp_path / "freshroot" / "mcp-work"
    serialized = launcher.canonical_json(receipt).decode()
    assert str(tmp_path / "cgroup") not in serialized
    assert str(tmp_path / "freshroot") not in serialized


def test_roles_started_callback_is_skipped_when_both_roles_are_not_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, kill_calls = _patch_native_lifecycle(
        monkeypatch,
        tmp_path,
        startup_lines=("READY:7001", "ERROR:role_start_failed"),
        identities=((object(), None),),
    )
    calls: list[tuple[launcher.RoleHandle, ...]] = []

    receipt, exit_code = launcher.run_native(
        config,
        config_sha256="b" * 64,
        on_roles_started=lambda handles: calls.append(handles),
    )

    assert exit_code != 0
    assert receipt["status"] == "gap"
    assert calls == []
    assert kill_calls


def test_roles_started_callback_is_skipped_when_identity_capture_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, kill_calls = _patch_native_lifecycle(
        monkeypatch,
        tmp_path,
        identities=((None, "role_observation_missing"), (object(), None)),
    )
    calls: list[tuple[launcher.RoleHandle, ...]] = []

    receipt, exit_code = launcher.run_native(
        config,
        config_sha256="c" * 64,
        on_roles_started=lambda handles: calls.append(handles),
    )

    assert exit_code != 0
    assert receipt["status"] == "gap"
    assert calls == []
    assert {pid for pid, _sig in kill_calls} == {7001, 7002}


def test_roles_started_callback_failure_is_typed_and_cleans_up_all_roles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, kill_calls = _patch_native_lifecycle(monkeypatch, tmp_path)
    calls = 0

    def callback(_handles: tuple[launcher.RoleHandle, ...]) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("private callback failure")

    receipt, exit_code = launcher.run_native(
        config,
        config_sha256="d" * 64,
        on_roles_started=callback,
    )

    assert exit_code == 1
    assert receipt["status"] == "gap"
    assert receipt["failure_codes"] == ["roles_started_callback_failed"]
    assert calls == 1
    assert {(pid, sig) for pid, sig in kill_calls} == {
        (7001, launcher.signal.SIGTERM),
        (7002, launcher.signal.SIGTERM),
    }
    serialized = launcher.canonical_json(receipt).decode()
    assert "private callback failure" not in serialized


def test_validation_only_works_without_posix_modules(tmp_path: Path) -> None:
    path, _value, digest = _write_config(tmp_path)
    script = """
import builtins
import sys
original_import = builtins.__import__
def closed_import(name, *args, **kwargs):
    if name in {"fcntl", "grp", "pwd"}:
        raise ModuleNotFoundError(name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = closed_import
from benchmarks.hosts.linux_role_launcher import main
raise SystemExit(main(sys.argv[1:]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, "--config", str(path),
         "--config-sha256", digest, "--validation-only"],
        cwd=Path(__file__).parents[1], text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert _receipt(result)["status"] == "validated"


def test_guest_paths_keep_posix_semantics_on_a_windows_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import PureWindowsPath

    _path, value, _digest = _write_config(tmp_path)

    def host_path(path: str) -> Path | PureWindowsPath:
        # Keep local fixture files on the real host; model Windows interpretation
        # for the guest paths that the public config validator must accept.
        if path.startswith("/runtime/"):
            return PureWindowsPath(path)
        return Path(path)

    monkeypatch.setattr(launcher, "Path", host_path)
    config = launcher.parse_config(value)
    assert config.roles[0].bindings[0].target == "/runtime/host/entry"
    assert config.roles[0].command == ("/runtime/host/entry", "--bounded")
    value["roles"][0]["runtime_bindings"][0]["target"] = "/proc/status"
    with pytest.raises(launcher.LauncherError, match="runtime_target_reserved"):
        launcher.parse_config(value)
    value["roles"][0]["runtime_bindings"][0]["target"] = "/runtime/host/entry"
    value["roles"][0]["command"] = ["/bin/sh"]
    with pytest.raises(launcher.LauncherError, match="command_shell_wrapper_forbidden"):
        launcher.parse_config(value)
    value["roles"][0]["runtime_bindings"][0]["target"] = "C:/runtime/host/entry"
    with pytest.raises(launcher.LauncherError, match="runtime_target_invalid"):
        launcher.parse_config(value)


def test_native_run_rejects_non_linux_before_posix_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    path, _value, digest = _write_config(tmp_path)
    monkeypatch.setattr(launcher.platform, "system", lambda: "Windows")
    monkeypatch.delattr(launcher.os, "geteuid", raising=False)
    assert launcher.main([
        "--config", str(path), "--config-sha256", digest, "--run",
    ]) == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["failure_codes"] == ["native_requires_linux"]
    assert receipt["native_mutation"] is False
