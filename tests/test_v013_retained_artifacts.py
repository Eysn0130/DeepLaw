from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from benchmarks.release.retained_artifact_manifest import (
    build_manifest,
    verify_manifest,
)
from deeplaw.util import canonical_json


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "uv.lock"], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=DeepLaw Test",
            "-c",
            "user.email=deeplaw@example.invalid",
            "commit",
            "-q",
            "-m",
            "candidate",
        ],
        cwd=repository,
        check=True,
    )
    return repository


def test_retained_manifest_binds_candidate_tree_lock_wheel_and_sdist(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "deeplaw-0.12.0-py3-none-any.whl"
    sdist = dist / "deeplaw-0.12.0.tar.gz"
    wheel.write_bytes(b"exact wheel bytes")
    sdist.write_bytes(b"exact sdist bytes")
    manifest = build_manifest(repository=repository, dist=dist)
    manifest_path = dist / "retained-artifact-manifest.json"
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")

    assert verify_manifest(
        repository=repository,
        dist=dist,
        manifest_path=manifest_path,
    ) == manifest
    assert manifest["package_version"] == "0.12.0"
    assert manifest["release_ready"] is False
    assert manifest["claim_eligible"] is False
    assert set(manifest["wheel"]) == {"filename", "sha256", "bytes"}
    assert set(manifest["sdist"]) == {"filename", "sha256", "bytes"}
    assert all("/" not in item["filename"] for item in (manifest["wheel"], manifest["sdist"]))

    wheel.write_bytes(b"changed wheel bytes")
    with pytest.raises(RuntimeError, match="hashes do not match"):
        verify_manifest(
            repository=repository,
            dist=dist,
            manifest_path=manifest_path,
        )
