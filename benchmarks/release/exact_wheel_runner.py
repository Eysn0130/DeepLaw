"""Install and execute exactly one retained DeepLaw wheel in a fresh venv."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import venv
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_VERSION = "deeplaw.exact-wheel-execution-receipt/v1"
PACKAGE_NAME = "deeplaw"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_WHEEL_RE = re.compile(
    r"^deeplaw-(?P<version>[0-9]+\.[0-9]+\.[0-9]+)-[A-Za-z0-9_.-]+\.whl$"
)


class ExactWheelExecutionError(RuntimeError):
    """Raised when exact-wheel execution cannot be proven closed."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ExactWheelExecutionError("receipt input must be a regular non-symlink file")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ExactWheelExecutionError("receipt input could not be read") from exc
    return digest.hexdigest()


def _strict_sha256(value: str, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ExactWheelExecutionError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _strict_json_loads(payload: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ExactWheelExecutionError("runtime probe returned duplicate JSON keys")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ExactWheelExecutionError(f"runtime probe returned non-finite JSON value: {value}")

    try:
        return json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ExactWheelExecutionError("runtime probe returned invalid JSON") from exc


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ExactWheelExecutionError("receipt is not canonical JSON") from exc


def _repository_root(repository: Path) -> Path:
    if repository.is_symlink() or not repository.is_dir():
        raise ExactWheelExecutionError("repository must be a regular directory")
    try:
        return repository.resolve(strict=True)
    except OSError as exc:
        raise ExactWheelExecutionError("repository could not be resolved") from exc


def _candidate_wheel(
    candidate_dir: Path,
    *,
    expected_wheel_sha256: str,
    expected_version: str,
) -> Path:
    _strict_sha256(expected_wheel_sha256, label="expected wheel SHA-256")
    if _SEMVER_RE.fullmatch(expected_version) is None:
        raise ExactWheelExecutionError("expected package version is not semver")
    if candidate_dir.is_symlink() or not candidate_dir.is_dir():
        raise ExactWheelExecutionError("Candidate Full directory must be a regular directory")
    try:
        directory = candidate_dir.resolve(strict=True)
        entries = list(directory.iterdir())
    except OSError as exc:
        raise ExactWheelExecutionError("Candidate Full directory could not be read") from exc
    wheels = [entry for entry in entries if entry.name.endswith(".whl")]
    if len(wheels) != 1:
        raise ExactWheelExecutionError("Candidate Full directory must contain exactly one wheel")
    wheel = wheels[0]
    if wheel.is_symlink() or not wheel.is_file():
        raise ExactWheelExecutionError("Candidate Full wheel must be regular and non-symlink")
    match = _WHEEL_RE.fullmatch(wheel.name)
    if match is None or match.group("version") != expected_version:
        raise ExactWheelExecutionError("Candidate Full wheel filename/version mismatch")
    observed_sha256 = _sha256_file(wheel)
    if observed_sha256 != expected_wheel_sha256:
        raise ExactWheelExecutionError("Candidate Full wheel hash mismatch")
    if wheel.stat().st_size < 1:
        raise ExactWheelExecutionError("Candidate Full wheel is empty")
    return wheel


def _venv_python(venv_path: Path) -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    executable = venv_path / relative
    if not executable.is_file():
        raise ExactWheelExecutionError("isolated venv Python executable is unavailable")
    return executable


def _safe_environment(venv_python: Path) -> dict[str, str]:
    bin_dir = venv_python.parent
    environment = {
        "PATH": str(bin_dir),
        "PYTHONNOUSERSITE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "LC_ALL": "C",
        "LANG": "C",
    }
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR", "PATHEXT"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
    return environment


def _create_venv(venv_path: Path) -> Path:
    try:
        venv.EnvBuilder(
            with_pip=False,
            system_site_packages=False,
            clear=False,
            symlinks=os.name != "nt",
        ).create(venv_path)
        python_executable = _venv_python(venv_path)
        completed = subprocess.run(
            [
                str(python_executable),
                "-I",
                "-m",
                "ensurepip",
                "--upgrade",
                "--default-pip",
            ],
            cwd=venv_path,
            env=_safe_environment(python_executable),
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExactWheelExecutionError("isolated venv creation failed") from exc
    if completed.returncode != 0:
        raise ExactWheelExecutionError("isolated venv pip bootstrap failed")
    return python_executable


def _install_wheel(*, python_executable: Path, wheel: Path, venv_path: Path) -> None:
    try:
        completed = subprocess.run(
            [
                str(python_executable),
                "-I",
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--no-cache-dir",
                "--disable-pip-version-check",
                "--force-reinstall",
                str(wheel),
            ],
            cwd=venv_path,
            env=_safe_environment(python_executable),
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExactWheelExecutionError("exact wheel installation failed") from exc
    if completed.returncode != 0:
        raise ExactWheelExecutionError("exact wheel installation failed")


def _probe_script() -> str:
    return r'''
import importlib.metadata
import importlib.util
import json
import pathlib
import platform
import sys

import deeplaw

origin = pathlib.Path(deeplaw.__file__).resolve()
site_packages = [
    pathlib.Path(item).resolve()
    for item in sys.path
    if isinstance(item, str) and "site-packages" in pathlib.Path(item).parts
]
distribution = importlib.metadata.distribution("deeplaw")
entrypoints = [
    item
    for item in distribution.entry_points
    if item.group == "console_scripts" and item.name == "deeplaw"
]
if len(entrypoints) != 1:
    raise RuntimeError("expected one deeplaw console entrypoint")
entrypoint = entrypoints[0]
module_name, _, _ = entrypoint.value.partition(":")
entry_spec = importlib.util.find_spec(module_name)
entry_origin = (
    pathlib.Path(entry_spec.origin).resolve()
    if entry_spec and entry_spec.origin
    else None
)
print(json.dumps({
    "python_implementation": sys.implementation.name,
    "python_version": platform.python_version(),
    "python_executable": str(pathlib.Path(sys.executable)),
    "site_packages": [str(item) for item in site_packages],
    "import_file": str(origin),
    "distribution_name": distribution.metadata["Name"],
    "distribution_version": distribution.version,
    "entrypoint_name": entrypoint.name,
    "entrypoint_group": entrypoint.group,
    "entrypoint_value": entrypoint.value,
    "entrypoint_file": str(entry_origin) if entry_origin else None,
}, sort_keys=True, separators=(",", ":")))
'''


def _probe_runtime(*, python_executable: Path, venv_path: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [str(python_executable), "-I", "-c", _probe_script()],
            cwd=venv_path,
            env=_safe_environment(python_executable),
            capture_output=True,
            check=False,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExactWheelExecutionError("isolated wheel runtime probe failed") from exc
    if completed.returncode != 0:
        raise ExactWheelExecutionError("isolated wheel runtime probe failed")
    value = _strict_json_loads(completed.stdout)
    if not isinstance(value, dict):
        raise ExactWheelExecutionError("isolated wheel runtime probe was not an object")
    return value


def _console_entrypoint(venv_path: Path) -> Path:
    relative = Path("Scripts/deeplaw.exe") if os.name == "nt" else Path("bin/deeplaw")
    selected = venv_path / relative
    if selected.is_symlink() or not selected.is_file():
        raise ExactWheelExecutionError(
            "installed deeplaw console executable is unavailable or unsafe"
        )
    return selected.resolve(strict=True)


def _relative_to_site_packages(
    path: Path, site_packages: list[Path], *, label: str
) -> tuple[Path, Path]:
    resolved = path.resolve(strict=True)
    for root in site_packages:
        try:
            return resolved.relative_to(root), root
        except ValueError:
            continue
    raise ExactWheelExecutionError(f"{label} did not resolve inside isolated site-packages")


def _validate_probe(
    observation: Mapping[str, Any],
    *,
    expected_version: str,
    repository: Path,
    venv_path: Path,
    expected_python: Path,
) -> dict[str, Any]:
    required = {
        "python_implementation",
        "python_version",
        "python_executable",
        "site_packages",
        "import_file",
        "distribution_name",
        "distribution_version",
        "entrypoint_name",
        "entrypoint_group",
        "entrypoint_value",
        "entrypoint_file",
    }
    if set(observation) != required:
        raise ExactWheelExecutionError("isolated wheel runtime probe fields are incomplete")
    if observation["distribution_name"] != PACKAGE_NAME:
        raise ExactWheelExecutionError("installed distribution name mismatch")
    if observation["distribution_version"] != expected_version:
        raise ExactWheelExecutionError("installed distribution version mismatch")
    if (
        observation["entrypoint_name"] != PACKAGE_NAME
        or observation["entrypoint_group"] != "console_scripts"
    ):
        raise ExactWheelExecutionError("installed console entrypoint mismatch")
    if (
        not isinstance(observation["entrypoint_value"], str)
        or not observation["entrypoint_value"].startswith("deeplaw.")
    ):
        raise ExactWheelExecutionError("installed console entrypoint target is invalid")
    if not isinstance(observation["site_packages"], list) or not observation["site_packages"]:
        raise ExactWheelExecutionError("isolated site-packages path is missing")
    try:
        site_packages = [Path(item).resolve(strict=True) for item in observation["site_packages"]]
        import_file = Path(observation["import_file"])
        entrypoint_file = Path(observation["entrypoint_file"])
        observed_python = Path(observation["python_executable"])
    except (TypeError, ValueError, OSError) as exc:
        raise ExactWheelExecutionError("isolated runtime probe paths are invalid") from exc
    if any(not item.is_dir() for item in site_packages):
        raise ExactWheelExecutionError("isolated site-packages path is not a directory")
    repository = repository.resolve(strict=True)
    venv_path = venv_path.resolve(strict=True)
    for path, label in (
        (import_file, "deeplaw import origin"),
        (entrypoint_file, "deeplaw console entrypoint"),
    ):
        if not path.is_absolute():
            raise ExactWheelExecutionError(f"{label} must be an absolute probe path")
        if path.is_symlink() or not path.is_file():
            raise ExactWheelExecutionError(f"{label} is not a regular file")
        if repository == path or repository in path.parents:
            raise ExactWheelExecutionError(f"{label} resolves to checkout source")
    try:
        if not observed_python.is_absolute():
            raise ExactWheelExecutionError("venv Python executable must be an absolute probe path")
        python_relative = observed_python.relative_to(venv_path)
    except ValueError as exc:
        raise ExactWheelExecutionError("Python executable is outside isolated venv") from exc
    if not python_relative.parts:
        raise ExactWheelExecutionError("Python executable path class is invalid")
    try:
        python_executable = observed_python.resolve(strict=True)
        expected_python = expected_python.resolve(strict=True)
    except OSError as exc:
        raise ExactWheelExecutionError("venv Python executable is unavailable") from exc
    if python_executable != expected_python:
        raise ExactWheelExecutionError("runtime probe used a different Python executable")
    if repository == python_executable or repository in python_executable.parents:
        raise ExactWheelExecutionError("venv Python executable resolves to checkout source")
    import_relative, import_root = _relative_to_site_packages(
        import_file, site_packages, label="deeplaw import origin"
    )
    entrypoint_relative, entrypoint_root = _relative_to_site_packages(
        entrypoint_file, site_packages, label="deeplaw console entrypoint"
    )
    if import_root != entrypoint_root:
        raise ExactWheelExecutionError("deeplaw import and console entrypoint roots differ")
    return {
        "python_implementation": observation["python_implementation"],
        "python_version": observation["python_version"],
        "python_executable": python_executable,
        "import_file": import_file,
        "import_relative": import_relative,
        "entrypoint_file": entrypoint_file,
        "entrypoint_relative": entrypoint_relative,
        "entrypoint_value": observation["entrypoint_value"],
    }


def _schema_path(repository: Path) -> Path:
    path = repository / "contracts" / "exact-wheel-execution-receipt.v1.schema.json"
    if path.is_symlink() or not path.is_file():
        raise ExactWheelExecutionError("exact wheel receipt schema is unavailable")
    return path


def _validate_receipt(receipt: Mapping[str, Any], *, repository: Path) -> None:
    try:
        schema = json.loads(_schema_path(repository).read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(receipt), key=lambda error: list(error.path))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ExactWheelExecutionError("exact wheel receipt schema could not be loaded") from exc
    if errors:
        raise ExactWheelExecutionError("exact wheel receipt failed its strict schema")


def _path_digest(path: Path) -> str:
    return _sha256_bytes(str(path.resolve(strict=True)).encode("utf-8"))


def _record_sha256(receipt: Mapping[str, Any]) -> str:
    payload = dict(receipt)
    payload.pop("record_sha256", None)
    return _sha256_bytes(_canonical_json(payload))


def execute_exact_wheel(
    *,
    candidate_dir: Path,
    expected_wheel_sha256: str,
    expected_version: str,
    repository: Path,
    venv_path: Path,
    runner_source: Path | None = None,
) -> dict[str, Any]:
    """Install and probe one Candidate Full wheel without executing checkout source."""

    repository = _repository_root(repository)
    candidate_wheel = _candidate_wheel(
        candidate_dir,
        expected_wheel_sha256=expected_wheel_sha256,
        expected_version=expected_version,
    )
    requested_venv = Path(venv_path).expanduser()
    if requested_venv.exists() or requested_venv.is_symlink():
        raise ExactWheelExecutionError("isolated venv path must not already exist")
    try:
        resolved_venv = requested_venv.resolve(strict=False)
    except OSError as exc:
        raise ExactWheelExecutionError("isolated venv path could not be resolved") from exc
    if resolved_venv == repository or repository in resolved_venv.parents:
        raise ExactWheelExecutionError("isolated venv must be outside the repository")
    source = Path(runner_source) if runner_source is not None else Path(__file__)
    source = source.resolve(strict=True)
    runner_source_sha256 = _sha256_file(source)
    created = False
    try:
        python_executable = _create_venv(resolved_venv)
        created = True
        _install_wheel(
            python_executable=python_executable,
            wheel=candidate_wheel,
            venv_path=resolved_venv,
        )
        observation = _probe_runtime(
            python_executable=python_executable,
            venv_path=resolved_venv,
        )
        runtime = _validate_probe(
            observation,
            expected_version=expected_version,
            repository=repository,
            venv_path=resolved_venv,
            expected_python=python_executable,
        )
        console_entrypoint = _console_entrypoint(resolved_venv)
        try:
            console_relative = console_entrypoint.relative_to(
                resolved_venv.resolve(strict=True)
            )
        except ValueError as exc:
            raise ExactWheelExecutionError(
                "installed deeplaw console executable is outside the isolated venv"
            ) from exc
        site_packages = runtime["import_file"].resolve(strict=True)
        import_sha256 = _sha256_file(site_packages)
        entrypoint_module_sha256 = _sha256_file(runtime["entrypoint_file"])
        console_entrypoint_sha256 = _sha256_file(console_entrypoint)
        python_sha256 = _sha256_file(runtime["python_executable"])
        receipt: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "status": "exact_wheel_executed",
            "runner_source_sha256": runner_source_sha256,
            "candidate": {
                "wheel_filename": candidate_wheel.name,
                "wheel_sha256": _sha256_file(candidate_wheel),
                "wheel_size": candidate_wheel.stat().st_size,
                "path_class": "candidate_full_wheel",
            },
            "venv": {
                "path_class": "new_isolated_venv",
                "path_sha256": _path_digest(resolved_venv),
                "created_new": True,
                "system_site_packages": False,
                "site_packages_path_class": "venv_site_packages",
            },
            "runtime": {
                "python_implementation": runtime["python_implementation"],
                "python_version": runtime["python_version"],
                "python_executable_sha256": python_sha256,
                "python_executable_path_class": "new_isolated_venv",
                "distribution_name": PACKAGE_NAME,
                "distribution_version": expected_version,
                "import_module": PACKAGE_NAME,
                "import_file_path_class": "venv_site_packages",
                "import_file_relative_path": runtime["import_relative"].as_posix(),
                "import_file_sha256": import_sha256,
            },
            "entrypoint": {
                "name": PACKAGE_NAME,
                "group": "console_scripts",
                "value": runtime["entrypoint_value"],
                "executable_path_class": "venv_bin",
                "executable_relative_path": console_relative.as_posix(),
                "executable_sha256": console_entrypoint_sha256,
                "module_path_class": "venv_site_packages",
                "module_relative_path": runtime["entrypoint_relative"].as_posix(),
                "module_sha256": entrypoint_module_sha256,
            },
            "environment_policy": {
                "python_isolated_mode": True,
                "pythonpath_cleared": True,
                "pythonhome_cleared": True,
                "user_site_disabled": True,
                "network_disabled_for_install": True,
                "candidate_source_only": True,
            },
        }
        receipt["record_sha256"] = _record_sha256(receipt)
        _validate_receipt(receipt, repository=repository)
        return receipt
    except ExactWheelExecutionError:
        if created:
            shutil.rmtree(resolved_venv, ignore_errors=True)
        raise
    except (OSError, ValueError, TypeError) as exc:
        if created:
            shutil.rmtree(resolved_venv, ignore_errors=True)
        raise ExactWheelExecutionError("exact wheel execution failed") from exc


def run_exact_wheel(**kwargs: Any) -> dict[str, Any]:
    """Compatibility alias for callers using the runner as a library."""

    return execute_exact_wheel(**kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--expected-wheel-sha256", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    receipt = execute_exact_wheel(
        candidate_dir=args.candidate_dir,
        expected_wheel_sha256=args.expected_wheel_sha256,
        expected_version=args.expected_version,
        repository=args.repository,
        venv_path=args.venv,
    )
    if args.receipt.is_symlink() or args.receipt.exists():
        raise ExactWheelExecutionError("receipt output path must be new and non-symlink")
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_bytes(_canonical_json(receipt) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
