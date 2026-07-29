from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from benchmarks.release.evidence import (
    environment_manifest,
    file_record,
    repository_binding,
    sha256_bytes,
    write_report,
)

SCHEMA_VERSION = "deeplaw.distribution-lifecycle/v1"
LEGACY_VERSION = "0.6.0"


class LifecycleError(RuntimeError):
    pass


def _run(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    operation: str,
    evidence: list[dict[str, Any]],
    timeout: int = 300,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        timeout=timeout,
    )
    evidence.append(
        {
            "sequence": len(evidence) + 1,
            "operation": operation,
            "returncode": process.returncode,
            "stdout_sha256": sha256_bytes(process.stdout),
            "stdout_bytes": len(process.stdout),
            "stderr_sha256": sha256_bytes(process.stderr),
            "stderr_bytes": len(process.stderr),
        }
    )
    if process.returncode != 0:
        stderr = process.stderr.decode("utf-8", errors="replace")[-4000:]
        raise LifecycleError(f"{operation} failed: {stderr}")
    return process


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_cli(root: Path) -> Path:
    return root / ("Scripts/deeplaw.exe" if os.name == "nt" else "bin/deeplaw")


def _assert_output(
    process: subprocess.CompletedProcess[bytes], expected: str, operation: str
) -> None:
    output = process.stdout.decode("utf-8", errors="strict").strip()
    if output != expected:
        raise LifecycleError(f"{operation} returned {output!r}, expected {expected!r}")


def _assert_wheel_version(path: Path, expected: str) -> None:
    if path.is_symlink() or not path.is_file() or path.suffix != ".whl":
        raise LifecycleError(f"wheel is unavailable: {path}")
    with zipfile.ZipFile(path) as archive:
        metadata_names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise LifecycleError("wheel must contain exactly one METADATA record")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
    if f"\nVersion: {expected}\n" not in f"\n{metadata}":
        raise LifecycleError(f"wheel metadata does not declare version {expected}")


def _isolated_environment(home: Path) -> dict[str, str]:
    if home.exists() or home.is_symlink():
        raise LifecycleError("distribution lifecycle home must not already exist")
    home.mkdir(mode=0o700)
    selected_home = str(home.absolute())
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": selected_home,
        "USERPROFILE": selected_home,
        "NO_COLOR": "1",
        "PYTHONUTF8": "1",
        "UV_NO_PROGRESS": "1",
    }
    if os.name == "nt":
        drive, tail = os.path.splitdrive(selected_home)
        if drive:
            environment["HOMEDRIVE"] = drive
            environment["HOMEPATH"] = tail or "\\"
    for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"):
        if name in os.environ:
            environment[name] = os.environ[name]
    return environment


def _create_environment(
    *,
    uv: str,
    root: Path,
    cwd: Path,
    environment: dict[str, str],
    operation: str,
    evidence: list[dict[str, Any]],
) -> Path:
    _run(
        [uv, "venv", "--python", sys.executable, "--no-project", str(root)],
        cwd=cwd,
        environment=environment,
        operation=operation,
        evidence=evidence,
    )
    python = _venv_python(root)
    if not python.is_file():
        raise LifecycleError(f"{operation} did not create a Python executable")
    return python


def _install(
    *,
    uv: str,
    python: Path,
    artifact: Path,
    constraints: Path,
    cwd: Path,
    environment: dict[str, str],
    operation: str,
    evidence: list[dict[str, Any]],
    upgrade: bool = False,
) -> None:
    command = [
        uv,
        "pip",
        "install",
        "--python",
        str(python),
        "--constraint",
        str(constraints),
        "--no-progress",
    ]
    if upgrade:
        command.append("--upgrade")
    command.append(str(artifact))
    _run(
        command,
        cwd=cwd,
        environment=environment,
        operation=operation,
        evidence=evidence,
    )


