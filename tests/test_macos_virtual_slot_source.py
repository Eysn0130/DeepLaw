"""Compiled CLI checks for the candidate macOS Virtualization slot launcher."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "benchmarks" / "hosts" / "macos_virtual_slot.m"


def _valid_args(kernel: Path, initrd: Path) -> list[str]:
    return [
        "--kernel",
        str(kernel),
        "--initrd",
        str(initrd),
        "--cpu",
        "1",
        "--memory-mib",
        "512",
        "--timeout-seconds",
        "5",
        "--validation-only",
    ]


@pytest.fixture(scope="module")
def launcher(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if sys.platform != "darwin":
        pytest.skip("Virtualization.framework launcher is Darwin-only")
    clang = shutil.which("clang")
    if clang is None:
        pytest.skip("clang is required for the native source check")

    build_dir = tmp_path_factory.mktemp("macos-virtual-slot-build")
    executable = build_dir / "macos_virtual_slot"
    completed = subprocess.run(
        [
            clang,
            "-fobjc-arc",
            "-O0",
            "-Wall",
            "-Wextra",
            "-framework",
            "Foundation",
            "-framework",
            "Virtualization",
            str(SOURCE),
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return executable


def _run(
    executable: Path, args: list[str]
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    completed = subprocess.run(
        [str(executable), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    records = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    return completed, records


def _images(tmp_path: Path) -> tuple[Path, Path]:
    kernel = tmp_path / "Image"
    initrd = tmp_path / "initrd"
    kernel.write_bytes(b"kernel")
    initrd.write_bytes(b"initrd")
    return kernel, initrd


def test_validation_only_accepts_explicit_bounded_config_without_starting_vm(
    launcher: Path, tmp_path: Path
) -> None:
    kernel, initrd = _images(tmp_path)
    completed, records = _run(launcher, _valid_args(kernel, initrd))

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert records[-1]["event"] == "configuration_validated"
    record = records[-1]
    assert record["validation_only"] is True
    assert record["cpu_count"] == 1
    assert record["memory_mib"] == 512
    assert record["timeout_seconds"] == 5
    assert record["network_devices"] == 0
    assert record["directory_sharing_devices"] == 0
    assert record["storage_devices"] == 0
    assert record["usb_controllers"] == 0
    assert record["vsock_devices"] == 1
    assert record["formal_admission"] is False
    rendered = completed.stdout + completed.stderr
    assert str(kernel) not in rendered
    assert str(initrd) not in rendered
    assert "console=" not in rendered
    assert "Host" not in rendered


def test_validation_only_rejects_unknown_cli_before_vm_start(
    launcher: Path, tmp_path: Path
) -> None:
    kernel, initrd = _images(tmp_path)
    args = [*_valid_args(kernel, initrd), "--unexpected", "value"]
    completed, records = _run(launcher, args)

    assert completed.returncode == 64
    assert records[-1]["event"] == "configuration_rejected"
    assert records[-1]["kind"] == "native_config"
    assert records[-1]["reason"] == "unknown_argument"


def test_validation_only_rejects_missing_required_cli_before_vm_start(launcher: Path) -> None:
    completed, records = _run(launcher, ["--validation-only"])

    assert completed.returncode == 64
    assert records[-1]["event"] == "configuration_rejected"
    assert records[-1]["kind"] == "native_config"
    assert records[-1]["reason"] == "missing_required_argument"


@pytest.mark.parametrize(
    ("option", "value", "reason"),
    [
        ("--cpu", "0", "cpu_out_of_bounds"),
        ("--cpu", "3", "cpu_out_of_bounds"),
        ("--memory-mib", "511", "memory_out_of_bounds"),
        ("--memory-mib", "4097", "memory_out_of_bounds"),
        ("--timeout-seconds", "0", "timeout_out_of_bounds"),
        ("--timeout-seconds", "1801", "timeout_out_of_bounds"),
        ("--guest-control-port", "0", "guest_control_port_out_of_bounds"),
        ("--guest-control-port", "65536", "guest_control_port_out_of_bounds"),
    ],
)
def test_validation_only_rejects_budget_out_of_bounds(
    launcher: Path,
    tmp_path: Path,
    option: str,
    value: str,
    reason: str,
) -> None:
    kernel, initrd = _images(tmp_path)
    args = _valid_args(kernel, initrd)
    if option == "--guest-control-port":
        args.extend([option, "4321", "--fd-handoff-socket", str(tmp_path.parent / "handoff")])
    args[args.index(option) + 1] = value
    completed, records = _run(launcher, args)

    assert completed.returncode == 64
    assert records[-1]["event"] == "configuration_rejected"
    assert records[-1]["kind"] == "native_config"
    assert records[-1]["reason"] == reason


def test_validation_only_rejects_missing_image_before_vm_start(
    launcher: Path, tmp_path: Path
) -> None:
    kernel, initrd = _images(tmp_path)
    kernel.unlink()
    completed, records = _run(launcher, _valid_args(kernel, initrd))

    assert completed.returncode == 64
    assert records[-1]["event"] == "configuration_rejected"
    assert records[-1]["kind"] == "native_config"
    assert records[-1]["reason"] == "kernel_or_initrd_unavailable"
    assert str(kernel) not in completed.stdout


def test_validation_only_requires_fixed_port_and_owner_local_handoff_pair(
    launcher: Path, tmp_path: Path
) -> None:
    kernel, initrd = _images(tmp_path)
    handoff_path = tmp_path.parent / "handoff"

    completed, records = _run(
        launcher,
        [*_valid_args(kernel, initrd), "--guest-control-port", "4321"],
    )
    assert completed.returncode == 64
    assert records[-1]["reason"] == "handoff_arguments_must_be_paired"

    completed, records = _run(
        launcher,
        [*_valid_args(kernel, initrd), "--fd-handoff-socket", str(tmp_path / "handoff")],
    )
    assert completed.returncode == 64
    assert records[-1]["reason"] == "handoff_arguments_must_be_paired"

    completed, records = _run(
        launcher,
        [
            *_valid_args(kernel, initrd),
            "--guest-control-port",
            "4321",
            "--fd-handoff-socket",
            str(handoff_path),
        ],
    )
    assert completed.returncode == 0
    assert records[-1]["fd_handoff_enabled"] is True
    assert records[-1]["guest_control_port"] == 4321


def test_validation_only_rejects_unbounded_or_public_handoff_socket_path(
    launcher: Path, tmp_path: Path
) -> None:
    kernel, initrd = _images(tmp_path)
    base = [*_valid_args(kernel, initrd), "--guest-control-port", "4321"]

    relative, records = _run(launcher, [*base, "--fd-handoff-socket", "handoff"])
    assert relative.returncode == 64
    assert records[-1]["reason"] == "handoff_socket_path_invalid"

    public, records = _run(
        launcher, [*base, "--fd-handoff-socket", "/tmp/deeplaw-slot-handoff"]
    )
    assert public.returncode == 64
    assert records[-1]["reason"] == "handoff_socket_path_invalid"


def test_source_contains_real_vsock_connect_and_scm_rights_frame() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "VZVirtioSocketDeviceConfiguration" in source
    assert "connectToPort:" in source
    assert "socket(AF_UNIX, SOCK_STREAM, 0)" in source
    assert "SCM_RIGHTS" in source
    assert "kHandoffFrameBytes = 16" in source
    assert "sendmsg" in source
    assert "sent == (ssize_t)sizeof(frame)" in source
    assert "getpeereid" in source
    assert "ownerPeerMatches" in source
    assert "no packet boundary" in source
    assert "kHandoffFrameBytes + 1" in source
    assert "MSG_WAITALL" in source
    assert "MSG_CTRUNC" in source
    assert "CMSG_LEN(sizeof(int))" in source
    assert "AF_INET" not in source
