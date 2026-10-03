"""Focused development checks for the credential-free macOS slot launcher."""

from __future__ import annotations

import json
import os
import select
import shlex
import socket
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from benchmarks.hosts.macos_slot_isolation import (
    CHALLENGE_MARKERS,
    MAX_OUTPUT_BYTES,
    MAX_TIMEOUT_SECONDS,
    LoopbackEndpoint,
    MacOSSlotConfig,
    SandboxLaunchError,
    SandboxUnavailableError,
    SlotConfigurationError,
    build_sandbox_profile,
    launch_slot,
    prepare_slot,
    sandbox_backend_available,
)

# These exercise the Darwin Seatbelt facility and POSIX ownership semantics,
# not the portable Knowledge kernel. Keep them in the existing reserved machine
# inventory; passing them does not attest a native Host or a complete process tree.
pytestmark = [
    pytest.mark.qualification,
    pytest.mark.skipif(os.name != "posix", reason="POSIX staging and Seatbelt prerequisites only"),
]

_COMMAND_EXECUTABLE = (
    Path("/bin/sh")
    if sys.platform == "darwin"
    else Path(sys.executable).resolve()
)


def _shell_config(
    read_root: Path,
    write_root: Path,
    script: str,
    *,
    outside_read_root: Path | None = None,
    endpoints: tuple[LoopbackEndpoint, ...] = (),
    timeout_seconds: float = 5.0,
    max_output_bytes: int = 4096,
) -> MacOSSlotConfig:
    if sys.platform != "darwin":
        pytest.skip("legacy shell policy requires the Darwin filesystem")
    read_roots = [Path("/bin"), read_root]
    if outside_read_root is not None:
        read_roots.append(outside_read_root)
    if _COMMAND_EXECUTABLE.parent not in read_roots:
        read_roots.insert(0, _COMMAND_EXECUTABLE.parent)
    return MacOSSlotConfig(
        command=(str(_COMMAND_EXECUTABLE), "-c", script),
        allowed_read_roots=read_roots,
        allowed_write_roots=(write_root,),
        allowed_loopback_endpoints=endpoints,
        cwd=read_root,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )


def _challenge_script(read_root: Path, write_root: Path, denied_root: Path) -> str:
    allowed = shlex.quote(str(read_root / "allowed.txt"))
    denied = shlex.quote(str(denied_root / "secret.txt"))
    inside = shlex.quote(str(write_root / "created.txt"))
    escape = shlex.quote(str(denied_root / "escape.txt"))
    read_ok = shlex.quote(str(write_root / "read-ok"))
    return (
        f"if /bin/cat {allowed} >/dev/null 2>&1; then : > {read_ok}; fi; "
        f"if /bin/cat {denied} >/dev/null 2>&1; then printf READ_ESCAPE; "
        f"else printf {CHALLENGE_MARKERS['read_denied']}; fi; "
        f"if : > {inside}; then :; else printf WRITE_ALLOWED_MISSING; fi; "
        f"if : > {escape}; then printf WRITE_ESCAPE; "
        f"else printf {CHALLENGE_MARKERS['write_denied']}; fi"
    )


def test_profile_is_closed_and_path_escaping_is_literal(tmp_path: Path) -> None:
    read_root = tmp_path / 'read" (allow file-read*)'
    write_root = tmp_path / "write\\root"
    read_root.mkdir(mode=0o700)
    write_root.mkdir(mode=0o700)
    config = _shell_config(read_root, write_root, "printf ok")

    profile = build_sandbox_profile(config)

    assert "(deny default)" in profile
    assert "(deny network-inbound network-outbound)" in profile
    assert "(allow network-outbound" not in profile
    assert '(allow file-read* (subpath "/")' not in profile
    assert "\\\" (allow file-read*)" in profile
    assert 'subpath "' + str(write_root).replace("\\", "\\\\") in profile
    assert "(allow file-write*" in profile
    assert str(read_root) not in profile
    assert str(read_root) not in repr(config)
    assert str(write_root) not in repr(config)


