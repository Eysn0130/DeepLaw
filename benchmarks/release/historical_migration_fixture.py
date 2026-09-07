"""Build and verify the pinned historical v0.6.0 migration fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Sequence
from email import policy
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any

PACKAGE_NAME = "deeplaw"
HISTORICAL_VERSION = "0.6.0"
SOURCE_COMMIT = "e0f1fe3ff01d3026df12673d57c69014c2c4dca4"
SOURCE_TREE = "ec2f85eb037a24e612ff62fb12b42c8061b617dd"
SOURCE_DATE_EPOCH = 1785157311
BUILD_CONSTRAINTS_RELATIVE_PATH = "benchmarks/release/build-constraints.txt"
BUILD_CONSTRAINTS_SHA256 = "0f03fb3f3925513d568a106da29a75e843a9a65923bdab7fad392cf22f0cdd1c"
PROVENANCE_FILENAME = "deeplaw-v060-fixture.json"
PROVENANCE_SCHEMA = "deeplaw.historical-migration-fixture/v1"
WHEEL_ENV_NAME = "DEEPLAW_V060_WHEEL"
WHEEL_SHA_ENV_NAME = "DEEPLAW_V060_WHEEL_SHA256"
FIXTURE_VERIFIED_ENV_NAME = "DEEPLAW_V060_FIXTURE_VERIFIED"

_WHEEL_PREFIX = f"{PACKAGE_NAME}-{HISTORICAL_VERSION}-"
_WHEEL_SUFFIX = ".whl"


class FixtureError(RuntimeError):
    """Raised when a historical fixture is missing or inconsistent."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path: Path, *, label: str) -> Path:
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise FixtureError(f"{label} is unavailable") from error
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise FixtureError(f"{label} must be a regular non-symlink file")
    return path


def _directory(path: Path, *, label: str, empty: bool = False) -> Path:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        if not empty:
            raise FixtureError(f"{label} is unavailable") from None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()
        return path.resolve(strict=True)
    except OSError as error:
        raise FixtureError(f"{label} is unavailable") from error
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise FixtureError(f"{label} must be a regular non-symlink directory")
    resolved = path.resolve(strict=True)
    if resolved == Path(resolved.anchor):
        raise FixtureError(f"{label} is too broad")
    if empty and any(resolved.iterdir()):
        raise FixtureError(f"{label} must be empty before a fixture is built")
    return resolved


def _git(repository: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=False,
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise FixtureError(f"git {' '.join(arguments)} failed{': ' + detail if detail else ''}")
    return result.stdout.strip()


def _verify_source(repository: Path) -> dict[str, Any]:
    repository = _directory(repository, label="repository")
    commit = _git(repository, "rev-parse", "--verify", f"{SOURCE_COMMIT}^{{commit}}").decode()
    if commit != SOURCE_COMMIT:
        raise FixtureError("historical source commit does not match the pinned commit")
    tree = _git(repository, "rev-parse", "--verify", f"{SOURCE_COMMIT}^{{tree}}").decode()
    if tree != SOURCE_TREE:
        raise FixtureError("historical source tree does not match the pinned tree")
    try:
        project = tomllib.loads(
            _git(repository, "show", f"{SOURCE_COMMIT}:pyproject.toml").decode("utf-8")
        )
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise FixtureError("historical pyproject.toml is invalid") from error
    metadata = project.get("project")
    if not isinstance(metadata, dict) or metadata.get("name") != PACKAGE_NAME:
        raise FixtureError("historical source package name is not deeplaw")
    if metadata.get("version") != HISTORICAL_VERSION:
        raise FixtureError("historical source version is not 0.6.0")
    constraints = _regular(repository / BUILD_CONSTRAINTS_RELATIVE_PATH, label="build constraints")
    if _file_sha256(constraints) != BUILD_CONSTRAINTS_SHA256:
        raise FixtureError("build constraints are not the pinned backend constraints")
    return {
        "commit": SOURCE_COMMIT,
        "tree": SOURCE_TREE,
        "version": HISTORICAL_VERSION,
        "source_date_epoch": SOURCE_DATE_EPOCH,
    }


def _safe_member(name: str) -> PurePosixPath:
    normalized = name[:-1] if name.endswith("/") else name
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != normalized
    ):
        raise FixtureError("historical archive contains an unsafe path")
    return path


