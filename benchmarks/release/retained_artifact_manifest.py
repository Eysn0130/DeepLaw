"""Create and verify a path-free manifest for retained candidate distributions."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path
from typing import Any

from deeplaw.util import canonical_json, strict_json_loads

SCHEMA_VERSION = "deeplaw.retained-candidate-artifacts/v1"


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
    wheels = sorted(dist.glob("deeplaw-0.12.0-*.whl"))
    sdists = sorted(dist.glob("deeplaw-0.12.0.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError("expected exactly one DeepLaw 0.12.0 wheel and sdist")
    return {
        "schema_version": SCHEMA_VERSION,
        "package_version": "0.12.0",
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
    manifest = strict_json_loads(manifest_path.read_text(encoding="utf-8"))
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
        args.output.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