def test_configuration_rejects_relative_symlink_and_wide_boundaries(
    tmp_path: Path,
) -> None:
    read_root = tmp_path / "read"
    write_root = tmp_path / "write"
    read_root.mkdir(mode=0o700)
    write_root.mkdir(mode=0o700)
    with pytest.raises(SlotConfigurationError):
        MacOSSlotConfig(
            command=("sh", "-c", "printf ok"),
            allowed_read_roots=(read_root,),
            allowed_write_roots=(write_root,),
        )
    linked = tmp_path / "linked"
    linked.symlink_to(read_root, target_is_directory=True)
    with pytest.raises(SlotConfigurationError):
        _shell_config(linked, write_root, "printf ok")
    linked_executable = read_root / "linked-sh"
    linked_executable.symlink_to("/bin/sh")
    with pytest.raises(SlotConfigurationError):
        MacOSSlotConfig(
            command=(str(linked_executable), "-c", "printf ok"),
            allowed_read_roots=(read_root,),
            allowed_write_roots=(write_root,),
        )
    with pytest.raises(SlotConfigurationError):
        MacOSSlotConfig(
            command=("/bin/sh", "-c", "printf ok"),
            allowed_read_roots=(Path("/"),),
            allowed_write_roots=(write_root,),
        )
    (read_root / "nested").mkdir(mode=0o700)
    with pytest.raises(SlotConfigurationError):
        MacOSSlotConfig(
            command=("/bin/sh", "-c", "printf ok"),
            allowed_read_roots=(Path("/bin"), read_root, read_root / "nested"),
            allowed_write_roots=(write_root,),
        )


def test_configuration_rejects_non_loopback_and_unbounded_limits(
    tmp_path: Path,
) -> None:
    read_root = tmp_path / "read"
    write_root = tmp_path / "write"
    read_root.mkdir(mode=0o700)
    write_root.mkdir(mode=0o700)
    common = {
        "command": ("/bin/sh", "-c", "printf ok"),
        "allowed_read_roots": (Path("/bin"), read_root),
        "allowed_write_roots": (write_root,),
    }
    for endpoint in (("8.8.8.8", 53), ("0.0.0.0", 1234), ("127.0.0.1", 0)):
        with pytest.raises(SlotConfigurationError):
            MacOSSlotConfig(**common, allowed_loopback_endpoints=(endpoint,))
    with pytest.raises(SlotConfigurationError):
        MacOSSlotConfig(**common, timeout_seconds=MAX_TIMEOUT_SECONDS + 1)
    with pytest.raises(SlotConfigurationError):
        MacOSSlotConfig(**common, max_output_bytes=MAX_OUTPUT_BYTES + 1)


def test_dry_run_is_path_free_and_never_observed(tmp_path: Path) -> None:
    read_root = tmp_path / "read"
    write_root = tmp_path / "write"
    read_root.mkdir(mode=0o700)
    write_root.mkdir(mode=0o700)
    config = _shell_config(read_root, write_root, "printf ok")

    plan = prepare_slot(config)
    public = plan.to_public_dict()

    assert plan.marker_observed is False
    assert plan.formal_qualification is False
    assert plan.process_tree_observed is False
    assert "complete_process_tree_observation_unavailable" in plan.not_qualified
    rendered = json.dumps(public, sort_keys=True)
    assert str(read_root) not in rendered
    assert str(write_root) not in rendered
    assert config.config_sha256 in rendered
    assert plan.profile_sha256 in rendered


@pytest.mark.skipif(sys.platform != "darwin", reason="requires Darwin sandbox-exec")
def test_real_sandbox_observes_filesystem_denials_and_preserves_allowed_write(
    tmp_path: Path,
) -> None:
    read_root = tmp_path / "read"
    write_root = tmp_path / "write"
    denied_root = tmp_path / "denied"
    for root in (read_root, write_root, denied_root):
        root.mkdir(mode=0o700)
    (read_root / "allowed.txt").write_text("allowed", encoding="utf-8")
    (denied_root / "secret.txt").write_text("secret", encoding="utf-8")
    config = _shell_config(
        read_root,
        write_root,
        _challenge_script(read_root, write_root, denied_root),
    )

    result = launch_slot(config, expected_challenges=("read_denied", "write_denied"))

    assert result.status == "completed"
    assert result.returncode == 0
    assert result.sandbox_exec_spawned is True
    assert result.marker_observed is True
    assert (write_root / "read-ok").is_file()
    assert (write_root / "created.txt").is_file()
    assert not (denied_root / "escape.txt").exists()
    public = result.to_public_dict()
    rendered = json.dumps(public, sort_keys=True)
    assert str(read_root) not in rendered
    assert str(denied_root) not in rendered
    assert "secret" not in rendered
    assert result.formal_qualification is False
    assert result.process_tree_observed is False
    assert result.process_tree_cleanup_observed is False