def _archive_source(repository: Path, destination: Path) -> Path:
    archive_path = destination / "source.tar"
    with archive_path.open("wb") as stream:
        result = subprocess.run(
            ["git", "archive", "--format=tar", "--prefix=source/", SOURCE_COMMIT],
            cwd=repository,
            check=False,
            stdout=stream,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise FixtureError(f"git archive failed{': ' + detail if detail else ''}")
    with tarfile.open(archive_path, mode="r:") as archive:
        members = archive.getmembers()
        for member in members:
            member_path = _safe_member(member.name)
            target = (destination / Path(*member_path.parts)).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise FixtureError("historical archive escapes its isolated directory")
            if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                raise FixtureError("historical archive contains an unsafe entry")
        archive.extractall(destination)
    source = destination / "source"
    if not source.is_dir() or source.is_symlink():
        raise FixtureError("historical archive did not produce an isolated source")
    return source


def _run_uv_build(source: Path, output: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment.update(
        PYTHONHASHSEED="0", SOURCE_DATE_EPOCH=str(SOURCE_DATE_EPOCH), TZ="UTC"
    )
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            "uv",
            "build",
            "--project",
            str(source),
            "--build-constraints",
            str(repository / BUILD_CONSTRAINTS_RELATIVE_PATH),
            "--wheel",
            "--out-dir",
            str(output),
        ],
        cwd=source,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=300,
    )
    if result.returncode:
        raise FixtureError(f"historical wheel build failed:\n{result.stdout}{result.stderr}")


def _inspect_wheel(path: Path) -> dict[str, Any]:
    _regular(path, label="historical wheel")
    if not path.name.startswith(_WHEEL_PREFIX) or not path.name.endswith(_WHEEL_SUFFIX):
        raise FixtureError("historical wheel name does not identify deeplaw 0.6.0")
    if len(path.name[len(_WHEEL_PREFIX) : -len(_WHEEL_SUFFIX)].split("-")) != 3:
        raise FixtureError("historical wheel filename tags are invalid")
    dist_info = f"{PACKAGE_NAME}-{HISTORICAL_VERSION}.dist-info"
    metadata_path = f"{dist_info}/METADATA"
    record_path = f"{dist_info}/RECORD"
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise FixtureError("historical wheel contains duplicate paths")
            for info in archive.infolist():
                _safe_member(info.filename)
                if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                    raise FixtureError("historical wheel contains a symlink entry")
            if metadata_path not in names or record_path not in names:
                raise FixtureError("historical wheel is missing METADATA or RECORD")
            message = Parser(policy=policy.default).parsestr(
                archive.read(metadata_path).decode("utf-8")
            )
            if message.get("Name", "").casefold() != PACKAGE_NAME:
                raise FixtureError("historical wheel metadata has the wrong package name")
            if message.get("Version") != HISTORICAL_VERSION:
                raise FixtureError("historical wheel metadata has the wrong version")
            record = archive.read(record_path)
            if not record or record_path.encode("utf-8") not in record:
                raise FixtureError("historical wheel RECORD is missing its self-entry")
    except (UnicodeDecodeError, zipfile.BadZipFile) as error:
        raise FixtureError("historical wheel is not a valid UTF-8 zip archive") from error
    size = path.stat().st_size
    return {
        "name": path.name,
        "size": size,
        "byte_size": size,
        "sha256": _file_sha256(path),
        "record_sha256": _sha256(record),
    }


def _provenance_body(source: dict[str, Any], wheel: dict[str, Any]) -> dict[str, Any]:
    source_record = {key: source[key] for key in ("commit", "tree", "version")}
    source_identity = _sha256(_canonical(source_record).encode("utf-8"))
    build = {
        "method": "git-archive-isolated-source",
        "builder": "uv build",
        "arguments": ["--wheel"],
        "constraints": BUILD_CONSTRAINTS_RELATIVE_PATH,
        "constraints_sha256": BUILD_CONSTRAINTS_SHA256,
        "source_date_epoch": source["source_date_epoch"],
    }
    build_identity = _sha256(
        _canonical({"source_identity": source_identity, "build": build}).encode("utf-8")
    )
    return {
        "schema_version": PROVENANCE_SCHEMA,
        "package": PACKAGE_NAME,
        "version": HISTORICAL_VERSION,
        "source": source_record,
        "source_identity": source_identity,
        "build": {**build, "identity": build_identity},
        "wheel": wheel,
    }


def _with_digest(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "record_sha256": _sha256(_canonical(body).encode("utf-8"))}


