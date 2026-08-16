from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release.exact_wheel_runner import (
    ExactWheelExecutionError,
    _validate_probe,
    execute_exact_wheel,
)

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPOSITORY / "contracts/exact-wheel-execution-receipt.v1.schema.json"
EXPECTED_VERSION = "0.12.0"


def _fake_wheel(candidate_dir: Path, *, name: str = "deeplaw-0.12.0-py3-none-any.whl") -> Path:
    candidate_dir.mkdir(parents=True, exist_ok=True)
    wheel = candidate_dir / name
    version = name.split("-")[1]
    dist_info = f"deeplaw-{version}.dist-info"
    files = {
        "deeplaw/__init__.py": f"__version__ = {version!r}\n",
        "deeplaw/cli.py": "def main():\n    return 0\n",
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: deeplaw\n"
            f"Version: {version}\n"
        ),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: pass24-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ),
        f"{dist_info}/entry_points.txt": (
            "[console_scripts]\n"
            "deeplaw = deeplaw.cli:main\n"
        ),
        f"{dist_info}/RECORD": "",
    }
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, payload in sorted(files.items()):
            archive.writestr(path, payload)
    return wheel


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_unique_wheel_is_installed_and_receipt_is_path_free(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    venv_path = tmp_path / "isolated-venv"

    receipt = execute_exact_wheel(
        candidate_dir=candidate_dir,
        expected_wheel_sha256=_sha256(wheel),
        expected_version=EXPECTED_VERSION,
        repository=REPOSITORY,
        venv_path=venv_path,
    )

    assert receipt["status"] == "exact_wheel_executed"
    assert receipt["candidate"] == {
        "wheel_filename": wheel.name,
        "wheel_sha256": _sha256(wheel),
        "wheel_size": wheel.stat().st_size,
        "path_class": "candidate_full_wheel",
    }
    assert receipt["runtime"]["distribution_version"] == EXPECTED_VERSION
    assert receipt["runtime"]["import_file_path_class"] == "venv_site_packages"
    assert receipt["entrypoint"]["value"] == "deeplaw.cli:main"
    assert receipt["entrypoint"]["executable_path_class"] == "venv_bin"
    assert receipt["entrypoint"]["executable_relative_path"].endswith("deeplaw")
    assert receipt["entrypoint"]["module_path_class"] == "venv_site_packages"
    assert receipt["entrypoint"]["module_relative_path"] == "deeplaw/cli.py"
    assert receipt["environment_policy"] == {
        "python_isolated_mode": True,
        "pythonpath_cleared": True,
        "pythonhome_cleared": True,
        "user_site_disabled": True,
        "network_disabled_for_install": True,
        "candidate_source_only": True,
    }
    assert str(tmp_path) not in json.dumps(receipt, sort_keys=True)
    assert "junit" not in receipt
    assert "platform" not in receipt
    assert "scorer" not in receipt
    record = dict(receipt)
    record.pop("record_sha256")
    canonical = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert receipt["record_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert list(_schema_validator().iter_errors(receipt)) == []


def test_candidate_directory_requires_one_regular_wheel(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    _fake_wheel(candidate_dir, name="deeplaw-0.12.0-py3-none-manylinux.whl")

    with pytest.raises(ExactWheelExecutionError, match="exactly one wheel"):
        execute_exact_wheel(
            candidate_dir=candidate_dir,
            expected_wheel_sha256=_sha256(wheel),
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=tmp_path / "isolated-venv",
        )


def test_candidate_wheel_hash_mismatch_fails_before_venv_creation(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    venv_path = tmp_path / "isolated-venv"

    with pytest.raises(ExactWheelExecutionError, match="hash mismatch"):
        execute_exact_wheel(
            candidate_dir=candidate_dir,
            expected_wheel_sha256="f" * 64,
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=venv_path,
        )
    assert not venv_path.exists()
    assert wheel.is_file()


def test_venv_inside_checkout_is_rejected(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    inside_repository = REPOSITORY / ".pass24-exact-wheel-venv"

    with pytest.raises(ExactWheelExecutionError, match="outside the repository"):
        execute_exact_wheel(
            candidate_dir=candidate_dir,
            expected_wheel_sha256=_sha256(wheel),
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=inside_repository,
        )
    assert not inside_repository.exists()


def _probe(
    *,
    venv_path: Path,
    site_packages: Path,
    import_file: Path,
    entrypoint_file: Path,
    python_executable: Path,
) -> dict[str, Any]:
    return {
        "python_implementation": "cpython",
        "python_version": "3.11.0",
        "python_executable": str(python_executable),
        "site_packages": [str(site_packages)],
        "import_file": str(import_file),
        "distribution_name": "deeplaw",
        "distribution_version": EXPECTED_VERSION,
        "entrypoint_name": "deeplaw",
        "entrypoint_group": "console_scripts",
        "entrypoint_value": "deeplaw.cli:main",
        "entrypoint_file": str(entrypoint_file),
    }


def test_checkout_source_import_origin_is_rejected(tmp_path: Path) -> None:
    venv_path = tmp_path / "isolated-venv"
    site_packages = venv_path / "lib" / "python3.11" / "site-packages"
    site_packages.mkdir(parents=True)
    entrypoint_file = site_packages / "deeplaw" / "cli.py"
    entrypoint_file.parent.mkdir()
    entrypoint_file.write_text("def main(): return 0\n", encoding="utf-8")
    python_executable = venv_path / "bin" / "python"
    python_executable.parent.mkdir()
    python_executable.write_bytes(b"python")
    observation = _probe(
        venv_path=venv_path,
        site_packages=site_packages,
        import_file=REPOSITORY / "src/deeplaw/__init__.py",
        entrypoint_file=entrypoint_file,
        python_executable=python_executable,
    )

    with pytest.raises(ExactWheelExecutionError, match="checkout source"):
        _validate_probe(
            observation,
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=venv_path,
            expected_python=python_executable,
        )
