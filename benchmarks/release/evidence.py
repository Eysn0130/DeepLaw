from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path, *, logical_name: str | None = None) -> dict[str, Any]:
    selected = path.resolve(strict=True)
    if selected.is_symlink() or not selected.is_file():
        raise RuntimeError(f"release evidence is not a regular file: {path}")
    return {
        "logical_name": logical_name or selected.name,
        "sha256": sha256_file(selected),
        "byte_size": selected.stat().st_size,
    }


def _git(repository: Path, *arguments: str) -> str:
    process = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if process.returncode != 0:
        raise RuntimeError(f"git {' '.join(arguments)} failed: {process.stderr.strip()}")
    return process.stdout.strip()


def contracts_binding(repository: Path) -> dict[str, Any]:
    contracts_root = repository / "contracts"
    records = [
        {
            "path": path.relative_to(repository).as_posix(),
            "sha256": sha256_file(path),
            "byte_size": path.stat().st_size,
        }
        for path in sorted(contracts_root.glob("*.json"), key=lambda item: item.name)
        if path.is_file() and not path.is_symlink()
    ]
    if not records:
        raise RuntimeError("contract inventory is empty")
    return {
        "count": len(records),
        "inventory_sha256": sha256_bytes(canonical_json(records).encode("utf-8")),
        "files": records,
    }


def repository_binding(repository: Path) -> dict[str, Any]:
    selected = repository.resolve(strict=True)
    project = tomllib.loads((selected / "pyproject.toml").read_text(encoding="utf-8"))
    version = project.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise RuntimeError("project version is unavailable")
    status = _git(selected, "status", "--porcelain=v1", "--untracked-files=all")
    return {
        "commit": _git(selected, "rev-parse", "HEAD"),
        "tree": _git(selected, "rev-parse", "HEAD^{tree}"),
        "worktree_clean": not bool(status),
        "package_version": version,
        "lock_sha256": sha256_file(selected / "uv.lock"),
        "pyproject_sha256": sha256_file(selected / "pyproject.toml"),
        "contracts": contracts_binding(selected),
    }


def environment_manifest(*, uv_executable: str = "uv") -> dict[str, Any]:
    uv = subprocess.run(
        [uv_executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if uv.returncode != 0:
        raise RuntimeError("uv version is unavailable")
    return {
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "machine": platform.machine(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_executable_name": Path(sys.executable).name,
        "uv_version": uv.stdout.strip(),
        "ci": os.environ.get("CI", "").lower() == "true",
        "github_actions": os.environ.get("GITHUB_ACTIONS", "").lower() == "true",
        "github_runner_os": os.environ.get("RUNNER_OS"),
        "github_runner_arch": os.environ.get("RUNNER_ARCH"),
    }


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid JSON evidence {path}: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON evidence must be an object: {path}")
    return payload


def write_report(path: Path, report: dict[str, Any]) -> None:
    body = {key: value for key, value in report.items() if key != "record_sha256"}
    body["record_sha256"] = sha256_bytes(canonical_json(body).encode("utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_record_digest(report: dict[str, Any], *, field: str) -> None:
    body = {key: value for key, value in report.items() if key != "record_sha256"}
    expected = sha256_bytes(canonical_json(body).encode("utf-8"))
    if report.get("record_sha256") != expected:
        raise RuntimeError(f"{field} record digest is invalid")