def _write_provenance(directory: Path, body: dict[str, Any]) -> None:
    (directory / PROVENANCE_FILENAME).write_text(
        json.dumps(_with_digest(body), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _built_wheel(output: Path) -> Path:
    entries = list(output.iterdir())
    marker = output / ".gitignore"
    if marker in entries:
        _regular(marker, label="uv build marker")
        if marker.read_bytes() != b"*":
            raise FixtureError("uv build marker is unexpected")
        marker.unlink()
        entries = list(output.iterdir())
    wheels = [item for item in entries if item.name.endswith(_WHEEL_SUFFIX)]
    if len(wheels) != 1 or len(entries) != 1:
        raise FixtureError("historical wheel build must produce exactly one wheel")
    return wheels[0]


def build_fixture(repository: Path, output_dir: Path) -> dict[str, Any]:
    """Build one source-bound historical wheel in an empty output directory."""

    source = _verify_source(repository)
    output = _directory(output_dir, label="fixture output directory", empty=True)
    with tempfile.TemporaryDirectory(prefix="deeplaw-v060-build-") as temporary:
        temporary_root = Path(temporary)
        isolated_source = _archive_source(repository.resolve(strict=True), temporary_root)
        build_output = temporary_root / "dist"
        build_output.mkdir()
        _run_uv_build(isolated_source, build_output)
        built_wheel = _built_wheel(build_output)
        wheel = output / built_wheel.name
        shutil.copyfile(built_wheel, wheel)
    body = _provenance_body(source, _inspect_wheel(wheel))
    _write_provenance(output, body)
    return {**body, "fixture_dir": str(output), "wheel_path": str(wheel.resolve(strict=True))}


def _load_provenance(path: Path) -> dict[str, Any]:
    _regular(path, label="fixture provenance")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FixtureError("fixture provenance is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise FixtureError("fixture provenance must be a JSON object")
    expected = {
        "schema_version",
        "package",
        "version",
        "source",
        "source_identity",
        "build",
        "wheel",
        "record_sha256",
    }
    if set(payload) != expected:
        raise FixtureError("fixture provenance has missing or extra fields")
    digest = payload["record_sha256"]
    body = {key: value for key, value in payload.items() if key != "record_sha256"}
    if not isinstance(digest, str) or len(digest) != 64 or any(
        c not in "0123456789abcdef" for c in digest
    ):
        raise FixtureError("fixture provenance record digest is malformed")
    if digest != _sha256(_canonical(body).encode("utf-8")):
        raise FixtureError("fixture provenance record digest is invalid")
    return payload


def verify_fixture(fixture_dir: Path) -> dict[str, Any]:
    """Verify one closed fixture directory and return its validated record."""

    directory = _directory(fixture_dir, label="fixture directory")
    entries = list(directory.iterdir())
    provenance = _load_provenance(directory / PROVENANCE_FILENAME)
    wheels = [item for item in entries if item.name.endswith(_WHEEL_SUFFIX)]
    if len(wheels) != 1:
        raise FixtureError("fixture must contain exactly one wheel")
    wheel_path = wheels[0]
    if any(item.name not in {PROVENANCE_FILENAME, wheel_path.name} for item in entries):
        raise FixtureError("fixture contains an unexpected file")
    wheel = _inspect_wheel(wheel_path)
    source = {"commit": SOURCE_COMMIT, "tree": SOURCE_TREE, "version": HISTORICAL_VERSION}
    if provenance.get("schema_version") != PROVENANCE_SCHEMA:
        raise FixtureError("fixture provenance schema is unsupported")
    if provenance.get("package") != PACKAGE_NAME or provenance.get("version") != HISTORICAL_VERSION:
        raise FixtureError("fixture provenance package or version is wrong")
    if provenance.get("source") != source:
        raise FixtureError("fixture provenance source commit, tree, or version is wrong")
    expected = _provenance_body({**source, "source_date_epoch": SOURCE_DATE_EPOCH}, wheel)
    if provenance.get("source_identity") != expected["source_identity"]:
        raise FixtureError("fixture provenance source identity is wrong")
    if provenance.get("build") != expected["build"]:
        raise FixtureError("fixture provenance build identity is wrong")
    if provenance.get("wheel") != wheel:
        raise FixtureError("fixture provenance wheel name, size, digest, or RECORD is wrong")
    return {
        **provenance,
        "fixture_dir": str(directory),
        "wheel_path": str(wheel_path.resolve(strict=True)),
    }


def _append_github_env(path: Path, result: dict[str, Any]) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = None
    except OSError as error:
        raise FixtureError("GitHub environment file is unavailable") from error
    if mode is not None and (stat.S_ISLNK(mode) or not stat.S_ISREG(mode)):
        raise FixtureError("GitHub environment file must be a regular non-symlink file")
    wheel_path = result["wheel_path"]
    if any(c in str(wheel_path) for c in ("\r", "\n", "\0")):
        raise FixtureError("historical wheel path cannot be written to GitHub environment")
    values = (
        (WHEEL_ENV_NAME, wheel_path),
        (WHEEL_SHA_ENV_NAME, result["wheel"]["sha256"]),
        (FIXTURE_VERIFIED_ENV_NAME, "true"),
    )
    try:
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n")
            stream.writelines(f"{key}={value}\n" for key, value in values)
    except OSError as error:
        raise FixtureError("GitHub environment file could not be updated") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--repository", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--fixture-dir", type=Path, required=True)
    verify.add_argument("--github-env", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            build_fixture(args.repository, args.output_dir)
        else:
            result = verify_fixture(args.fixture_dir)
            if args.github_env is not None:
                _append_github_env(args.github_env, result)
    except (FixtureError, OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
