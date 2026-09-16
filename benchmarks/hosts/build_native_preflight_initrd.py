"""Build a bounded, zero-model native preflight initrd.

This module only assembles owner-supplied bytes.  It does not install
packages, invoke a model, start a VM, or establish a formal Host admission.
The base initrd is kept as the first byte sequence in the output and a
deterministic gzip-compressed ``newc`` archive is appended to it.

The manifest is deliberately closed.  The paths for the repository modules
are registry keys rather than free-form inputs; their source bytes are read
from the repository root and their archive destinations are fixed below.
Tests may replace ``MODULE_REGISTRY`` with a small mapping, but the public CLI
always uses the production registry in this file.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from .native_slot_owner_preflight import _freeze

SCHEMA: Final[str] = "deeplaw.native-preflight-build/v1"
PURPOSE: Final[str] = "zero_model_preflight"
FORK_PURPOSE: Final[str] = "zero_model_fork_preflight"
MAX_MANIFEST_BYTES: Final[int] = 64 * 1024
MAX_FILE_BYTES: Final[int] = 512 * 1024 * 1024
MAX_TOTAL_INPUT_BYTES: Final[int] = 768 * 1024 * 1024
MAX_WHEELS: Final[int] = 64
MAX_PATH_CHARS: Final[int] = 4096
MAX_MODULE_KEY_CHARS: Final[int] = 512
MAX_IDENTIFIER_CHARS: Final[int] = 64
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}"
)
_WHEEL_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._+\-]{0,254}\.whl"
)
_SCHEMA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "purpose",
        "run_id",
        "candidate_id",
        "base_initrd",
        "opencode",
        "wheels",
        "modules",
    }
)
_FILE_KEYS: Final[frozenset[str]] = frozenset({"path", "sha256"})

# This is the owner-frozen production registry.  Keep the values as archive
# paths (rather than source Paths) so the receipt and cpio namespace never
# contain an absolute local path.
MODULE_REGISTRY: dict[str, str] = {
    "benchmarks/hosts/linux_guest_observer.py": (
        "opt/benchmarks/hosts/linux_guest_observer.py"
    ),
    "benchmarks/hosts/linux_role_launcher.py": "opt/benchmarks/hosts/linux_role_launcher.py",
    "benchmarks/hosts/linux_guest_slot_control.py": (
        "opt/benchmarks/hosts/linux_guest_slot_control.py"
    ),
    "benchmarks/hosts/native_slot_frames.py": "opt/benchmarks/hosts/native_slot_frames.py",
    "benchmarks/hosts/linux_proc_connector.py": "opt/benchmarks/hosts/linux_proc_connector.py",
    "benchmarks/hosts/linux_process_tree_metadata.py": (
        "opt/benchmarks/hosts/linux_process_tree_metadata.py"
    ),
    "benchmarks/hosts/linux_process_observer.py": "opt/benchmarks/hosts/linux_process_observer.py",
    "benchmarks/hosts/linux_http_route_observer.py": (
        "opt/benchmarks/hosts/linux_http_route_observer.py"
    ),
    "benchmarks/hosts/linux_audit_syscall_metadata.py": (
        "opt/benchmarks/hosts/linux_audit_syscall_metadata.py"
    ),
    "benchmarks/hosts/linux_role_boundary_probe.py": "opt/linux_role_boundary_probe.py",
    "benchmarks/hosts/native_fork_observation.py": (
        "opt/benchmarks/hosts/native_fork_observation.py"
    ),
    "benchmarks/hosts/v013_native_event_adapter.py": (
        "opt/benchmarks/hosts/v013_native_event_adapter.py"
    ),
    "benchmarks/hosts/host_process_receipt_v2.py": (
        "opt/benchmarks/hosts/host_process_receipt_v2.py"
    ),
    "contracts/host-process-receipt.v2.schema.json": (
        "opt/contracts/host-process-receipt.v2.schema.json"
    ),
    "adapters/opencode/plugins/deeplaw-native.ts": "opt/plugin-source/deeplaw-native.ts",
    "benchmarks/hosts/linux_mcp_socket_transport.py": "opt/linux_mcp_socket_transport.py",
    "benchmarks/hosts/maintenance_task_mcp.py": (
        "opt/mcp-modules/benchmarks/hosts/maintenance_task_mcp.py"
    ),
    "benchmarks/hosts/maintenance_task_cases.py": (
        "opt/mcp-modules/benchmarks/hosts/maintenance_task_cases.py"
    ),
    "benchmarks/hosts/native_guest/bootstrap.py": "opt/native-bootstrap.py",
    "benchmarks/hosts/native_guest/boundary_gate.py": "opt/boundary_gate.py",
    "benchmarks/hosts/native_guest/opencode_entry.py": "opt/opencode_entry.py",
    "benchmarks/hosts/native_guest/mcp_entry.py": "opt/mcp_entry.py",
    "benchmarks/hosts/native_guest/mcp_client.py": "opt/mcp_client.py",
    "benchmarks/hosts/native_guest/mcp_relay.py": "opt/mcp_relay.py",
    "benchmarks/hosts/native_guest/init.sh": "init",
}

_EXECUTABLE_MODULES: Final[frozenset[str]] = frozenset(
    {
        "benchmarks/hosts/native_guest/init.sh",
    }
)
_EMPTY_PACKAGE_PATHS: Final[tuple[str, ...]] = (
    "opt/benchmarks/__init__.py",
    "opt/benchmarks/hosts/__init__.py",
    "opt/mcp-modules/benchmarks/__init__.py",
    "opt/mcp-modules/benchmarks/hosts/__init__.py",
)
_OWNER_INPUT_PATH: Final[str] = "opt/owner-input.json"
_WHEEL_INPUTS_PATH: Final[str] = "opt/wheel-inputs.json"
_OPENCODE_ARCHIVE_PATH: Final[str] = "opt/opencode"
_WHEEL_ARCHIVE_PREFIX: Final[str] = "opt/python-artifacts"
_INPUTS_DIRECTORY: Final[str] = "inputs"
_OUTPUT_INITRD_NAME: Final[str] = "initrd"
_OUTPUT_RECEIPT_NAME: Final[str] = "receipt.json"


class NativeInitrdBuildError(ValueError):
    """The manifest, inputs, or deterministic archive contract is invalid."""


@dataclass(frozen=True, slots=True)
class _ArchiveEntry:
    name: str
    mode: int
    source: Path | None
    data: bytes | None
    size: int


def _fail(code: str) -> None:
    raise NativeInitrdBuildError(code)


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise NativeInitrdBuildError("json_not_canonical") from error


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_sha256(value: Any, *, code: str = "digest_invalid") -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("manifest_duplicate_key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    _fail("manifest_nonfinite_number")


def _read_manifest(path: Path, expected_sha256: str) -> dict[str, Any]:
    expected = _validate_sha256(expected_sha256, code="manifest_digest_invalid")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            _fail("manifest_file_invalid")
        if info.st_size > MAX_MANIFEST_BYTES:
            _fail("manifest_too_large")
        with os.fdopen(descriptor, "rb") as incoming:
            descriptor = None
            raw = incoming.read(MAX_MANIFEST_BYTES + 1)
    except NativeInitrdBuildError:
        raise
    except (OSError, ValueError) as error:
        raise NativeInitrdBuildError("manifest_file_invalid") from error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
    if len(raw) > MAX_MANIFEST_BYTES:
        _fail("manifest_too_large")
    if _sha256_bytes(raw) != expected:
        _fail("manifest_digest_mismatch")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except NativeInitrdBuildError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise NativeInitrdBuildError("manifest_json_invalid") from error
    if not isinstance(value, dict):
        _fail("manifest_object_required")
    return value


def _closed_object(value: Any, keys: frozenset[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(code)
    return value


def _validate_path_string(value: Any, *, code: str, limit: int = MAX_PATH_CHARS) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or "\x00" in value
        or not value.isascii()
    ):
        _fail(code)
    return value


def _validate_repo_relative_key(value: Any, *, code: str = "module_path_invalid") -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_MODULE_KEY_CHARS
        or not value.isascii()
        or "\\" in value
    ):
        _fail(code)
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        _fail(code)
    return value


def _validate_identifier(value: Any, *, code: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > MAX_IDENTIFIER_CHARS
        or _IDENTIFIER_RE.fullmatch(value) is None
        or not value.isascii()
    ):
        _fail(code)
    return value


def _validate_manifest(value: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    if set(value) != _SCHEMA_KEYS:
        _fail("manifest_keys_invalid")
    if value.get("schema") != SCHEMA:
        _fail("manifest_schema_invalid")
    if value.get("purpose") not in (PURPOSE, FORK_PURPOSE):
        _fail("manifest_purpose_invalid")
    run_id = _validate_identifier(value.get("run_id"), code="run_id_invalid")
    candidate_id = _validate_identifier(
        value.get("candidate_id"), code="candidate_id_invalid"
    )
    base = _closed_object(value.get("base_initrd"), _FILE_KEYS, "base_initrd_invalid")
    opencode = _closed_object(value.get("opencode"), _FILE_KEYS, "opencode_invalid")
    for item, code in ((base, "base_initrd"), (opencode, "opencode")):
        _validate_path_string(item["path"], code=f"{code}_path_invalid")
        _validate_sha256(item["sha256"], code=f"{code}_digest_invalid")
    wheels = value.get("wheels")
    if not isinstance(wheels, list) or not 1 <= len(wheels) <= MAX_WHEELS:
        _fail("wheels_invalid")
    wheel_names: set[str] = set()
    for wheel in wheels:
        item = _closed_object(wheel, _FILE_KEYS, "wheel_invalid")
        path = _validate_path_string(item["path"], code="wheel_path_invalid")
        _validate_sha256(item["sha256"], code="wheel_digest_invalid")
        name = Path(path).name
        if not name or name in {".", ".."} or name.casefold() in wheel_names:
            _fail("wheel_basename_duplicate")
        if _WHEEL_NAME_RE.fullmatch(name) is None:
            _fail("wheel_basename_invalid")
        _validate_archive_path(f"{_WHEEL_ARCHIVE_PREFIX}/{name}", code="wheel_path_invalid")
        wheel_names.add(name.casefold())
    modules = value.get("modules")
    if not isinstance(modules, dict):
        _fail("modules_invalid")
    for key, digest in modules.items():
        _validate_repo_relative_key(key)
        _validate_sha256(digest, code="module_digest_invalid")

    registry = MODULE_REGISTRY
    if not isinstance(registry, Mapping):
        _fail("module_registry_invalid")
    if set(modules) != set(registry):
        _fail("module_registry_mismatch")
    for source_key, archive_name in registry.items():
        _validate_repo_relative_key(source_key, code="module_registry_invalid")
        if not isinstance(archive_name, str):
            _fail("module_registry_invalid")
        _validate_path_string(archive_name, code="module_archive_path_invalid")
        _validate_archive_path(archive_name, code="module_archive_path_invalid")
    return {
        "purpose": value["purpose"],
        "run_id": run_id,
        "candidate_id": candidate_id,
        "base_initrd": base,
        "opencode": opencode,
        "wheels": wheels,
        "modules": modules,
    }, Path.cwd()


def _validate_archive_path(value: str, *, code: str = "archive_path_invalid") -> str:
    if not value or value.startswith("/") or "\\" in value or "\x00" in value:
        _fail(code)
    path = PurePosixPath(value)
    if path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        _fail(code)
    return value


def _manifest_path(raw_path: str, manifest_path: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return manifest_path.parent / path


def _inspect_source(path: Path) -> int:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
    except OSError as error:
        raise NativeInitrdBuildError("input_file_invalid") from error
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
        _fail("input_file_invalid")
    return info.st_size


def _module_executable(source_key: str, archive_name: str) -> bool:
    return source_key in _EXECUTABLE_MODULES or archive_name in {
        "init",
    }


def _copy_bytes(source: Path, output: Any, expected_size: int) -> None:
    copied = 0
    with source.open("rb") as incoming:
        while chunk := incoming.read(1024 * 1024):
            copied += len(chunk)
            if copied > expected_size:
                _fail("frozen_input_changed")
            output.write(chunk)
    if copied != expected_size:
        _fail("frozen_input_changed")


def _pad4(size: int) -> bytes:
    return b"\0" * ((-size) % 4)


def _newc_header(name: str, mode: int, size: int) -> bytes:
    name_bytes = name.encode("utf-8")
    fields = (
        "070701",
        f"{0:08x}",
        f"{mode:08x}",
        f"{0:08x}",
        f"{0:08x}",
        f"{1:08x}",
        f"{0:08x}",
        f"{size:08x}",
        f"{0:08x}",
        f"{0:08x}",
        f"{0:08x}",
        f"{0:08x}",
        f"{len(name_bytes) + 1:08x}",
        f"{0:08x}",
    )
    header = "".join(fields).encode("ascii")
    if len(header) != 110:
        raise AssertionError("newc header must be 110 bytes")
    return header


def _entry(name: str, mode: int, source: Path | None, data: bytes | None) -> _ArchiveEntry:
    _validate_archive_path(name)
    if source is not None and data is not None:
        raise AssertionError("archive entry has two data sources")
    if source is None and data is None:
        raise AssertionError("archive entry has no data source")
    size = source.stat().st_size if source is not None else len(data or b"")
    return _ArchiveEntry(name=name, mode=mode, source=source, data=data, size=size)


def _directory_names(files: list[_ArchiveEntry]) -> list[str]:
    names = {"."}
    for item in files:
        parent = PurePosixPath(item.name).parent
        while str(parent) not in {"", "."}:
            names.add(parent.as_posix())
            parent = parent.parent
    return sorted(names)


def _write_newc(output: Any, files: list[_ArchiveEntry]) -> None:
    names: set[str] = set()
    for item in files:
        if item.name in names:
            _fail("archive_path_duplicate")
        names.add(item.name)
    directories = _directory_names(files)
    for directory in directories:
        if directory in names:
            _fail("archive_path_duplicate")
        names.add(directory)
        name = directory.encode("utf-8") + b"\0"
        output.write(_newc_header(directory, stat.S_IFDIR | 0o755, 0))
        output.write(name)
        output.write(_pad4(110 + len(name)))
    for item in sorted(files, key=lambda value: value.name):
        name = item.name.encode("utf-8") + b"\0"
        output.write(_newc_header(item.name, item.mode, item.size))
        output.write(name)
        output.write(_pad4(110 + len(name)))
        if item.source is not None:
            _copy_bytes(item.source, output, item.size)
        else:
            output.write(item.data or b"")
        output.write(_pad4(item.size))
    trailer = b"TRAILER!!!\0"
    output.write(_newc_header("TRAILER!!!", 0, 0))
    output.write(trailer)
    output.write(_pad4(110 + len(trailer)))


def _frozen_target(inputs: Path, logical_name: str) -> Path:
    # ``logical_name`` is generated from fixed labels and validated module
    # keys; it never comes from a source path in the receipt.
    target = inputs / logical_name
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    return target


def _freeze_input(
    source: Path,
    target: Path,
    expected: str,
    *,
    executable: bool = False,
) -> dict[str, Any]:
    try:
        return _freeze(source, target, expected, executable=executable)
    except NativeInitrdBuildError:
        raise
    except OSError as error:
        raise NativeInitrdBuildError("input_file_invalid") from error
    except ValueError as error:
        code = str(error) or "input_invalid"
        raise NativeInitrdBuildError(code) from error


def _make_archive_entries(
    frozen: Mapping[str, Path],
    *,
    wheel_names: list[str],
    module_specs: list[tuple[str, str, Path]],
    owner_input: bytes,
    wheel_inputs: bytes,
) -> list[_ArchiveEntry]:
    entries = [
        _entry(_OPENCODE_ARCHIVE_PATH, stat.S_IFREG | 0o755, frozen["opencode"], None),
        _entry(_OWNER_INPUT_PATH, stat.S_IFREG | 0o644, None, owner_input),
        _entry(_WHEEL_INPUTS_PATH, stat.S_IFREG | 0o644, None, wheel_inputs),
    ]
    for wheel_name in wheel_names:
        entries.append(
            _entry(
                f"{_WHEEL_ARCHIVE_PREFIX}/{wheel_name}",
                stat.S_IFREG | 0o644,
                frozen[f"wheel:{wheel_name}"],
                None,
            )
        )
    for source_key, archive_name, path in module_specs:
        entries.append(
            _entry(
                archive_name,
                stat.S_IFREG | (0o755 if _module_executable(source_key, archive_name) else 0o644),
                path,
                None,
            )
        )
    for package_path in _EMPTY_PACKAGE_PATHS:
        entries.append(_entry(package_path, stat.S_IFREG | 0o644, None, b""))
    return entries


def _record_digest(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return _sha256_bytes(_canonical_json(body))


def _write_bytes_create(path: Path, value: bytes, mode: int) -> None:
    with path.open("xb") as output:
        output.write(value)
    path.chmod(mode)


def _write_initrd(
    path: Path,
    base: Path,
    archive_entries: list[_ArchiveEntry],
) -> int:
    copied = 0
    with path.open("xb") as output:
        with base.open("rb") as incoming:
            while chunk := incoming.read(1024 * 1024):
                copied += len(chunk)
                output.write(chunk)
        with gzip.GzipFile(
            fileobj=output,
            mode="wb",
            filename="",
            mtime=0,
            compresslevel=9,
        ) as compressed:
            _write_newc(compressed, archive_entries)
    path.chmod(0o644)
    return copied


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as incoming:
        while chunk := incoming.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_initrd(manifest: Path, manifest_sha256: str, destination: Path) -> dict[str, Any]:
    """Freeze manifest inputs and build one deterministic engineering initrd.

    ``destination`` must not exist before this call.  A failed build removes
    only the fresh destination created by this invocation, so an owner can
    reopen the operation with corrected inputs and a new destination.
    """

    manifest = Path(manifest)
    destination = Path(destination)
    if os.path.lexists(destination):
        _fail("destination_exists")
    manifest_value = _read_manifest(manifest, manifest_sha256)
    normalized, repo_root = _validate_manifest(manifest_value)
    destination = destination.absolute()
    created = False
    try:
        destination.mkdir(mode=0o700)
        destination.chmod(0o700)
        created = True
        inputs = destination / _INPUTS_DIRECTORY
        inputs.mkdir(mode=0o700)
        inputs.chmod(0o700)

        base_spec = normalized["base_initrd"]
        opencode_spec = normalized["opencode"]
        wheels_spec = normalized["wheels"]
        modules_spec = normalized["modules"]

        source_specs: list[tuple[str, Path, str, bool]] = [
            (
                "base_initrd",
                _manifest_path(base_spec["path"], manifest.absolute()),
                base_spec["sha256"],
                False,
            ),
            (
                "opencode",
                _manifest_path(opencode_spec["path"], manifest.absolute()),
                opencode_spec["sha256"],
                False,
            ),
        ]
        wheel_names: list[str] = []
        for wheel in wheels_spec:
            source = _manifest_path(wheel["path"], manifest.absolute())
            wheel_name = Path(wheel["path"]).name
            wheel_names.append(wheel_name)
            source_specs.append((f"wheel:{wheel_name}", source, wheel["sha256"], False))
        module_specs: list[tuple[str, str, Path]] = []
        for source_key in sorted(MODULE_REGISTRY):
            archive_name = MODULE_REGISTRY[source_key]
            source = repo_root / PurePosixPath(source_key)
            module_specs.append((source_key, archive_name, source))
            source_specs.append((f"module:{source_key}", source, modules_spec[source_key], False))

        total_size = 0
        for _, source, _, _ in source_specs:
            total_size += _inspect_source(source)
            if total_size > MAX_TOTAL_INPUT_BYTES:
                _fail("total_input_size_exceeded")

        frozen: dict[str, Path] = {}
        input_records: list[dict[str, Any]] = []
        for logical_name, source, expected, _ in source_specs:
            target_name = logical_name.replace(":", "/", 1)
            target = _frozen_target(inputs, target_name)
            executable = logical_name == "module:benchmarks/hosts/native_guest/init.sh"
            record = _freeze_input(source, target, expected, executable=executable)
            frozen[logical_name] = target
            input_records.append(
                {
                    "logical_name": logical_name,
                    "sha256": record["sha256"],
                    "bytes": record["bytes"],
                }
            )
        if sum(int(record["bytes"]) for record in input_records) > MAX_TOTAL_INPUT_BYTES:
            _fail("total_input_size_exceeded")

        owner_input = _canonical_json(
            {
                "candidate_id": normalized["candidate_id"],
                "purpose": normalized["purpose"],
                "run_id": normalized["run_id"],
            }
        )
        wheel_inputs = _canonical_json(
            [
                {"name": name, "sha256": digest}
                for name, digest in sorted(
                    ((Path(wheel["path"]).name, wheel["sha256"]) for wheel in wheels_spec),
                    key=lambda item: item[0].casefold(),
                )
            ]
        )
        archive_entries = _make_archive_entries(
            frozen,
            wheel_names=wheel_names,
            module_specs=[
                (key, archive, frozen[f"module:{key}"])
                for key, archive, _ in module_specs
            ],
            owner_input=owner_input,
            wheel_inputs=wheel_inputs,
        )
        initrd_path = destination / _OUTPUT_INITRD_NAME
        _write_initrd(initrd_path, frozen["base_initrd"], archive_entries)
        initrd_bytes = initrd_path.stat().st_size
        initrd_sha256 = _sha256_file(initrd_path)

        generated_record = {
            "logical_name": "owner-input.json",
            "sha256": _sha256_bytes(owner_input),
            "bytes": len(owner_input),
        }
        generated_wheel_record = {
            "logical_name": "wheel-inputs.json",
            "sha256": _sha256_bytes(wheel_inputs),
            "bytes": len(wheel_inputs),
        }
        files = sorted(
            [*input_records, generated_record, generated_wheel_record],
            key=lambda item: item["logical_name"],
        )
        receipt: dict[str, Any] = {
            "schema": SCHEMA,
            "purpose": normalized["purpose"],
            "run_id": normalized["run_id"],
            "candidate_id": normalized["candidate_id"],
            "manifest_sha256": _validate_sha256(manifest_sha256, code="manifest_digest_invalid"),
            "files": files,
            "initrd_sha256": initrd_sha256,
            "initrd_bytes": initrd_bytes,
            "formal_admission": False,
            "record_sha256": "",
        }
        receipt["record_sha256"] = _record_digest(receipt)
        _write_bytes_create(
            destination / _OUTPUT_RECEIPT_NAME,
            _canonical_json(receipt) + b"\n",
            0o400,
        )
        return receipt
    except Exception:
        if created and destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = build_initrd(args.manifest, args.manifest_sha256, args.destination)
    except NativeInitrdBuildError as error:
        print(json.dumps({"formal_admission": False, "error": str(error)}), file=sys.stderr)
        return 2
    print(_canonical_json(receipt).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MAX_FILE_BYTES",
    "MAX_MANIFEST_BYTES",
    "MAX_TOTAL_INPUT_BYTES",
    "MAX_WHEELS",
    "MODULE_REGISTRY",
    "PURPOSE",
    "SCHEMA",
    "NativeInitrdBuildError",
    "build_initrd",
    "main",
]