def run(
    repository: Path,
    *,
    dist: Path,
    legacy_wheel: Path,
    uv: str,
) -> dict[str, Any]:
    binding = repository_binding(repository)
    version = binding["package_version"]
    if version == LEGACY_VERSION or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise LifecycleError(f"commercial lifecycle package version is invalid: {version}")
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise LifecycleError("dist must contain exactly one wheel and one sdist")
    wheel, sdist = wheels[0].resolve(strict=True), sdists[0].resolve(strict=True)
    legacy = legacy_wheel.resolve(strict=True)
    _assert_wheel_version(wheel, version)
    _assert_wheel_version(legacy, LEGACY_VERSION)
    if f"deeplaw-{version}" not in wheel.name or f"deeplaw-{version}" not in sdist.name:
        raise LifecycleError("distribution filenames do not match the commercial version")

    evidence: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="deeplaw-distribution-lifecycle-") as temporary:
        root = Path(temporary)
        environment = _isolated_environment(root / "home")
        constraints = root / "runtime-constraints.txt"
        export = _run(
            [
                uv,
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--output-file",
                str(constraints),
            ],
            cwd=repository,
            environment={**os.environ, "NO_COLOR": "1", "UV_NO_PROGRESS": "1"},
            operation="export_locked_runtime_constraints",
            evidence=evidence,
        )
        del export

        wheel_root = root / "wheel-environment"
        wheel_python = _create_environment(
            uv=uv,
            root=wheel_root,
            cwd=root,
            environment=environment,
            operation="create_wheel_environment",
            evidence=evidence,
        )
        _install(
            uv=uv,
            python=wheel_python,
            artifact=wheel,
            constraints=constraints,
            cwd=root,
            environment=environment,
            operation="install_verified_wheel",
            evidence=evidence,
        )
        wheel_version = _run(
            [str(_venv_cli(wheel_root)), "--version"],
            cwd=root,
            environment=environment,
            operation="verify_wheel_cli",
            evidence=evidence,
        )
        _assert_output(wheel_version, f"deeplaw {version}", "verify_wheel_cli")
        _run(
            [uv, "pip", "check", "--python", str(wheel_python)],
            cwd=root,
            environment=environment,
            operation="check_wheel_dependencies",
            evidence=evidence,
        )
        _run(
            [uv, "pip", "uninstall", "--python", str(wheel_python), "deeplaw"],
            cwd=root,
            environment=environment,
            operation="uninstall_verified_wheel",
            evidence=evidence,
        )
        absent = _run(
            [
                str(wheel_python),
                "-I",
                "-c",
                "import importlib.util; assert importlib.util.find_spec('deeplaw') is None",
            ],
            cwd=root,
            environment=environment,
            operation="verify_wheel_uninstalled",
            evidence=evidence,
        )
        del absent

        upgrade_root = root / "upgrade-environment"
        upgrade_python = _create_environment(
            uv=uv,
            root=upgrade_root,
            cwd=root,
            environment=environment,
            operation="create_upgrade_environment",
            evidence=evidence,
        )
        _install(
            uv=uv,
            python=upgrade_python,
            artifact=legacy,
            constraints=constraints,
            cwd=root,
            environment=environment,
            operation="install_legacy_wheel",
            evidence=evidence,
        )
        legacy_version = _run(
            [str(_venv_cli(upgrade_root)), "--version"],
            cwd=root,
            environment=environment,
            operation="verify_legacy_cli",
            evidence=evidence,
        )
        _assert_output(legacy_version, f"deeplaw {LEGACY_VERSION}", "verify_legacy_cli")
        _install(
            uv=uv,
            python=upgrade_python,
            artifact=wheel,
            constraints=constraints,
            cwd=root,
            environment=environment,
            operation="upgrade_legacy_to_verified_wheel",
            evidence=evidence,
            upgrade=True,
        )
        upgraded_version = _run(
            [str(_venv_cli(upgrade_root)), "--version"],
            cwd=root,
            environment=environment,
            operation="verify_upgraded_cli",
            evidence=evidence,
        )
        _assert_output(upgraded_version, f"deeplaw {version}", "verify_upgraded_cli")
        _run(
            [uv, "pip", "uninstall", "--python", str(upgrade_python), "deeplaw"],
            cwd=root,
            environment=environment,
            operation="uninstall_upgraded_wheel",
            evidence=evidence,
        )

        sdist_root = root / "sdist-environment"
        sdist_python = _create_environment(
            uv=uv,
            root=sdist_root,
            cwd=root,
            environment=environment,
            operation="create_sdist_environment",
            evidence=evidence,
        )
        _install(
            uv=uv,
            python=sdist_python,
            artifact=sdist,
            constraints=constraints,
            cwd=root,
            environment=environment,
            operation="install_verified_sdist",
            evidence=evidence,
        )
        sdist_version = _run(
            [str(_venv_cli(sdist_root)), "--version"],
            cwd=root,
            environment=environment,
            operation="verify_sdist_cli",
            evidence=evidence,
        )
        _assert_output(sdist_version, f"deeplaw {version}", "verify_sdist_cli")
        _run(
            [uv, "pip", "uninstall", "--python", str(sdist_python), "deeplaw"],
            cwd=root,
            environment=environment,
            operation="uninstall_verified_sdist",
            evidence=evidence,
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "binding": binding,
        "environment": environment_manifest(uv_executable=uv),
        "artifacts": {
            "wheel": file_record(wheel, logical_name=wheel.name),
            "sdist": file_record(sdist, logical_name=sdist.name),
            "legacy_wheel": file_record(legacy, logical_name=legacy.name),
        },
        "legacy_source": {
            "version": LEGACY_VERSION,
            "expected_commit": "e0f1fe3ff01d3026df12673d57c69014c2c4dca4",
        },
        "command_evidence": evidence,
        "gates": {
            "wheel_install": True,
            "wheel_uninstall": True,
            "sdist_install": True,
            "sdist_uninstall": True,
            "upgrade_from_0_6_0": True,
            "cli_version": True,
            "locked_runtime_constraints": True,
        },
        "passed": True,
    }


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Exercise install, upgrade, and uninstall against verified distributions."
    )
    parser.add_argument("--repository", type=Path, default=repository)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--legacy-wheel", type=Path, required=True)
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(
            args.repository.resolve(),
            dist=args.dist.resolve(),
            legacy_wheel=args.legacy_wheel.resolve(),
            uv=args.uv,
        )
        write_report(args.output.resolve(), report)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
