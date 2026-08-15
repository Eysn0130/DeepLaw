"""Create and verify a path-free manifest for retained candidate distributions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "deeplaw.retained-candidate-artifacts/v1"


class SingleArtifactError(RuntimeError):
    """Raised when a consumer does not use the reproducibly verified bytes."""


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("retained artifact manifest contains a duplicate key")
        value[key] = item
    return value


def _strict_json_loads(payload: str) -> Any:
    return json.loads(payload, object_pairs_hook=_strict_json_object)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "filename": path.name,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


def _package_version(repository: Path) -> str:
    value = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    version = value.get("project", {}).get("version")
    if not isinstance(version, str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise RuntimeError("package version is not a bounded pure semver")
    return version


def _distribution_files(directory: Path) -> dict[str, Path]:
    selected = directory.expanduser().resolve(strict=True)
    files = {
        path.name: path
        for path in selected.iterdir()
        if path.is_file()
        and not path.is_symlink()
        and (path.name.endswith(".whl") or path.name.endswith(".tar.gz"))
    }
    wheels = [name for name in files if name.endswith(".whl")]
    sdists = [name for name in files if name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1 or len(files) != 2:
        raise SingleArtifactError("expected exactly one verified wheel and sdist")
    return files


def verify_single_artifact_source(
    *,
    verified_dist: Path,
    consumer_dist: Path,
) -> dict[str, Any]:
    """Require one consumer directory to contain the exact verified artifacts."""

    verified = _distribution_files(verified_dist)
    consumer = _distribution_files(consumer_dist)
    if set(verified) != set(consumer):
        raise SingleArtifactError("consumer artifact names differ from verified artifacts")
    artifacts: list[dict[str, Any]] = []
    for name in sorted(verified):
        expected = verified[name]
        observed = consumer[name]
        expected_sha256 = _sha256(expected)
        if (
            expected_sha256 != _sha256(observed)
            or expected.stat().st_size != observed.stat().st_size
        ):
            raise SingleArtifactError(f"verified artifact hash differs for {name}")
        artifacts.append(
            {
                "filename": name,
                "sha256": expected_sha256,
                "bytes": expected.stat().st_size,
            }
        )
    return {
        "schema_version": "deeplaw.single-verified-artifact-source/v1",
        "status": "verified",
        "artifacts": artifacts,
    }


def build_manifest(*, repository: Path, dist: Path) -> dict[str, Any]:
    repository = repository.resolve(strict=True)
    dist = dist.resolve(strict=True)
    if _git(
        repository,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        ".",
        ":(exclude)dist/**",
    ):
        raise RuntimeError("retained candidate source tree must be clean")
    version = _package_version(repository)
    wheels = sorted(dist.glob(f"deeplaw-{version}-*.whl"))
    sdists = sorted(dist.glob(f"deeplaw-{version}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError(f"expected exactly one DeepLaw {version} wheel and sdist")
    return {
        "schema_version": SCHEMA_VERSION,
        "package_version": version,
        "release_ready": False,
        "claim_eligible": False,
        "git_commit": _git(repository, "rev-parse", "HEAD"),
        "git_tree": _git(repository, "rev-parse", "HEAD^{tree}"),
        "lock_sha256": _sha256(repository / "uv.lock"),
        "wheel": _artifact(wheels[0]),
        "sdist": _artifact(sdists[0]),
    }


def verify_manifest(
    *, repository: Path, dist: Path, manifest_path: Path
) -> dict[str, Any]:
    manifest = _strict_json_loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RuntimeError("retained artifact manifest must be an object")
    expected = build_manifest(repository=repository, dist=dist)
    if manifest != expected:
        raise RuntimeError("retained candidate identity or artifact hashes do not match")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.verify:
        verify_manifest(
            repository=args.repository,
            dist=args.dist,
            manifest_path=args.output,
        )
    else:
        manifest = build_manifest(repository=args.repository, dist=args.dist)
        args.output.write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