@pytest.mark.skipif(sys.platform != "darwin", reason="requires Darwin sandbox-exec")
def test_real_sandbox_defaults_to_network_denial_and_allows_one_loopback_port(
    tmp_path: Path,
) -> None:
    read_root = tmp_path / "read"
    write_root = tmp_path / "write"
    read_root.mkdir(mode=0o700)
    write_root.mkdir(mode=0o700)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = int(server.getsockname()[1])
    try:
        denied_script = (
            f"if /usr/bin/nc -z -w 1 127.0.0.1 {port}; then printf NETWORK_ESCAPE; "
            f"else printf {CHALLENGE_MARKERS['network_denied']}; fi"
        )
        denied = _shell_config(read_root, write_root, denied_script)
        denied_result = launch_slot(denied, expected_challenges=("network_denied",))
        assert denied_result.status == "completed"
        assert denied_result.marker_observed is True

        allowed_script = (
            f"if /usr/bin/nc -z -w 1 127.0.0.1 {port}; then : > "
            f"{shlex.quote(str(write_root / 'network-ok'))}; fi"
        )
        allowed = _shell_config(
            read_root,
            write_root,
            allowed_script,
            endpoints=(LoopbackEndpoint("localhost", port),),
        )
        allowed_result = launch_slot(allowed)
        assert allowed_result.status == "completed"
        assert (write_root / "network-ok").is_file()
    finally:
        server.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="requires Darwin sandbox-exec")
def test_real_sandbox_uses_closed_environment_and_fixed_output_timeout_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_root = tmp_path / "read"
    write_root = tmp_path / "write"
    read_root.mkdir(mode=0o700)
    write_root.mkdir(mode=0o700)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-enter-slot")
    monkeypatch.setenv("DEEPLAW_TEST_AMBIENT_SECRET", "must-not-enter-slot")
    environment_probe = (
        "if [ -z \"${OPENAI_API_KEY-}\" ] && [ -z \"${DEEPLAW_TEST_AMBIENT_SECRET-}\" ]; "
        f"then : > {shlex.quote(str(write_root / 'environment-closed'))}; fi"
    )
    clean = _shell_config(read_root, write_root, environment_probe)
    clean_result = launch_slot(clean)
    assert clean_result.status == "completed"
    assert (write_root / "environment-closed").is_file()
    assert "must-not-enter-slot" not in json.dumps(clean_result.to_public_dict())

    output_probe = "i=0; while [ $i -lt 100000 ]; do printf x; i=$((i + 1)); done"
    output_config = _shell_config(
        read_root,
        write_root,
        output_probe,
        max_output_bytes=64,
        timeout_seconds=5,
    )
    output_result = launch_slot(output_config)
    assert output_result.status == "output_limit"
    assert output_result.output_limit_exceeded is True
    assert output_result.stdout_bytes + output_result.stderr_bytes <= 64

    timeout_config = _shell_config(
        read_root,
        write_root,
        "while :; do :; done",
        timeout_seconds=0.2,
    )
    timeout_result = launch_slot(timeout_config)
    assert timeout_result.status == "timeout"
    assert timeout_result.timed_out is True
    assert timeout_result.formal_qualification is False


def test_non_macos_never_falls_back_to_unsandboxed_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_root = tmp_path / "read"
    write_root = tmp_path / "write"
    read_root.mkdir(mode=0o700)
    write_root.mkdir(mode=0o700)
    config = _shell_config(read_root, write_root, "printf ok")
    monkeypatch.setattr(sys, "platform", "linux")

    assert sandbox_backend_available() is False
    with pytest.raises(SandboxUnavailableError):
        launch_slot(config)


