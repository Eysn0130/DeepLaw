"""Pure validation primitives for an explicit broker non-stdlib runtime tree."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

RUNTIME_MANIFEST_SCHEMA_VERSION = "deeplaw.broker-runtime-input/v1"
RUNTIME_ROOT_IDENTITY = "private_external_nonstdlib_runtime"
RUNTIME_MANIFEST_MAX_BYTES = 512 * 1024
RUNTIME_MANIFEST_MAX_FILES = 4096
RUNTIME_FILE_MAX_BYTES = 16 * 1024 * 1024
RUNTIME_TOTAL_MAX_BYTES = 64 * 1024 * 1024
RUNTIME_PATH_MAX_BYTES = 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# This is deliberately stdlib-only and contains no caller-provided source.
BROKER_RUNTIME_BOOTSTRAP = (
    "import sys\n"
    "sys.dont_write_bytecode=True\n"
    "import os,runpy\n"
    "runtime_root,staged_source=sys.argv[1:3]\n"
    "source_root=os.path.join(runtime_root,'source')\n"
    "site_packages=os.path.join(runtime_root,'site-packages')\n"
    "if not os.path.isdir(source_root) or not os.path.isdir(site_packages):"
    " raise RuntimeError('broker runtime roots are incomplete')\n"
    "sys.path[:]=[source_root,site_packages]+sys.path\n"
    "sys.argv=[staged_source,*sys.argv[3:]]\n"
    "runpy.run_path(staged_source,run_name='__main__')\n"
)
BROKER_RUNTIME_BOOTSTRAP_SHA256 = hashlib.sha256(
    BROKER_RUNTIME_BOOTSTRAP.encode("utf-8")
).hexdigest()


class BrokerRuntimeInputError(ValueError):
    """An explicit runtime manifest or relative path is unsafe or malformed."""


@dataclass(frozen=True)
class RuntimeFile:
    relative_path: str
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class RuntimeManifest:
    runtime_root_identity: str
    files: tuple[RuntimeFile, ...]
    wheel_sha256: str
    lock_sha256: str

    @property
    def total_bytes(self) -> int:
        return sum(item.byte_size for item in self.files)

    @property
    def artifact_digests(self) -> dict[str, str]:
        return {
            "wheel_sha256": self.wheel_sha256,
            "lock_sha256": self.lock_sha256,
        }

    def as_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_MANIFEST_SCHEMA_VERSION,
            "runtime_root_identity": self.runtime_root_identity,
            "files": [
                {
                    "relative_path": item.relative_path,
                    "byte_size": item.byte_size,
                    "sha256": item.sha256,
                }
                for item in self.files
            ],
            "artifacts": dict(self.artifact_digests),
        }


def validate_runtime_relative_path(value: object) -> str:
    """Validate one canonical, root-relative POSIX path."""

    if not isinstance(value, str) or not value:
        raise BrokerRuntimeInputError("runtime manifest path is invalid")
    if len(value.encode("utf-8")) > RUNTIME_PATH_MAX_BYTES:
        raise BrokerRuntimeInputError("runtime manifest path exceeds its byte bound")
    if "\x00" in value or "\\" in value or value.startswith(("/", "~")):
        raise BrokerRuntimeInputError("runtime manifest path is not root-relative")
    windows = PureWindowsPath(value)
    if windows.drive or windows.root:
        raise BrokerRuntimeInputError("runtime manifest path has a drive or root")
    posix = PurePosixPath(value)
    if posix.is_absolute() or any(part in ("", ".", "..") for part in posix.parts):
        raise BrokerRuntimeInputError("runtime manifest path has unsafe components")
    if "/".join(posix.parts) != value:
        raise BrokerRuntimeInputError("runtime manifest path is not canonical")
    if not (
        value.startswith("source/") or value.startswith("site-packages/")
    ):
        raise BrokerRuntimeInputError("runtime manifest path is outside its module roots")
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise BrokerRuntimeInputError("runtime manifest contains duplicate keys")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise BrokerRuntimeInputError(f"runtime manifest contains non-finite value {value}")


def _sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise BrokerRuntimeInputError(f"runtime manifest {label} is not a SHA-256")
    if value == "0" * 64:
        raise BrokerRuntimeInputError(f"runtime manifest {label} is empty")
    return value


def parse_runtime_manifest(raw: bytes) -> RuntimeManifest:
    """Parse and validate bounded manifest bytes without touching the filesystem."""

    if not isinstance(raw, bytes) or not 1 <= len(raw) <= RUNTIME_MANIFEST_MAX_BYTES:
        raise BrokerRuntimeInputError("runtime manifest exceeds its byte bound")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except BrokerRuntimeInputError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BrokerRuntimeInputError("runtime manifest is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BrokerRuntimeInputError("runtime manifest must be an object")
    if set(value) != {
        "schema_version",
        "runtime_root_identity",
        "files",
        "artifacts",
    }:
        raise BrokerRuntimeInputError("runtime manifest fields are not closed")
    if value["schema_version"] != RUNTIME_MANIFEST_SCHEMA_VERSION:
        raise BrokerRuntimeInputError("runtime manifest schema version is unsupported")
    if value["runtime_root_identity"] != RUNTIME_ROOT_IDENTITY:
        raise BrokerRuntimeInputError("runtime manifest root identity is unsupported")

    raw_files = value["files"]
    if (
        not isinstance(raw_files, list)
        or not 1 <= len(raw_files) <= RUNTIME_MANIFEST_MAX_FILES
    ):
        raise BrokerRuntimeInputError("runtime manifest file count is outside its bound")
    files: list[RuntimeFile] = []
    paths: set[str] = set()
    total_bytes = 0
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or set(raw_file) != {
            "relative_path",
            "byte_size",
            "sha256",
        }:
            raise BrokerRuntimeInputError("runtime manifest file entry is not closed")
        relative_path = validate_runtime_relative_path(raw_file["relative_path"])
        if relative_path in paths:
            raise BrokerRuntimeInputError("runtime manifest contains duplicate paths")
        paths.add(relative_path)
        byte_size = raw_file["byte_size"]
        if type(byte_size) is not int or not 0 <= byte_size <= RUNTIME_FILE_MAX_BYTES:
            raise BrokerRuntimeInputError("runtime manifest file size is outside its bound")
        total_bytes += byte_size
        if total_bytes > RUNTIME_TOTAL_MAX_BYTES:
            raise BrokerRuntimeInputError("runtime manifest total size exceeds its bound")
        files.append(
            RuntimeFile(
                relative_path=relative_path,
                byte_size=byte_size,
                sha256=_sha256(raw_file["sha256"], label="file digest"),
            )
        )

    artifacts = value["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "wheel_sha256",
        "lock_sha256",
    }:
        raise BrokerRuntimeInputError("runtime manifest artifact fields are not closed")
    return RuntimeManifest(
        runtime_root_identity=RUNTIME_ROOT_IDENTITY,
        files=tuple(sorted(files, key=lambda item: item.relative_path)),
        wheel_sha256=_sha256(artifacts["wheel_sha256"], label="wheel digest"),
        lock_sha256=_sha256(artifacts["lock_sha256"], label="lock digest"),
    )


__all__ = [
    "BROKER_RUNTIME_BOOTSTRAP",
    "BROKER_RUNTIME_BOOTSTRAP_SHA256",
    "RUNTIME_FILE_MAX_BYTES",
    "RUNTIME_MANIFEST_MAX_BYTES",
    "RUNTIME_MANIFEST_MAX_FILES",
    "RUNTIME_MANIFEST_SCHEMA_VERSION",
    "RUNTIME_PATH_MAX_BYTES",
    "RUNTIME_ROOT_IDENTITY",
    "RUNTIME_TOTAL_MAX_BYTES",
    "BrokerRuntimeInputError",
    "RuntimeFile",
    "RuntimeManifest",
    "parse_runtime_manifest",
    "validate_runtime_relative_path",
]
