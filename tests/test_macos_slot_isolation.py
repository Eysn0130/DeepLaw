"""Focused development checks for the credential-free macOS slot launcher."""

from __future__ import annotations

import json
import shlex
import socket
import sys
from pathlib import Path

import pytest

from benchmarks.hosts.macos_slot_isolation import (
    CHALLENGE_MARKERS,
    MAX_OUTPUT_BYTES,
    MAX_TIMEOUT_SECONDS,
    LoopbackEndpoint,
    MacOSSlotConfig,
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
    pytest.mark.skipif(sys.platform != "darwin", reason="Darwin Seatbelt facility only"),
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