@pytest.mark.parametrize("root_kind", ["read", "write"])
def test_staging_hardlinks_are_rejected_before_launch(
    tmp_path: Path, root_kind: str,
) -> None:
    read_root, write_root, denied_root = (tmp_path / name for name in ("read", "write", "denied"))
    for root in (read_root, write_root, denied_root):
        root.mkdir(mode=0o700)
    secret = denied_root / "canary"
    secret.write_text("synthetic canary", encoding="utf-8")
    config = _shell_config(read_root, write_root, "printf ok")
    alias = (read_root if root_kind == "read" else write_root) / "alias"
    alias.hardlink_to(secret)
    with pytest.raises(SlotConfigurationError):
        _shell_config(read_root, write_root, "printf ok")
    with pytest.raises(SlotConfigurationError):
        launch_slot(config)
    assert secret.read_text(encoding="utf-8") == "synthetic canary"


@pytest.mark.skipif(sys.platform != "darwin", reason="requires Darwin sandbox-exec")
def test_group_cleanup_failure_is_explicit_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    import time

    import benchmarks.hosts.macos_slot_isolation as slot

    read_root, write_root = tmp_path / "read", tmp_path / "write"
    read_root.mkdir(mode=0o700)
    write_root.mkdir(mode=0o700)
    original = os.killpg
    calls = 0

    def fail_first_signal(pid: int, sig: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic group signal failure")
        original(pid, sig)

    monkeypatch.setattr(slot.os, "killpg", fail_first_signal)
    config = _shell_config(read_root, write_root, "/bin/sleep 10 & wait", timeout_seconds=0.2)
    started = time.monotonic()
    result = launch_slot(config)
    assert result.status == "cleanup_unconfirmed"
    assert result.timed_out is True
    assert result.process_tree_cleanup_observed is False
    assert calls >= 2
    assert time.monotonic() - started < 3


@pytest.mark.skipif(sys.platform != "darwin", reason="requires Darwin sandbox-exec")
def test_backend_spawn_and_child_marker_do_not_attest_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import benchmarks.hosts.macos_slot_isolation as slot

    read_root, write_root = tmp_path / "read", tmp_path / "write"
    read_root.mkdir(mode=0o700)
    write_root.mkdir(mode=0o700)
    config = _shell_config(read_root, write_root, f"printf {CHALLENGE_MARKERS['read_denied']}")
    result = launch_slot(config, expected_challenges=("read_denied",))
    assert result.marker_observed is True
    assert result.formal_qualification is False
    assert "observed_challenge" not in result.to_public_dict()
    assert "sandbox_applied" not in result.to_public_dict()
    monkeypatch.setattr(slot, "build_sandbox_profile", lambda _: "(version 999)")
    rejected = launch_slot(config)
    assert rejected.status == "exited"
    assert rejected.returncode != 0
    assert rejected.sandbox_exec_spawned is True
    assert rejected.formal_qualification is False


def test_endpoint_declares_dual_stack_outbound_only(tmp_path: Path) -> None:
    read_root, write_root = tmp_path / "read", tmp_path / "write"
    read_root.mkdir(mode=0o700)
    write_root.mkdir(mode=0o700)
    config = _shell_config(
        read_root, write_root, "printf ok", endpoints=(LoopbackEndpoint("localhost", 12345),),
    )
    profile = build_sandbox_profile(config)
    assert '(remote tcp "localhost:12345")' in profile
    assert "(allow network-inbound" not in profile
    with pytest.raises(SlotConfigurationError):
        _shell_config(
            read_root, write_root, "printf ok",
            endpoints=(LoopbackEndpoint("127.0.0.1", 12345),),
        )


def _strict_config(tmp_path: Path, **options: object) -> MacOSSlotConfig:
    inputs, outputs = tmp_path / "inputs", tmp_path / "outputs"
    inputs.mkdir(mode=0o700, exist_ok=True)
    outputs.mkdir(mode=0o700, exist_ok=True)
    executable = inputs / "synthetic-runtime"
    if not executable.exists():
        executable.write_bytes(b"synthetic executable bytes")
        executable.chmod(0o700)
    values = {
        "command": (str(executable),), "allowed_read_roots": (inputs,),
        "allowed_write_roots": (outputs,), "cwd": inputs,
        "policy_mode": "strict_single_process",
    }
    values.update(options)
    return MacOSSlotConfig(**values)


def test_strict_policy_is_explicit_closed_and_keeps_legacy_result_shape(tmp_path: Path) -> None:
    config = _strict_config(tmp_path)
    profile = build_sandbox_profile(config)
    assert '(import "system.sb")' not in profile
    assert '(import "dyld-support.sb")' in profile
    assert "(allow process*)" not in profile
    assert "(deny process-fork process-info* mach* network* ipc-posix* iokit*)" in profile
    assert "SYS_ptrace SYS_proc_info" in profile
    assert f'(allow process-exec (literal "{config.command[0]}"))' in profile
    assert '(allow file-read* file-test-existence (subpath "/System"))' not in profile
    strict = prepare_slot(config).to_public_dict()
    assert strict["strict_policy"]["production_runner_integrated"] is False
    assert strict["formal_qualification"] is False
    assert str(tmp_path) not in json.dumps(strict)
    legacy = MacOSSlotConfig(
        command=config.command, allowed_read_roots=config.read_roots,
        allowed_write_roots=config.write_roots,
    )
    assert '(import "system.sb")' in build_sandbox_profile(legacy)
    assert "strict_policy" not in prepare_slot(legacy).to_public_dict()


def test_strict_policy_rejects_unsafe_roots_aliases_and_loopback_opt_in(tmp_path: Path) -> None:
    config = _strict_config(tmp_path)
    for root in (Path("/"), Path("/bin"), Path("/usr/bin"), Path.home()):
        with pytest.raises(SlotConfigurationError):
            _strict_config(tmp_path, allowed_read_roots=(*config.read_roots, root))
    with pytest.raises(SlotConfigurationError):
        _strict_config(tmp_path, allowed_loopback_endpoints=(LoopbackEndpoint("localhost", 1),))
    with pytest.raises(SlotConfigurationError):
        _strict_config(tmp_path, policy_mode="unknown")
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    (runtime / "escaped").symlink_to(config.command[0])
    with pytest.raises(SlotConfigurationError):
        _strict_config(tmp_path, runtime_read_roots=(runtime,))
    (runtime / "escaped").unlink()
    (runtime / "library").write_bytes(b"runtime")
    (runtime / "internal-alias").symlink_to("library")
    accepted = _strict_config(tmp_path, runtime_read_roots=(runtime,))
    assert prepare_slot(accepted).strict_metadata is not None


def test_strict_input_bytes_are_bound_and_mutation_fails_before_spawn(tmp_path: Path) -> None:
    config = _strict_config(tmp_path)
    initial = prepare_slot(config).strict_metadata["runtime_tree_sha256"]
    Path(config.command[0]).write_bytes(b"changed executable bytes")
    with pytest.raises(SlotConfigurationError):
        prepare_slot(config)
    rebound = _strict_config(tmp_path)
    assert prepare_slot(rebound).strict_metadata["runtime_tree_sha256"] != initial


def test_strict_launch_closes_ambient_environment_and_extra_fds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import benchmarks.hosts.macos_slot_isolation as slot

    config = _strict_config(tmp_path)
    monkeypatch.setenv("DEEPLAW_SYNTHETIC_OWNER_VALUE", "must-not-be-inherited")
    captured: dict[str, object] = {}

    def capture_spawn(command: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise OSError("synthetic spawn failure")

    monkeypatch.setattr(slot, "sandbox_backend_available", lambda: True)
    monkeypatch.setattr(slot, "_strict_metadata", lambda _: {"production_runner_integrated": False})
    monkeypatch.setattr(slot.subprocess, "Popen", capture_spawn)
    with pytest.raises(SandboxLaunchError):
        launch_slot(config)
    assert captured["close_fds"] is True
    assert "pass_fds" not in captured
    assert captured["shell"] is False
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["env"] == {
        "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "LC_CTYPE": "C",
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
        "HOME": str(config.write_roots[0]), "TMPDIR": str(config.write_roots[0]),
    }


_STRICT_NATIVE_PROBE = r"""
#include <errno.h>
#include <fcntl.h>
#include <libproc.h>
#include <mach/mach.h>
#include <netinet/in.h>
#include <servers/bootstrap.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
static int denied_read(const char *p) {
    errno = 0; int fd = open(p, O_RDONLY);
    if (fd >= 0) { close(fd); return 0; }
    return errno == EPERM || errno == EACCES;
}
int main(int argc, char **argv) {
    if (argc == 3 && !strcmp(argv[1], "target")) {
        mach_port_t p = MACH_PORT_NULL;
        if (mach_port_allocate(mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &p) ||
            mach_port_insert_right(mach_task_self(), p, p, MACH_MSG_TYPE_MAKE_SEND) ||
            bootstrap_register(bootstrap_port, argv[2], p)) return 90;
        puts("SYNTHETIC_READY"); fflush(stdout);
        for (;;) pause();
    }
    if (argc != 10) return 91;
    pid_t target = atoi(argv[6]);
    errno = 0; int fd_closed = fcntl(atoi(argv[5]), F_GETFD) == -1 && errno == EBADF;
    errno = 0; pid_t child = fork();
    int fork_denied = child == -1 && (errno == EPERM || errno == EACCES);
    if (child == 0) _exit(0);
    if (child > 0) waitpid(child, NULL, 0);
    char *args[] = {argv[0], "unused", NULL};
    int spawn_error = posix_spawn(&child, argv[0], NULL, NULL, args, environ);
    int spawn_denied = spawn_error == EPERM || spawn_error == EACCES;
    if (!spawn_error) waitpid(child, NULL, 0);
    struct proc_bsdinfo info;
    errno = 0; int proc_result = proc_pidinfo(target, PROC_PIDTBSDINFO, 0, &info, sizeof(info));
    int proc_denied = proc_result == 0 && (errno == EPERM || errno == EACCES);
    mach_port_t task = MACH_PORT_NULL, name = MACH_PORT_NULL, inspect = MACH_PORT_NULL;
    int task_denied = task_for_pid(mach_task_self(), target, &task) != KERN_SUCCESS;
    int name_denied = task_name_for_pid(mach_task_self(), target, &name) != KERN_SUCCESS;
    errno = 0;
    int inspect_result = syscall(SYS_task_inspect_for_pid, mach_task_self(), target, &inspect);
    int inspect_denied = inspect_result == -1 && (errno == EPERM || errno == EACCES);
    errno = 0; int ptrace_result = ptrace(PT_ATTACH, target, 0, 0);
    int ptrace_denied = ptrace_result == -1 && (errno == EPERM || errno == EACCES);
    if (!ptrace_result) ptrace(PT_DETACH, target, 0, 0);
    mach_port_t service = MACH_PORT_NULL;
    int mach_denied = bootstrap_look_up(bootstrap_port, argv[7], &service) != KERN_SUCCESS;
    struct sockaddr_in addr = {0}; addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK); addr.sin_port = htons(atoi(argv[8]));
    errno = 0; int s = socket(AF_INET, SOCK_STREAM, 0);
    int net_result = s < 0 ? -1 : connect(s, (struct sockaddr *)&addr, sizeof(addr));
    int net_denied = net_result == -1 && (errno == EPERM || errno == EACCES);
    if (s >= 0) close(s);
    int owner_denied = denied_read(argv[1]), alias_denied = denied_read(argv[2]);
    int traversal_denied = denied_read(argv[3]);
    errno = 0; int write_fd = open(argv[9], O_CREAT | O_WRONLY, 0600);
    int write_denied = write_fd < 0 && (errno == EPERM || errno == EACCES);
    if (write_fd >= 0) close(write_fd);
    FILE *out = fopen(argv[4], "w"); if (!out) return 92;
    fprintf(out, "{\"owner_read_denied\":%d,\"alias_read_denied\":%d,"
        "\"traversal_read_denied\":%d,\"fd_closed\":%d,\"fork_denied\":%d,"
        "\"spawn_denied\":%d,\"proc_info_denied\":%d,\"task_port_denied\":%d,"
        "\"task_name_denied\":%d,\"task_inspect_denied\":%d,\"ptrace_denied\":%d,"
        "\"mach_lookup_denied\":%d,\"network_denied\":%d,\"owner_write_denied\":%d}",
        owner_denied, alias_denied, traversal_denied, fd_closed, fork_denied,
        spawn_denied, proc_denied, task_denied, name_denied, inspect_denied,
        ptrace_denied, mach_denied, net_denied, write_denied);
    fclose(out);
    puts("DEEPLAW_SLOT_CHALLENGE_READ_DENIED");
    puts("DEEPLAW_SLOT_CHALLENGE_WRITE_DENIED");
    puts("DEEPLAW_SLOT_CHALLENGE_NETWORK_DENIED");
    return 0;
}
"""


@pytest.mark.skipif(sys.platform != "darwin", reason="requires Darwin sandbox-exec and SDK")
def test_strict_native_python_staging_and_synthetic_denials(tmp_path: Path) -> None:
    inputs, outputs, runtime, owner = (tmp_path / name for name in (
        "inputs", "outputs", "runtime", "owner",
    ))
    for root in (inputs, outputs, runtime, owner):
        root.mkdir(mode=0o700)
    (inputs / "allowed").write_text("public staged input", encoding="utf-8")
    runner = inputs / "runner.py"
    runner.write_text(textwrap.dedent("""\
        import pathlib, sys
        data = pathlib.Path(sys.argv[1]).read_bytes()
        pathlib.Path(sys.argv[2]).write_text(str(len(data)))
        print("PYTHON_STAGED_OK")
        """), encoding="utf-8")
    python = MacOSSlotConfig(
        command=(str(Path(sys.executable).resolve()), "-I", "-S", str(runner),
                 str(inputs / "allowed"), str(outputs / "positive")),
        allowed_read_roots=(inputs,), allowed_write_roots=(outputs,),
        runtime_read_roots=(Path(sys.base_prefix).resolve(),),
        policy_mode="strict_single_process", cwd=inputs, timeout_seconds=5,
    )
    positive = launch_slot(python)
    assert positive.status == "completed"
    assert positive.returncode == 0
    assert positive.stderr_bytes == 0
    assert (outputs / "positive").read_text() == "19"
    assert positive.strict_metadata["dyld_support_sha256"]

    source, executable = runtime / "probe.c", runtime / "probe"
    source.write_text(_STRICT_NATIVE_PROBE, encoding="utf-8")
    compiled = subprocess.run(
        ["/usr/bin/clang", "-Wno-deprecated-declarations", str(source), "-o", str(executable)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert compiled.returncode == 0, compiled.stderr
    service_name = "com.deeplaw.synthetic." + uuid.uuid4().hex
    target = subprocess.Popen(
        [str(executable), "target", service_name], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    canary = owner / "private"
    canary.write_bytes(os.urandom(32))
    canary.chmod(0o600)
    alias = owner / "alias"
    alias.symlink_to(canary)
    inherited = os.open(canary, os.O_RDONLY)
    os.set_inheritable(inherited, True)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        assert select.select([target.stdout], [], [], 3)[0], "synthetic target not ready"
        assert target.stdout.readline(128).strip() == "SYNTHETIC_READY"
        config = MacOSSlotConfig(
            command=(str(executable), str(canary), str(alias),
                     str(inputs / ".." / "owner" / "private"), str(outputs / "negatives.json"),
                     str(inherited), str(target.pid), service_name,
                     str(server.getsockname()[1]), str(owner / "write-escape")),
            allowed_read_roots=(inputs,), allowed_write_roots=(outputs,),
            runtime_read_roots=(runtime,), policy_mode="strict_single_process",
            cwd=inputs, timeout_seconds=5,
        )
        result = launch_slot(
            config, expected_challenges=("read_denied", "write_denied", "network_denied"),
        )
        assert result.status == "completed"
        assert result.returncode == 0
        assert result.stderr_bytes == 0
        observations = json.loads((outputs / "negatives.json").read_text())
        assert len(observations) == 14
        assert all(value == 1 for value in observations.values()), observations
        assert not (owner / "write-escape").exists()
        assert result.marker_observed is True
        assert result.formal_qualification is False
        assert result.process_tree_cleanup_observed is False
        assert result.strict_metadata["production_runner_integrated"] is False
        assert str(tmp_path) not in json.dumps(result.to_public_dict())
        (outputs / "engineering-results.json").write_text(json.dumps({
            "python": positive.to_public_dict(), "synthetic_native": result.to_public_dict(),
        }, sort_keys=True), encoding="utf-8")
    finally:
        os.close(inherited)
        server.close()
        target.terminate()
        try:
            target.wait(timeout=3)
        except subprocess.TimeoutExpired:
            target.kill()
            target.wait(timeout=3)
        target.stdout.close()
