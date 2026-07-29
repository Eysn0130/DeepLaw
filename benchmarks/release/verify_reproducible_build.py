from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from benchmarks.release.evidence import (
    canonical_json,
    environment_manifest,
    repository_binding,
    sha256_bytes,
)

SCHEMA_VERSION = "deeplaw.reproducible-build-report/v2"
DEFAULT_SOURCE_DATE_EPOCH = 946684800
_REQUIRED_WHEEL_PATHS = (
    "deeplaw/security/openvex.json",
    "deeplaw/contracts/baseline-adapter-registry.v1.schema.json",
    "deeplaw/contracts/document-engine-actual-pdf-diagnostic.v1.schema.json",
    "deeplaw/contracts/installed-license-inventory.v1.schema.json",
    "deeplaw/contracts/knowledge-identity.v2.schema.json",
    "deeplaw/contracts/knowledge-inbox-artifact.v1.schema.json",
    "deeplaw/contracts/knowledge-ingest-job.v1.schema.json",
    "deeplaw/contracts/knowledge-ingest-job.v2.schema.json",
    "deeplaw/contracts/knowledge-lineage-review.v1.schema.json",
    "deeplaw/contracts/knowledge-projection.v2.schema.json",
    "deeplaw/contracts/source-ir.v1.schema.json",
    "deeplaw/contracts/knowledge-query-plan.v1.schema.json",
    "deeplaw/contracts/knowledge-retrieval-trace.v1.schema.json",
    "deeplaw/contracts/knowledge-review-transform.v1.schema.json",
    "deeplaw/contracts/knowledge-snapshot.v1.schema.json",
    "deeplaw/contracts/local-reranker-manifest.v1.schema.json",
    "deeplaw/contracts/reproducible-build-report.v1.schema.json",
    "deeplaw/contracts/relation-carry-forward.v1.schema.json",
    "deeplaw/contracts/retrieval-profile.v1.schema.json",
    "deeplaw/contracts/retrieval-fabric-scale-diagnostic.v1.schema.json",
    "deeplaw/contracts/retrieval-regression-suite.v1.schema.json",
    "deeplaw/contracts/skill-bundle.v1.schema.json",
    "deeplaw/contracts/source-snapshot.v1.schema.json",
    "deeplaw/contracts/typed-compiler-benchmark.v1.schema.json",
    "deeplaw/contracts/typed-compiler-benchmark-input.v1.schema.json",
)
_REQUIRED_SDIST_PATHS = (
    "benchmarks/baselines/collection_gate.py",
    "benchmarks/baselines/manual_adapter.py",
    "benchmarks/baselines/obsidian-workflow-v1.md",
    "benchmarks/baselines/official_adapter.py",
    "benchmarks/baselines/registry-v0.7.json",
    "benchmarks/hosts/codex-plugin-smoke-2026-07-28.json",
    "benchmarks/hosts/run_codex_plugin_smoke.py",
    "benchmarks/hosts/run_no_model_host_acceptance.py",
    "benchmarks/release/build-constraints.txt",
    "benchmarks/release/commercial_release.py",
    "benchmarks/release/evidence.py",
    "benchmarks/release/evaluator_candidate.py",
    "benchmarks/release/platform_gate.py",
    "benchmarks/release/post_release_verify.py",
    "benchmarks/release/run_distribution_lifecycle.py",
    "benchmarks/release/verify_oci.py",
    "benchmarks/release/verify_reproducible_build.py",
    "benchmarks/release/write_checksums.py",
    "docs/INSTALL_UPGRADE_ROLLBACK.md",
    "docs/RELEASE_NOTES_v0.7.0.md",
    "docs/RELEASE_NOTES_v0.9.0.md",
    "docs/V0_7_ACCEPTANCE_MATRIX.md",
    "docs/V0_9_ACCEPTANCE_MATRIX.md",
    "packaging/oci/Dockerfile",
    "pyproject.toml",
    "uv.lock",
)
_REQUIRED_BUILD_PACKAGES = frozenset(
    {"hatchling", "packaging", "pathspec", "pluggy", "trove-classifiers"}
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_name(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or "" in path.parts:
        raise RuntimeError(f"distribution contains an unsafe path: {value}")
    return path.as_posix()


def archive_inventory(path: Path) -> dict[str, Any]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = sorted(_safe_archive_name(item.filename) for item in archive.infolist())
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            names = sorted(_safe_archive_name(item.name) for item in archive.getmembers())
    else:
        raise RuntimeError(f"unsupported distribution artifact: {path.name}")
    if len(names) != len(set(names)):
        raise RuntimeError(f"distribution contains duplicate paths: {path.name}")
    if any(name.endswith((".pyc", ".pyo")) or "/__pycache__/" in name for name in names):
        raise RuntimeError(f"distribution contains generated Python bytecode: {path.name}")
    inventory_sha256 = hashlib.sha256(
        ("\n".join(names) + "\n").encode("utf-8")
    ).hexdigest()
    return {
        "path_count": len(names),
        "inventory_sha256": inventory_sha256,
        "paths": names,
    }


def _build(repository: Path, output: Path, *, source_date_epoch: int) -> list[Path]:
    environment = {
        **os.environ,
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": str(source_date_epoch),
        "TZ": "UTC",
    }
    process = subprocess.run(
        [
            "uv",
            "build",
            "--build-constraints",
            str(repository / "benchmarks" / "release" / "build-constraints.txt"),
            "--out-dir",
            str(output),
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if process.returncode != 0:
        raise RuntimeError(f"distribution build failed:\n{process.stdout}{process.stderr}")
    artifacts = sorted(
        (
            path
            for path in output.iterdir()
            if path.is_file()
            and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
        ),
        key=lambda path: path.name,
    )
    if len(artifacts) != 2 or {path.suffix for path in artifacts} != {".whl", ".gz"}:
        raise RuntimeError("release build must produce exactly one wheel and one sdist")
    return artifacts


def _required_wheel_paths(repository: Path) -> tuple[str, ...]:
    contract_root = repository / "contracts"
    contracts = tuple(
        f"deeplaw/contracts/{path.name}"
        for path in sorted(contract_root.glob("*.json"), key=lambda item: item.name)
        if path.is_file() and not path.is_symlink()
    )
    if not contracts:
        raise RuntimeError("release build has no repository contract inventory")
    return tuple(sorted({*_REQUIRED_WHEEL_PATHS, *contracts}))


def _verify_build_inputs(repository: Path) -> dict[str, Any]:
    constraints_path = repository / "benchmarks" / "release" / "build-constraints.txt"
    lock_path = repository / "uv.lock"
    pyproject_path = repository / "pyproject.toml"
    for path, field in (
        (constraints_path, "build constraints"),
        (lock_path, "dependency lock"),
        (pyproject_path, "project metadata"),
    ):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"exact release {field} is unavailable")
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    build_system = pyproject.get("build-system")
    if not isinstance(build_system, dict) or build_system != {
        "requires": ["hatchling==1.31.0"],
        "build-backend": "hatchling.build",
    }:
        raise RuntimeError("PEP 517 build backend does not match its exact closed contract")
    constraints: dict[str, dict[str, Any]] = {}
    current: str | None = None
    for raw_line in constraints_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not raw_line[:1].isspace():
            requirement = line.removesuffix("\\").strip()
            if "==" not in requirement or " " in requirement:
                raise RuntimeError("build constraint must use an exact package==version pin")
            name, version = requirement.split("==", maxsplit=1)
            if name in constraints or not name or not version:
                raise RuntimeError("build constraints contain a duplicate or invalid package")
            constraints[name] = {"version": version, "hashes": set()}
            current = name
            continue
        if current is None or not line.startswith("--hash=sha256:"):
            raise RuntimeError("build constraint continuation must contain a SHA-256 hash")
        digest = line.removesuffix("\\").removeprefix("--hash=sha256:").strip()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise RuntimeError("build constraint contains an invalid SHA-256 hash")
        constraints[current]["hashes"].add(f"sha256:{digest}")
    if set(constraints) != _REQUIRED_BUILD_PACKAGES:
        raise RuntimeError("build constraints do not match the closed build dependency set")
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise RuntimeError("dependency lock has no package inventory")
    locked: dict[str, dict[str, Any]] = {}
    for package in packages:
        if isinstance(package, dict) and package.get("name") in _REQUIRED_BUILD_PACKAGES:
            name = package["name"]
            if name in locked:
                raise RuntimeError(f"dependency lock has duplicate build package: {name}")
            locked[name] = package
    if set(locked) != _REQUIRED_BUILD_PACKAGES:
        raise RuntimeError("dependency lock is missing a constrained build package")
    versions: dict[str, str] = {}
    for name in sorted(_REQUIRED_BUILD_PACKAGES):
        package = locked[name]
        version = package.get("version")
        sdist = package.get("sdist")
        wheels = package.get("wheels")
        if (
            not isinstance(version, str)
            or version != constraints[name]["version"]
            or not isinstance(sdist, dict)
            or not isinstance(sdist.get("hash"), str)
            or not isinstance(wheels, list)
            or not wheels
            or any(
                not isinstance(item, dict) or not isinstance(item.get("hash"), str)
                for item in wheels
            )
        ):
            raise RuntimeError(f"locked build package does not match its constraint: {name}")
        locked_hashes = {sdist["hash"], *(item["hash"] for item in wheels)}
        if constraints[name]["hashes"] != locked_hashes:
            raise RuntimeError(f"build constraint hashes differ from the lock: {name}")
        versions[name] = version
    return {
        "build_constraints_sha256": _sha256(constraints_path),
        "lock_sha256": _sha256(lock_path),
        "build_dependencies": versions,
    }


def _prepare_artifact_directory(path: Path) -> Path:
    selected = path.expanduser().absolute()
    if selected.is_symlink() or (selected.exists() and not selected.is_dir()):
        raise RuntimeError("verified artifact output must be a regular directory")
    selected.mkdir(parents=True, exist_ok=True)
    if any(selected.iterdir()):
        raise RuntimeError("verified artifact output directory must be empty")
    return selected


def _publish_verified_artifacts(
    artifacts: dict[str, Path],
    output: Path,
) -> None:
    for name, source in sorted(artifacts.items()):
        destination = output / name
        if destination.exists() or destination.is_symlink():
            raise RuntimeError(f"verified artifact destination is not new: {name}")
        with source.open("rb") as source_stream, destination.open("xb") as destination_stream:
            shutil.copyfileobj(source_stream, destination_stream, length=1024 * 1024)
            destination_stream.flush()
            os.fsync(destination_stream.fileno())
        if (
            _sha256(destination) != _sha256(source)
            or destination.read_bytes() != source.read_bytes()
        ):
            raise RuntimeError(f"published artifact differs from verified bytes: {name}")


def verify(
    repository: Path,
    *,
    source_date_epoch: int,
    artifact_directory: Path | None = None,
) -> dict[str, Any]:
    binding = repository_binding(repository)
    required_wheel_paths = _required_wheel_paths(repository)
    build_inputs = _verify_build_inputs(repository)
    publish_to = (
        _prepare_artifact_directory(artifact_directory)
        if artifact_directory is not None
        else None
    )
    with tempfile.TemporaryDirectory(prefix="deeplaw-reproducible-build-") as temporary:
        temporary_root = Path(temporary)
        first = _build(repository, temporary_root / "first", source_date_epoch=source_date_epoch)
        second = _build(repository, temporary_root / "second", source_date_epoch=source_date_epoch)
        first_by_name = {path.name: path for path in first}
        second_by_name = {path.name: path for path in second}
        if set(first_by_name) != set(second_by_name):
            raise RuntimeError("repeated builds produced different artifact names")
        artifacts: list[dict[str, Any]] = []
        for name in sorted(first_by_name):
            first_path = first_by_name[name]
            second_path = second_by_name[name]
            first_sha = _sha256(first_path)
            second_sha = _sha256(second_path)
            if first_sha != second_sha or first_path.read_bytes() != second_path.read_bytes():
                raise RuntimeError(f"artifact is not byte-for-byte reproducible: {name}")
            inventory = archive_inventory(first_path)
            if name.endswith(".whl"):
                missing = [
                    item for item in required_wheel_paths if item not in inventory["paths"]
                ]
                if missing:
                    raise RuntimeError(
                        "wheel package inventory is missing required files: " + ", ".join(missing)
                    )
            else:
                roots = {PurePosixPath(item).parts[0] for item in inventory["paths"]}
                if len(roots) != 1:
                    raise RuntimeError("sdist package inventory has multiple archive roots")
                root = next(iter(roots))
                missing = [
                    item
                    for item in _REQUIRED_SDIST_PATHS
                    if f"{root}/{item}" not in inventory["paths"]
                ]
                if missing:
                    raise RuntimeError(
                        "sdist package inventory is missing required files: "
                        + ", ".join(missing)
                    )
            artifacts.append(
                {
                    "name": name,
                    "sha256": first_sha,
                    "byte_size": first_path.stat().st_size,
                    "path_count": inventory["path_count"],
                    "inventory_sha256": inventory["inventory_sha256"],
                }
            )
        if publish_to is not None:
            _publish_verified_artifacts(first_by_name, publish_to)
    dirty = not binding["worktree_clean"]
    report = {
        "schema_version": SCHEMA_VERSION,
        "binding": binding,
        "environment": environment_manifest(),
        "repository_commit": binding["commit"],
        "working_tree_dirty": dirty,
        "source_date_epoch": source_date_epoch,
        **build_inputs,
        "reproducible": True,
        "package_inventory_verified": True,
        "artifacts": artifacts,
        "artifact_release_eligible": not dirty,
        "artifact_release_blockers": ["working_tree_not_frozen"] if dirty else [],
    }
    report["record_sha256"] = sha256_bytes(canonical_json(report).encode("utf-8"))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build DeepLaw twice and verify byte-identical wheel/sdist artifacts."
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--source-date-epoch", type=int, default=DEFAULT_SOURCE_DATE_EPOCH)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        help="Publish the verified bytes into a new or empty directory.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.source_date_epoch < 315532800:
        raise SystemExit("SOURCE_DATE_EPOCH must be compatible with ZIP timestamps")
    try:
        report = verify(
            args.repository.resolve(),
            source_date_epoch=args.source_date_epoch,
            artifact_directory=(
                args.artifact_dir.expanduser().absolute()
                if args.artifact_dir is not None
                else None
            ),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
